"""
Application-level fault-injection proxy (TCP).

Sits between the benchmark client and an inference server and injects:
  - one-way delay and jitter on every chunk, in both directions (RTT grows by ~2x delay)
  - bandwidth limits, fixed or replayed from a trace (t_s,kbps[,rtt_ms])
  - scheduled outages: in-flight connections are cut and new ones refused ("reset")
    or accepted but never answered ("blackhole")

Runs without root, so it works in WSL and in CI. It does not drop individual
packets (TCP retransmits would hide that anyway); use Linux `tc netem` on a real
Linux host for packet-level loss.

Every fault event is logged with a Unix timestamp so analyze_faults.py can line
it up with benchmark.py's per-request log.

Usage:
    python faultproxy.py --listen 127.0.0.1:18080 --target 127.0.0.1:11434 \
        --config faults/outage.json --log results/proxy_events.csv
"""
import argparse
import asyncio
import csv
import json
import random
import signal
import time


class Faults:
    def __init__(self, cfg, seed):
        self.delay = cfg.get("delay_ms", 0) / 1000
        self.jitter = cfg.get("jitter_ms", 0) / 1000
        self.kbps = cfg.get("bandwidth_kbps")          # None = unlimited
        self.trace = self._load_trace(cfg.get("trace"))
        self.trace_loop = cfg.get("trace_loop", True)
        self.outages = self._expand_outages(cfg.get("outages", []))
        self.mode = cfg.get("outage_mode", "reset")
        self.rng = random.Random(seed)
        self.t0 = time.time()

    @staticmethod
    def _load_trace(path):
        if not path:
            return None
        rows = []
        with open(path) as f:
            for r in csv.DictReader(f):
                rows.append((float(r["t_s"]),
                             float(r["kbps"]) if r.get("kbps") else None,
                             float(r["rtt_ms"]) if r.get("rtt_ms") else None))
        rows.sort()
        return rows

    @staticmethod
    def _expand_outages(spec):
        """[{start_s, duration_s}] or {first_s, every_s, duration_s, count}."""
        out = []
        for o in spec:
            if "every_s" in o:
                for k in range(int(o.get("count", 1000))):
                    s = o.get("first_s", 0) + k * o["every_s"]
                    out.append((s, s + o["duration_s"]))
            else:
                out.append((o["start_s"], o["start_s"] + o["duration_s"]))
        return sorted(out)

    def elapsed(self):
        return time.time() - self.t0

    def in_outage(self, t=None):
        t = self.elapsed() if t is None else t
        return any(s <= t < e for s, e in self.outages)

    def _trace_point(self, t):
        if not self.trace:
            return None, None
        span = self.trace[-1][0] or 1.0
        if self.trace_loop:
            t = t % span
        point = self.trace[0]
        for row in self.trace:
            if row[0] <= t:
                point = row
            else:
                break
        return point[1], point[2]

    def one_way_delay(self):
        kbps, rtt_ms = self._trace_point(self.elapsed())
        base = rtt_ms / 2000 if rtt_ms is not None else self.delay
        d = base + (self.rng.gauss(0, self.jitter) if self.jitter else 0)
        return max(0.0, d)

    def bandwidth_kbps(self):
        kbps, _ = self._trace_point(self.elapsed())
        return kbps if kbps is not None else self.kbps


