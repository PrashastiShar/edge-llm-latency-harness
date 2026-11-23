# Harness validation run (2026-11-23) - MOCK SERVERS, NOT RESEARCH RESULTS

Produced by `python tests/smoke_test.py` on the author's laptop (WSL2 Ubuntu).
Edge and cloud are mock streaming servers with fixed, made-up speeds
(edge: 50 ms TTFT, 20 tokens x 10 ms, one request at a time; cloud: 150 ms TTFT,
20 tokens x 5 ms). Latency numbers here say nothing about real models or networks.

What it validates (all 8 checks passed):
- stable runs succeed 100%; a transparent proxy adds negligible delay
- injected jitter raises latency (cloud p50 0.26 s -> 0.36 s); trace replay raises the tail
- edge outage (reset): edge-only 40% success; fallback 100% success, 48% served by cloud
- bursty arrivals: edge-only queues (p95 2.12 s); fallback offloads 72% (p95 0.78 s)
- cloud outage (blackhole): failures recorded; recovery time measured
- cost model and break-even calculation produce output
