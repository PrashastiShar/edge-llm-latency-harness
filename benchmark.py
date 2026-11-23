"""
Cloud-only latency baseline for Mini-project A (tail latency).

Logs every request to CSV, reports p50/p95/p99 for TTFT, per-token time
and end-to-end latency, and plots empirical CDFs.

Usage:
    export GROQ_API_KEY=...
    python latency_test.py --requests 300 --runs 5 --condition stable
"""
import argparse
import csv
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests

DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
# llama3-8b-8192 was shut down on 2025-08-30 and llama-3.1-8b-instant on
# 2026-08-16 (free/dev tier). Check https://console.groq.com/docs/models
# and pass --model to override.
DEFAULT_MODEL = "openai/gpt-oss-20b"
PROMPT = "Write a short paragraph about cloud computing."


def one_request(session, url, headers, payload, timeout):
    """Send one streaming request. Returns a dict of measurements."""
    rec = {"status": None, "ttft_s": None, "total_s": None,
           "n_chunks": 0, "completion_tokens": None, "error": ""}
    t0 = time.perf_counter()
    try:
        with session.post(url, headers=headers, json=payload,
                          stream=True, timeout=timeout) as r:
            rec["status"] = r.status_code
            if r.status_code != 200:
                rec["error"] = r.text[:300].replace("\n", " ")
                rec["total_s"] = time.perf_counter() - t0
                return rec
            for raw in r.iter_lines():
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                if usage:
                    rec["completion_tokens"] = usage.get("completion_tokens")
                for choice in chunk.get("choices", []):
                    content = (choice.get("delta") or {}).get("content")
                    if content:  # the first chunk often carries only the role
                        if rec["ttft_s"] is None:
                            rec["ttft_s"] = time.perf_counter() - t0
                        rec["n_chunks"] += 1
    except (requests.RequestException, json.JSONDecodeError) as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
    rec["total_s"] = time.perf_counter() - t0
    return rec


def tpot(rec):
    """Mean time per output token after the first, in seconds."""
    n = rec["completion_tokens"] or rec["n_chunks"]
    if rec["ttft_s"] is None or not n or n < 2:
        return None
    return (rec["total_s"] - rec["ttft_s"]) / (n - 1)


def summarise(name, values):
    v = np.array([x for x in values if x is not None], dtype=float)
    if v.size == 0:
        print(f"{name:<14} no data")
        return
    p50, p95, p99 = np.percentile(v, [50, 95, 99])
    print(f"{name:<14} n={v.size:<5} mean={v.mean():.4f}  sd={v.std(ddof=1) if v.size > 1 else 0:.4f}  "
          f"p50={p50:.4f}  p95={p95:.4f}  p99={p99:.4f}")
    if v.size < 100:
        print(f"{'':<14} warning: p99 from {v.size} samples is not meaningful")


def ecdf(ax, values, label):
    v = np.sort([x for x in values if x is not None])
    if len(v):
        ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post", label=label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--requests", type=int, default=300, help="per run")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--gap", type=float, default=0.2, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--condition", default="stable", help="label, e.g. stable / jittery-trace")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        sys.exit("Set GROQ_API_KEY in the environment.")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": args.max_tokens,
        "temperature": 0,
    }

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(args.out, f"{args.condition}_{stamp}")
    with open(base + "_config.json", "w") as f:
        json.dump({**vars(args), "payload": {**payload}, "python": sys.version,
                   "platform": platform.platform(), "requests_lib": requests.__version__,
                   "started_utc": stamp}, f, indent=2)

    session = requests.Session()  # reuse the TLS connection, as a real client would
    for _ in range(args.warmup):
        one_request(session, args.url, headers, payload, args.timeout)

    rows = []
    fields = ["run", "i", "wall_utc", "condition", "status", "ttft_s", "tpot_s",
              "total_s", "n_chunks", "completion_tokens", "error"]
    with open(base + "_requests.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for run in range(args.runs):
            for i in range(args.requests):
                wall = datetime.now(timezone.utc).isoformat()
                rec = one_request(session, args.url, headers, payload, args.timeout)
                if rec["status"] == 429:  # rate limited: back off, keep the row
                    time.sleep(5)
                row = {"run": run, "i": i, "wall_utc": wall, "condition": args.condition,
                       "tpot_s": tpot(rec), **rec}
                w.writerow(row)
                f.flush()
                rows.append(row)
                ttft = f"{rec['ttft_s']:.4f}s" if rec["ttft_s"] is not None else "n/a"
                print(f"run {run+1} req {i+1}: status={rec['status']} total={rec['total_s']:.4f}s "
                      f"ttft={ttft} {rec['error'][:80]}")
                time.sleep(args.gap)

    ok = [r for r in rows if r["status"] == 200 and r["ttft_s"] is not None]
    print(f"\n--- {args.condition}: {len(ok)}/{len(rows)} successful ---")
    summarise("TTFT (s)", [r["ttft_s"] for r in ok])
    summarise("TPOT (s)", [r["tpot_s"] for r in ok])
    summarise("End-to-end (s)", [r["total_s"] for r in ok])
    per_run = [np.percentile([r["total_s"] for r in ok if r["run"] == k], 95)
               for k in range(args.runs) if any(r["run"] == k for r in ok)]
    if len(per_run) > 1:
        print(f"p95 end-to-end across runs: mean={np.mean(per_run):.4f}  sd={np.std(per_run, ddof=1):.4f}")

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    ecdf(axes[0], [r["ttft_s"] for r in ok], "TTFT")
    ecdf(axes[1], [r["total_s"] for r in ok], "End-to-end")
    for ax, t in zip(axes, ["Time to first token (s)", "End-to-end latency (s)"]):
        ax.set_xlabel(t)
        ax.set_ylabel("Cumulative probability")
        ax.grid(True, alpha=0.3)
    fig.suptitle(f"{args.model}, condition={args.condition}, n={len(ok)}")
    fig.tight_layout()
    fig.savefig(base + "_cdf.png", dpi=150)
    print(f"Saved {base}_requests.csv, _config.json, _cdf.png")


if __name__ == "__main__":
    main()
