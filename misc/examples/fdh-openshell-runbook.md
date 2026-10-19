# Runbook: τ³ voice evaluation of the FDH agent on the shared stack, OpenShell runtime, eval mode

**Date:** 2026-10-16 · **Status:** the `frontend-delegation-hermes-agent/single-gpu` recipe is up in **eval mode**
with the default **OpenShell** Hermes runtime (Compose project `nemotron-voice-agent`, files
`docker-compose.yml` + `docker/docker-compose.fdh-eval.yaml` + a session-only key overlay, §1). The container under
test is **`nemotron-voice-agent-frontend-delegation-hermes-agent-single-gpu-1`** (the voice side, port **7860**,
plain `ws://`, healthy, `FDH_PROFILE=eval`); the Hermes backend runs in the OpenShell sandbox `fdh-hermes`. The tau2
side needs only the `fdhos_run` helper of this runbook; `src/tau2/` is not modified ·
**Agent deploy guide:** `nemotron-voice-agent/docs/how-to/deploy-frontend-delegation-hermes-agent.md` ·
**Agent code:** `nemotron-voice-agent/src/examples/frontend_delegation_hermes/` (`README.md`) ·
**Parent runbook (I0, `.env`, metric definitions, gpt-5.5 rule):**
[`../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md`](../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md)
(called "the parent runbook" below) · **Sister runbooks (same layout):** [`fba-tau3-runbook.md`](fba-tau3-runbook.md)
(same repository Realtime server, the base-URL trick and bearer come from there),
[`fdh-pipecat-tau3-runbook.md`](fdh-pipecat-tau3-runbook.md) (the former FDH Pipecat container, port 8776),
[`fdh-tau-tau3-runbook.md`](fdh-tau-tau3-runbook.md) · **Scripts:**
[`../prototypes/fdh_voice_eval/`](../prototypes/fdh_voice_eval/README.md) (only `fdh_logs.py`; the shell scripts
don't know this stack, §2)

This runbook takes the running FDH OpenShell deployment to a τ³ voice report for **airline, retail, telecom and
banking_knowledge** (mock only for the smoke run), with every log and artifact archived in the dump repo. The agent
under test is the Frontend Delegation Hermes agent: a fast frontend LLM that talks and delegates, and a Hermes
`AIAgent` backend (one worker per session) behind the FDH gateway, here inside an NVIDIA OpenShell sandbox, served by
the repository's OpenAI Realtime gateway on the shared Pipecat speech stack.

## Rules (read before anything else)

> **ALWAYS ASK the user which Inference Hub model to use for the user simulator and the judge before any run —
> smoke included, every campaign, every time: `azure/openai/gpt-5.2` or `openai/openai/gpt-5.5`.** Never pick one
> yourself and never carry a choice over from an earlier campaign or another runbook. The same model is used for the
> user simulator LLM, the NL-assertion judge (`TAU2_JUDGE_MODEL`) and the hallucination check / review model
> (`TAU2_REVIEW_MODEL`). The user TTS (`gpt-4o-mini-tts`) and the user decision LLM (`gpt-4.1`) stay as in every other
> runbook (I0, `tau2_ihub_overrides.py`). The `fdhos_run` helper (§3.3) **refuses to start** until
> `FDHOS_USER_JUDGE` is set to `gpt52` or `gpt55`: set it only from the user's answer. gpt-5.2 and gpt-5.5 numbers
> are **not comparable** (2026-10-08 FDH campaign: telecom 0.439 → 0.725, retail 0.658 → 0.623); keep one model for
> every domain of a campaign and name it in the run card.

> **Do not start any run (smoke included) until the user says go.**

> **ASK the user before recreating, restarting or stopping any container of the recipe** (voice side, `fdh-hermes`,
> the OpenShell gateway, `nemo-speech`), and before stopping any other container. Archive first (§9). Stopping
> `fdh-hermes` **deletes the sandbox and its logs** (§2.3).

> **Speech complexity `regular` for every domain, mock smoke included.** Never `control`. `fdhos_run` hardcodes it.

> **ALWAYS ASK the user before changing τ²'s user-simulator turn-taking thresholds — every time, for every run.**
> Run with tau2's defaults (`src/tau2/config.py`, in seconds of audio time): `--wait-to-respond-self` **5.0**,
> `--wait-to-respond-other` **1.0**, `--silence-annotation-threshold` **4.0**, and the fixed
> `DEFAULT_AUDIO_NATIVE_MAX_INACTIVE_SECONDS` **40**. `fdhos_run` passes none of these flags. A run with other values
> is a labelled diagnostic (tag it, e.g. `TAU3_TAG=tt10-2-8`; put it in the run card), never next to
> default-threshold results.

> **Concurrency: 1 for mock, 4 for every other domain** (the FDH campaigns' setting; `TAU3_CONCURRENCY` overrides,
> only with the user's OK). 4 is the voice side's Realtime cap (`FDH_MAX_SESSIONS=4`; a fifth session gets
> `server_busy`, close 1013); the gateway allows 8 (`FDH_GATEWAY_MAX_SESSIONS=8`).

> **MANDATORY: archive every run (smoke, stopped, invalid) in
> `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/` (§9) right
> after it finishes, and before you recreate, restart or stop any container of the recipe, or change agent code.**
> The gateway event log, the Hermes worker logs and the backend decision traces live **only inside the sandbox**
> while it runs (§2.3); a recreated voice container has no old `docker logs` (50 MB × 5 rotation). A run is not
> finished until it is archived.

## Where commands run

| Tag | Directory |
|---|---|
| **[tau2]** | `/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar` (every eval command runs from here) |
| **[agent]** | `/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent` (branch `dev/smasurekar/fbv-fdh-experimental`, `07076e9`, clean on 2026-10-16; the code the stack runs) |
| **[tools]** | `/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar` (only for `report_adapter` and `fingerprint_check`, which [agent] no longer has, §2) |
| **[hermes]** | the `HERMES_AGENT_DIR` of `[agent]/.env` (Hermes commit `af26acab…`, built into the image; provenance only) |
| **[dump]** | `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump` |

Don't source `fdh_lib.sh` for this runbook (its `AGENT` points at [tools] and its ports/containers are the old
ones). Shell setup for every command below (paste into every new shell, tmux included):

```bash
export TAU2=/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
export AGENT=/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent
export TOOLS=/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar
export DUMP=/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump
export IHUB=https://inference-api.nvidia.com/v1
export FDHOS_V=nemotron-voice-agent-frontend-delegation-hermes-agent-single-gpu-1   # voice side (container under test)
export FDHOS_H=nemotron-voice-agent-fdh-hermes-1                                    # OpenShell controller
export FDHOS_OSGW=nemotron-voice-agent-hermes-openshell-gateway-1                   # OpenShell gateway
export FDHOS_SPEECH=nemotron-voice-agent-nemo-speech-1
export FDHOS_URL=ws://localhost:7860/v1/realtime                                     # eval mode: TLS off
export FDHOS_MODEL=nvidia/nemotron-realtime-frontend-delegation-hermes              # the only model the route serves
export FDHOS_EV=$AGENT/logs/fdh_voice_events.pipecat.jsonl                           # voice event log (host bind mount)
export FDHOS_CONSOLES=$TAU2/data/simulations/_consoles FDHOS_METRICS=$TAU2/data/simulations/_metrics
export FDHOS_MAP=$FDHOS_CONSOLES/fdhos_session_map.tsv                               # FDH sid <-> Realtime gateway sid (provenance, §2.2)
export FDHOS_SNAP=$FDHOS_CONSOLES/fdhos_sandbox_logs                                 # copies of the sandbox's /app/logs (§2.3)
export CAMPAIGN=$(cat $FDHOS_CONSOLES/CAMPAIGN_FDHOS 2>/dev/null)
FDHOS_KEY=$(sed -n 's/^REALTIME_API_KEY=//p' $AGENT/.env | tr -d '"'"'")             # the Realtime bearer; never echo it
DC="docker compose -f docker-compose.yml -f docker/docker-compose.fdh-eval.yaml --profile frontend-delegation-hermes-agent/single-gpu"  # run in [agent]

fdh_logs() { python3 $TAU2/misc/prototypes/fdh_voice_eval/fdh_logs.py "$@"; }       # stdlib log helpers
fdhos_env() { docker inspect ${1:-$FDHOS_V} --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -vE 'KEY|TOKEN|SECRET|PASS'; }
fdhos_gw_health() {   # FDH gateway /health, through the OpenShell gateway (the voice side's own route)
  docker exec $FDHOS_H curl -s -H 'Host: default--fdh-hermes.openshell.localhost' http://hermes-openshell-gateway:8080/health; echo; }
fdhos_workload() { docker ps --filter ancestor=nemotron-voice-agent-fdh:local --format '{{.Names}}' | grep -v -- '-supervisor$' | head -1; }
fdhos_sandbox_logs() {   # <dest>: copy the sandbox's /app/logs (gateway events, worker logs, traces) to <dest>/logs
  mkdir -p "$1" && docker exec $FDHOS_H openshell sandbox exec fdh-hermes -- tar -C /app -cf - logs | tar -xf - -C "$1"; }
fdhos_map_update() {     # add "gateway_sid<TAB>fdh_sid" pairs from the app log to $FDHOS_MAP (provenance only, §2.2)
  mkdir -p "$FDHOS_CONSOLES"; touch "$FDHOS_MAP"
  { cat "$FDHOS_MAP"; docker logs $FDHOS_V 2>&1 \
      | sed -nE 's/.*\[stream_id=(sess_[0-9a-f]+)\] \[(sess_[0-9a-f]+)\] session started.*/\1\t\2/p'; } \
    | sort -u > "$FDHOS_MAP.tmp" && mv "$FDHOS_MAP.tmp" "$FDHOS_MAP"; }
fdhos_sessions() {       # <run>: session ids of that run, as tau2's task.log records them (= FDH ids of the event/worker logs)
  grep -rhoE 'session created \(session_id=sess_[0-9a-f]+\)' $TAU2/data/simulations/$1/artifacts --include=task.log 2>/dev/null \
    | grep -oE 'sess_[0-9a-f]+' | sort -u; }
```

---

## 0. What you are measuring, and how this stack differs

The metrics are the parent runbook's §0/§7 (Pass^1; tau2's L_R/L_Y, R_R/R_Y, I_A, S_BC/S_VT/S_ND; mean realtime
response latency; frontend/backend per-turn latency; FE/BE tokens per task; checks C1–C8; the P0.1 429 gate; exact
join; measured filler), computed by `fba_voice_metrics.py` from the voice event log after the FDH report adapter.

