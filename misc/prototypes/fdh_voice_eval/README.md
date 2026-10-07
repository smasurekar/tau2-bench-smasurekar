# fdh_voice_eval: τ³ voice evaluation of the Frontend Delegation Agent (Hermes backend)

Scripts for evaluating the `fdh-voice` server (the agent repo's
`src/prototypes/voice_delegation_hermes_agent`, OpenAI Realtime on port 8775) on tau2's full-duplex voice benchmark.
They reuse `../fba_voice_eval/` (I0 `tau2_ihub.py` and the I2 `fba_voice_metrics.py`). No file under `src/tau2/`
is modified.

- Runbook: [`../voice-frontend-delegation-hermes-tau3-runbook.md`](../voice-frontend-delegation-hermes-tau3-runbook.md)
- Agent runbook (starting the stack): `nemotron-voice-agent-smasurekar/misc/prototypes/frontend-delegation-hermes/runbook.md`

| File | Role | Runbook |
|---|---|---|
| `fdh_lib.sh` | Sourced by every script: paths (overridable), per-arm port/container/profile/event log/gateway config/gateway log, the default arm (`FDH_ARM`, `geval`), run and model names, the concurrency rule (1 for `mock`, 4 otherwise), and `fdh_run` | §0, §4 |
| `check_stack.sh [arm...]` | Read-only preflight: voice server(s) and their profile and event log; gateway (Hermes), its config and event log, and its `/health` against that config (prompt variants, domains, catalog hash); `nemo-speech`, `fdh-voice-web`, I0, `.env` keys, pyaudio, rank_bm25, the join fix, campaign, commits | §1, §2 |
| `apply_join_fix.sh` | Applies the concurrency-safe session join to `fba_voice_metrics.py` (idempotent), then runs its tests | §2.3 |
| `new_campaign.sh [label]` | Writes `_consoles/CAMPAIGN_FDH` (refuses to overwrite without `--force`) | top |
| `run.sh <arm> <domain> <cx> [tau2 args]` | One tau2 run through I0; banking_knowledge gets `--retrieval-config ${TAU3_RETRIEVAL:-bm25}` | §5 |
| `campaign.sh [--arm] [--cx] [domain...]` | The reportable runs, domain by domain (default: airline retail telecom banking_knowledge); for tmux | §6 |
| `status.sh [run...]` | Progress: tau2 status line, error counts, agent-side counts, health. No argument = every run of the campaign | §6 |
| `report.sh [--arms] [--cx] [--tag] [domain...]` | Report adapter → setup record → `fba_voice_metrics.py` → check summary (C6 allowed to fail) → 429 gate (a throttled run is invalid) → exact join against `task.log` → measured filler latency | §7 |
| `archive.sh <run>... \| --all \| --reports` | Copies runs, their agent/gateway/worker logs, configs and provenance (including the agent's untracked prototype source) to `$DUMP/tau-3-voice/$CAMPAIGN/` | §9 |
| `fdh_logs.py` | Stdlib helpers behind the scripts: `sessions`, `filter`, `exact-join`, `filler`, `status`, `checks`, `ratelimit` (the 429 gate), `campaign-runs` | — |
| `tests/` | Offline tests for `fdh_logs.py` | — |

Environment overrides (defaults in `fdh_lib.sh`): `TAU2`, `AGENT`, `HERMES`, `DUMP`, `IHUB`, `DOCKER_HOST_IP`,
`FDH_GATEWAY_URL`, `FDH_GATEWAY_LOG`, `FDH_WORKER_LOGS`, `FDH_CONSOLES`, `FDH_METRICS`, `CAMPAIGN`, `FDH_ARM`; per run: `TAU3_TAG`,
`TAU3_CONCURRENCY`, `TAU3_RETRIEVAL`.

Arms (`fdh_lib.sh`): `geval` (the default) runs `profiles/realtime_eval.yaml` with `gateway.eval.yaml`, the
domain-agnostic profile. `dlg` (`tau3_eval.yaml`) and `silentack` (`tau3_eval_silent_ack.yaml`) pair with
`gateway.yaml` and are the historical arms. `geval` and `dlg` share `fdh-voice` on port 8775, so only one runs at a time.

## Quick start

```bash
E=misc/prototypes/fdh_voice_eval
$E/check_stack.sh                         # fix every FAIL first
$E/apply_join_fix.sh                      # once
$E/new_campaign.sh                        # once per campaign
TAU3_TAG=smoke $E/run.sh geval mock regular --num-tasks 1   # smoke (concurrency 1; regular everywhere)
TAU3_TAG=smoke $E/run.sh geval airline regular --num-tasks 4
$E/report.sh --tag smoke mock airline
tmux new -s fdh                           # then, inside tmux in the tau2 checkout:
misc/prototypes/fdh_voice_eval/campaign.sh   # the four domains at concurrency 4
$E/status.sh
$E/report.sh; $E/archive.sh --all; $E/archive.sh --reports   # archive even if a report check failed
```

## Tests

```bash
uv run pytest misc/prototypes/fdh_voice_eval/tests -q
```
