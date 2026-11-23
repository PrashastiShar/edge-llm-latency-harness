"""
Cost and energy accounting from the request logs (paper's Mini-project B).

For every logged request it attributes
  edge time   : served by the edge, or the wasted edge attempt before a fallback
  cloud time  : served by the cloud (end-to-end time; an over-estimate of server busy time)
  tokens/bytes: completion tokens and response bytes of cloud-served requests
and prices them under the labelled scenarios in cost_scenarios.json (hypothetical
prices, not billing data). Failed requests still cost money, so the headline metric
is cost per SUCCESSFUL request.

Break-even: cloud prices are scaled by a multiplier m. For each fallback-X / cloud-X
pair it finds where the hybrid stops being cheaper than cloud-only, and reports it as
a price ratio (cloud cost per cloud-served request / edge cost per edge-served request).

Usage:
    python cost.py --scenarios cost_scenarios.json --base mid
"""
import argparse
import glob
import json
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
        frames.append(df)
    if not frames:
        raise SystemExit("no request logs found")
    df = pd.concat(frames, ignore_index=True)
    # logs from before strategies existed were edge-only
    for col, default in [("served_by", "edge"), ("fell_back", False), ("wasted_s", 0.0),
                         ("resp_bytes", 0), ("completion_tokens", np.nan)]:
        if col not in df:
            df[col] = default
    df["served_by"] = df["served_by"].fillna("edge")
    df["fell_back"] = df["fell_back"].astype(str).str.lower().eq("true")
    df["wasted_s"] = df["wasted_s"].fillna(0.0)
    df["ok"] = (df["status"] == 200) & df["ttft_s"].notna()
    tokens = df["completion_tokens"].fillna(df.get("n_chunks", 0))
    df["edge_s"] = np.where(df["served_by"] == "edge", df["total_s"], df["wasted_s"])
    df["cloud_s"] = np.where(df["served_by"] == "cloud", df["total_s"] - df["wasted_s"], 0.0)
    df["cloud_tokens"] = np.where(df["served_by"] == "cloud", tokens, 0)
    df["edge_tokens"] = np.where(df["served_by"] == "edge", tokens, 0)
    df["cloud_bytes"] = np.where(df["served_by"] == "cloud", df["resp_bytes"], 0)
    return df


def parts(df, sc):
    """Per-condition totals: edge cost part, cloud cost part (at multiplier 1)."""
    edge_per_s = (sc["edge_power_w"] / 1000 / 3600 * sc["electricity_per_kwh"]
                  + sc.get("edge_device_per_hour", 0) / 3600)
    out = []
    for cond, g in df.groupby("condition"):
        cloud = (sc.get("cloud_vm_per_hour", 0) / 3600 * g["cloud_s"].sum()
                 / max(sc.get("cloud_utilisation", 1), 1e-9)
                 + sc.get("cloud_api_per_m_output_tokens", 0) * g["cloud_tokens"].sum() / 1e6
                 + sc.get("egress_per_gb", 0) * g["cloud_bytes"].sum() / 1e9)
        edge = edge_per_s * g["edge_s"].sum()
        n_ok = int(g["ok"].sum())
        n_edge = int((g["served_by"] == "edge").sum())
        n_cloud = int((g["served_by"] == "cloud").sum())
        edge_j = sc["edge_power_w"] * g["edge_s"].sum()
        out.append({"condition": cond, "n": len(g), "n_ok": n_ok,
                    "success_rate": n_ok / len(g), "edge_served": n_edge,
                    "cloud_served": n_cloud, "edge_part": edge, "cloud_part": cloud,
                    "edge_J_per_edge_token": edge_j / max(g["edge_tokens"].sum(), 1),
                    "edge_cost_per_edge_req": edge / n_edge if n_edge else np.nan,
                    "cloud_cost_per_cloud_req": cloud / n_cloud if n_cloud else np.nan})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--scenarios", default="cost_scenarios.json")
    ap.add_argument("--conditions", nargs="*")
    ap.add_argument("--base", default="mid", help="scenario used for the break-even chart")
    ap.add_argument("--out", default="cost")
    args = ap.parse_args()

    spec = json.load(open(args.scenarios))
    cur = spec.get("currency", "units")
    df = load(args.results, args.conditions)

    tables = []
    for name, sc in spec["scenarios"].items():
        t = parts(df, sc)
        t["scenario"] = name
        t["cost_per_success"] = (t["edge_part"] + t["cloud_part"]) / t["n_ok"].clip(lower=1)
        tables.append(t)
    table = pd.concat(tables, ignore_index=True)
    table.to_csv(args.out + "_table.csv", index=False)

    wide = table.pivot(index="condition", columns="scenario", values="cost_per_success")
    with open(args.out + "_table.md", "w") as f:
        f.write(f"Cost per successful request ({cur}); hypothetical labelled price scenarios.\n\n")
        f.write("| Condition | " + " | ".join(wide.columns) + " |\n")
        f.write("|---|" + "---|" * len(wide.columns) + "\n")
        for cond, r in wide.iterrows():
            f.write(f"| {cond} | " + " | ".join(f"{v:.6f}" for v in r.values) + " |\n")
    pd.set_option("display.width", 200)
    print(wide.to_string(float_format=lambda v: f"{v:.6f}"))

    # break-even: cost_X(m) = edge_part + m * cloud_part, per successful request
    base = table[table["scenario"] == args.base].set_index("condition")
    m = np.logspace(-3, 3, 400)
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    for cond, r in base.iterrows():
        ax.plot(m, (r["edge_part"] + m * r["cloud_part"]) / max(r["n_ok"], 1), label=cond)
    lines = []
    for cond in base.index:
        if not cond.startswith("fallback-"):
            continue
        twin = "cloud-" + cond[len("fallback-"):]
        if twin not in base.index:
            continue
        h, c = base.loc[cond], base.loc[twin]
        eh, ch = h["edge_part"] / max(h["n_ok"], 1), h["cloud_part"] / max(h["n_ok"], 1)
        cc = c["cloud_part"] / max(c["n_ok"], 1)
        if cc > ch:
            m_star = eh / (cc - ch)
            e_req = h["edge_cost_per_edge_req"]
            ratio = (m_star * c["cloud_cost_per_cloud_req"] / e_req) if e_req else np.nan
            ax.axvline(m_star, color="grey", ls=":", lw=0.8)
            lines.append(f"{cond} vs {twin}: hybrid cheaper while cloud prices > {m_star:.4g} x "
                         f"'{args.base}' (price ratio cloud/edge per request > {ratio:.3g})")
        else:
            lines.append(f"{cond} vs {twin}: hybrid never cheaper (it uses as much cloud as cloud-only)")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(f"Cloud price multiplier (x scenario '{args.base}')")
    ax.set_ylabel(f"Cost per successful request ({cur})")
    ax.set_title("Break-even: hybrid vs cloud-only")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(args.out + "_breakeven.png", dpi=200)
    with open(args.out + "_breakeven.txt", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    print(f"\nSaved {args.out}_table.csv/.md, {args.out}_breakeven.png/.txt")


if __name__ == "__main__":
    main()
