# Runbook: τ³ voice evaluation of the `fdh-tau` container (Frontend Delegation Hermes, τ³ adapter)

**Date:** 2026-10-12 · **Status:** `fdh-tau` is up (image `nemotron-voice-agent-fdh:latest`, host port 8775, healthy,
`realtime_eval.yaml` + `gateway.eval.yaml`). The tau2 side is ready. The `fdh_voice_eval/` scripts **do not yet know
this container** (§2): `check_stack.sh` gives 3 FAILs against it, and `report.sh`/`archive.sh` read the wrong
container, gateway and module paths. Until they are ported, use the manual commands in this runbook for those steps ·
**Starts the container:** the agent repo skill `nemotron-voice-agent-smasurekar/.agents/skills/nemotron-voice-agent-fdh-deploy/`
(`SKILL.md`, `references/tau.md`) ·
**Parent runbook (methodology, metrics, gpt-5.5 command, report reading):**
[`../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md`](../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md)
(called "the parent runbook" below) · **Scripts:** [`../prototypes/fdh_voice_eval/`](../prototypes/fdh_voice_eval/README.md)

This runbook takes the running `fdh-tau` container to a τ³ voice report for **airline, retail, telecom and
banking_knowledge** (mock only for the smoke run), archived in the dump repo. It covers only what differs from the
parent runbook; everything it does not mention (metric definitions, check meanings, how to read the numbers) is the
parent runbook's. `src/tau2/` is not modified.

The rules of the parent runbook all still apply:

