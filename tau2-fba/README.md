# tau2-fba

Runs the text **Frontend/Backend Agent** prototype
(`nemotron-voice-agent/src/prototypes/text_frontend_backend_agent`) as a
[τ²-bench](https://github.com/sierra-research/tau2-bench) half-duplex agent, in both variants:

| Registered agent | Prototype mode | What talks to the user |
|---|---|---|
| `fba_paired` | `frontend_backend` | the frontend LLM, which owns only `call_backend` and delegates task work to the backend |
| `fba_backend_only` | `backend_only` | the backend LLM directly, with its own history |

**τ²-bench is not modified.** This package registers both agent factories at runtime and
calls τ²'s own runner, like `tau2-hermes/`. `git diff -- src/ tests/` stays empty.

- **Run it:** [`../misc/prototypes/text-frontend-backend-agent-tau2-runbook.md`](../misc/prototypes/text-frontend-backend-agent-tau2-runbook.md)
- **Why it works this way:** [`../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md`](../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md)

## How it works

The prototype runs with `backend.tools.execution: external`. When its backend asks for tools,
`send()` returns them instead of running them. That is exactly τ²'s protocol: the agent returns a
tool call, τ²'s environment executes it, and the agent is re-entered with the result. So the
adapter is a direct mapping, with no threads or bridge.

- **Tool routing:** τ² domain tools go to the backend only. The frontend is offered only
  `call_backend`. Both are checked at construction and on every LLM call. A mismatch raises
  `ToolSurfaceError` (retried, then `infrastructure_error`, never scored).
- **LLM calls** go through τ²'s own `generate()` (LiteLLM, retries, `llm_debug` logs).
- **Metrics** ride on fields τ² already persists: `usage`, `cost`, `generation_time_seconds`, and
  `raw_data["fba"]` (per-role tokens and latency for every step, plus a turn summary with the
  filler latency on the message that ends each user turn).
- **Prompts, models, and reasoning settings** come from the prototype's own `config/agent.yaml`.
  Per-domain persona and capability list: [`tau2_fba/domains.yaml`](tau2_fba/domains.yaml).

## Layout

| File | Role |
|---|---|
| `run_fba_eval.py` | Driver: `--mode paired\|backend_only`, runs one domain, writes the FBA report next to `results.json` |
| `fba_report.py` | Compares runs (both arms, plus an optional `llm_agent` baseline) from their `results.json` |
| `tools/inspect_fba_surface.py` | Prints each role's tools, models, and prompts without making any LLM call |
| `tau2_fba/agent.py` | `FBAHalfDuplexAgent`: message mapping, turn timing, failure semantics |
| `tau2_fba/client.py` | The prototype's `ChatClient` on τ²'s `generate()`, plus the tool-surface guard |
| `tau2_fba/config.py` | Prototype `agent.yaml` → τ²-side overrides → prototype `Config` |
| `tau2_fba/metrics.py` | Pass^k (via τ²'s `compute_metrics`), latency, filler, tokens, checks |

## Test

```bash
cd tau2-fba && ../.venv/bin/python -m pytest -q     # offline: no network, no keys
```

28 tests, including three arms driven end to end through τ²'s real `run_domain` on the `mock`
domain with scripted LLMs. The suite skips if the prototype can't be found. Set
`FBA_PROTOTYPE_ROOT` if it isn't the sibling `nemotron-voice-agent-smasurekar` checkout.

## Reporting

An `fba_*` number measures **the prototype scaffold plus two models**. It isn't comparable to
published τ-bench leaderboard numbers, which all use `llm_agent`. Name both models, and hold the
user simulator, judge, trials, and `--max-concurrency` fixed across the arms you compare.
