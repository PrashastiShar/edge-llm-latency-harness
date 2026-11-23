"""
Latency benchmark for OpenAI-compatible chat endpoints (Ollama, vLLM, hosted APIs).

Strategies
  single    send every request to --url (use for edge-only or cloud-only)
  fallback  try --edge-url first; if no first token within --ttft-deadline seconds,
            or the edge request fails, restart the request on --cloud-url

Arrival processes
  closed    one request at a time, --gap seconds apart (default; earlier results used this)
  poisson   open loop, exponential gaps with mean 1/--rate
  bursty    open loop, bursts of --burst-size requests back-to-back, bursts --burst-every s apart

Every request is logged to CSV (status, TTFT, per-token time, end-to-end time, bytes,
which endpoint served it, Unix start/end times for lining up with fault logs).

Usage:
    export LLM_API_KEY=unused          # or GROQ_API_KEY; local servers ignore it
    python benchmark.py --url http://localhost:11434/v1/chat/completions \
        --model qwen2.5:1.5b --requests 100 --runs 5 --condition edge-idle
"""
import argparse
import csv
import json
import os
import platform
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests

DEFAULT_MODEL = "qwen2.5:1.5b"
PROMPT = "Write a short paragraph about cloud computing."

FIELDS = ["run", "i", "wall_utc", "condition", "strategy", "status", "served_by",
          "fell_back", "edge_error", "ttft_s", "tpot_s", "total_s", "wasted_s",
          "n_chunks", "completion_tokens", "req_bytes", "resp_bytes",
          "t_sched_epoch", "t_start_epoch", "t_end_epoch", "error"]

_local = threading.local()
_warned = []


def warn_if_buffered(r):
    """Non-chunked streams are read in 512-byte blocks, which delays TTFT."""
    if not _warned and "chunked" not in r.headers.get("Transfer-Encoding", "").lower():
        _warned.append(1)
        print("warning: response is not chunked; TTFT may be over-estimated by read buffering",
              file=sys.stderr)


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def stream_once(url, headers, payload, connect_timeout, read_timeout):
    """One streaming request. Times are relative to this call's start."""
    rec = {"status": None, "ttft_s": None, "total_s": None, "n_chunks": 0,
           "completion_tokens": None, "req_bytes": len(json.dumps(payload).encode()),
           "resp_bytes": 0, "error": ""}
    t0 = time.perf_counter()
    try:
        with session().post(url, headers=headers, json=payload, stream=True,
                            timeout=(connect_timeout, read_timeout)) as r:
            rec["status"] = r.status_code
            if r.status_code != 200:
                rec["error"] = r.text[:300].replace("\n", " ")
                rec["resp_bytes"] = len(r.content or b"")
                return rec
            warn_if_buffered(r)
            done = False
            for raw in r.iter_lines():
                rec["resp_bytes"] += len(raw) + 1
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    done = True
                    break
                chunk = json.loads(data)
                usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                if usage:
                    rec["completion_tokens"] = usage.get("completion_tokens")
                for choice in chunk.get("choices", []):
                    if (choice.get("delta") or {}).get("content"):
                        if rec["ttft_s"] is None:
                            rec["ttft_s"] = time.perf_counter() - t0
                        rec["n_chunks"] += 1
            if not done:
                rec["status"] = -1
                rec["error"] = "stream ended without [DONE]"
    except (requests.RequestException, json.JSONDecodeError, ConnectionError) as e:
        rec["status"] = -1         # connection failed, timed out, or stream broke
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
        session().close()          # drop a possibly broken keep-alive connection
        _local.__dict__.pop("s", None)
    finally:
        rec["total_s"] = time.perf_counter() - t0
    return rec


def ok(rec):
    return rec["status"] == 200 and rec["ttft_s"] is not None


def tpot(rec):
    n = rec["completion_tokens"] or rec["n_chunks"]
    if rec["ttft_s"] is None or not n or n < 2:
        return None
    return (rec["total_s"] - rec["ttft_s"]) / (n - 1)


