"""
End-to-end smoke test, runnable in CI: mock edge + mock cloud, every strategy,
jitter, bursty arrivals and outages, then all analysis scripts. ~2 minutes.

    python tests/smoke_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def main():
    tmp = tempfile.mkdtemp(prefix="harness_smoke_")
    res = os.path.join(tmp, "results")
    servers = [
        subprocess.Popen([PY, os.path.join(ROOT, "tests", "mock_server.py"), "--port", "21434",
                          "--ttft", "0.05", "--tokens", "20", "--token-delay", "0.01", "--serial"]),
        subprocess.Popen([PY, os.path.join(ROOT, "tests", "mock_server.py"), "--port", "21435",
                          "--ttft", "0.15", "--tokens", "20", "--token-delay", "0.005"]),
    ]
    try:
        time.sleep(1)
        faults = os.path.join(tmp, "faults")
        os.makedirs(faults)
        json.dump({"delay_ms": 40, "jitter_ms": 20}, open(os.path.join(faults, "jit.json"), "w"))
        json.dump({"trace": os.path.join(ROOT, "traces", "synthetic_mobile.csv")},
                  open(os.path.join(faults, "trace.json"), "w"))
        out_r = {"outages": [{"first_s": 2, "every_s": 6, "duration_s": 2}], "outage_mode": "reset"}
        out_b = {**out_r, "outage_mode": "blackhole"}
        json.dump(out_r, open(os.path.join(faults, "reset.json"), "w"))
        json.dump(out_b, open(os.path.join(faults, "bh.json"), "w"))
        f = lambda n: os.path.join(faults, n)
        cfg = {
            "results_dir": res,
            "endpoints": {"edge": {"target": "127.0.0.1:21434", "proxy_port": 28081},
                          "cloud": {"target": "127.0.0.1:21435", "proxy_port": 28082}},
            "defaults": {"model": "mock", "requests": 25, "runs": 1, "warmup": 1,
                         "ttft_deadline": 0.5, "gap": 0.1, "no_plot": True},
            "experiments": [
                {"condition": "edge-proxied", "strategy": "single", "via": "edge"},
                {"condition": "cloud-stable", "strategy": "single", "via": "cloud"},
                {"condition": "cloud-jittery", "strategy": "single", "via": "cloud",
                 "faults": {"cloud": f("jit.json")}},
                {"condition": "cloud-trace", "strategy": "single", "via": "cloud",
                 "faults": {"cloud": f("trace.json")}},
                {"condition": "fallback-stable", "strategy": "fallback"},
                {"condition": "fallback-bursty", "strategy": "fallback",
                 "options": {"arrival": "bursty", "burst_size": 8, "burst_every": 2}},
                {"condition": "edge-bursty", "strategy": "single", "via": "edge",
                 "options": {"arrival": "bursty", "burst_size": 8, "burst_every": 2}},
                {"condition": "cloud-bursty", "strategy": "single", "via": "cloud",
                 "options": {"arrival": "bursty", "burst_size": 8, "burst_every": 2}},
                {"condition": "edge-outage", "strategy": "single", "via": "edge",
                 "faults": {"edge": f("reset.json")}},
                {"condition": "fallback-edge-outage", "strategy": "fallback",
                 "faults": {"edge": f("reset.json")}},
                {"condition": "cloud-outage", "strategy": "single", "via": "cloud",
                 "faults": {"cloud": f("bh.json")}, "options": {"timeout": 1}},
            ],
        }
        cfg_path = os.path.join(tmp, "experiments.json")
        json.dump(cfg, open(cfg_path, "w"), indent=1)
        env = {**os.environ, "LLM_API_KEY": "unused"}
        subprocess.check_call([PY, os.path.join(ROOT, "run_all.py"), "--config", cfg_path], env=env)

        os.chdir(tmp)
        subprocess.check_call([PY, os.path.join(ROOT, "compare.py"), "--results", res,
                               "--baseline", "edge-proxied"])
        subprocess.check_call([PY, os.path.join(ROOT, "analyze_faults.py"), "--results", res])
        subprocess.check_call([PY, os.path.join(ROOT, "cost.py"), "--results", res,
                               "--scenarios", os.path.join(ROOT, "cost_scenarios.json")])

        t = pd.read_csv("comparison_table.csv").set_index("condition")
        fs = pd.read_csv("fault_summary.csv").set_index("condition")
        checks = {
            "stable runs all succeed": t.loc["cloud-stable", "success_rate"] == 1.0,
            "jitter raises cloud latency": t.loc["cloud-jittery", "p50_s"] > t.loc["cloud-stable", "p50_s"],
            "edge outage causes failures": fs.loc["edge-outage", "failed_total"] > 0,
            "fallback hides edge outage": t.loc["fallback-edge-outage", "success_rate"] == 1.0
                                          and t.loc["fallback-edge-outage", "fallback_share"] > 0,
            "bursts make fallback offload": t.loc["fallback-bursty", "fallback_share"] > 0,
            "blackhole outage causes failures": fs.loc["cloud-outage", "failed_total"] > 0,
            "recovery time measured": fs["recovery_p50"].notna().any(),
            "cost table written": os.path.exists("cost_table.md"),
        }
        print()
        for name, passed in checks.items():
            print(("PASS " if passed else "FAIL ") + name)
        if not all(checks.values()):
            sys.exit(1)
        print(f"\nAll checks passed. Outputs in {tmp}")
    finally:
        for s in servers:
            s.terminate()


if __name__ == "__main__":
    main()
