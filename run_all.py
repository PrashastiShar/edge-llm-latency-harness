"""
Run the experiment matrix in experiments.json: one configuration file for everything.

For each experiment it starts a fault proxy in front of the edge and/or cloud endpoint,
runs benchmark.py through the proxies, stops them, and appends a line to
results/manifest.jsonl linking the request log to the proxy event logs.

Usage:
    python run_all.py                        # everything
    python run_all.py --only cloud-stable    # one or more by condition name
    python run_all.py --dry-run              # print the commands only
"""
import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = "/v1/chat/completions"


def wait_port(host, port, timeout=10):
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "experiments.json"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    out = cfg.get("results_dir", "results")
    os.makedirs(out, exist_ok=True)
    endpoints = cfg["endpoints"]           # name -> {"target": "host:port", "proxy_port": n}
    defaults = cfg.get("defaults", {})

    for exp in cfg["experiments"]:
        cond = exp["condition"]
        if args.only and cond not in args.only:
            continue
        if exp.get("skip"):
            print(f"skipping {cond} (skip: true)")
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        faults = exp.get("faults", {})        # endpoint name -> fault json path (or null)
        used = [exp["via"]] if exp["strategy"] == "single" else ["edge", "cloud"]

        proxies, proxy_logs = [], {}
        for name in used:
            ep = endpoints[name]
            log = os.path.join(out, f"{cond}_{stamp}_proxy-{name}.csv")
            cmd = [sys.executable, os.path.join(HERE, "faultproxy.py"),
                   "--listen", f"127.0.0.1:{ep['proxy_port']}", "--target", ep["target"],
                   "--log", log, "--seed", str(exp.get("seed", 0))]
            if faults.get(name):
                cmd += ["--config", faults[name]]
            proxy_logs[name] = log
            proxies.append(cmd)

        def url(name):
            return f"http://127.0.0.1:{endpoints[name]['proxy_port']}{PATH}"

        bench = [sys.executable, os.path.join(HERE, "benchmark.py"),
                 "--condition", cond, "--strategy", exp["strategy"], "--out", out]
        if exp["strategy"] == "single":
            bench += ["--url", url(exp["via"]), "--endpoint-label", exp["via"]]
        else:
            bench += ["--edge-url", url("edge"), "--cloud-url", url("cloud")]
        opts = {**defaults, **exp.get("options", {})}
        for k, v in opts.items():
            flag = "--" + k.replace("_", "-")
            if isinstance(v, bool):
                if v:
                    bench.append(flag)
            else:
                bench += [flag, str(v)]
        if exp.get("notes"):
            bench += ["--notes", exp["notes"]]

        print(f"\n=== {cond} ===")
        for c in proxies:
            print("proxy:", " ".join(c))
        print("bench:", " ".join(bench))
        if args.dry_run:
            continue

        procs = [subprocess.Popen(c) for c in proxies]
        try:
            for name in used:
                if not wait_port("127.0.0.1", endpoints[name]["proxy_port"]):
                    raise SystemExit(f"proxy for {name} did not start")
            before = set(glob.glob(os.path.join(out, f"{cond}_*_requests.csv")))
            rc = subprocess.call(bench)
            after = set(glob.glob(os.path.join(out, f"{cond}_*_requests.csv")))
        finally:
            for p in procs:
                p.send_signal(signal.SIGINT)
            for p in procs:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
        new = sorted(after - before)
        with open(os.path.join(out, "manifest.jsonl"), "a") as f:
            f.write(json.dumps({"condition": cond, "strategy": exp["strategy"],
                                "via": exp.get("via"), "faults": faults,
                                "requests_csv": new[-1] if new else None,
                                "proxy_logs": proxy_logs, "exit_code": rc,
                                "started_utc": stamp}) + "\n")
        if rc != 0:
            print(f"warning: {cond} exited with code {rc}")


if __name__ == "__main__":
    main()
