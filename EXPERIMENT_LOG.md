# Experiment log

## 2026-11-23: edge-stable (UNCONTROLLED - device state changed mid-run)
- Data: results/edge-stable_20261123T073116Z_requests.csv (+ _config.json, _cdf.png)
- Setup: Ollama 0.40.11, qwen2.5:1.5b, localhost, WSL2 Ubuntu on Windows laptop, VPN ON (client running; traffic was localhost only)
- Workload: 5 runs x 100 requests, same prompt, temperature 0, max_tokens 256, 3 warm-up requests, 0.2 s gap
- Output length: 136 tokens for every request (checked in the smoke test)
- Start: 15:31 local (07:31 UTC), on battery at 100%, other applications open
- Change: about 16:19 local (08:19 UTC, run 3 request ~92), battery at 45%:
  charger connected and other applications closed (both changes at once)
- Battery used: 100% -> 45% in about 48 minutes on battery, under inference load plus other apps

| Run | Device state | Mean (s) | p50 | p95 | p99 | Max |
|---|---|---|---|---|---|---|
| 1 | battery, apps open | 9.23 | 9.03 | 11.97 | 13.88 | 14.08 |
| 2 | battery, apps open | 9.67 | 9.42 | 11.65 | 12.79 | 13.30 |
| 3 | mixed (changed ~req 92) | 8.59 | 8.04 | 11.75 | 14.04 | 24.25 |
| 4 | plugged in, apps closed | 6.15 | 6.15 | 7.01 | 7.53 | 7.64 |
| 5 | plugged in, apps closed | 5.96 | 6.00 | 6.42 | 6.60 | 7.41 |

- Pooled (all 500): mean 7.92, p50 7.70, p95 11.44, p99 13.30; TTFT p50 0.058, p95 0.125
- Outlier: run 3 request 14 = 24.25 s, cause unknown
- Limitation: charger and closed apps changed together, so their separate effects are unknown.
  Rerunning as controlled conditions: edge-idle, edge-battery, edge-busy.

## 2026-11-23: edge-idle
- Plugged in, apps closed, Best performance, VPN off
- Start 18:49 battery 100%, end 18:49 battery 100%
- Data: results/edge-idle_20261123T104907Z_requests.csv
- Results: (paste summary here)

## 2026-11-23: edge-idle (controlled baseline)
- Data: results/edge-idle_20261123T105238Z_requests.csv (+ _config.json, _cdf.png)
- Setup: same model and workload as edge-stable; charger connected, other apps closed,
  Windows power mode Best performance, VPN OFF
- Start: 18:52 local (10:52 UTC), about 48 minutes; battery: not logged (fill in if known)

| Run | Mean (s) | p50 | p95 | p99 | Max |
|---|---|---|---|---|---|
| 1 | 5.90 | 5.83 | 6.56 | 6.88 | 8.29 |
| 2 | 5.58 | 5.51 | 6.24 | 6.85 | 7.09 |
| 3 | 5.66 | 5.63 | 6.15 | 6.24 | 6.25 |
| 4 | 5.73 | 5.67 | 6.29 | 6.70 | 7.01 |
| 5 | 5.75 | 5.73 | 6.19 | 6.38 | 6.46 |

- Pooled (500/500 ok): mean 5.72, p50 5.66, p95 6.30, p99 6.73
- TTFT p50 0.046, p95 0.055; TPOT p50 0.042 s (about 24 tokens/s)
- p95 across runs: mean 6.29, sd 0.16 (edge-stable: sd 2.79)
- vs edge-stable runs 1-2 (battery + apps open): p95 11.91 -> 6.30 (about 1.9x)
- vs edge-stable runs 4-5: mean 6.06 -> 5.72; VPN off and power mode also changed, so the cause is unclear

## 2026-11-23: edge-idle replication
- Data: results/edge-idle_20261123T114853Z_requests.csv; same setup as edge-idle (plugged in, apps closed, Best performance, VPN off)
- Start 19:48 local (11:48 UTC)
- Pooled (500/500 ok): mean 5.65, p50 5.65, p95 6.21, p99 6.68, max 7.20; p95 across runs sd 0.16
- vs first edge-idle: mean 5.72 -> 5.65, p95 6.30 -> 6.21 (about 1%); baseline is reproducible

## Correction: edge-stable timeline (from request timestamps + battery report)
- 16:05:06-16:05:45 local: on battery, apps open, 11.6-13.9 s per request
- 16:05:58-16:07:03: latency fell to ~6.5-7 s while STILL on battery (cause unknown)
- 16:07:22: charger connected (battery report, 57%). The request that started 16:07:10
  took 24.25 s (4x median) and spans this moment: likely power-state transition stall
  (single event; association, not proven cause)
- 16:07:35-~16:19: plugged in but apps still open, ~8-9 s per request (not faster)
- ~16:19: apps closed, ~6.5 s per request for the rest of the run
- Conclusion: charger did not reduce latency; closing apps did. Consistent with edge-battery.
  Earlier note "16:19, 45%, charger and apps together" is superseded.

## 2026-11-23: edge-busy
- Data: results/edge-busy_20261123T132404Z_requests.csv
- Setup: plugged in, Best performance, VPN off, browser video playing for whole run; start 21:24 local
- Per-run mean 6.60-7.12 s; p95 7.19-8.26 s
- Pooled (500/500 ok): mean 6.94, p50 6.85, p95 7.98, p99 8.62, max 9.22; TPOT p50 0.050 (idle 0.042)
- vs edge-idle: mean +21%, p95 +27%, p99 +28%; p95 run sd 0.43 (idle 0.16)
- Run 4 req 24-28 cluster at 8-9 s: bursty interference
- Conclusion: background load raises latency and tail, but one video (+27% p95) is less than
  edge-stable battery period (+89% p95). Remaining gap: heavier app load and/or power-saving
  mode on battery; not separable from existing data. Optional test: edge-powersave.

## Decision (2026-11-23)
- edge-stable slowdown attributed to applications running during runs 1-3; all later runs
  (edge-idle, edge-battery) had no other applications running; edge-busy had only the test video.
- Power mode during edge-stable was not recorded; reported as an uncontrolled factor.
  No edge-powersave run planned.

## Note
- results/aborted/edge-idle_20261123T104907Z: run started 18:49 and stopped before any request completed (header only); excluded from analysis.

## Note
- results/aborted/edge-idle_20261123T104907Z: run started 18:49 and stopped before any request completed (header only); excluded from analysis.

## 2026-11-23: harness validation (mock servers)
- tests/smoke_test.py: all 8 checks passed on the laptop; outputs in validation/smoke_20261123/
- Mock servers only: validates the tooling (fault proxy, strategies, arrivals, fault analysis,
  cost model), not research results. Kept out of results/ to avoid mixing with real data.
