"""
Line up fault events (proxy logs) with request logs and report, per outage:

  failed        requests overlapping the outage that failed
  fell_back     requests overlapping the outage served by the cloud after an edge failure
  no_service_s  outage start -> first successful first token after it
                (how long a user saw nothing; ~ deadline for a good fallback)
  recovery_s    outage end -> first successful first token after it
                (how quickly service resumed once the fault cleared)

Reads results/manifest.jsonl written by run_all.py.

Usage:
    python analyze_faults.py                     # all experiments with outages
    python analyze_faults.py --out fault_summary
"""
import argparse
import json
import os

import numpy as np
import pandas as pd


def outages_from(log_path):
    if not log_path or not os.path.exists(log_path):
        return []
    ev = pd.read_csv(log_path)
    starts = ev[ev["event"] == "outage_start"]["epoch_s"].tolist()
    ends = ev[ev["event"] == "outage_end"]["epoch_s"].tolist()
    return list(zip(starts, ends[:len(starts)]))


def analyse(entry):
    req = pd.read_csv(entry["requests_csv"])
    req["ok"] = (req["status"] == 200) & req["ttft_s"].notna()
    req["first_token_epoch"] = req["t_start_epoch"] + req["ttft_s"]
    rows = []
    for where, log in entry["proxy_logs"].items():
        for k, (s, e) in enumerate(outages_from(log)):
            overlap = req[(req["t_start_epoch"] < e) & (req["t_end_epoch"] > s)]
            good = req[req["ok"]]
            after_s = good[good["first_token_epoch"] >= s]["first_token_epoch"]
            after_e = good[good["first_token_epoch"] >= e]["first_token_epoch"]
            rows.append({
                "condition": entry["condition"], "strategy": entry["strategy"],
                "fault_on": where, "outage": k + 1, "duration_s": e - s,
                "requests_overlapping": len(overlap),
                "failed": int((~overlap["ok"]).sum()),
                "fell_back": int(overlap.get("fell_back", pd.Series(dtype=bool))
                                 .astype(str).str.lower().eq("true").sum()),
                "no_service_s": (after_s.min() - s) if len(after_s) else np.nan,
                "recovery_s": (after_e.min() - e) if len(after_e) else np.nan,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="fault_summary")
    args = ap.parse_args()

    manifest = os.path.join(args.results, "manifest.jsonl")
    if not os.path.exists(manifest):
        raise SystemExit(f"{manifest} not found; run experiments with run_all.py")
    entries = [json.loads(l) for l in open(manifest) if l.strip()]
    rows = []
    for e in entries:
        if e.get("requests_csv") and os.path.exists(e["requests_csv"]):
            rows += analyse(e)
    if not rows:
        raise SystemExit("No outages found in the proxy logs.")
    per_outage = pd.DataFrame(rows)
    per_outage.to_csv(args.out + "_per_outage.csv", index=False)

    summary = per_outage.groupby(["condition", "strategy", "fault_on"]).agg(
        outages=("outage", "count"),
        failed_total=("failed", "sum"),
        fell_back_total=("fell_back", "sum"),
        no_service_p50=("no_service_s", "median"),
        no_service_max=("no_service_s", "max"),
        recovery_p50=("recovery_s", "median"),
        recovery_max=("recovery_s", "max"),
    ).reset_index()
    summary.to_csv(args.out + ".csv", index=False)
    with open(args.out + ".md", "w") as f:
        f.write("| Condition | Fault on | Outages | Failed | Fell back | No service p50 (s) | "
                "No service max | Recovery p50 (s) | Recovery max |\n|---|---|---|---|---|---|---|---|---|\n")
        for r in summary.itertuples():
            f.write(f"| {r.condition} | {r.fault_on} | {r.outages} | {r.failed_total} | "
                    f"{r.fell_back_total} | {r.no_service_p50:.2f} | {r.no_service_max:.2f} | "
                    f"{r.recovery_p50:.2f} | {r.recovery_max:.2f} |\n")
    pd.set_option("display.width", 200)
    print(summary.round(2).to_string(index=False))
    print(f"\nSaved {args.out}.csv, {args.out}.md, {args.out}_per_outage.csv")


if __name__ == "__main__":
    main()