> **ALWAYS ASK the user which Inference Hub model to use for the user simulator and the judge before any run:
> `azure/openai/gpt-5.2` (the scripts' default) or `openai/openai/gpt-5.5`.** Never pick one yourself or carry one
> over from an earlier campaign. Same model for user sim, NL-assertion judge and review model. Exact gpt-5.5 command:
> parent runbook §2.2.1.

> **Do not start any run (smoke included) until the user says go.**

> **Speech complexity `regular` for every domain, mock smoke included.** Never `control`.

> **Concurrency: 1 for mock, 4 for every other domain** (`fdh_concurrency`; `TAU3_CONCURRENCY` overrides). 4 is the
> container's capacity: voice `FDH_MAX_SESSIONS=4`, ASR `max_streams=4`, gateway `FDH_GATEWAY_MAX_SESSIONS=8`.

> **MANDATORY: archive every run (smoke, stopped, invalid) in
> `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/` before you
> restart, recreate or reconfigure `fdh-tau`, or change agent code.** With this container that matters even more:
> `docker compose ... up -d` *recreates* the container whenever an env value changes, and a recreated container has
> no old `docker logs` (§9).

## Where commands run

| Tag | Directory |
|---|---|
| **[tau2]** | `/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar` (every eval script runs from here) |
| **[agent]** | `/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar` (the container's Compose file and source) |
| **[hermes]** | `/home/smasurekar/Desktop/Swapnil/github_repos/hermes-agent-smasurekar` (built into the image; provenance only) |
| **[dump]** | `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump` |

Shell setup for every manual command below:

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
source misc/prototypes/fdh_voice_eval/fdh_lib.sh     # TAU2 AGENT HERMES DUMP IHUB FDH_CONSOLES FDH_METRICS CAMPAIGN ...
E=misc/prototypes/fdh_voice_eval
F=docker/docker-compose.frontend-delegation-hermes.yaml   # relative to [agent]
C=fdh-tau                                                 # container name
```

---

## 0. What is different from the `fdh-voice` stack

| | Old stack (parent runbook) | **`fdh-tau`** |
|---|---|---|
| Code | `src/prototypes/voice_delegation_hermes_agent/` | `src/examples/frontend_delegation_hermes/` (module `examples.frontend_delegation_hermes`; τ³ entry point `tau_server.py`, adapter `adapters/tau/`) |
| Container | `fdh-voice`, `docker compose run --rm` | **`fdh-tau`**, Compose service `fdh-tau` (profile `tau`) of `docker/docker-compose.frontend-delegation-hermes.yaml`, project `nemotron-voice-agent-fdh`, `restart: unless-stopped` |
| Image | `frontend-backend-agent-single-gpu` (Python 3.12) | `nemotron-voice-agent-fdh:latest` (`docker/Dockerfile.fdh`, Python 3.14, Hermes baked in from `HERMES_AGENT_DIR`) |
| Gateway | host process on `:8790` (`logs/fdh_gateway.out`, `fdh_gateway.pid`) | **inside the container**, `127.0.0.1:8790`, started by `docker/fdh/entrypoint.sh`. **Not reachable from the host.** Health: `docker exec fdh-tau curl -s 127.0.0.1:8790/health` |
| Hermes workers | host processes | inside the container, one per Realtime session |
| Profile / gateway config | in the container `Cmd` / the gateway's argv | env **`FDH_PROFILE`**, **`FDH_GATEWAY_CONFIG`** (`docker inspect` `.Config.Cmd` is `null`) |
| Gateway stdout | `logs/fdh_gateway.out` | `docker logs fdh-tau` (with the voice server) |
| Voice port | 8775 | 8775 (unchanged; `FDH_VOICE_HOST_PORT`) |
| Event / gateway / worker logs | `logs/fdh_voice_events.realtime_eval.jsonl`, `logs/fdh_gateway_events.realtime_eval.jsonl`, `logs/fdh_workers/` | **the same host files** (bind-mount `[agent]/logs` → `/app/logs`), now written as root |
| `/health` `prototype` | `frontend-delegation-hermes` | same; `config_hash` is new (`c9b2d1545512` for `realtime_eval.yaml`) because the config paths moved |
| Gateway `/health` (`gateway.eval.yaml`) | `domain_notes` false, `domains: []` | every backend variant `true` (incl. `domain_notes`), `domains: ["telecom"]`, `backend_catalog_sha256 55639a8b238b7eec` |
| `fdh-voice` name | the τ³ server | **the Pipecat adapter** (demo / τ² eval, port 8776). Must be **stopped** for a reportable τ³ run |
| Docker logs | unbounded | `json-file`, **50 MB × 3** rotation: a long run can rotate its early lines away (§9) |

**Numbers are not comparable with campaigns on the old stack** unless you have shown parity: different image
(Python 3.14), gateway and workers now share the voice container's CPU, the eval profile's fixes (`gateway.eval.yaml`
has every geval/F-fix variant on) and agent commit differ. Start a new campaign (§3) and compare only runs made on
`fdh-tau` at the same agent commit. Pair runs by profile name, not by an old `config_hash` (the prompt hashes
`frontend_prompt`, `backend_system_sha256`, `backend_catalog_sha256`, `session_tools_sha256` are unchanged by the
move).

---

## 1. Start or check `fdh-tau` [agent]

Start and stop the container only through the deploy skill (`SKILL.md` → τ³ → `references/tau.md` §2–§4). The
commands, for reference:

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar
F=docker/docker-compose.frontend-delegation-hermes.yaml
docker ps --filter name='^fdh-voice$' --format '{{.Names}} {{.Status}}'   # expect nothing (Pipecat adapter stopped)
export FDH_PROFILE=realtime_eval.yaml FDH_GATEWAY_CONFIG=gateway.eval.yaml   # the geval arm
docker compose --env-file .env -f $F --profile tau up -d fdh-tau             # add --build only after a rebuild trigger
```

Checks (the expected values are those seen on 2026-10-12):

```bash
curl -s localhost:8775/health; echo
#   "status":"ok", "prototype":"frontend-delegation-hermes", "max_sessions":4, "backend":{"link":"websocket",...},
#   "frontend":{"decider":"llm","model":"nvidia/nvidia/nemotron-3.5-lightning"}, config_hash c9b2d1545512
docker exec fdh-tau curl -s 127.0.0.1:8790/health; echo
#   "ok":true, "max_sessions":8, "agent_kind":"hermes", "hermes":{"model":"nvidia/nvidia/nemotron-3-ultra",
#   "reasoning":true,"backend_trace":true}, "domains":["telecom"], "backend_catalog_sha256":"55639a8b238b7eec"
docker inspect fdh-tau --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^FDH_(PROFILE|GATEWAY_CONFIG|EVENT_LOG|GATEWAY_LOG|WORKER_LOG_DIR|MAX_SESSIONS|GATEWAY_MAX_SESSIONS)='
#   FDH_PROFILE=realtime_eval.yaml  FDH_GATEWAY_CONFIG=gateway.eval.yaml
#   FDH_EVENT_LOG=logs/fdh_voice_events.realtime_eval.jsonl  FDH_GATEWAY_LOG=logs/fdh_gateway_events.realtime_eval.jsonl
#   FDH_WORKER_LOG_DIR=logs/fdh_workers  FDH_MAX_SESSIONS=4  FDH_GATEWAY_MAX_SESSIONS=8
docker logs fdh-tau 2>&1 | grep -E 'config hash|VAD silence|ASR channel ready|TTS channel ready|warm-up'
#   VAD silence=800ms; ASR nemotron-speech-streaming-en-0.6b max_streams=4; TTS magpie-tts-multilingual voice=John
docker ps --format '{{.Names}}' | grep -x nemotron-voice-agent-nemo-speech-1                # nemo-speech running
pgrep -af 'prototypes\.voice_delegation_hermes_agent\.sidecar\.gateway_server'           # expect nothing: no old host gateway
```

The optional text smoke tests of `references/tau.md` §4–§5 (`backend_probe`, `voice_chat --io text`) write
sessions into the same event log under their own `pine-fdh-*` tags; they do not disturb the campaign scripts, which
select sessions by model tag. Don't run them during a campaign run (they take capacity and Hub quota).

**After any agent code, prompt or config change:** `src/` is mounted read-only and re-read at process start, so
recreate the container with the same `up -d` (no rebuild needed except for the triggers in
`references/prerequisites.md`). **Archive first (§9)**, then start a new campaign (§3).

---

## 2. Eval-script status against `fdh-tau` [tau2]

`fdh_lib.sh` and the scripts were written for the old stack. Checked on 2026-10-12 (agent `2fcec8d`, tau2
`2b6ab79`):

| Script | Works against `fdh-tau`? | Why / what to do instead |
|---|---|---|
| `new_campaign.sh` | **yes** | |
| `run.sh`, `campaign.sh` | **yes** | only use port 8775 and the tau2 side. Run names/tags are unchanged (`pine-fdh-voice-geval-…`) |
| `apply_join_fix.sh` | **yes** (already applied) | |
| `status.sh` | **mostly** | per-run tau2 and agent-side counts work (they read the shared host log files). The last line, `gateway …`, is empty: the gateway is not on the host. Use `docker exec fdh-tau curl -s 127.0.0.1:8790/health` |
| `check_stack.sh` | **no: 3 FAILs, 1 WARN** | it inspects container `fdh-voice` (not `fdh-tau`), looks for the profile in `.Config.Cmd` (now `null`; it is env `FDH_PROFILE`), and queries the gateway at `http://172.17.0.1:8790` and a host `prototypes.…gateway_server` process. Its tau2 and provenance rows are still valid. Use §1's commands for the agent rows |
| `report.sh` | **partly** | it runs the **old** `prototypes.voice_delegation_hermes_agent.cli.report_adapter` and writes `_setup/geval.json` from `fdh-voice` and the host gateway (→ `unknown`/stale models). The metrics, checks, 429 gate (reads the host `logs/fdh_workers/`), exact join and filler steps are fine. Run the adapter and setup record by hand first (§7) |
| `archive.sh` | **partly** | tau2 results, per-run event/gateway/worker logs, `nemo-speech` logs and provenance are right. **Wrong or missing:** `agent/docker_logs.txt` and `container_inspect.json`/`agent_llm_env.txt` (from `fdh-voice`), `agent/config/delegation/` (the old prototype's config), `gateway_health.json` (empty), `gateway_env.txt` (empty). Add them by hand (§9) |

> **Danger:** if the Pipecat `fdh-voice` container is running, the unported scripts will silently read *its*
> logs, env and inspect. It must be stopped anyway for a reportable run (§1).

**Porting the scripts** (not done yet; ask before changing them, and re-run
`uv run pytest misc/prototypes/fdh_voice_eval/tests -q` after). The minimal change set:

- `fdh_container`: `geval|dlg` → `${FDH_CONTAINER:-fdh-tau}`; `silentack` → `fdh-tau-silentack` (port 8777 per
  `references/tau.md` §6).
- a `fdh_gateway_health <arm>` helper = `docker exec <container> curl -s 127.0.0.1:8790/health`, used instead of
  `curl $FDH_GATEWAY_URL/health` in `check_stack.sh`, `status.sh`, `report.sh`, `archive.sh`.
- `check_stack.sh`: compare `fdh_profile` with env `FDH_PROFILE` (strip `profiles/`) and the gateway config with env
  `FDH_GATEWAY_CONFIG`; drop the host `pgrep` (or `docker exec <c> ps`); load the gateway config with
  `examples.frontend_delegation_hermes.core.sidecar.gateway_config` / `…core.sidecar.templates` from
  `src/examples/frontend_delegation_hermes/config/`.
- `report.sh`: `-m examples.frontend_delegation_hermes.cli.report_adapter`.
- `archive.sh`: config from `src/examples/frontend_delegation_hermes/config/.`; gateway env from
  `docker inspect` (`BACKEND_LLM_*`, `FDH_*`); no `logs/fdh_gateway.out`.

---

## 3. One-time tau2 setup and the campaign [tau2]

The tau2 setup is the parent runbook §2 and is already done on this host (`check_stack.sh`'s `== tau2` rows all
PASS: I0, `.env` keys, pyaudio, rank_bm25, the §2.3 join fix, dump folder). `banking_knowledge` uses
`--retrieval-config bm25` (`TAU3_RETRIEVAL`, parent §2.4).

### 3.1 Close out the current campaign first

`_consoles/CAMPAIGN_FDH` currently names **`2026-10-12_07-08-42Z_fdh-voice-container`**. It holds one run,
`fdh_voice_geval_retail_regular_smoke` (started 2026-10-12 12:38 IST), made **before** this `fdh-tau` container was
started (16:12 UTC), and it is **not archived** (no folder in the dump). Decide with the user whether to archive it
(`$E/archive.sh --all && $E/archive.sh --reports`, then a run card that says its container is gone and its
`docker_logs.txt` is missing) before replacing the campaign.

### 3.2 New campaign and run names

```bash
$E/new_campaign.sh fdh-tau --force        # e.g. 2026-10-12_16-30-00Z_fdh-tau
```

**Run names collide with old campaigns.** `data/simulations/` still has `fdh_voice_geval_airline_regular`,
`fdh_voice_geval_telecom_regular`, `fdh_voice_geval_mock_regular_smoke`, `…_telecom_regular_smoke`, and
`…_retail_regular_smoke` from earlier stacks, and `run.sh` **resumes** an existing run of the same name. Give every
run of this campaign a tag (no `_` in the tag):

```bash
export TAU3_TAG=tau                       # → fdh_voice_geval_<domain>_regular_tau, model pine-fdh-voice-geval-<domain>-regular-tau
# smoke runs: TAU3_TAG=tau-smoke
```

and pass the same `--tag` to `report.sh` (§7). (Alternative: move the old run folders out of `data/simulations/`
after confirming they are archived.) With gpt-5.5, combine both into the tag, e.g. `TAU3_TAG=tau-gpt55`.

---

## 4. Preflight [tau2 + agent]

```bash
$E/check_stack.sh 2>&1 | sed -n '/== tau2/,$p'     # tau2 + provenance rows only (the agent rows are wrong, §2)
```

then the §1 checks. Ready when: both `/health` as in §1, `fdh-voice` and `fdh-voice-web` not running, `nemo-speech`
running, no host gateway, the tau2 rows all PASS, and a campaign is set. Record the provenance lines (agent, tau2,
Hermes commits and dirty counts) for the run card. The agent repo is dirty: `src/examples/frontend_delegation_hermes/`
and `docker/Dockerfile.fdh`, `docker/fdh/`, the Compose file are **untracked**; `archive.sh` tars the untracked
`src/`, `tests/`, `misc/` (`agent_untracked.tgz`) but not `docker/` — copy those by hand (§9).

---

## 5. Smoke runs [tau2] — only after the user says go and has chosen the user/judge model

gpt-5.2 (default; for gpt-5.5 use the parent runbook §2.2.1 command with `TAU3_TAG=tau-smoke-gpt55`):

```bash
TAU3_TAG=tau-smoke $E/run.sh geval mock regular --num-tasks 1      # concurrency 1
TAU3_TAG=tau-smoke $E/run.sh geval airline regular --num-tasks 4   # concurrency 4: four sessions/workers/ASR streams at once
for d in retail telecom banking_knowledge; do TAU3_TAG=tau-smoke $E/run.sh geval $d regular --num-tasks 1; done
```

Then the report (do §7's manual steps first) — `$E/report.sh --tag tau-smoke mock airline retail telecom banking_knowledge`.

### 5.1 Gate (parent runbook §5.1, with these changes)

`RUN` is the run name, `MODEL=$(fdh_model $RUN)`, `EV=$AGENT/logs/fdh_voice_events.realtime_eval.jsonl`.

| Check | Command | Pass when |
|---|---|---|
| I0 active, user/judge model, concurrency, `regular`, no infra errors, reward, audio | parent §5.1 (unchanged) | as there |
| No failed LLM calls | `$E/status.sh $RUN` → `badLLM` | `0` |
| Agent saw the run | `$E/status.sh $RUN` → `sessions`, `open_sessions` | sessions = tasks; 0 open after the run |
| Hermes backend | `docker exec fdh-tau curl -s 127.0.0.1:8790/health` | `agent_kind hermes` |
| Normalization / schema rules | parent §5.1 rows, with `$EV` | as there |
| Deployed fingerprint | in [agent]: `PYTHONPATH=src uv run python -m examples.frontend_delegation_hermes.cli.fingerprint_check <events of the run's sessions> --profile src/examples/frontend_delegation_hermes/config/profiles/realtime_eval.yaml --gateway-config src/examples/frontend_delegation_hermes/config/gateway.eval.yaml` (get the run's events with `python3 $TAU2/$E/fdh_logs.py filter $EV $RUN --out /tmp/$RUN` → `/tmp/$RUN/events.jsonl`) | `fingerprint: OK`, one per domain |
| Concurrent workers (airline) | `grep -E '"event": "(session_open\|capacity_refused)"' $AGENT/logs/fdh_gateway_events.realtime_eval.jsonl \| tail`; `docker exec fdh-tau curl -s 127.0.0.1:8790/health` → `refused` | 4 `session_open` close in time; `refused` 0 |
| No agent failures | `docker logs --since "$(head -1 $FDH_CONSOLES/$RUN.start)" fdh-tau 2>&1 \| grep -Ei " failed\|error\|\b401\b\|\b429\b" \| head` | nothing relevant |
| Container did not restart | `docker inspect fdh-tau --format '{{.RestartCount}} {{.State.StartedAt}}'` | `0`, and `StartedAt` before the run's first `.start` (a restart mid-run means `unless-stopped` brought it back after the gateway or voice server died: the run is invalid) |
| Worker health | `grep -il traceback $AGENT/logs/fdh_workers/sess_*.log` (only this run's sessions: `agent/sessions.txt` after archiving) | no tracebacks |
| Container resources (airline smoke) | `docker stats --no-stream fdh-tau` during the run | CPU not pegged; note RSS (4 Hermes workers now live in this container) |
| Report | `report.sh` after §7's manual steps | `report.sh OK`: adapter exit 0; C1–C5, C7, C8 PASS, C6 FAIL (by design); every `P0.1` line OK; `0 mismatch` |

Use the airline smoke's mean simulation `duration` to re-estimate §6's wall times; this stack has not been timed yet.

---

## 6. Reportable runs [tau2]

Full `base` split, 1 trial, `regular`, concurrency 4, in tmux (pass the key into the session):

```bash
tmux new -s fdh-tau
# inside tmux, in [tau2]:
export TAU3_TAG=tau
misc/prototypes/fdh_voice_eval/campaign.sh          # airline retail telecom banking_knowledge, geval, regular
```

Watch with `$E/status.sh` and `docker exec fdh-tau curl -s 127.0.0.1:8790/health` (sessions, workers, `refused`).

| Domain | Tasks (`base`) | Old-stack wall time at concurrency 4 (re-estimate from the smoke) |
|---|---|---|
| airline | 50 | ~2.5 h |
| retail | 114 | ~5.5 h |
| telecom | 114 | ~5.5 h |
| banking_knowledge | 97 | ~4.5 h |

- **Resume:** rerun the same command with the same `TAU3_TAG`; add `--auto-resume` to rerun infra errors
  (`$E/campaign.sh retail telecom -- --auto-resume`).
- **Freeze the container for the whole campaign:** same image, `FDH_PROFILE`, `FDH_GATEWAY_CONFIG`, env and agent
  commit for every domain. Never run `docker compose ... up` for `fdh-tau` during a campaign: a changed env value
  recreates it.
- **Keep the Pipecat `fdh-voice` stopped** and don't use `voice_chat`/`backend_probe` against 8775 during a run.
- **Docker log rotation (50 MB × 3):** archive each domain right after it finishes (§9, `archive.sh <run>` plus the
  manual `docker logs` copy), so its container logs are still there.

---

## 7. Compute the metrics [tau2]

`report.sh` has two steps that are wrong for `fdh-tau` (§2). Handle them before running it:

1. **Report adapter.** `report.sh` calls the *old* prototype's adapter
   (`prototypes.voice_delegation_hermes_agent.cli.report_adapter`, `report.sh` line 32) and overwrites
   `_metrics/_legacy/fdh_voice_events.realtime_eval.legacy.jsonl` with its output, which the metrics then read.
   Change that module to `examples.frontend_delegation_hermes.cli.report_adapter` before the first report (ask the
   user first; it is one line and part of the §2 port). The new adapter on its own, for a check:

   ```bash
   (cd $AGENT && PYTHONPATH=src uv run python -m examples.frontend_delegation_hermes.cli.report_adapter \
      logs/fdh_voice_events.realtime_eval.jsonl --out /tmp/fdh_tau.legacy.jsonl)    # exit 0 = no orphaned tool call
   ```

2. **Setup record** (`_metrics/_setup/geval.json`, the model columns of the results table). `report.sh` builds it
   only when a container named `fdh-voice` exists, otherwise keeps the existing file. Write it from `fdh-tau` first:

   ```bash
   H=$(docker exec fdh-tau curl -s 127.0.0.1:8790/health)
   fe=$(docker inspect fdh-tau --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^FRONTEND_LLM_MODEL=//p')
   be=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hermes"]["model"])' <<<"$H")
   asr=$(docker logs fdh-tau 2>&1 | sed -n 's/.*ASR channel ready: .* model=\([^ ]*\) .*/\1/p' | tail -1)
   tts=$(docker logs fdh-tau 2>&1 | sed -n 's/.*TTS channel ready: .* model=\([^ ]*\) voice=\([^ ]*\) .*/\1 (voice \2)/p' | tail -1)
   mkdir -p $FDH_METRICS/_setup && cat > $FDH_METRICS/_setup/geval.json <<JSON
   {"backend_llm": "$be (Hermes AIAgent, one worker per session, in fdh-tau)", "backend_reasoning": "on (enable_thinking, reasoning_budget 1024)",
    "frontend_llm": "$fe", "frontend_reasoning": "off (enable_thinking false)",
    "asr": "$asr (NeMo Speech, streaming)", "tts": "$tts (NeMo Speech)"}
   JSON
   cat $FDH_METRICS/_setup/geval.json
   # 2026-10-12: nemotron-3.5-lightning | nemotron-3-ultra | nemotron-speech-streaming-en-0.6b | magpie-tts-multilingual (voice John)
   ```

   Make sure the Pipecat `fdh-voice` is **not** running, or `report.sh` overwrites this file from it.

Then:

```bash
$E/report.sh --tag tau                    # four domains → _metrics/fdh_voice_geval_regular_tau/
```

`report.sh` prints `WARN fdh-voice not running: keeping the existing …/geval.json` — expected. The other steps
(metrics, checks, the 429 gate on `$AGENT/logs/fdh_workers/`, exact join, measured filler) work unchanged; expected
results are the parent runbook §7 table (C6 FAIL by design).

---

## 8. How to read the numbers

Parent runbook §8, plus:

- **One container now holds the voice server, the gateway and up to 4 Hermes workers.** Their CPU contention is
  part of the measured latency; it was not on the old stack. Don't compare latencies with old-stack campaigns.
- **Telecom note:** `gateway.eval.yaml` runs the disclosed telecom domain note (`domain_notes` true, `domains:
  ["telecom"]`). Name it next to telecom numbers; the ablation is `gateway.eval_no_note.yaml` (`references/tau.md` §8.6).
- **VAD:** the τ³ adapter uses 800 ms end-of-turn silence and ignores the client's 500 ms (`honor_client_values: false`).
- **M1 proactive status** fires after `FDH_PROACTIVE_AFTER_S=15` s of silence while WORKING (container env).

---

## 9. Archive everything [tau2 → dump]

Mandatory, before any `fdh-tau` restart/recreate, and right after each domain (log rotation).

```bash
$E/archive.sh fdh_voice_geval_airline_regular_tau     # per run as it finishes, or: $E/archive.sh --all
```

Then, until `archive.sh` is ported, **fix up each run folder** by hand (`D=$DUMP/tau-3-voice/$CAMPAIGN/<run>`):

```bash
RUN=fdh_voice_geval_airline_regular_tau; D=$DUMP/tau-3-voice/$CAMPAIGN/$RUN
docker logs --since "$(head -1 $FDH_CONSOLES/$RUN.start)" fdh-tau > $D/agent/docker_logs.txt 2>&1
docker inspect fdh-tau | python3 -c 'import json,re,sys; d=json.load(sys.stdin)
for c in d: c["Config"]["Env"]=[re.sub(r"^([^=]*(KEY|TOKEN|SECRET|PASS)[^=]*)=.*", r"\1=<redacted>", e) for e in c["Config"]["Env"]]
print(json.dumps(d, indent=1))' > $D/agent/container_inspect.json
docker inspect fdh-tau --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(FRONTEND|BACKEND)_LLM_|^FDH_' \
  | grep -vE 'KEY|TOKEN|SECRET|PASS' | tee $D/provenance/agent_llm_env.txt > $D/provenance/gateway_env.txt
docker exec fdh-tau curl -s 127.0.0.1:8790/health > $D/agent/config/gateway_health.json
rm -rf $D/agent/config/delegation && mkdir -p $D/agent/config/delegation \
  && cp -r $AGENT/src/examples/frontend_delegation_hermes/config/. $D/agent/config/delegation/
mkdir -p $D/agent/config/docker && cp $AGENT/docker/docker-compose.frontend-delegation-hermes.yaml \
  $AGENT/docker/Dockerfile.fdh $AGENT/docker/fdh/* $D/agent/config/docker/
docker image inspect nemotron-voice-agent-fdh:latest --format '{{.Id}} {{.Created}}' > $D/provenance/image.txt
```

Then `$E/archive.sh --reports`, check `_reports/` holds only this campaign's reports, and write the run card
`$DUMP/tau-3-voice/$CAMPAIGN/README.md` (parent runbook §9 list), adding: container `fdh-tau`, image id, Compose
file, `FDH_PROFILE`/`FDH_GATEWAY_CONFIG`, the run tag, and which archive files were added by hand. The secret scan,
Git LFS check and commit are the parent runbook §9. Never copy `.env`.

Files the container wrote under `[agent]/logs` are owned by root; `sudo chown -R "$USER" $AGENT/logs` before moving
or deleting any of them (copying works without it).

---

## 10. Stop [agent]

Only after archiving, and only if nothing else needs it:

```bash
cd $AGENT && docker compose -f docker/docker-compose.frontend-delegation-hermes.yaml --profile tau down   # up to 75 s
```

`fdh-tau` has `restart: unless-stopped`: it comes back after a reboot or a Docker restart unless it was stopped.

---

## 11. Troubleshooting (specific to `fdh-tau`)

Parent runbook §11 still applies for tau2/I0 and agent-behaviour symptoms; the skill's
`references/troubleshooting.md` covers the container.

| Symptom | Check |
|---|---|
| `check_stack.sh`: `fdh-voice does not run profiles/realtime_eval.yaml`, `FDH_EVENT_LOG=`, `gateway http://172.17.0.1:8790 not healthy` | expected until the scripts are ported (§2); use §1 |
| `status.sh` last line `gateway ` empty | expected (§2); `docker exec fdh-tau curl -s 127.0.0.1:8790/health` |
| `docker ps` shows `fdh-tau` `Restarting` or `RestartCount` > 0 | the gateway or voice server exited, and the entrypoint takes the container down with it. `docker logs fdh-tau` (`gateway not healthy`, `unknown adapter`, a traceback). Runs in progress during a restart are invalid |
| `curl localhost:8775/health` fails right after `up -d` | the entrypoint waits for the gateway, then the voice server warms up; the healthcheck has a 90 s start period |
| `/health` shows another `config_hash` or features | the container runs another `FDH_PROFILE` (a shell export or `.env` value wins over the Compose default). `docker inspect` env, then re-`up -d` with the right exports — after archiving |
| Sessions missing (`status.sh` `sessions=0`) | `FDH_EVENT_LOG` of the container is not `logs/fdh_voice_events.realtime_eval.jsonl`, or the run name/tag differs from what you passed to `status.sh` |
| `capacity` refusals / `refused` > 0 in gateway health | back-to-back sessions while a worker is still stopping (up to 15 s), or concurrency > 4, or someone else used port 8775. Rerun with `--auto-resume` |
| `Permission denied` moving files under `[agent]/logs` | root-owned (container writes): `sudo chown -R "$USER" logs` |
| Old run resumed instead of a new one | the run name existed in `data/simulations/` (§3.2). Use `TAU3_TAG` |
| Hermes import errors in worker logs | image built from a Hermes checkout at the wrong commit: `HERMES_AGENT_DIR` must be clean and at the commit in `docker/fdh/requirements.txt`'s header; rebuild (`--build`) |

---

## 12. What to record when you report

Parent runbook §12, plus: container `fdh-tau`, image `nemotron-voice-agent-fdh:latest` (id and created date), the
Compose file, `FDH_PROFILE=realtime_eval.yaml`, `FDH_GATEWAY_CONFIG=gateway.eval.yaml`, in-container gateway, the
run tag (`tau`), the user/judge model the user chose, the telecom note on, `bm25` for banking_knowledge, and that
this is the first campaign on the containerized stack (no parity with the old host-gateway stack established).
