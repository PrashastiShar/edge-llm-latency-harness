"""
Compare conditions: one CDF figure plus a p50/p95/p99 table.

Usage:
    python compare.py                                   # every condition in results/
    python compare.py --conditions edge-idle edge-battery edge-busy edge-stable
"""
import argparse
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def load(results_dir, conditions):
    frames = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*_requests.csv"))):
        df = pd.read_csv(path)
        if df.empty:
            continue
        cond = str(df["condition"].iloc[0])
        if cond == "smoketest" or (conditions and cond not in conditions):
            continue
        df["source_file"] = os.path.basename(path)
        frames.append(df)
    if not frames:
        raise SystemExit(f"No matching *_requests.csv files in {results_dir}")
    df = pd.concat(frames, ignore_index=True)
    return df[(df["status"] == 200) & df["ttft_s"].notna()]


def summarise(df, order):
    rows = []
    for cond in order:
        g = df[df["condition"] == cond]
        e2e = g["total_s"].to_numpy()
        per_run_p95 = g.groupby(["source_file", "run"])["total_s"].quantile(0.95)
        rows.append({
            "condition": cond,
            "n": len(g),
            "runs": len(per_run_p95),
            "mean_s": e2e.mean(),
            "p50_s": np.percentile(e2e, 50),
            "p95_s": np.percentile(e2e, 95),
            "p99_s": np.percentile(e2e, 99),
            "max_s": e2e.max(),
            "p95_run_sd_s": per_run_p95.std(ddof=1) if len(per_run_p95) > 1 else np.nan,
            "ttft_p95_s": np.percentile(g["ttft_s"], 95),
            "tpot_p50_s": g["tpot_s"].median(),
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--conditions", nargs="*", help="which conditions, in legend order")
    ap.add_argument("--baseline", default="edge-idle", help="condition to compare against")
    ap.add_argument("--out", default="comparison")
    args = ap.parse_args()

    df = load(args.results, args.conditions)
    order = args.conditions or sorted(df["condition"].unique())
    order = [c for c in order if c in set(df["condition"])]
    table = summarise(df, order)

    if args.baseline in set(table["condition"]):
        base = table.set_index("condition").loc[args.baseline]
        for col in ["mean_s", "p95_s", "p99_s"]:
            table[col.replace("_s", "_vs_base")] = (table[col] / base[col] - 1) * 100

    pd.set_option("display.width", 200)
    print(table.round(3).to_string(index=False))
    table.round(4).to_csv(args.out + "_table.csv", index=False)
    with open(args.out + "_table.md", "w") as f:
        f.write("| Condition | n | Mean (s) | p50 | p95 | p99 | Max | p95 run sd |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in table.itertuples():
            f.write(f"| {r.condition} | {r.n} | {r.mean_s:.2f} | {r.p50_s:.2f} | {r.p95_s:.2f} | "
                    f"{r.p99_s:.2f} | {r.max_s:.2f} | {r.p95_run_sd_s:.2f} |\n")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for cond in order:
        v = np.sort(df.loc[df["condition"] == cond, "total_s"].to_numpy())
        y = np.arange(1, len(v) + 1) / len(v)
        axes[0].step(v, y, where="post", label=f"{cond} (n={len(v)})")
        axes[1].step(v, 1 - y + 1 / len(v), where="post", label=cond)
    axes[0].set_ylabel("Cumulative probability")
    axes[0].set_title("End-to-end latency CDF")
    for q in (0.5, 0.95, 0.99):
        axes[0].axhline(q, color="grey", lw=0.6, ls=":")
    axes[1].set_yscale("log")
    axes[1].set_ylabel("P(latency > x)  [log]")
    axes[1].set_title("Tail (complementary CDF)")
    for ax in axes:
        ax.set_xlabel("End-to-end latency (s)")
        ax.grid(True, alpha=0.3)
    axes[0].legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(args.out + "_cdf.png", dpi=200)
    print(f"\nSaved {args.out}_cdf.png, {args.out}_table.csv, {args.out}_table.md")


if __name__ == "__main__":
    main()
