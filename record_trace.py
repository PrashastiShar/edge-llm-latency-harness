"""
Record a REAL round-trip-time trace (t_s,kbps,rtt_ms) without root.

Measures TCP connect time to a host:port once per interval; one TCP handshake is
about one round trip. Point it at your cloud VM's SSH port and run it while the
laptop is on the network you want to capture (e.g. a phone hotspot while moving).
kbps is left empty (not measured); faultproxy.py then applies only the RTT.

Usage:
    python record_trace.py --host VM_IP --port 22 --seconds 600 --out traces/hotspot_walk.csv
"""
import argparse
import csv
import socket
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    t0 = time.time()
    lost = 0
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "kbps", "rtt_ms"])
        while time.time() - t0 < args.seconds:
            tick = time.time()
            try:
                s = time.perf_counter()
                socket.create_connection((args.host, args.port), timeout=args.timeout).close()
                rtt = (time.perf_counter() - s) * 1000
            except OSError:
                rtt = args.timeout * 1000      # record a timeout as a very slow sample
                lost += 1
            w.writerow([round(tick - t0, 2), "", round(rtt, 1)])
            f.flush()
            time.sleep(max(0, args.interval - (time.time() - tick)))
    print(f"wrote {args.out}; {lost} timeouts recorded as {args.timeout * 1000:.0f} ms")


if __name__ == "__main__":
    main()