| | `fdh-pipecat` runbook (former `fdh-voice`) | **This stack** |
|---|---|---|
| Server | FDH's own Pipecat server, port 8776 | the **repository server** (`src/server.py`, Realtime gateway `src/realtime/gateway.py`), port **7860**, `EXAMPLE_SELECTION=frontend-delegation-hermes-agent` |
| Containers | one `fdh-voice` (voice + gateway + workers) | **voice side** `…-frontend-delegation-hermes-agent-single-gpu-1` (image `nemotron-voice-agent:latest`); **`…-fdh-hermes-1`** (OpenShell controller); **OpenShell sandbox `fdh-hermes`** (workload `openshell-default--fdh-hermes-<uuid>` + `-supervisor`, image `nemotron-voice-agent-fdh:local`) runs the FDH gateway and the Hermes workers; **`…-hermes-openshell-gateway-1`** (127.0.0.1:8080) routes the backend link; shared `nemotron-voice-agent-nemo-speech-1` |
| Backend link | in-container `127.0.0.1:8790` | `ws://default--fdh-hermes.openshell.localhost:8080/v1/backend` (through the OpenShell gateway) |
| Model routing | any `pine-…` tag | **only `?model=nvidia/nemotron-realtime-frontend-delegation-hermes`**; the deploy guide: "the former `pine-*` model names and ports 8775/8776 no longer reach FDH". `fdhos_run` puts the real model in the base URL (§2.1) |
| Auth | none | **`REALTIME_API_KEY` bearer** (set in `[agent]/.env`): `PINE_API_KEY` = that key |
| Profile | `FDH_PROFILE=pipecat_realtime_eval.yaml` | `FDH_PROFILE=eval` (Realtime sessions always load `profiles/realtime_eval.yaml` + `profiles/pipecat_realtime_eval.yaml`), `config hash` **`817a51963745`** (includes the OpenShell backend URL) |
| Gateway config | `gateway.eval.yaml` | **the same**, on `fdh-hermes` (`FDH_GATEWAY_CONFIG=gateway.eval.yaml`, from the eval overlay) |
| Turn taking | Silero `stop_secs` 3.0 s of wall time | **Silero `stop_secs` 0.8 s of input audio**, `audio_idle_timeout 0`, `user_speech_timeout 0` (`adapters/pipecat.eval.yaml`); server-owned, so tau2's 500 ms request is ignored (`session_updated` echoes 800 ms) |
| Run identity in the event log | `session_start.model` = the run's `pine-` tag | `session_start.model` is always the real model id. Runs are told apart by **session id**: the id tau2's `task.log` records (`session created (session_id=sess_<20 hex>)`) is the FDH session id of the event log, gateway event log and worker log names (verified 2026-10-16: exact join 4/4) |
| Event log | `logs/fdh_voice_events.pipecat.jsonl` | the same path, now in `[agent]/logs` (bind mount, root-owned) |
| Gateway / worker logs | host `logs/fdh_gateway_events.pipecat.jsonl`, `logs/fdh_workers_pipecat/` | **inside the sandbox** at `/app/logs/…` (same names); copied out by `fdhos_sandbox_logs` (§2.3) |
| Docker logs | 50 MB × 3 | voice side **50 MB × 5** (DEBUG level); `fdh-hermes` 50 MB × 3; sandbox workload unbounded, **deleted with the sandbox** |
| Restart policy | `restart: "no"` | voice side `unless-stopped`; `fdh-hermes` `no` (if it dies, the backend is gone and tasks fail) |

Models (2026-10-16, from the `ready` line, `/health`, the gateway health and the Pipecat init lines) — the same as in
the other FDH/FBV runbooks:

| Role | Model |
|---|---|
| Frontend LLM | `nvidia/nvidia/nemotron-3.5-lightning` on the Inference Hub, reasoning off (`enable_thinking false`), `max_tokens 256` |
| Backend LLM | `nvidia/nvidia/nemotron-3-ultra` (Hermes `AIAgent`, one worker per session), reasoning on, budget 1024 |
| ASR | `nemotron-speech-streaming-en-0.6b` (NeMo-Speech.cpp, Pipecat `NvidiaForceEouSTTService`) |
| TTS | `magpie-tts-multilingual`, catalog voice **`John`** (`services.local.yaml` singlegpu; client voice ignored), Pipecat `NvidiaTTSService` `per_sentence` |
| User simulator / judge / review | **the user's choice**: `azure/openai/gpt-5.2` or `openai/openai/gpt-5.5` (Rules) |
| User TTS / decision LLM (I0) | `openai/openai/gpt-4o-mini-tts` / `openai/azure/openai/gpt-4.1` (unchanged) |

**Numbers are a new series.** Don't compare with the `fdh-tau` / `fdh-pipecat` campaigns (other server, 0.8 s
audio-time VAD instead of 3 s wall time, sandboxed backend, other `config_hash`) unless the user asks for a labelled
cross-stack comparison; pair runs by the prompt hashes and `session_tools_sha256` (fingerprint, §5.1), not by
`config_hash`.

---

## 1. Check the deployment as it runs now [agent]

Start, stop and recreate only through the deploy guide (*Deploy in Eval Mode*, *Instructions for AI Agents*): eval
overlay on every Compose command, OpenShell runtime (no plain overlay).

```bash
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -E 'nemotron-voice-agent-|openshell-default|fdh|fbv'
#   …-frontend-delegation-hermes-agent-single-gpu-1  nemotron-voice-agent:latest  Up (healthy)  0.0.0.0:7860->7860/tcp
#   …-fdh-hermes-1  nemotron-voice-agent-fdh-openshell-cli:local  Up (healthy)
#   …-hermes-openshell-gateway-1  ghcr.io/nvidia/openshell/gateway:0.1.5-pre.4  Up  127.0.0.1:8080
#   openshell-default--fdh-hermes-<uuid>(-supervisor)  Up
#   …-hermes-openshell-init-1, …-fdh-hermes-image-1  Exited (0)        (one-shot, expected)
#   …-nemo-speech-1  Up  50051;   fbv-v2, fdh-voice, …  Exited         (nothing else on 7860/50051 traffic)
docker inspect $FDHOS_V --format 'image={{.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}} health={{.State.Health.Status}}'
docker inspect $FDHOS_V --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} rw={{.RW}}{{println}}{{end}}'
fdhos_env | grep -E '^(FDH_|FRONTEND_LLM_|PIPELINE_TLS|EXAMPLE_SELECTION|REALTIME_SERVICE_PLATFORM|UVICORN_)'
fdhos_env $FDHOS_H | grep -E '^(FDH_|BACKEND_LLM_)'
docker inspect $FDHOS_V --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' | tr , '\n'
curl -s localhost:7860/health; echo
fdhos_gw_health
docker logs $FDHOS_H 2>&1 | grep -E 'gateway config|OpenShell CLI'
docker logs $(fdhos_workload) 2>&1 | grep 'FDH gateway'
(cd $AGENT && find src -type f ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
docker logs $FDHOS_V 2>&1 | grep -E 'frontend-delegation-hermes-agent ready|Pipecat adapter settings|Setting VAD params|Initialized Nvidia(STT|TTS)Service' | tail -4 | cut -c1-260
```

Seen on 2026-10-16 (agent `07076e9`, clean tree):