class Proxy:
    def __init__(self, listen, target, faults, log_path):
        self.listen_host, self.listen_port = listen
        self.target = target
        self.f = faults
        self.conns = {}
        self.next_id = 0
        self.log_file = open(log_path, "w", newline="")
        self.log = csv.writer(self.log_file)
        self.log.writerow(["epoch_s", "elapsed_s", "event", "conn", "detail"])
        self.event("proxy_start", -1, json.dumps({"target": f"{target[0]}:{target[1]}"}))

    def event(self, name, conn, detail=""):
        self.log.writerow([f"{time.time():.6f}", f"{self.f.elapsed():.6f}", name, conn, detail])
        self.log_file.flush()

    async def pump(self, cid, reader, writer, direction):
        """Copy bytes, holding each chunk for delay+jitter and bandwidth, in order.

        Reading and delayed writing run concurrently: every chunk is stamped when it
        arrives and released at arrival + delay, so a constant delay shifts the whole
        stream without bunching tokens together."""
        queue = asyncio.Queue()

        async def read_side():
            link_free = release = time.monotonic()
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    now = time.monotonic()
                    release = max(release, now + self.f.one_way_delay())  # keep order
                    kbps = self.f.bandwidth_kbps()
                    if kbps:
                        link_free = max(link_free, release) + len(data) * 8 / (kbps * 1000)
                        release = link_free
                    await queue.put((release, data))
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                await queue.put((None, None))

        async def write_side():
            while True:
                release, data = await queue.get()
                if data is None:
                    break
                wait = release - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                writer.write(data)
                await writer.drain()

        rtask = asyncio.create_task(read_side())
        try:
            await write_side()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            rtask.cancel()
            try:
                writer.close()
            except Exception:
                pass

    async def handle(self, creader, cwriter):
        cid = self.next_id
        self.next_id += 1
        if self.f.in_outage():
            self.event("refused_in_outage", cid, self.f.mode)
            if self.f.mode == "blackhole":
                self.conns[cid] = (cwriter, None, [])
                return  # hold the socket open, never answer
            cwriter.transport.abort()
            return
        try:
            sreader, swriter = await asyncio.open_connection(*self.target)
        except OSError as e:
            self.event("upstream_connect_failed", cid, str(e))
            cwriter.transport.abort()
            return
        self.event("conn_open", cid)
        tasks = [asyncio.create_task(self.pump(cid, creader, swriter, "up")),
                 asyncio.create_task(self.pump(cid, sreader, cwriter, "down"))]
        self.conns[cid] = (cwriter, swriter, tasks)
        await asyncio.gather(*tasks, return_exceptions=True)
        self.conns.pop(cid, None)
        self.event("conn_close", cid)

    def cut_all(self):
        for cid, (cw, sw, tasks) in list(self.conns.items()):
            for t in tasks:
                t.cancel()
            for w in (cw, sw):
                if w is not None:
                    w.transport.abort()
            self.event("conn_reset_by_outage", cid)
        self.conns.clear()

    async def outage_clock(self):
        for s, e in self.f.outages:
            now = self.f.elapsed()
            if e <= now:
                continue
            if s > now:
                await asyncio.sleep(s - now)
            self.event("outage_start", -1, json.dumps({"start_s": s, "end_s": e}))
            if self.f.mode == "reset":
                self.cut_all()
            await asyncio.sleep(max(0, e - self.f.elapsed()))
            if self.f.mode == "blackhole":
                self.cut_all()
            self.event("outage_end", -1, json.dumps({"start_s": s, "end_s": e}))

    async def run(self):
        server = await asyncio.start_server(self.handle, self.listen_host, self.listen_port)
        clock = asyncio.create_task(self.outage_clock())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        print(f"faultproxy listening on {self.listen_host}:{self.listen_port} -> "
              f"{self.target[0]}:{self.target[1]}", flush=True)
        async with server:
            await stop.wait()
        clock.cancel()
        self.event("proxy_stop", -1)
        self.log_file.close()


def hostport(s):
    h, p = s.rsplit(":", 1)
    return h, int(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:18080")
    ap.add_argument("--target", required=True, help="host:port of the real server")
    ap.add_argument("--config", help="JSON fault config; omit for a transparent proxy")
    ap.add_argument("--log", default="proxy_events.csv")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    cfg = json.load(open(args.config)) if args.config else {}
    asyncio.run(Proxy(hostport(args.listen), hostport(args.target),
                      Faults(cfg, args.seed), args.log).run())


if __name__ == "__main__":
    main()
