# edge-llm-latency-harness

Reproducible harness for **tail latency, failure behaviour and cost of edge, cloud and
hybrid LLM inference**. It implements the evaluation practices proposed in the position
paper *Limitations in Real-World Evaluation for Edge-Cloud Collaborative LLM Inference*
(P. Sharma, Wuhan University of Technology): Mini-project A (tail latency under network
conditions) and Mini-project B (cost and energy accounting).

**Status**
- Edge-only experiments: **done** (2,000 logged requests, four device conditions; results below).
- Fault injection, cloud-only and hybrid strategies, bursty arrivals, cost model: **built and
  tested end-to-end against mock servers** (`tests/smoke_test.py`, run in CI).
  **Not yet run on real cloud hardware.**

## Components

| File | Purpose |
|---|---|
| `benchmark.py` | Streaming client for any OpenAI-compatible endpoint. Strategies: `single` (edge-only or cloud-only) and `fallback` (edge first; cloud if no first token within a deadline or the edge fails). Arrivals: closed loop, Poisson, bursty. Logs every request: status, TTFT, time per output token, end-to-end time, bytes, which side served it, Unix start/end times. |
| `faultproxy.py` | Fault-injection TCP proxy (no root; works in WSL and CI): delay and jitter, bandwidth limit, replay of a network trace, scheduled outages (`reset` = connections cut and refused, `blackhole` = traffic silently vanishes). Logs every fault event with timestamps. |
| `run_all.py` + `experiments.json` | Runs the whole experiment matrix from one configuration file, each run through fault proxies, and writes `results/manifest.jsonl`. |
| `compare.py` | CDF and log-scale tail plot; table with success rate, fallback share, p50/p95/p99 and change against a baseline. |
| `analyze_faults.py` | Per outage: failed requests, fallbacks, time without service, recovery time after the fault clears. |
| `cost.py` + `cost_scenarios.json` | Cost per successful request under labelled (hypothetical) price scenarios, edge energy per token, break-even price ratio for hybrid vs cloud-only. |
| `record_trace.py` / `make_trace.py` | Record a real RTT trace (e.g. on a phone hotspot) without root / generate a clearly labelled synthetic one. |
| `cloud_setup.sh`, `netem.sh` | Install the same model on a cloud VM (reached via SSH tunnel, no open port); packet-level `tc netem` on a real Linux host. |
| `tests/` | Mock streaming server and end-to-end smoke test (also in `.github/workflows/smoke.yml`). |
| `EXPERIMENT_LOG.md`, `results/` | Lab notebook and raw per-request data. |

## Quick start (edge only)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:1.5b
export LLM_API_KEY=unused        # local servers ignore it; keys are never stored in code
python benchmark.py --url http://localhost:11434/v1/chat/completions \
  --model qwen2.5:1.5b --requests 100 --runs 5 --condition edge-idle
python compare.py
```

Check the harness without any model: `python tests/smoke_test.py` (about 2 minutes).

## Full study (edge + cloud + hybrid + faults)

1. On a cloud VM: `bash cloud_setup.sh` (installs Ollama and `qwen2.5:1.5b`, prints hardware).
2. On the laptop, keep a tunnel open: `ssh -N -L 11435:127.0.0.1:11434 USER@VM_IP`.
3. Optional real trace: `python record_trace.py --host VM_IP --port 22 --seconds 600 --out traces/my_trace.csv`
   and point `faults/trace.json` at it.
4. `python run_all.py` (or `--only cloud-stable`), then:
   `python compare.py`, `python analyze_faults.py`, `python cost.py`.

The matrix in `experiments.json`: edge through a transparent proxy (control), cloud-only
stable / jittery / trace, fallback stable / jittery, all three strategies under bursty
arrivals, and outages on the edge (reset) or cloud (blackhole) path.

## Method

- One fixed prompt, `temperature=0`, `max_tokens=256`; replies were 136 tokens, so latency
  differences reflect the system, not output length.
- Warm-up requests discarded; at least 5 runs per condition.
- TTFT is taken at the first chunk containing text. Failed requests are logged and
  excluded from latency statistics, never timed as successes.
- Fallback time includes the wasted edge attempt.
- Proxy overhead (measured with the mock server): TTFT +0.5 ms; per-token timing unchanged.

## Results so far (edge-only)

Edge device: Dell Inspiron 14 5430 laptop, CPU inference, WSL2 Ubuntu, Ollama 0.40.11,
Qwen2.5-1.5B. A laptop is **not** a constrained edge device; treat these as an upper bound.

| Condition | Device state | Mean (s) | p50 | p95 | p99 | Max |
|---|---|---|---|---|---|---|
| edge-idle | plugged in, no other apps | 5.72 | 5.66 | 6.30 | 6.73 | 8.29 |
| edge-battery | on battery, no other apps | 5.65 | 5.65 | 6.21 | 6.68 | 7.20 |
| edge-busy | plugged in, browser video playing | 6.94 | 6.85 | 7.98 | 8.62 | 9.22 |
| edge-stable | uncontrolled: other apps open, battery then charger | 7.92 | 7.70 | 11.44 | 13.30 | 24.25 |

500 successful requests per condition. Highlights:

- **Device state is a tail source, not only the network.** Background load raised p95 by
  27% (one video) to about 89% (several apps); battery alone changed nothing.
- **A power-source switch coincided with a 24 s request** (about 4x the median): a single
  observed event, not a proven cause.
- **Energy (coarse, whole-system, Windows battery report):** about 170 J per request
  (about 1.25 J per output token) at about 29 W; the apps-open state used about 38% more
  energy per request despite lower power draw.

Details, timelines and corrections are in `EXPERIMENT_LOG.md`.

## Limitations

Single prompt and model; laptop CPU rather than embedded hardware; energy from battery
percentage (includes screen and OS); prices are hypothetical scenarios; the proxy emulates
delay, bandwidth and outages at application level, not packet loss (use `netem.sh` on a
Linux host for that); in closed-loop runs a fast-failing outage produces many quick
failures, so compare outages by time without service, not by failure counts; output
quality and privacy are not evaluated.

## Earlier prototype (withdrawn)

An earlier repository reported Groq API latency under simulated jitter. Those numbers are
**withdrawn**: the script requested a model Groq had decommissioned, Groq is not reachable
from the author's region, and the script did not check HTTP status, so it most likely
timed error responses. That repository has been deleted.

## Next steps

1. Run `experiments.json` against a real cloud VM; record a real mobile trace.
2. Add a deliberate edge process crash (stop Ollama) alongside the proxy outages.
3. Confidence-based fallback (needs token log-probabilities and a quality metric).
4. Multi-step and multi-agent workflows under the same fault schedules.