- voice side: image `sha256:4cf616b66607…` (built 18:59 IST), started 13:33:32Z, restarts 0, mounts `src` (ro),
  `examples_registry.yaml` (ro), `logs` (rw); `FDH_PROFILE=eval`, `PIPELINE_TLS=false`, `FDH_MAX_SESSIONS=4`,
  `FDH_EVENT_LOG=logs/fdh_voice_events.pipecat.jsonl`,
  `FDH_BACKEND_URL=ws://default--fdh-hermes.openshell.localhost:8080/v1/backend`, `FDH_PROACTIVE_AFTER_S=15`,
  `FDH_BACKEND_DELAY_S=5` (demo profile only; eval never delays), `UVICORN_WS_PING_INTERVAL=0`,
  `UVICORN_WS_PING_TIMEOUT=20`, `REALTIME_SERVICE_PLATFORM=singlegpu`.
- `fdh-hermes`: `FDH_GATEWAY_CONFIG=gateway.eval.yaml`, `FDH_GATEWAY_MAX_SESSIONS=8`,
  `BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra`, image `nemotron-voice-agent-fdh:local` (`a84f40abf50a…`),
  OpenShell CLI `0.1.5-pre.4`; log line `fdh-hermes (OpenShell runtime): image nemotron-voice-agent-fdh:local, gateway config gateway.eval.yaml`.
- Compose files: `docker-compose.yml`, `docker/docker-compose.fdh-eval.yaml`, and a **session-only key overlay**
  `/tmp/claude-1000/…/scratchpad/fdh-voice-key.yaml` that passes `NVIDIA_API_KEY` to the voice service
  (`NVIDIA_API_KEY` is **empty in `[agent]/.env`**). That file can disappear; see §10 before any Compose command.
- `/health` → `{"status":"ok"}` (liveness only). Gateway health: `"ok":true`, `"max_sessions":8`, `"refused":0`,
  `"agent_kind":"hermes"`, every `backend_features` flag `true` (incl. `domain_notes`), `"domains":["telecom"]`,
  `"backend_catalog_sha256":"55639a8b238b7eec"`, `"hermes":{"model":"nvidia/nvidia/nemotron-3-ultra","backend_trace":true,"reasoning":true}`.
- `FDH gateway: config gateway.eval.yaml, source 498895c3ba30…` = the host `src` hash (image matches the checkout).
- Ready line (after the first session): `frontend-delegation-hermes-agent ready (adapter=pipecat, profile=eval)`,
  `config hash 817a51963745`, Pipecat `{'vad': {'stop_secs': 0.8, 'audio_idle_timeout': 0.0}, 'turn':
  {'user_speech_timeout': 0.0}, 'tts_synthesis_mode': 'per_sentence', 'smart_turn': False}`; `Setting VAD params …
  stop_secs=0.8`; STT `nemotron-speech-streaming-en-0.6b`; TTS `magpie-tts-multilingual`.
- The event log already holds **2 sessions** from someone's smoke test (13:35–13:36Z, `get_customer_by_phone`). They
  are outside any run's time span and their ids never appear in a `task.log`, so they don't enter a report.

NLTK `punkt_tab` (deploy guide eval step 3) lives in the container layer: fetch it once after every recreate, before
the first session — `docker exec $FDHOS_V .venv/bin/python -c "import nltk; print(nltk.download('punkt_tab', quiet=True))"` → `True`.

---

## 2. What tau2 needs on this stack

### 2.1 URL, model and bearer (as `fbv-v2`)

tau2 builds `"{PINE_REALTIME_BASE_URL}?model={model}"` and requires `model` to start with `pine-`; the route only
serves `nvidia/nemotron-realtime-frontend-delegation-hermes` (`model_not_available` otherwise). With
`PINE_REALTIME_BASE_URL=$FDHOS_URL?model=$FDHOS_MODEL&x_tau_run=<run>` the final URL is
`…?model=nvidia/…hermes&x_tau_run=<run>?model=pine-…`: the server's query parser sees one `model` (the real one)
and a harmless `x_tau_run`. `PINE_API_KEY` = `REALTIME_API_KEY` of `[agent]/.env` (close 1008
`realtime authentication failed` otherwise). TLS is off in eval mode, so tau2's `websockets.connect` works. Nothing
in tau2 or the agent changes; `fdhos_run` does all of it.

### 2.2 Session ids

The Realtime gateway logs its own connection id (`Realtime WS connected session_id=sess_<32 hex>`, the app log's
`stream_id`), but the `session.created` event comes from FDH and carries the **FDH session id** (`sess_<20 hex>`).
That is the id tau2 writes to `task.log` and the one used in the voice event log, the gateway event log and the worker
log names, so `fdh_logs exact-join` works on `join.csv` directly (2026-10-16 smoke: 4 primary joins, 4 exact). The app
log links the two:

```
[stream_id=sess_684a9301…(gateway)] [sess_0cd2692c…(FDH, = task.log)] session started (model=nvidia/nemotron-realtime-frontend-delegation-hermes, adapter=pipecat)
```

`fdhos_map_update` keeps these pairs in `$FDHOS_MAP` only to find a session's lines in `docker logs` (they are tagged
with the gateway `stream_id`). `fdh_logs sessions/filter/status` select by `session_start.model == pine-…`, which never
matches here: use `fdhos_sessions <run>`.

### 2.3 Gateway, worker and trace logs are in the sandbox

In the OpenShell runtime the FDH gateway writes `/app/logs/fdh_gateway_events.pipecat.jsonl` and
`/app/logs/fdh_workers_pipecat/sess_<fdh>-<n>.log` + `sess_<fdh>.trace.jsonl` **inside the sandbox**. Nothing
reaches the host until `fdh-hermes` stops, and then only if its save step works:

- The deploy guide's `openshell sandbox download fdh-hermes /app/logs /out/now` **fails on this build**
  (`failed to resolve sandbox source path '/app/logs'`, tested 2026-10-16, also with a file path). The save-on-stop
  step may fail the same way: **never rely on `logs/openshell/<time>/`.**
- `openshell sandbox exec … tar` works (tested): `fdhos_sandbox_logs <dest>` streams `/app/logs` to `<dest>/logs/`
  on the host, owned by you. Archive (§9) and the 429 gate (§7) use it.
- The sandbox workload's `docker logs` (FDH gateway stdout, worker stderr, Landlock denials) are unbounded but are
  deleted with the sandbox: archive them before any stop. Egress decisions: `docker exec $FDHOS_H openshell logs fdh-hermes`.

### 2.4 Script status

| Tool | Works? | Instead |
|---|---|---|
| `fdh_voice_eval/*.sh` (`run.sh`, `campaign.sh`, `status.sh`, `check_stack.sh`, `report.sh`, `archive.sh`, `new_campaign.sh`) | **no** | old ports 8775/8776, old containers, `AGENT` = [tools], host gateway; use this runbook's helpers |
| `fdh_logs.py checks`, `ratelimit`, `filler`, `exact-join` | **yes** | |
| `fdh_logs.py sessions`, `filter`, `status` | **no** | select by `pine-` model tag; use `fdhos_sessions` |
| `report_adapter`, `fingerprint_check` | **yes, from [tools]** | `[agent]` dropped `src/examples/frontend_delegation_hermes/cli/`. Both ran clean on this stack's event log on 2026-10-16 (adapter exit 0; `fingerprint: OK` with `[agent]`'s `profiles/pipecat_realtime_eval.yaml` + `gateway.eval.yaml`) |
| `fba_voice_metrics.py` | **yes** | `--model <run>=$FDHOS_MODEL` (selection by model + the run's time span + tool call ids) |

---

## 3. tau2 setup, the campaign, and the run helper [tau2]

### 3.1 One-time tau2 setup (already done on this host)

The parent runbook §2: `uv sync --extra voice --extra knowledge --extra dev` (`--inexact` keeps other extras),
portaudio/pyaudio, **I0** (`misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py` + `tau2_ihub.py`; every run goes
through `tau2_ihub.py`, never plain `tau2 run`), and `$TAU2/.env` with the Hub key (`OPENAI_API_KEY=sk-…`),
`TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2`, `TAU2_JUDGE_BASE_URL`, `TAU2_USER_TTS_*`. `fdhos_run` sets the judge
and review model on its command line, so `.env` stays as it is for both gpt-5.2 and gpt-5.5. `banking_knowledge`
uses `--retrieval-config bm25` (`TAU3_RETRIEVAL` overrides). Quick check:

```bash
cd $TAU2 && uv run tau2 check-data && ls misc/prototypes/fba_voice_eval/tau2_ihub*.py
grep -E '^TAU2_(JUDGE_MODEL|JUDGE_BASE_URL|USER_TTS_MODEL|USER_TTS_BASE_URL)=' .env
grep -q '^OPENAI_API_KEY=sk-' .env && echo "OPENAI_API_KEY set"
```

The parent runbook §2.3 endpoint check (user LLM, judge, decision, review, user TTS) is worth repeating at the start
of a campaign; with gpt-5.5 use the model `openai/openai/openai/gpt-5.5` there.

### 3.2 New campaign

One dump folder per campaign: same deployment (images, Compose files, profile, gateway config, agent commit) and
the same user/judge model throughout. `CAMPAIGN_FDHOS` is this stack's own pointer (`CAMPAIGN_FDH` and
`CAMPAIGN_FBV` belong to the other runbooks; leave them alone).

