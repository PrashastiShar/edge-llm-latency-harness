"""
Generate a SYNTHETIC mobile-like network trace (t_s,kbps,rtt_ms) for faultproxy.py.

Two-state (good/bad) Markov model with lognormal variation inside each state.
It is labelled synthetic on purpose: prefer a real trace from record_trace.py
(for example on a phone hotspot while walking) or a public dataset.

Usage:
    python make_trace.py --seconds 600 --out traces/synthetic_mobile.csv
"""
import argparse
import csv
import math
import random


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--good-rtt", type=float, default=60, help="median RTT in good state, ms")
    ap.add_argument("--bad-rtt", type=float, default=350, help="median RTT in bad state, ms")
    ap.add_argument("--good-kbps", type=float, default=8000)
    ap.add_argument("--bad-kbps", type=float, default=300)
    ap.add_argument("--p-good-to-bad", type=float, default=0.03, help="per second")
    ap.add_argument("--p-bad-to-good", type=float, default=0.15, help="per second")
    ap.add_argument("--out", default="traces/synthetic_mobile.csv")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    state = "good"
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "kbps", "rtt_ms"])
        for t in range(args.seconds):
            if state == "good" and rng.random() < args.p_good_to_bad:
                state = "bad"
            elif state == "bad" and rng.random() < args.p_bad_to_good:
                state = "good"
            rtt = (args.good_rtt if state == "good" else args.bad_rtt) * math.exp(rng.gauss(0, 0.35))
            kbps = (args.good_kbps if state == "good" else args.bad_kbps) * math.exp(rng.gauss(0, 0.5))
            w.writerow([t, round(kbps, 1), round(rtt, 1)])
    print(f"wrote {args.out} ({args.seconds} s, synthetic)")


if __name__ == "__main__":
    main()
