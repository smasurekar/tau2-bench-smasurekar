# Frontend/Backend Agent on tau2-bench — metrics

## Headline

Latencies in seconds, `mean (p90)`. Tokens are per task (one simulation), averaged.

| Arm | Domain | Sims | Pass^1 | Backend turn latency | Filler latency | Filler present | Time to first response | FE tokens/task | BE tokens/task |
|---|---|---|---|---|---|---|---|---|---|
| fba_paired | mock | 2 | 1.000 | 3.67 (5.72) | 0.64 (0.81) | 100% | 0.64 (0.81) | 2,384 | 2,972 |
| fba_backend_only | mock | 2 | 1.000 | 2.78 (5.08) | — | — | 2.78 (5.08) | 0 | 4,665 |

## Latency detail

| Arm | Domain | Backend turn p50 / p95 | Backend calls per turn | Backend LLM call mean (p90) | FE+BE LLM per turn | Filler p50 / p95 | TTFR p50 / p95 | FE completion tokens per call |
|---|---|---|---|---|---|---|---|---|
| fba_paired | mock | 3.67 / 5.97 | 2.50 | 1.47 (3.39) | 4.31 (6.19) | 0.64 / 0.83 | 0.64 / 0.83 | 58 |
| fba_backend_only | mock | 1.78 / 5.75 | 1.75 | 1.59 (3.09) | 2.78 (5.08) | — / — | 1.78 / 5.76 | — |

## Tokens per task

| Arm | Domain | Role | Prompt | Completion | of which reasoning | Cached prompt | Total |
|---|---|---|---|---|---|---|---|
| fba_paired | mock | frontend | 2,326 | 58 | 0 | 0 | 2,384 |
| fba_paired | mock | backend | 2,788 | 185 | 108 | 512 | 2,972 |
| fba_backend_only | mock | frontend | 0 | 0 | 0 | 0 | 0 |
| fba_backend_only | mock | backend | 4,162 | 504 | 346 | 0 | 4,665 |

## Diagnostics

| Arm | Domain | Turns | Decisions | Turns with no backend work | Backend errors | Repairs | Contract violations | Placeholders | Infra errors excluded |
|---|---|---|---|---|---|---|---|---|---|
| fba_paired | mock | 2 | delegate=2 | 0 | 0 | 0 | 0 | 0 | 0 |
| fba_backend_only | mock | 4 | backend_only=4 | 0 | 0 | 0 | 0 | 0 | 0 |

## Checks

- **fba_paired / mock**: 
  - ⚠ only 1 trial(s): Pass^2..4 need --num-trials 4.
  - ⚠ prototype had uncommitted changes (--allow-dirty-prototype): not reproducible.
- **fba_backend_only / mock**: 
  - ⚠ only 1 trial(s): Pass^2..4 need --num-trials 4.
  - ⚠ prototype had uncommitted changes (--allow-dirty-prototype): not reproducible.

## Provenance

- **fba_paired / mock** — `data/simulations/fba_smoke_paired_mock` · agent llm `nvidia/nvidia/nemotron-3-ultra` · user llm `openai/azure/openai/gpt-5.2` · trials 1 · frontend `nvidia/nvidia/nemotron-3.5-lightning` · prototype `51036508e0` (DIRTY) · max_concurrency 1
- **fba_backend_only / mock** — `data/simulations/fba_smoke_backend_only_mock` · agent llm `nvidia/nvidia/nemotron-3-ultra` · user llm `openai/azure/openai/gpt-5.2` · trials 1 · prototype `51036508e0` (DIRTY) · max_concurrency 1

## How to read this

- **Backend turn latency**: backend LLM time summed over one user turn (all tool rounds), averaged over turns that did backend work. Paired turns the frontend answered itself are excluded; their count is *Turns with no backend work*.
- **Filler latency**: user message reaching the agent -> frontend's `call_backend` decision, over delegated turns that carried filler. Non-streaming: an upper bound on a streaming frontend, and the schema generates `query` before `filler_text`.
- **Time to first response**: filler latency where filler exists; otherwise the full turn (backend-only and llm_agent have no filler).
- Latencies depend on endpoint load: compare arms only at equal `max_concurrency`.
- Cost is omitted: LiteLLM cannot price Inference Hub models and reports 0.0.
- Scaffold results (fba_*) are not comparable to published leaderboard numbers.
