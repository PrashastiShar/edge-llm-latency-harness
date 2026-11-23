# llm-tail-latency

Measurement harness for **tail latency of LLM inference**, built to test the evaluation
practices proposed in the position paper *Limitations in Real-World Evaluation for
Edge-Cloud Collaborative LLM Inference* (P. Sharma, Wuhan University of Technology).

**Status:** the edge-only arm of the paper's Mini-project A is done (2,000 logged requests,
four device conditions). Cloud-only, network-fault injection and the hybrid fallback
strategy are **not implemented yet** (see Roadmap).

## What is here

| File | Purpose |
|---|---|
| `benchmark.py` | Streaming client for any OpenAI-compatible chat endpoint (Ollama, vLLM, hosted APIs). Logs **every request** to CSV: time to first token (TTFT), time per output token (TPOT), end-to-end latency, token count, HTTP status, errors. Writes a config file per run and reports p50/p95/p99. |
| `compare.py` | Compares conditions: CDF and log-scale tail plot, plus a percentile table (CSV + Markdown) with change against a baseline. |
| `EXPERIMENT_LOG.md` | Lab notebook: setup, device state, timeline, corrections and decisions for every run. |
| `results/` | Raw per-request CSVs, run configs and per-run CDF plots. |
| `comparison_*` | Cross-condition figure and tables. |

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# local model via Ollama
ollama pull qwen2.5:1.5b
export GROQ_API_KEY=unused        # the script checks that a key variable exists
python benchmark.py --url http://localhost:11434/v1/chat/completions \
  --model qwen2.5:1.5b --requests 100 --runs 5 --condition edge-idle

python compare.py --conditions edge-idle edge-battery edge-busy edge-stable
```

API keys are read from the environment, never from the code.

## Method

- One fixed prompt, `temperature=0`, `max_tokens=256`; every reply was 136 tokens,
  so latency differences reflect the system, not output length.
- 3 warm-up requests discarded; then 5 runs x 100 sequential requests, 0.2 s gap (closed loop).
- TTFT is measured at the first chunk containing text, not the first SSE line.
- Failed requests are logged and excluded from latency statistics, never timed as successes.

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
  27% (one video) to roughly 89% (several apps), while battery alone changed nothing.
- **A power-source switch coincided with a 24 s request** (about 4x the median), a single
  observed event, not a proven cause.
- **Energy (coarse, whole-system, from Windows battery report):** about 170 J per request
  (about 1.25 J per output token) at about 29 W. The slower apps-open state used about 38%
  more energy per request despite lower power draw.

Details, timelines and corrections are in `EXPERIMENT_LOG.md`.

## Limitations

Single prompt and model; sequential requests with no bursty arrivals; local model only,
so no network in the path yet; laptop CPU rather than embedded hardware; energy from
battery percentage, including screen and OS; output quality and privacy not evaluated.

## Earlier version (withdrawn)

The first commit of this repo reported Groq API results (stable vs. jittery network).
Those numbers are **withdrawn**: that script requested a model Groq had decommissioned,
Groq is not reachable from the author's region, and the script did not check HTTP
status, so it most likely timed error responses. The current `benchmark.py` fixes all
three issues. The old files remain in the git history.

## Roadmap

1. Cloud-only arm: same model on a cloud VM, measured from the laptop.
2. Network conditions: `tc netem` delay, jitter and loss; replay of a recorded trace.
3. **Fault injection:** scripted disconnections with recovery-time measurement.
4. Confidence-based edge-to-cloud fallback strategy.
5. Cost model and break-even price ratio (paper's Mini-project B).
6. Automated, repeatable runs (CI) and bursty workloads.