def do_request(args, headers, payload_for):
    """Run one logical request under the chosen strategy."""
    t_start = time.time()
    if args.strategy == "single":
        rec = stream_once(args.url, headers, payload_for(args.model),
                          args.connect_timeout, args.timeout)
        rec.update(served_by=args.endpoint_label, fell_back=False, edge_error="", wasted_s=0.0)
    else:
        edge = stream_once(args.edge_url, headers, payload_for(args.edge_model or args.model),
                           args.connect_timeout, args.ttft_deadline)
        if ok(edge):
            rec = edge
            rec.update(served_by="edge", fell_back=False, edge_error="", wasted_s=0.0)
        else:
            cloud = stream_once(args.cloud_url, headers,
                                payload_for(args.cloud_model or args.model),
                                args.connect_timeout, args.timeout)
            wasted = edge["total_s"]
            rec = cloud
            if cloud["ttft_s"] is not None:
                rec["ttft_s"] = cloud["ttft_s"] + wasted
            rec["total_s"] = cloud["total_s"] + wasted
            rec["req_bytes"] += edge["req_bytes"]
            rec["resp_bytes"] += edge["resp_bytes"]
            rec.update(served_by="cloud", fell_back=True,
                       edge_error=(edge["error"] or f"status {edge['status']}")[:200],
                       wasted_s=wasted)
    rec["t_start_epoch"] = t_start
    rec["t_end_epoch"] = time.time()
    rec["tpot_s"] = tpot(rec)
    return rec