```bash
echo "$(date -u +%Y-%m-%d_%H-%M-%SZ)_fdh-openshell-<gpt52|gpt55>[-smoke]" > $FDHOS_CONSOLES/CAMPAIGN_FDHOS
export CAMPAIGN=$(cat $FDHOS_CONSOLES/CAMPAIGN_FDHOS); echo $CAMPAIGN
mkdir -p $DUMP/tau-3-voice/$CAMPAIGN
```

Before replacing `CAMPAIGN_FDHOS`, make sure the previous campaign is archived (§9).

### 3.3 The run helper

Paste into the shell (after the Shell setup):

```bash
# usage: FDHOS_USER_JUDGE=gpt52|gpt55 [TAU3_TAG=smoke] [TAU3_RETRIEVAL=bm25] [TAU3_CONCURRENCY=n] fdhos_run <domain> [tau2 args...]
fdhos_run() {
  [ $# -ge 1 ] || { echo "usage: fdhos_run <domain> [tau2 args...]" >&2; return 2; }
  local domain=$1; shift
  local um
  case ${FDHOS_USER_JUDGE:-} in
    gpt52) um=openai/azure/openai/gpt-5.2;;
    gpt55) um=openai/openai/openai/gpt-5.5;;      # LiteLLM openai/ prefix + Hub name openai/openai/gpt-5.5
    *) echo "FDHOS_USER_JUDGE is not set. ASK the user: gpt52 (azure/openai/gpt-5.2) or gpt55 (openai/openai/gpt-5.5)" >&2; return 2;;
  esac
  [ -n "$FDHOS_KEY" ] || { echo "FDHOS_KEY empty: REALTIME_API_KEY not found in $AGENT/.env" >&2; return 2; }
  [ "$(fdhos_env | sed -n 's/^FDH_PROFILE=//p')" = eval ] || { echo "voice side is not FDH_PROFILE=eval: refusing (§1)" >&2; return 2; }
  local tag=${TAU3_TAG:-}; [ "$FDHOS_USER_JUDGE" = gpt55 ] && tag=${tag:+$tag-}gpt55
  local name=fdhos_${domain}_regular${tag:+_$tag}
  local model=pine-${name//_/-}                                   # tau2 needs pine-; only tau2's run label here
  local conc=${TAU3_CONCURRENCY:-$([ "$domain" = mock ] && echo 1 || echo 4)}
  local extra=(); [ "$domain" = banking_knowledge ] && extra=(--retrieval-config "${TAU3_RETRIEVAL:-bm25}")
  mkdir -p "$FDHOS_CONSOLES"
  date -Is >> "$FDHOS_CONSOLES/$name.start"                      # one line per (re)start; line 1 = first start
  echo "== $name  label=$model  url=$FDHOS_URL?model=$FDHOS_MODEL  container=$FDHOS_V  runtime=openshell  user/judge/review=$um  max_concurrency=$conc  speech_complexity=regular  turn-taking=tau2 defaults  campaign=${CAMPAIGN:-unset}  ${extra[*]}" \
    | tee -a "$FDHOS_CONSOLES/$name.log"
  [ -z "${TAU3_CONCURRENCY:-}" ] || echo "WARN max_concurrency override $conc: approved by the user?" | tee -a "$FDHOS_CONSOLES/$name.log"
  ( cd "$TAU2" && export TAU2_JUDGE_MODEL="$um" TAU2_REVIEW_MODEL="$um" \
      PINE_REALTIME_BASE_URL="$FDHOS_URL?model=$FDHOS_MODEL&x_tau_run=$name" PINE_API_KEY="$FDHOS_KEY" && \
    uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain "$domain" --audio-native \
      --audio-native-provider openai --audio-native-model "$model" \
      --speech-complexity regular \
      --user-llm "$um" --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
      --review-model "$um" \
      --task-split-name base --num-trials 1 --max-concurrency "$conc" \
      --save-to "$name" --verbose-logs "${extra[@]}" "$@" ) 2>&1 | tee -a "$FDHOS_CONSOLES/$name.log"
  local rc=${PIPESTATUS[0]}
  date -Is > "$FDHOS_CONSOLES/$name.end"
  fdhos_map_update
  return "$rc"
}
```

- Run names: `fdhos_<domain>_regular[_<tag>]`, e.g. `fdhos_airline_regular_gpt55`, `fdhos_mock_regular_smoke-gpt55`.
  gpt-5.5 runs always get `gpt55` in the tag, so they never resume or overwrite a gpt-5.2 run. No `_` in tags. No
  `fdhos_*` run exists in `data/simulations/` yet (2026-10-16).
- Extra arguments go last and win (`--num-tasks 5`, `--task-ids …`, `--auto-resume`). **Never** pass
  `--wait-to-respond-*`, `--silence-annotation-threshold`, `--speech-complexity` or `--user-llm` here (Rules).
- The same name resumes an existing run (tau2 `--save-to`); add `--auto-resume` to rerun infrastructure errors.

---

## 4. Preflight [tau2 + agent]

Ready when every line holds:

```bash
docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' $FDHOS_V $FDHOS_H     # healthy restarts=0 (both)
curl -s localhost:7860/health; echo                                                          # {"status":"ok"}
fdhos_env | grep -E '^(FDH_PROFILE|PIPELINE_TLS|FDH_EVENT_LOG|FDH_MAX_SESSIONS)='             # eval, false, logs/…pipecat.jsonl, 4
fdhos_env $FDHOS_H | grep '^FDH_GATEWAY_CONFIG='                                             # gateway.eval.yaml
fdhos_gw_health | python3 -c 'import json,sys; h=json.load(sys.stdin); print(h["ok"], h["sessions"], h["refused"], h["agent_kind"], h["hermes"]["model"], h["backend_catalog_sha256"], h["domains"])'
#   True 0 0 hermes nvidia/nvidia/nemotron-3-ultra 55639a8b238b7eec ['telecom']
fdhos_workload                                                                               # openshell-default--fdh-hermes-<uuid>
docker ps --format '{{.Names}}' | grep -x $FDHOS_SPEECH                                       # nemo-speech running
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -vE "nemotron-voice-agent-|openshell-default"   # other agents up? (expect nothing)
echo "campaign=$CAMPAIGN"; git -C $AGENT log --oneline -1; git -C $AGENT status --short | wc -l; git -C $TAU2 log --oneline -1
```

- **Other agents on the same `nemo-speech` and Hub key** (`fbv-v2`, `fdh-voice`, `fdh-tau`, …): all exited on
  2026-10-16. If one is up, any traffic on it shares ASR/TTS and Hub quota: **ask the user** whether to stop it
  (after its own runs are archived) and note the answer in the run card.
- **The OpenShell gateway is host-wide** (`127.0.0.1:8080`, `/var/lib/openshell`): no second OpenShell stack on
  this host during a campaign.
- Record provenance for the run card: agent `HEAD` + branch + dirty count, tau2 `HEAD`, the three image ids (§9), the
  `FDH gateway … source` hash and the host `src` hash (§1).

Handshake probe (one Realtime session, no audio; checks URL, bearer, TLS off, the model trick, and that the event log
and the id map are written). It opens an FDH session and a Hermes worker for a few seconds — run it only with the
user's OK, and not during a run:

```bash
cd $TAU2 && FDHOS_KEY="$FDHOS_KEY" uv run python - <<'PY'
import asyncio, json, os, websockets
url = f"{os.environ['FDHOS_URL']}?model={os.environ['FDHOS_MODEL']}&x_tau_run=preflight?model=pine-fdhos-preflight"
async def main():
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {os.environ['FDHOS_KEY']}"}) as ws:
        ev = json.loads(await asyncio.wait_for(ws.recv(), 60))
        print(ev["type"], ev.get("session", {}).get("id"), ev.get("session", {}).get("model"), ev.get("error", ""))
asyncio.run(main())
PY
#   session.created sess_<32 hex> nvidia/nemotron-realtime-frontend-delegation-hermes
fdhos_map_update; tail -1 $FDHOS_MAP; tail -1 $FDHOS_EV | python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); print(r["kind"], r["session_id"])'
```

`model_not_available` = the base-URL trick is missing; close 1008 = wrong `FDHOS_KEY`; an SSL error = TLS is on
(not eval mode); `server_busy` (1013) = 4 Realtime sessions open or the warm-up failed (`startup failed` in the app
log); a hang on the first session after a recreate = NLTK `punkt_tab` (§1). The probe's session is outside any run's
time span and never appears in a `task.log`.

---

## 5. Smoke runs [tau2] — only after the user says go and has chosen the user/judge model

```bash
export FDHOS_USER_JUDGE=<gpt52|gpt55>        # the user's answer, nothing else
TAU3_TAG=smoke fdhos_run mock --num-tasks 1               # concurrency 1
TAU3_TAG=smoke fdhos_run airline --num-tasks 4            # concurrency 4: four sessions / workers / ASR streams at once
for d in retail telecom banking_knowledge; do TAU3_TAG=smoke fdhos_run $d --num-tasks 1; done
```

