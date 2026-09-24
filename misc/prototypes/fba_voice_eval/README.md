# fba_voice_eval: τ³ voice evaluation of the Voice Frontend/Backend Agent

Helpers for evaluating the prototype voice agent (`fba-voice` container, OpenAI Realtime server) on
tau2's full-duplex voice benchmark. No file under `src/tau2/` is modified.

- Plan: [`../voice-frontend-backend-agent-tau3-integration-plan.md`](../voice-frontend-backend-agent-tau3-integration-plan.md)
- Runbook: [`../voice-frontend-backend-agent-tau3-runbook.md`](../voice-frontend-backend-agent-tau3-runbook.md)

| File | Role |
|---|---|
| `tau2_ihub_overrides.py` | I0: routes the voice user simulator's TTS (`openai/openai/gpt-4o-mini-tts`) and tau2's hardcoded decision and review LLM calls to the Inference Hub |
| `tau2_ihub.py` | `tau2` CLI with I0 installed: `uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run ...` (same arguments as `tau2 run`) |
| `fba_voice_metrics.py` | I2: joins tau2 run directories with the agent's event log and reports Pass^1, the interaction metrics, backend turn latency, filler voice latency and tokens per task by role |
| `tests/` | Offline tests for both (no network, no keys) |

## Metrics script

```bash
uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py \
  --run paired=data/simulations/fba_voice_paired_mock_control \
  --run bo=data/simulations/fba_voice_bo_mock_control \
  --event-log paired=$AGENT/logs/fba_voice_events.jsonl \
  --event-log bo=$AGENT/logs/fba_voice_bo_events.jsonl \
  --out data/simulations/_metrics/fba_voice_mock_control
```

- `--run ARM=DIR` can be repeated, for example one per domain. Arms named `bo`, `backend_only` or
  `backend-only` are treated as the backend-only arm; any other name is treated as paired.
- `--event-log ARM=FILE`: one per arm. The file can hold many runs; sessions are selected by the run's
  model tag (`audio_native_config.model`, override with `--model ARM_OR_RUN_NAME=TAG`) and by the
  run's time span.
- Outputs in `--out`: `fba_voice_report.md`, `fba_voice_metrics.json`, `fba_voice_per_task.csv`,
  `fba_voice_per_turn.csv`, `interaction_metrics.json`, `join.csv`.
- Exit code 1 if any check (C1–C8, plan §4.2) fails. Warnings (C2: no per-role usage in the log; C4
  with barge-in cancellations; C5: agent failures) are printed but don't fail the run.

**Filler TTS estimate.** The projected filler voice latency adds a TTS first-audio time to the exact
part (endpointing + filler text). It is the median, over the run's answered turns, of the time from the
answer step's `agent_turn_done` to the turn's `turn_latency` record (first answer audio). If no turn has
both, it falls back to the filler records' `first_answer_audio − backend_done`. The source and sample
count are in the report.

## Tests

```bash
uv run pytest misc/prototypes/fba_voice_eval/tests -q
```