def schedule(args, rng):
    """Planned send offsets (seconds from run start) for open-loop arrivals."""
    if args.arrival == "poisson":
        t, out = 0.0, []
        for _ in range(args.requests):
            out.append(t)
            t += rng.expovariate(args.rate)
        return out
    if args.arrival == "bursty":
        return [(k // args.burst_size) * args.burst_every for k in range(args.requests)]
    return None


def summarise(name, values):
    v = np.array([x for x in values if x is not None], dtype=float)
    if v.size == 0:
        print(f"{name:<14} no data")
        return
    p50, p95, p99 = np.percentile(v, [50, 95, 99])
    sd = v.std(ddof=1) if v.size > 1 else 0
    print(f"{name:<14} n={v.size:<5} mean={v.mean():.4f}  sd={sd:.4f}  "
          f"p50={p50:.4f}  p95={p95:.4f}  p99={p99:.4f}")
    if v.size < 100:
        print(f"{'':<14} warning: p99 from {v.size} samples is not meaningful")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["single", "fallback"], default="single")
    ap.add_argument("--url", help="endpoint for --strategy single")
    ap.add_argument("--endpoint-label", default="edge", choices=["edge", "cloud"],
                    help="single strategy: is --url the edge or the cloud? (used by cost.py)")
    ap.add_argument("--edge-url")
    ap.add_argument("--cloud-url")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--edge-model")
    ap.add_argument("--cloud-model")
    ap.add_argument("--ttft-deadline", type=float, default=2.0,
                    help="fallback: give up on the edge if no token arrives within this many seconds")
    ap.add_argument("--arrival", choices=["closed", "poisson", "bursty"], default="closed")
    ap.add_argument("--rate", type=float, default=0.1, help="poisson: mean requests per second")
    ap.add_argument("--burst-size", type=int, default=5)
    ap.add_argument("--burst-every", type=float, default=60.0)
    ap.add_argument("--max-concurrency", type=int, default=16)
    ap.add_argument("--requests", type=int, default=100, help="per run")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--gap", type=float, default=0.2, help="closed loop: seconds between requests")
    ap.add_argument("--connect-timeout", type=float, default=5)
    ap.add_argument("--timeout", type=float, default=120, help="read timeout")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--condition", default="stable")
    ap.add_argument("--notes", default="", help="free text stored in the config file")
    ap.add_argument("--out", default="results")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    if args.strategy == "single" and not args.url:
        sys.exit("--strategy single needs --url")
    if args.strategy == "fallback" and not (args.edge_url and args.cloud_url):
        sys.exit("--strategy fallback needs --edge-url and --cloud-url")

    key = os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not key:
        sys.exit("Set LLM_API_KEY in the environment (any value for local servers).")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def payload_for(model):
        return {"model": model, "messages": [{"role": "user", "content": PROMPT}],
                "stream": True, "stream_options": {"include_usage": True},
                "max_tokens": args.max_tokens, "temperature": 0}

    rng = random.Random(args.seed)
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(args.out, f"{args.condition}_{stamp}")
    with open(base + "_config.json", "w") as f:
        json.dump({**vars(args), "prompt": PROMPT, "python": sys.version,
                   "platform": platform.platform(), "requests_lib": requests.__version__,
                   "started_utc": stamp}, f, indent=2)

    for _ in range(args.warmup):
        do_request(args, headers, payload_for)

    rows, lock = [], threading.Lock()
    with open(base + "_requests.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()

        def record(run, i, t_sched, rec):
            row = {"run": run, "i": i, "condition": args.condition, "strategy": args.strategy,
                   "wall_utc": datetime.fromtimestamp(rec["t_start_epoch"], timezone.utc).isoformat(),
                   "t_sched_epoch": t_sched, **rec}
            with lock:
                w.writerow(row)
                f.flush()
                rows.append(row)
            ttft = f"{rec['ttft_s']:.4f}s" if rec["ttft_s"] is not None else "n/a"
            fb = " [fallback]" if rec.get("fell_back") else ""
            print(f"run {run+1} req {i+1}: status={rec['status']} total={rec['total_s']:.4f}s "
                  f"ttft={ttft}{fb} {rec['error'][:60]}", flush=True)

        for run in range(args.runs):
            plan = schedule(args, rng)
            if plan is None:
                for i in range(args.requests):
                    t_sched = time.time()
                    rec = do_request(args, headers, payload_for)
                    record(run, i, t_sched, rec)
                    if rec["status"] == 429:
                        time.sleep(5)
                    time.sleep(args.gap)
            else:
                t_run = time.time()
                with ThreadPoolExecutor(max_workers=args.max_concurrency) as pool:
                    futures = []
                    for i, off in enumerate(plan):
                        delay = t_run + off - time.time()
                        if delay > 0:
                            time.sleep(delay)
                        t_sched = t_run + off
                        futures.append(pool.submit(
                            lambda i=i, t_sched=t_sched: record(
                                run, i, t_sched, do_request(args, headers, payload_for))))
                    for fut in futures:
                        fut.result()

    good = [r for r in rows if ok(r)]
    print(f"\n--- {args.condition}: {len(good)}/{len(rows)} successful ---")
    if args.strategy == "fallback":
        fb = sum(1 for r in rows if r.get("fell_back"))
        print(f"fallback to cloud: {fb}/{len(rows)}")
    summarise("TTFT (s)", [r["ttft_s"] for r in good])
    summarise("TPOT (s)", [r["tpot_s"] for r in good])
    summarise("End-to-end (s)", [r["total_s"] for r in good])
    per_run = [np.percentile([r["total_s"] for r in good if r["run"] == k], 95)
               for k in range(args.runs) if any(r["run"] == k for r in good)]
    if len(per_run) > 1:
        print(f"p95 end-to-end across runs: mean={np.mean(per_run):.4f}  sd={np.std(per_run, ddof=1):.4f}")

    if not args.no_plot and good:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        for ax, key_, label in [(axes[0], "ttft_s", "Time to first token (s)"),
                                (axes[1], "total_s", "End-to-end latency (s)")]:
            v = np.sort([r[key_] for r in good])
            ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post")
            ax.set_xlabel(label)
            ax.set_ylabel("Cumulative probability")
            ax.grid(True, alpha=0.3)
        fig.suptitle(f"{args.condition} ({args.strategy}, {args.arrival}), n={len(good)}")
        fig.tight_layout()
        fig.savefig(base + "_cdf.png", dpi=150)
    print(f"Saved {base}_requests.csv and _config.json")


if __name__ == "__main__":
    main()