After each run: archive it (§9). Then the report (§7) with `tag=smoke` (`smoke-gpt55` for gpt-5.5) and the five
domains, and `_reports/` (§9).

### 5.1 Gate

`RUN` is the run name, `SINCE=$(head -1 $FDHOS_CONSOLES/$RUN.start)`, `R=$TAU2/data/simulations/$RUN`,
`SNAP=$DUMP/tau-3-voice/$CAMPAIGN/$RUN/agent/sandbox` (written by §9).

| Check | Command | Pass when |
|---|---|---|
| I0 active, right user/judge model | `grep -E 'OVERRIDE' $FDHOS_CONSOLES/$RUN.log`; the `==` header line | `USER TTS OVERRIDE` (gpt-4o-mini-tts), `USER DECISION LLM OVERRIDE` (gpt-4.1), `REVIEW LLM OVERRIDE` = the chosen model |
| Judge really used the chosen model | `cat $R/artifacts/*/sim_*/llm_debug/*nl_assertions_eval* 2>/dev/null \| grep -o '"model": "[^"]*"' \| sort \| uniq -c` (and `*user_streaming_response*`) | only the chosen model |
| No failed tau2-side LLM calls | `grep -cE "AuthenticationError\|Error in (backchannel\|interruption) decision\|ELEVENLABS_API_KEY not found\|Missing Anthropic" $FDHOS_CONSOLES/$RUN.log` | `0` |
| Connected to the right model | `fdhos_sessions $RUN \| wc -l`; `grep -cE 'model_not_available\|Session configuration failed\|server_busy' $FDHOS_CONSOLES/$RUN.log` | = tasks run (+ retries); `0` |
| No infra errors, reward | metrics box at the end of the console log | `Infra Errors` 0; `user_stop`/`agent_stop`; a reward per task |
| Default turn taking, `regular` | header line; `grep -E 'wait.to.respond\|silence.annotation' $FDHOS_CONSOLES/$RUN.log` | `turn-taking=tau2 defaults`, `speech_complexity=regular`, no non-default flag |
| Agent saw the run | `for s in $(fdhos_sessions $RUN); do grep -c "\"session_id\": \"$s\"" $FDHOS_EV; done` | every session has records; `session_end` for each |
| Profile / VAD | `grep '"session_start"' $FDHOS_EV \| tail -1`; `grep '"fdh_session_start"' $FDHOS_EV \| tail -1 \| grep -o '"config_hash": "[^"]*"'` | `adapter pipecat`, `stop_secs 0.8`, `audio_idle_timeout 0.0`, `config_hash 817a51963745` |
| Deployed fingerprint | in [tools]: `PYTHONPATH=src uv run python -m examples.frontend_delegation_hermes.cli.fingerprint_check $D/agent/events.jsonl --profile $AGENT/src/examples/frontend_delegation_hermes/config/profiles/pipecat_realtime_eval.yaml --gateway-config $AGENT/src/examples/frontend_delegation_hermes/config/gateway.eval.yaml` (`D` = the run's dump folder, §9) | `fingerprint: OK`, one `session_tools_sha256` per domain |
| Hermes backend, no refusals | `fdhos_gw_health` | `agent_kind hermes`, `refused` 0, `sessions` 0 after the run |
| Concurrent workers (airline) | `grep -E '"event": "(session_open\|capacity_refused)"' $SNAP/logs/fdh_gateway_events.pipecat.jsonl \| tail` | 4 `session_open` close in time, no `capacity_refused` |
| No 429s (P0.1) | §7 `fdh_logs ratelimit` | every line OK (a throttled session makes the run INVALID) |
| No agent failures | `docker logs --since "$SINCE" $FDHOS_V 2>&1 \| grep -Ei " failed\|error\|traceback\|\b401\b\|\b429\b\|server_busy\|1011\|1013" \| head`; `grep -Ei 'traceback\|error' $SNAP/../sandbox_workload_logs.txt \| head` | nothing relevant |
| Worker health | `grep -il traceback $SNAP/logs/fdh_workers_pipecat/sess_*.log` (this run's sessions) | no tracebacks |
| Sandbox egress | `grep -i deny $DUMP/tau-3-voice/$CAMPAIGN/$RUN/agent/openshell_policy_logs.txt \| head` | no denied call the agent needed |
| Nothing restarted | `docker inspect $FDHOS_V $FDHOS_H --format '{{.Name}} {{.RestartCount}} {{.State.StartedAt}}'`; `fdhos_workload` unchanged | `0`, started before `$SINCE`; same workload name as at the start |
| Listen | `$R/artifacts/task_*/sim_*/audio/both.wav` (if present) or the agent turns in `task.log` | agent speaks, filler audible, no long dead air, no user cut off mid-ID |
| Report | §7 | adapter exit 0; C1–C5, C7, C8 PASS (C4/C5/C8 WARN: read them), C6 FAIL by design; every `P0.1` line OK; exact-join `0 mismatch` |

**First smoke on this stack** (2026-10-16, campaign `2026-10-16_13-58-25Z_fdh-openshell-retail4-smoke-gpt55-c2`,
retail tasks 2/43/55/102, concurrency 2, gpt-5.5): Pass^1 1.000 (4/4), C1–C3/C5/C7/C8 PASS, C4 WARN, C6 FAIL by
design, P0.1 OK (backend run p90 11.2 s), exact join 4/4, fingerprint OK; 7–18 min per task (task 55 was rerun once
by tau2's hallucination check). For each new campaign, still read the smoke report's join, C-checks and the per-turn CSV before the full runs;
stop and tell the user about anything odd. Use the airline smoke's mean simulation `duration` to estimate §6's wall
times.

---

## 6. Reportable runs [tau2]

Full `base` split, 1 trial, `regular`, concurrency 4, in tmux (the shell needs the Shell setup and `fdhos_run`):

```bash
tmux new -s fdhos -e OPENAI_API_KEY="$OPENAI_API_KEY"
# inside tmux: Shell setup + fdhos_run definition, then
export FDHOS_USER_JUDGE=<gpt52|gpt55>   # the user's answer
export TAU3_TAG=                        # empty for the reportable runs (gpt55 is appended automatically)
out=$FDHOS_CONSOLES/fdhos_campaign.out
for d in airline retail telecom banking_knowledge; do
  echo "== $(date -Is) start $d" | tee -a $out; fdhos_run $d; echo "== $(date -Is) end $d exit=$?" | tee -a $out
done
```

| Domain | Tasks (`base`) | Wall time at concurrency 4 (FDH estimate; re-estimate from the smoke) |
|---|---|---|
| airline | 50 | ~2.5 h |
| retail | 114 | ~5.5 h |
| telecom | 114 | ~5.5 h |
| banking_knowledge | 97 | ~4.5 h |

Status while running:

```bash
RUN=$(ls -t $FDHOS_CONSOLES/fdhos_*.start | head -1 | xargs basename | sed 's/\.start$//'); echo $RUN
tail -c 3000 $FDHOS_CONSOLES/$RUN.log | grep -a 'Status:' | tail -1
grep -rah 'FINISHED SIMULATION' --include=task.log $TAU2/data/simulations/$RUN/artifacts | wc -l   # if Status: freezes
fdhos_sessions $RUN | wc -l
fdhos_gw_health | python3 -c 'import json,sys; h=json.load(sys.stdin); print("sessions", h["sessions"], "workers", h["workers"], "refused", h["refused"])'
docker inspect -f '{{.Name}} {{.State.Health.Status}} {{.RestartCount}}' $FDHOS_V $FDHOS_H
```

- **Freeze the stack for the whole campaign:** no `up`, restart, rebuild or agent-code edit. `[agent]/src` is mounted
  live into the voice side (an edit changes the running code at the next process start), while the sandbox runs the
  code built into `nemotron-voice-agent-fdh:local`: an edit makes the two sides differ. If something must change:
  stop, archive (§9), new campaign.
- **Nothing else on port 7860** during a run (no browser, no `realtime_test_client`, no probe): its sessions would land
  in the run's time span. The `eval` profile already refuses browser UI sessions.
- **Archive each domain right after it finishes** (§9): the voice-side `docker logs` rotate, and the sandbox logs are
  lost if anything stops `fdh-hermes`.
- Resume: rerun the same `fdhos_run <domain>` with the same `FDHOS_USER_JUDGE`/`TAU3_TAG`, plus `--auto-resume` to
  rerun infra errors.

---

## 7. Compute the metrics [tau2]

Run after every run of the report is archived (§9 writes the sandbox snapshot the 429 gate reads).

```bash
cd $TAU2
tag=smoke; doms=(mock airline retail telecom banking_knowledge)    # reportable: tag=""; doms=(airline retail telecom banking_knowledge)
[ "$FDHOS_USER_JUDGE" = gpt55 ] && tag=${tag:+$tag-}gpt55
A=fdhos; out=$FDHOS_METRICS/fdhos_regular${tag:+_$tag}; mkdir -p $out $FDHOS_METRICS/_setup $FDHOS_METRICS/_legacy
runs=() mods=() dirs=() names=()
for d in "${doms[@]}"; do n=fdhos_${d}_regular${tag:+_$tag}; r=$TAU2/data/simulations/$n
  [ -f $r/results.json ] || { echo "skip $r"; continue; }
  runs+=(--run "$A=$r"); mods+=(--model "$n=$FDHOS_MODEL"); dirs+=($r); names+=($n); done

# 1. this report's sessions: the last session of each *scored* simulation (results.json -> its task.log). Discarded
#    attempts (hallucination-check reruns, infra retries) overlap other tasks in time and would confuse the join
#    (2026-10-16 full retail: task 57 joined a discarded task-59 attempt until this filter was added).
for d in "${dirs[@]}"; do uv run python - "$d" <<'PY2' 2>/dev/null; done | sort -u > $out/sessions_fdh.txt
import glob, re, sys
from tau2.data_model.simulation import Results
R = sys.argv[1]
for s in Results.load(f"{R}/results.json").simulations:
    f = glob.glob(glob.escape(f"{R}/artifacts/task_{s.task_id}/sim_{s.id}/task.log"))   # telecom ids hold [..]
    ids = re.findall(r"session created \(session_id=(sess_[0-9a-f]+)\)", open(f[0]).read()) if f else []
    print(ids[-1] if ids else f"MISSING task {s.task_id}", flush=True)
PY2
wc -l < $out/sessions_fdh.txt; grep -c MISSING $out/sessions_fdh.txt     # = tasks of the report; 0
python3 - $FDHOS_EV $out/sessions_fdh.txt $out/events.jsonl <<'PY'
import json, sys
src, ids, dst = sys.argv[1:]; sids = {l.strip() for l in open(ids) if l.strip()}; n = 0
with open(src) as f, open(dst, "w") as o:
    for line in f:
        try:
            if json.loads(line).get("session_id") in sids: o.write(line); n += 1
        except json.JSONDecodeError: pass
print(f"events.jsonl: {n} records for {len(sids)} sessions")
PY
legacy=$FDHOS_METRICS/_legacy/fdhos${tag:+_$tag}.legacy.jsonl
(cd $TOOLS && PYTHONPATH=src uv run python -m examples.frontend_delegation_hermes.cli.report_adapter $out/events.jsonl --out $legacy); echo "adapter exit=$? (0 = no orphaned tool call)"

# 2. setup record (model columns of the results table), from the running stack
H=$(fdhos_gw_health)
fe=$(fdhos_env | sed -n 's/^FRONTEND_LLM_MODEL=//p')
be=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hermes"]["model"])' <<<"$H")
L=$(docker logs $FDHOS_V 2>&1)
asr=$(sed -n 's/.*Initialized NvidiaSTTService with model: \([^ ]*\).*/\1/p' <<<"$L" | tail -1)
tts=$(sed -n 's/.*Initialized NvidiaTTSService with model: \([^ ]*\).*/\1/p' <<<"$L" | tail -1)
cat > $FDHOS_METRICS/_setup/$A.json <<JSON
{"backend_llm": "$be (Hermes AIAgent, one worker per session, OpenShell sandbox)", "backend_reasoning": "on (enable_thinking, reasoning_budget 1024)",
 "frontend_llm": "$fe (FDH shared stack, Pipecat 0.8 s audio-time VAD)", "frontend_reasoning": "off (enable_thinking false)",
 "asr": "$asr (NeMo Speech, streaming, Pipecat NvidiaForceEouSTTService)", "tts": "$tts (voice John, NeMo Speech, Pipecat NvidiaTTSService per_sentence)"}
JSON
cat $FDHOS_METRICS/_setup/$A.json
# 2026-10-16: nemotron-3.5-lightning | nemotron-3-ultra | nemotron-speech-streaming-en-0.6b | magpie-tts-multilingual (voice John)
# If the app log rotated and asr/tts are empty, fill them by hand from §0.

# 3. metrics and checks
TAU2_JUDGE_MODEL=$( [ "$FDHOS_USER_JUDGE" = gpt55 ] && echo openai/openai/openai/gpt-5.5 || echo openai/azure/openai/gpt-5.2 ) \
uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py "${runs[@]}" "${mods[@]}" \
  --event-log "$A=$legacy" --setup "$A=$FDHOS_METRICS/_setup/$A.json" --out $out > $out/console.txt 2>&1
echo "metrics exit=$? (1 expected when only C6 fails)"
fdh_logs checks $out/fba_voice_metrics.json --allow-fail C6 --join $out/join.csv --event-logs $out/events.jsonl | tee $out/checks.txt

# 4. 429 gate (P0.1) on the archived sandbox snapshots (worker logs + gateway events of every run)
wl=$out/_workers; rm -rf $wl; mkdir -p $wl; gl=()
for n in "${names[@]}"; do s=$DUMP/tau-3-voice/$CAMPAIGN/$n/agent/sandbox/logs
  cp $s/fdh_workers_pipecat/*.log $wl/ 2>/dev/null; gl+=($s/fdh_gateway_events.pipecat.jsonl); done
fdh_logs ratelimit --join $out/join.csv --worker-logs $wl --gateway-logs "${gl[@]}" | tee -a $out/checks.txt

# 5. exact join against the session id each simulation's task.log records
fdh_logs exact-join "${dirs[@]}" --join $out/join.csv | tee $out/exact_join.txt

# 6. measured filler / answer first audio
fdh_logs filler $out/events.jsonl --join $out/join.csv --json $out/filler_measured.json | tee $out/filler_measured.txt
cat $out/fba_voice_results_table.md
```

Expected: C1 PASS (1:1 join), C2 PASS (per-role usage), C3 PASS, C4 PASS or WARN (barge-in undercount in tau2; it stays FAIL
when a single session has no barge-in — read the gaps: on 2026-10-16 only retail task 61, 3.5%; 2026-10-18 airline task
40, 10%), C1 may list a few "unmatched sessions" when runs back to back overlap in time (2026-10-18: the last telecom
task and the first airline tasks): each is a `primary` row of its own run in `join.csv`, and exact-join is 0 mismatch, C5
PASS/WARN (read the failures), **C6 FAIL by design** (the filler is spoken), C7 PASS, C8 PASS (WARN lists
agent-caused endings); every `P0.1` line OK; exact-join `0 mismatch`. The adapter exits 1 ("orphaned tool call") when a session ended while a
backend run was in flight (user stop, `too_many_errors`): name those tasks in the run card; it is not data loss. The judge env var
only labels the results table; the judging happened during the run. A tau2-only view, if wanted: parent runbook §7.1.

---

## 8. How to read the numbers

The parent runbook §8 applies (tau2's clock runs at ~half real time, so use *mean realtime response latency* for the
real answer delay; tokens per simulation; non-official user voices; S_VT approximate), plus:

- **Turn taking is Pipecat's, in input audio:** Silero 800 ms stop, no wall-clock timers (`audio_idle_timeout 0`,
  `user_speech_timeout 0`). That is the τ³ adapter's 800 ms (`fdh-tau`), not the old Pipecat container's 3 s of wall
  time. Spelled IDs across pauses > 800 ms still split (no spelling hold, `spelling_hold: false`).
- **Backend in a sandbox:** the Hermes workers' calls go through the OpenShell proxy (the real key is injected only
  for `inference-api.nvidia.com`). Backend latency includes that hop and up to 4 workers sharing the sandbox's CPU.
- **Telecom note:** `gateway.eval.yaml` runs the disclosed telecom domain note (`domain_notes` true, `domains:
  ["telecom"]`); name it next to telecom numbers.
- **M1 proactive status** after `FDH_PROACTIVE_AFTER_S=15` s of silence while the backend works; **filler is spoken**
  (C6 FAIL by design).
- **Concurrency 4:** latencies include contention on `nemo-speech`, the sandbox and the Hub; don't compare with
  concurrency-1 numbers (`fbv-v2`).
- **User/judge model:** name it next to every number; gpt-5.2 and gpt-5.5 results are separate series.

---

## 9. Archive everything [tau2 → dump]

Mandatory after each run (smoke, stopped and invalid ones too), right after each domain, and before any recreate or
stop. It copies, never deletes, the shared source logs.

```bash
RUN=fdhos_airline_regular_gpt55                      # repeat per run
R=$TAU2/data/simulations/$RUN; D=$DUMP/tau-3-voice/$CAMPAIGN/$RUN
SINCE=$(head -1 $FDHOS_CONSOLES/$RUN.start); UNTIL=$(cat $FDHOS_CONSOLES/$RUN.end 2>/dev/null || date -Is)
mkdir -p $D/{tau2,agent/raw,agent/config,agent/sandbox,provenance}

# 1. tau2: results, trajectories, audio, task logs, llm_debug; console log and run window
rsync -a $R/ $D/tau2/$RUN/
cp $FDHOS_CONSOLES/$RUN.{log,start,end} $D/tau2/ 2>/dev/null

# 2. session ids (task.log = FDH ids), plus the gateway stream_id of each for docker-log lookups
fdhos_sessions $RUN > $D/agent/sessions.txt
fdhos_map_update; awk -F'\t' 'NR==FNR{g[$1];next} ($2 in g)' $D/agent/sessions.txt $FDHOS_MAP > $D/agent/session_map.tsv
wc -l $D/agent/sessions.txt $D/agent/session_map.tsv                # equal; one per simulation (+ discarded/retried attempts)

# 3. the sandbox's /app/logs (gateway events, worker logs, decision traces): full snapshot + this run's files
fdhos_sandbox_logs $D/agent/sandbox_full && du -sh $D/agent/sandbox_full
mkdir -p $D/agent/sandbox/logs/fdh_workers_pipecat
while read -r s; do cp $D/agent/sandbox_full/logs/fdh_workers_pipecat/$s[-.]* $D/agent/sandbox/logs/fdh_workers_pipecat/ 2>/dev/null; done < $D/agent/sessions.txt
ls $D/agent/sandbox/logs/fdh_workers_pipecat | wc -l               # ~2 files per session (.log + .trace.jsonl)

# 4. this run's records from the voice and gateway event logs, plus the full raw voice log
python3 - $D/agent/sessions.txt $D/agent \
  $FDHOS_EV:events.jsonl $D/agent/sandbox_full/logs/fdh_gateway_events.pipecat.jsonl:sandbox/logs/fdh_gateway_events.pipecat.jsonl <<'PY'
import json, sys
ids, out, *pairs = sys.argv[1:]; sids = {l.strip() for l in open(ids) if l.strip()}
for p in pairs:
    src, dst = p.rsplit(":", 1); n = 0
    try:
        with open(src) as f, open(f"{out}/{dst}", "w") as o:
            for line in f:
                try:
                    if json.loads(line).get("session_id") in sids: o.write(line); n += 1
                except json.JSONDecodeError: pass
    except FileNotFoundError: pass
    print(f"{dst}: {n} records for {len(sids)} sessions")
PY
cp $FDHOS_EV $D/agent/raw/

# 5. container logs for the run window, health, redacted container definitions
docker logs --since "$SINCE" --until "$UNTIL" $FDHOS_V > $D/agent/docker_logs.txt 2>&1
docker logs $FDHOS_H > $D/agent/fdh_hermes_controller_logs.txt 2>&1
docker logs $(fdhos_workload) > $D/agent/sandbox_workload_logs.txt 2>&1            # FDH gateway + workers stdout/stderr
docker exec $FDHOS_H openshell logs fdh-hermes > $D/agent/openshell_policy_logs.txt 2>&1
docker logs --since "$SINCE" --until "$UNTIL" $FDHOS_OSGW > $D/agent/openshell_gateway_logs.txt 2>&1
docker logs --since "$SINCE" --until "$UNTIL" $FDHOS_SPEECH > $D/agent/nemo_speech_logs.txt 2>&1
curl -s localhost:7860/health > $D/agent/health.json; fdhos_gw_health > $D/agent/gateway_health.json
for c in $FDHOS_V $FDHOS_H $(fdhos_workload); do
  docker inspect $c | python3 -c 'import json,re,sys; d=json.load(sys.stdin)
for c in d: c["Config"]["Env"]=[re.sub(r"^([^=]*(KEY|TOKEN|SECRET|PASS)[^=]*)=.*", r"\1=<redacted>", e) for e in c["Config"]["Env"]]
print(json.dumps(d, indent=1))' > $D/agent/inspect_$c.json; done
{ fdhos_env; echo "--- fdh-hermes"; fdhos_env $FDHOS_H; } | grep -E '^(---|FDH_|FRONTEND_LLM_|BACKEND_LLM_|PIPELINE_|EXAMPLE_SELECTION|REALTIME_SERVICE_PLATFORM|UVICORN_|OPENSHELL_)' > $D/provenance/agent_env.txt
grep -E 'frontend-delegation-hermes-agent ready|Pipecat adapter settings|Setting VAD params|Initialized Nvidia(STT|TTS)Service' $D/agent/docker_logs.txt | head -5 > $D/provenance/agent_ready_lines.txt
grep -E 'gateway config|OpenShell CLI' $D/agent/fdh_hermes_controller_logs.txt > $D/provenance/hermes_runtime.txt
grep 'FDH gateway' $D/agent/sandbox_workload_logs.txt >> $D/provenance/hermes_runtime.txt
(cd $AGENT && find src -type f ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1) > $D/provenance/host_src_sha256.txt

# 6. the agent config and code actually served, Compose files (the key overlay only as key names)
cp -r $AGENT/src/examples/frontend_delegation_hermes/config $D/agent/config/fdh_config
cp $AGENT/src/examples/frontend_delegation_hermes/{services.local.yaml,prompts.yaml,tools.yaml} $AGENT/examples_registry.yaml \
   $AGENT/docker-compose.yml $AGENT/docker/docker-compose.fdh-eval.yaml $AGENT/docker/fdh/gateway.env $D/agent/config/
cp -r $AGENT/docker/fdh/openshell $D/agent/config/openshell
docker inspect $FDHOS_V --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' | tr , '\n' > $D/provenance/compose_files.txt
tar -czf $D/provenance/agent_fdh_src.tgz -C $AGENT --exclude __pycache__ \
  src/examples/frontend_delegation_hermes src/examples/shared src/realtime src/server.py docker/Dockerfile.fdh docker/fdh \
  docs/how-to/deploy-frontend-delegation-hermes-agent.md
docker image inspect nemotron-voice-agent:latest nemotron-voice-agent-fdh:local nemotron-voice-agent-fdh-openshell-cli:local \
  --format '{{.RepoTags}} {{.Id}} {{.Created}}' > $D/provenance/images.txt
grep -m1 'Hermes commit' $AGENT/docker/fdh/requirements.txt > $D/provenance/hermes_commit.txt

# 7. provenance of both repos, tau2 eval helpers, key names only
for X in AGENT TAU2 TOOLS; do d=${!X}; git -C $d rev-parse HEAD > $D/provenance/${X,,}_commit.txt
  git -C $d branch --show-current >> $D/provenance/${X,,}_commit.txt
  git -C $d status --short > $D/provenance/${X,,}_status.txt; git -C $d diff > $D/provenance/${X,,}_uncommitted.diff; done
cp $TOOLS/src/examples/frontend_delegation_hermes/cli/{report_adapter.py,fingerprint_check.py} $D/provenance/
cp $TAU2/misc/prototypes/fba_voice_eval/{tau2_ihub_overrides.py,tau2_ihub.py,fba_voice_metrics.py} \
   $TAU2/misc/prototypes/fdh_voice_eval/fdh_logs.py $TAU2/misc/examples/fdh-openshell-runbook.md $D/provenance/
grep -E '^(TAU2_JUDGE_(MODEL|BASE_URL|JSON_MODE)|TAU2_USER_TTS_(MODEL|BASE_URL)|TAU2_USER_DECISION_MODEL)=' $TAU2/.env > $D/provenance/tau2_endpoints.txt
echo "user/judge/review model used: $FDHOS_USER_JUDGE (see the console log header; tau2_endpoints.txt is the .env default)" \
  > $D/provenance/user_judge_model.txt
sed -n 's/^\([A-Z0-9_]*\)=.*/\1/p' $TAU2/.env > $D/provenance/tau2_env_keys.txt
du -sh $D
```

- `sessions.txt` has one session per simulation attempt: tau2's hallucination-check reruns and infra retries add
  more (the 2026-10-16 smoke: 5 sessions for 4 tasks). A shorter `session_map.tsv` only means the app log rotated.
- `sandbox_full/` holds every session the sandbox has seen (other runs too); `sandbox/` holds this run's. Keep both
  for the first campaign; later, drop `sandbox_full/` from the dump if size matters (it is needed by nothing after §7).
- Then run the fingerprint check of §5.1 on `$D/agent/events.jsonl` and save its output as `$D/provenance/fingerprint.txt`.

Once per campaign, after the reports (§7):

```bash
rsync -a $FDHOS_METRICS/fdhos_regular* $FDHOS_METRICS/_setup/fdhos.json $FDHOS_METRICS/_legacy/fdhos* $DUMP/tau-3-voice/$CAMPAIGN/_reports/
cp $FDHOS_MAP $DUMP/tau-3-voice/$CAMPAIGN/_reports/
ls $DUMP/tau-3-voice/$CAMPAIGN/_reports/          # only this campaign's fdhos_* reports
```

Write the run card `$DUMP/tau-3-voice/$CAMPAIGN/README.md`: date; the deployment (recipe
`frontend-delegation-hermes-agent/single-gpu`, Compose files incl. the eval overlay, **OpenShell runtime** and its
version, the three image ids, `FDH_PROFILE=eval`, `FDH_GATEWAY_CONFIG=gateway.eval.yaml`, `config hash`, the `FDH
gateway … source` hash = host `src` hash); route `ws://localhost:7860/v1/realtime`, model
`nvidia/nemotron-realtime-frontend-delegation-hermes`; Pipecat `pipecat.eval.yaml` (0.8 s audio-time VAD);
frontend/backend/ASR/TTS models and reasoning; **user simulator / judge / review model (the user's choice)**; user
TTS and decision models (I0); turn-taking thresholds (tau2 defaults 5.0 / 1.0 / 4.0 s); speech complexity
`regular`; concurrency (1 mock, 4 others); `bm25` for banking_knowledge; domains, split `base`, trials; agent commit +
branch + dirty state; tau2 commit; [tools] commit (report adapter, fingerprint); any other container that was
running; the results table; the failed checks (C6 by design); how the sandbox logs were
copied; anything unusual.

**Before committing in [dump]:**

```bash
cd $DUMP
grep -rIlP '(?<![A-Za-z0-9_-])(sk-|nvapi-)[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{16,}' tau-3-voice/$CAMPAIGN | head   # must print nothing
# (the lookbehind matters: gpt-5.5's reasoning `encrypted_content` blobs contain "…sk-…" mid-token; 2026-10-18 telecom)
grep -rIl -- "$FDHOS_KEY" tau-3-voice/$CAMPAIGN | head                                                             # must print nothing
du -sh tau-3-voice/$CAMPAIGN; git lfs track | grep -q wav || { git lfs install && git lfs track "tau-3-voice/**/*.wav" && git add .gitattributes; }
git add tau-3-voice/$CAMPAIGN && git commit -m "tau-3-voice: $CAMPAIGN fdh openshell (<domains>, regular, <gpt52|gpt55>)"
# git push only when the user asks
```

Never copy any `.env` or the key overlay file. The event logs and traces hold transcripts and tool payloads: keep
them in the dump repo only. Files the voice container wrote under `$AGENT/logs` are owned by root: `sudo chown -R
"$USER" $AGENT/logs` before moving or deleting them (copying works without it).

**Layout:**

```
voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/
├── README.md                          run card
├── _reports/                          fdhos_regular[_tag]/ (report, CSVs, checks, join, exact join,
│                                      filler), fdhos.json (setup), legacy event log, fdhos_session_map.tsv
└── fdhos_<domain>_regular[_tag]/
    ├── tau2/<run>/                    results.json, simulations/, artifacts/ (audio, task.log, llm_debug)
    ├── tau2/<run>.log|.start|.end     console output and run window
    ├── agent/sessions.txt, session_map.tsv
    ├── agent/events.jsonl             this run's voice event records;  agent/raw/ the full voice event log
    ├── agent/sandbox/logs/            this run's gateway events, worker logs, decision traces
    ├── agent/sandbox_full/logs/       the sandbox's whole /app/logs at archive time
    ├── agent/docker_logs.txt, fdh_hermes_controller_logs.txt, sandbox_workload_logs.txt, openshell_policy_logs.txt,
    │   openshell_gateway_logs.txt, nemo_speech_logs.txt, health.json, gateway_health.json, inspect_*.json (redacted)
    ├── agent/config/                  FDH config dir, services/prompts/tools, registry, Compose files, gateway.env, openshell/
    └── provenance/                    commits/status/diffs (agent, tau2, tools), agent_fdh_src.tgz, images, Hermes commit,
                                       hashes, env (no secrets), ready lines, fingerprint, tau2 helpers, this runbook
```

---

## 10. Stop / recreate [agent] — ask first, archive first

**Compose and the key.** `NVIDIA_API_KEY` is empty in `[agent]/.env`; the running stack got it from a session-only
overlay (§1, voice service) and from the shell (`fdh-hermes`). Any Compose command that recreates services needs the
key again: ask the user to export it in the shell (`export NVIDIA_API_KEY=…`, never in a file in the repo or the
dump), or to point you at their overlay. Without it `fdh-hermes` refuses to start and the voice side's LLM calls
fail. Read-only checks in this runbook use plain `docker`, never Compose, for that reason.

Recreate (after an agent change; then a **new campaign**):

```bash
cd $AGENT && $DC up -d --build                       # deploy guide, eval mode; keep --build after any src change
docker exec $FDHOS_V .venv/bin/python -c "import nltk; print(nltk.download('punkt_tab', quiet=True))"
```

then §1 again (new `FDH gateway … source` hash = host `src` hash, new workload name, new image ids).

Stop (only if the user wants it):

```bash
cd $AGENT && $DC down          # fdh-hermes deletes the sandbox (up to 60 s); its save to logs/openshell/<time>/ may fail (§2.3)
```

`fdh-hermes` has `restart: "no"`, the voice side `unless-stopped`. Leave `nemotron-voice-agent-nemo-speech-1` and
`/var/lib/openshell` alone. To go back to the browser demo: `up -d --build` without the eval overlay (deploy guide).

---

## 11. Troubleshooting

The parent runbook §11 covers the tau2/I0 side (pyaudio, `ELEVENLABS_API_KEY not found`, Azure prefix, TTS 404, slow
runs, C4 WARN); the deploy guide's *Troubleshooting* covers the recipe. Specific to this runbook:

| Symptom | Check |
|---|---|
| `model_not_available` / `Model 'pine-…' is not available` | `PINE_REALTIME_BASE_URL` lacks `?model=nvidia/nemotron-realtime-frontend-delegation-hermes&x_tau_run=…` (run through `fdhos_run`) |
| Handshake closed with 1008 `realtime authentication failed` | `FDHOS_KEY` (= `REALTIME_API_KEY` in `[agent]/.env`) wrong or empty |
| `CERTIFICATE_VERIFY_FAILED` / SSL errors in `task.log` | the stack runs without the eval overlay (`PIPELINE_TLS=true`): §10 recreate with the overlay |
| `server_busy`, close 1013 | > 4 Realtime sessions (concurrency override? a stray client on 7860?), or the warm-up failed (`startup failed` in the app log) |
| `session.update` fails with `worker_start_timeout` / `backend_unavailable` | a Hermes worker didn't start: `sandbox_workload_logs`, `fdhos_sandbox_logs` → `fdh_workers_pipecat/`; `fdhos_gw_health` |
| App log `backend gateway not reachable` | `fdh-hermes` or the sandbox died (`restart: "no"`): `docker logs $FDHOS_H`, `fdhos_workload` empty? Archive, then §10 |
| Worker log: call to a host other than `inference-api.nvidia.com` fails | sandbox policy: `openshell_policy_logs.txt`; don't change `policy.yaml` mid-campaign |
| `server_error`, close 1011, asks for `UVICORN_WS_PING_INTERVAL=0` | someone set it in `.env`/shell; recreate without it |
| First session after a recreate hangs or fails | NLTK `punkt_tab` (§1) |
| Every turn `agent_error` | `NVIDIA_API_KEY` missing in the voice container (§10) or no Hub access |
| Exact-join mismatches | another client used 7860 during the run, or a retried attempt; compare `sessions.txt` with `session_start` times |
| `openshell sandbox download` fails | known on this build (§2.3); use `fdhos_sandbox_logs` |
| `fdh_logs sessions/filter/status` report 0 sessions | expected: they select by `pine-` model tag (§2.4); use `fdhos_sessions` |
| `fdhos_run`: `FDHOS_USER_JUDGE is not set` | intended: ask the user gpt-5.2 or gpt-5.5 |
| Old run resumed instead of a new one | same run name in `data/simulations/`; use a `TAU3_TAG` |
| `Permission denied` under `$AGENT/logs` | root-owned: `sudo chown -R "$USER" $AGENT/logs` |
| Long dead air, then the task ends | backend latency against tau2's fixed 40 s inactivity limit; an agent result. Don't change tau2 |

---

## 12. What to record when you report

The parent runbook §12 items, with: recipe `frontend-delegation-hermes-agent/single-gpu` in **eval mode** with the
**OpenShell** Hermes runtime (`v0.1.5-pre.4`), container `…-frontend-delegation-hermes-agent-single-gpu-1`, the three
image ids, Compose files (`docker-compose.yml` + `docker/docker-compose.fdh-eval.yaml` + the key overlay),
`FDH_PROFILE=eval`, `FDH_GATEWAY_CONFIG=gateway.eval.yaml`, `config hash 817a51963745` (or the new one), the `FDH
gateway … source` hash, route `ws://localhost:7860/v1/realtime` and model
`nvidia/nemotron-realtime-frontend-delegation-hermes`, Pipecat `pipecat.eval.yaml` (Silero 0.8 s of input audio,
no wall-clock timers), frontend `nemotron-3.5-lightning` / backend `nemotron-3-ultra` / ASR
`nemotron-speech-streaming-en-0.6b` / TTS `magpie-tts-multilingual` voice John, **the user simulator / judge /
review model the user chose (gpt-5.2 or gpt-5.5)**, user TTS `gpt-4o-mini-tts` and decision LLM `gpt-4.1`, tau2's
default turn-taking thresholds (unchanged), `regular`, concurrency 1 mock / 4 others, `bm25` for banking_knowledge,
the telecom note on, agent commit + branch + dirty state, tau2 and [tools] commits, other agents running on the
shared `nemo-speech`, the filler being audible (C6 FAIL by design), that the numbers are a new series, and the dump
path `voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/`.
