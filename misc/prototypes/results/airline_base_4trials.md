# Frontend/Backend Agent on tau2-bench — metrics

## Headline

Latencies in seconds, `mean (p90)`. Tokens are per task (one simulation), averaged.

| Arm | Domain | Sims | Pass^1 | Pass^2 | Pass^3 | Pass^4 | Backend turn latency | Filler latency | Filler present | Time to first response | FE tokens/task | BE tokens/task |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fba_paired | airline | 200 | 0.710 | 0.583 | 0.500 | 0.440 | 13.70 (27.46) | 1.72 (4.77) | 100% | 1.72 (4.77) | 15,939 | 130,094 |
| fba_backend_only | airline | 200 | 0.790 | 0.700 | 0.650 | 0.620 | 10.14 (24.36) | — | — | 10.15 (24.42) | 0 | 96,368 |

## Latency detail

| Arm | Domain | Backend turn p50 / p95 | Backend calls per turn | Backend LLM call mean (p90) | FE+BE LLM per turn | Filler p50 / p95 | TTFR p50 / p95 | FE completion tokens per call |
|---|---|---|---|---|---|---|---|---|
| fba_paired | airline | 11.48 / 34.56 | 4.51 | 3.04 (7.27) | 15.39 (29.65) | 0.92 / 6.81 | 0.92 / 6.81 | 132 |
| fba_backend_only | airline | 6.87 / 30.22 | 3.38 | 3.00 (7.28) | 10.14 (24.36) | — / — | 6.89 / 30.24 | — |

## Tokens per task

| Arm | Domain | Role | Prompt | Completion | of which reasoning | Cached prompt | Total |
|---|---|---|---|---|---|---|---|
| fba_paired | airline | frontend | 15,402 | 536 | 0 | 0 | 15,939 |
| fba_paired | airline | backend | 122,560 | 7,534 | 5,836 | 95,855 | 130,094 |
| fba_backend_only | airline | frontend | 0 | 0 | 0 | 0 | 0 |
| fba_backend_only | airline | backend | 91,658 | 4,710 | 3,419 | 63,160 | 96,368 |

## Diagnostics

| Arm | Domain | Turns | Decisions | Turns with no backend work | Backend errors | Repairs | Contract violations | Placeholders | Infra errors excluded |
|---|---|---|---|---|---|---|---|---|---|
| fba_paired | airline | 810 | delegate=808, direct=2 | 2 | 0 | 1 | 0 | 1 | 0 |
| fba_backend_only | airline | 687 | backend_only=687 | 0 | 0 | 0 | 0 | 0 | 0 |

## Checks

- **fba_paired / airline**: 
  - ⚠ prototype had uncommitted changes (--allow-dirty-prototype): not reproducible.
- **fba_backend_only / airline**: 
  - ⚠ prototype had uncommitted changes (--allow-dirty-prototype): not reproducible.

## Provenance

- **fba_paired / airline** — `data/simulations/fba_paired_airline_base_4trials` · agent llm `nvidia/nvidia/nemotron-3-ultra` · user llm `openai/azure/openai/gpt-5.2` · trials 4 · frontend `nvidia/nvidia/nemotron-3.5-lightning` · prototype `51036508e0` (DIRTY) · max_concurrency 4
- **fba_backend_only / airline** — `data/simulations/fba_backend_only_airline_base_4trials` · agent llm `nvidia/nvidia/nemotron-3-ultra` · user llm `openai/azure/openai/gpt-5.2` · trials 4 · prototype `51036508e0` (DIRTY) · max_concurrency 4

## How to read this

- **Backend turn latency**: backend LLM time summed over one user turn (all tool rounds), averaged over turns that did backend work. Paired turns the frontend answered itself are excluded; their count is *Turns with no backend work*.
- **Filler latency**: user message reaching the agent -> frontend's `call_backend` decision, over delegated turns that carried filler. Non-streaming: an upper bound on a streaming frontend, and the schema generates `query` before `filler_text`.
- **Time to first response**: filler latency where filler exists; otherwise the full turn (backend-only and llm_agent have no filler).
- Latencies depend on endpoint load: compare arms only at equal `max_concurrency`.
- Cost is omitted: LiteLLM cannot price Inference Hub models and reports 0.0.
- Scaffold results (fba_*) are not comparable to published leaderboard numbers.
