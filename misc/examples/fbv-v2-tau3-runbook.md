# Runbook: τ³ voice evaluation of the `fbv-v2` container (Frontend/Backend Verdict Agent, Pipecat, repo server)

**Date:** 2026-10-15 · **Status:** `fbv-v2` is up and healthy (image `nemotron-voice-agent:fbv-v2`
`2b0ee970e0d7`, host port **7860**, profile `tau3_eval_frontend_verdict_speak_history`, Pipecat settings `eval.yaml`),
but **tau2 cannot evaluate it as it runs now** (§2): it serves TLS with a self-signed certificate, rejects tau2's
`pine-…` model name, and writes no event log. §2.1 recreates it in **eval mode** (plain `ws://`, event and filler log on
the host); the tau2 side then needs only the `fbv_run` helper of this runbook. `src/tau2/` is not modified ·
**Agent code:** `nemotron-voice-agent/src/examples/frontend_backend_verdict/` (`README.md`) ·
**Agent deploy guide:** `nemotron-voice-agent/docs/how-to/deploy-frontend-backend-verdict-agent.md` ·
**Parent runbook (I0, `.env`, metric definitions, the metrics script):**
[`../prototypes/voice-frontend-backend-agent-tau3-runbook.md`](../prototypes/voice-frontend-backend-agent-tau3-runbook.md)
(called "the FBA runbook" below) · **gpt-5.5 rule and numbers:**
[`../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md`](../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md) §2.2.1 ·
**Sister runbooks (same layout):** [`fdh-tau-tau3-runbook.md`](fdh-tau-tau3-runbook.md), [`fdh-pipecat-tau3-runbook.md`](fdh-pipecat-tau3-runbook.md)

This runbook takes `fbv-v2` to a τ³ voice report for **airline, retail, telecom and banking_knowledge** (mock only
for the smoke run), with every log and artifact archived in the dump repo. The agent under test is the
Frontend/Backend **Verdict** agent: a fast frontend LLM that answers or delegates, a reasoning backend LLM with the
conversation history, a frontend barge-in verdict, identifier normalization and tool-continuation resume, served by
the repository's OpenAI Realtime gateway on the shared Pipecat speech stack (README, *Architecture*).

## Rules (read before anything else)

> **ALWAYS ASK the user which Inference Hub model to use for the user simulator and the judge before any run —
> smoke included, every campaign, every time: `azure/openai/gpt-5.2` or `openai/openai/gpt-5.5`.** Never pick one
> yourself and never carry a choice over from an earlier campaign or another runbook. The same model is used for the
> user simulator LLM, the NL-assertion judge (`TAU2_JUDGE_MODEL`) and the hallucination check / review model
> (`TAU2_REVIEW_MODEL`); the user TTS (`gpt-4o-mini-tts`) and the user decision LLM (`gpt-4.1`) stay unchanged. The
> `fbv_run` helper (§3.3) **refuses to start** until `FBV_USER_JUDGE` is set to `gpt52` or `gpt55`: set it only from
> the user's answer. gpt-5.2 and gpt-5.5 numbers are **not comparable** (on the 2026-10-08 FDH campaign telecom went
> 0.439 → 0.725, retail 0.658 → 0.623); keep one model for every domain of a campaign and name it in the run card.

> **Do not start any run (smoke included) until the user says go.**

> **ASK the user before recreating, restarting or stopping `fbv-v2`, and before stopping any other container**
> (§2.1, §4). Archive first (§9).

> **ASK the user which `tau3_eval*` profile to evaluate** if they have not said so in the current request
> (agent deploy guide, *Instructions for AI Agents*). `fbv-v2` runs `tau3_eval_frontend_verdict_speak_history` now;
> confirm it, don't assume it. Never evaluate the `default` profile (generic prompt catalog).

> **Speech complexity `regular` for every domain, mock smoke included.** Never `control`. `fbv_run` hardcodes it.

> **Do not change τ²'s user-simulator turn-taking / patience parameters.** Run with tau2's defaults
> (`src/tau2/config.py`, in seconds of audio time): `--wait-to-respond-self` **5.0**, `--wait-to-respond-other`
> **1.0**, `--silence-annotation-threshold` **4.0**, and the fixed `DEFAULT_AUDIO_NATIVE_MAX_INACTIVE_SECONDS` **40**.
> `fbv_run` passes none of these flags; never add them. If anyone wants them changed, **ask the user first, every
> time**, and treat such a run as a labelled diagnostic (tag it, e.g. `TAU3_TAG=tt10-2-8`), never next to
> default-threshold results.

> **Concurrency 1 for every domain** (`--max-concurrency 1`). The agent's `tau3_eval.yaml` asks for 1 "until
> `max_sessions` is measured", and the deploy guide says the same. Changing it (`TAU3_CONCURRENCY`) needs the user's
> OK, and makes latencies incomparable with concurrency-1 runs.

> **MANDATORY: archive every run (smoke, stopped, invalid) in
> `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/` (§9) before you
> recreate, restart or reconfigure `fbv-v2`, or change agent code.** The container runs the agent repo's working
> tree (`./src` is bind-mounted read-only), and most of the example is **untracked** in git: the archive must carry a
> copy of the source. A recreated container has no old `docker logs`, and the app logs rotate (50 MB × 5). A run is
> not finished until it is archived.

## Where commands run

| Tag | Directory |
|---|---|
| **[tau2]** | `/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar` (every eval command runs from here) |
| **[agent]** | `/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent` (branch `dev/smasurekar/fbv-shared-pipecat`; the code `fbv-v2` runs) |
| **[dump]** | `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump` |

Note: this is **`nemotron-voice-agent`**, not the `nemotron-voice-agent-smasurekar` checkout the FDH runbooks use.
Don't source `fdh_lib.sh` for this runbook (its `AGENT` points at the other checkout); use the setup below.

Shell setup for every command below (paste into every new shell, tmux included):

```bash
export TAU2=/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
export AGENT=/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent
export DUMP=/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump
export IHUB=https://inference-api.nvidia.com/v1
export FBV_C=fbv-v2                                                   # container
export FBV_URL=ws://localhost:7860/v1/realtime                        # after §2.1 (eval mode, TLS off)
export FBV_MODEL=nvidia/nemotron-realtime-frontend-backend-verdict    # the only model the gateway serves
export FBV_EV=$AGENT/logs/fbv_voice_events.jsonl FBV_FL=$AGENT/logs/fbv_filler.jsonl
export FBV_CONSOLES=$TAU2/data/simulations/_consoles FBV_METRICS=$TAU2/data/simulations/_metrics
export FBV_SPEECH=nemotron-voice-agent-nemo-speech-1
export FBV_OVERRIDE=$FBV_CONSOLES/fbv-v2-eval.override.yaml           # written in §2.1
export CAMPAIGN=$(cat $FBV_CONSOLES/CAMPAIGN_FBV 2>/dev/null)
FBV_KEY=$(sed -n 's/^REALTIME_API_KEY=//p' $AGENT/.env | tr -d '"'"'")   # the gateway's bearer; never echo it
fbv_logs() { python3 $TAU2/misc/prototypes/fdh_voice_eval/fdh_logs.py "$@"; }   # stdlib log helpers (exact-join, checks)
fbv_env() { docker inspect $FBV_C --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -vE 'KEY|TOKEN|SECRET|PASS'; }
fbv_profile() { fbv_env | sed -n 's/^FBV_PROFILE=//p'; }
fbv_arm() {   # FBV_PROFILE of the running container -> arm name in run names (no `_`)
  case $(fbv_profile) in
    tau3_eval_frontend_verdict_speak_history) echo vsh;;
    tau3_eval_frontend_verdict_speak_history_write_gate) echo vshwg;;
    tau3_eval_frontend_verdict_speak) echo vs;;
    tau3_eval_frontend_verdict) echo verdict;;
    tau3_eval_backend_history) echo hist;;
    tau3_eval_backend_history_noguide) echo histng;;
    tau3_eval|tau3_eval_normalization) echo base;;
    *) echo "profile '$(fbv_profile)' is not a tau3_eval* profile: refusing" >&2; return 1;;
  esac
}
```

---

## 0. What you are measuring, and how this stack differs

The metrics are the FBA runbook §0 and §7.3 (Pass^1; tau2's L_R/L_Y, R_R/R_Y, I_A, S_BC/S_VT/S_ND; mean realtime
response latency; frontend/backend per-turn LLM latency; FE/BE tokens per task), computed by the same
`fba_voice_metrics.py` from the agent's event log. The event kinds it needs (`session_start`, `agent_turn_start`,
`agent_turn_done` with per-role usage, `turn_latency`, `filler_timing`, `thinking_cancelled`, `response_done`) are all
emitted by this example.

| | FBA / FDH runbooks | **`fbv-v2`** |
|---|---|---|
| Server | prototype or FDH voice server, own port per arm | the **repository server** (`src/server.py`, Realtime gateway `src/realtime/gateway.py`), port **7860**, one example locked (`EXAMPLE_SELECTION=frontend-backend-verdict-agent`) |
| Container | `docker compose run` / FDH Compose file | Compose project **`fbv-v2`**, service `frontend-backend-verdict-agent-single-gpu` of `[agent]/docker-compose.yml` + an override, `restart: unless-stopped`, logs `json-file` **50 MB × 5** |
| Model routing | any `pine-…` tag accepted | **only `?model=nvidia/nemotron-realtime-frontend-backend-verdict`**; `pine-…` gets `model_not_available` (tested 2026-10-15). tau2 needs a `pine-` model to use `PINE_REALTIME_BASE_URL`, so `fbv_run` puts the real model in the base URL (§3.3) |
| Auth | `PINE_API_KEY=unused` | **`REALTIME_API_KEY` is enforced** (bearer): `PINE_API_KEY` = that key from `[agent]/.env` |
| TLS | plain `ws://` | **`wss://` with a self-signed cert** (`PIPELINE_TLS=true`); tau2's `websockets.connect` rejects it. Eval mode sets `PIPELINE_TLS=false` |
| Event log | `FBA_VOICE_EVENT_LOG` on a host bind mount | **off** (unset, no logs mount) until §2.1 |
| Run identity in the event log | `session_start.model` = the run's `pine-` tag | `session_start.model` is always `nvidia/nemotron-realtime-frontend-backend-verdict`. Runs are told apart by **session id**: tau2's `task.log` records `session created (session_id=sess_…)`, the same id as the event log's `session_id` (exact join), and by time span (concurrency 1) |
| Turn detection | prototype VAD, or Pipecat 3 s wall time | Pipecat Silero VAD, **800 ms of input audio** (`settings/eval.yaml`: `stop_secs 0.8`, `audio_idle_timeout 0`, `stt_ttfs_p99 3.0`); the server owns it (`honor_client_values: false`), so tau2's 500 ms is ignored and `session.updated` echoes 800 ms |
| Filler | `log_only` (silent) in the main arms | **spoken** in the default profile (`filler.mode: speak`): tau2 hears it. Pass^1 is **not** comparable with silent-filler arms; C6 FAILs by design |
| Barge-in during backend work | `cancel_and_merge` | `frontend_verdict` (4 s deadline, `same_query_guard`) |
| Backend history | off in `paired` | **on**, `include: full` (`…_history` profiles) |
| Write gate | — | off, except in `tau3_eval_frontend_verdict_speak_history_write_gate` |
| Capacity | 1 or 4 | `server.max_sessions: 8`; evaluated at **concurrency 1** |
| Voice | catalog voice | catalog voice `John` (client voice ignored, a warning per session) |

Models (from the `verdict agent ready` log line, 2026-10-15): frontend `nvidia/nvidia/nemotron-3.5-lightning`
(reasoning off), backend `nvidia/nvidia/nemotron-3-ultra` (reasoning on, budget 1024), ASR
`nemotron-speech-streaming-en-0.6b`, TTS `magpie-tts-multilingual` voice `John`, both on the shared
`nemotron-voice-agent-nemo-speech-1` sidecar (host port 50051, reached as `nemo-speech:host-gateway`). Tau prompt
catalog `core/config/prompts/tau.yaml`, sha256 `a29ee978525777ad9811935c0f3efa1784d948889676ee13642745d4023a4a0b`.

**Numbers are a new series.** Don't compare with the FBA prototype (`fba-voice*`) or FDH campaigns unless the user
asks for a labelled cross-stack comparison: different server, Pipecat turn taking, audible filler, backend history.

---

## 1. Check `fbv-v2` as it runs now [agent]

```bash
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -E 'fbv|fdh|fba|nemo-speech'
#   fbv-v2  nemotron-voice-agent:fbv-v2  Up … (healthy)  0.0.0.0:7860->7860/tcp
#   nemotron-voice-agent-nemo-speech-1  Up …  50051
#   fdh-voice  … Up … 8776   <- another agent on the same nemo-speech and Hub: see §4
docker inspect $FBV_C --format 'image={{.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}} restart={{.HostConfig.RestartPolicy.Name}}'
docker inspect $FBV_C --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} rw={{.RW}}{{println}}{{end}}'
fbv_env | grep -E '^(FBV_|FBA_|PIPELINE_TLS|EXAMPLE_SELECTION|REALTIME_SERVICE_PLATFORM|UVICORN_)'
docker inspect $FBV_C --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

Seen on 2026-10-15 (before §2.1): image `sha256:2b0ee970e0d7…` (built 11:44 IST from the working tree), mounts only
`src` and `examples_registry.yaml` (both read-only), `PIPELINE_TLS=true`, `FBV_PROFILE=tau3_eval_frontend_verdict_speak_history`,
`FBV_PIPECAT_SETTINGS=eval.yaml`, `UVICORN_WS_PING_INTERVAL=0`, `UVICORN_WS_PING_TIMEOUT=20`, **no `FBA_VOICE_EVENT_LOG`**.
Config files: `[agent]/docker-compose.yml` plus a **temporary** override in `~/.claude/jobs/f1449141/tmp/fbv-v2.override.yaml`
(image tag `fbv-v2`, `container_name: fbv-v2`, `extra_hosts nemo-speech:host-gateway`, `NVIDIA_API_KEY` from the
shell). That file can disappear; §2.1 writes a durable copy with the eval additions.

```bash
curl -sk https://localhost:7860/health; echo        # {"status":"ok"} (liveness only: no speech or LLM check)
docker logs $FBV_C 2>&1 | grep -E 'verdict agent ready|prompts .*sha256|ASR asr:|TTS tts:' | tail -4
```

The ready/prompt lines appear only after the first session (warm-up runs on first connect).

---

## 2. Why tau2 can't run against it yet, and the eval mode

Checked on 2026-10-15 with a one-handshake probe (no audio):

1. **TLS.** `ssl.create_default_context()` → `CERTIFICATE_VERIFY_FAILED: self-signed certificate`. tau2 connects with
   `websockets.connect(url, additional_headers=…)` and default verification, so every task would be an infra error.
2. **Model name.** tau2 builds `"{PINE_REALTIME_BASE_URL}?model={model}"` and requires `model` to start with `pine-`.
   `?model=pine-fbv-probe` → `model_not_available`. With the base URL
   `…/v1/realtime?model=nvidia/nemotron-realtime-frontend-backend-verdict&x_tau_run=<run>` the final URL is
   `…?model=nvidia/…verdict&x_tau_run=<run>?model=pine-…`: the query parser sees one `model` (the real one) and a
   harmless `x_tau_run`, and `session.created` comes back with `model: nvidia/nemotron-realtime-frontend-backend-verdict`.
   `fbv_run` does this; nothing in tau2 or the agent changes.
3. **No event log** (`FBA_VOICE_EVENT_LOG` unset, no `logs` mount): without it only Pass^1 and tau2's interaction
   metrics are available, not the realtime latency, per-role latency, tokens or checks C1–C8.

### 2.1 Recreate `fbv-v2` in eval mode [agent] — ASK first, archive first

**Ask the user** before doing this (it recreates the container; any session on 7860 is cut, and the current
`docker logs` are lost). If any run was made against the current container, archive it first (§9). The change is
only Compose-side: same image `nemotron-voice-agent:fbv-v2`, same code, same profile, plus

- `PIPELINE_TLS=false` → plain `ws://localhost:7860`;
- `FBA_VOICE_EVENT_LOG=/app/logs/fbv_voice_events.jsonl`, `FBA_FILLER_LOG=/app/logs/fbv_filler.jsonl` with `[agent]/logs`
  bind-mounted at `/app/logs` (relative paths resolve against `/app`).

```bash
cd $AGENT
mkdir -p logs                                  # as your user, before Docker creates it as root
mkdir -p $FBV_CONSOLES && cat > $FBV_OVERRIDE <<'YAML'
# fbv-v2 in tau3 eval mode: the original fbv-v2 override (image tag, container name, nemo-speech on the host)
# plus plain ws:// and the agent's event/filler logs on the host. Used with [agent]/docker-compose.yml.
services:
  frontend-backend-verdict-agent-single-gpu:
    image: nemotron-voice-agent:fbv-v2
    container_name: fbv-v2
    extra_hosts:
      - "nemo-speech:host-gateway"
    volumes:
      - ./logs:/app/logs
    environment:
      NVIDIA_API_KEY: ${NVIDIA_API_KEY:?NVIDIA_API_KEY must be set in the shell}
      PIPELINE_TLS: "false"
      FBA_VOICE_EVENT_LOG: /app/logs/fbv_voice_events.jsonl
      FBA_FILLER_LOG: /app/logs/fbv_filler.jsonl
YAML
```

`NVIDIA_API_KEY` is **empty in `[agent]/.env`**; the shell must export the Inference Hub key that both LLMs use
(the same one `fbv-v2` was started with; ask the user if it is not in the shell). The profile comes from
`FBV_PROFILE` (shell or `.env`, else the Compose default `tau3_eval_frontend_verdict_speak_history`): export the
profile the user chose so it can't drift.

```bash
cd $AGENT
export FBV_PROFILE=tau3_eval_frontend_verdict_speak_history     # the user's choice (Rules)
DC="docker compose -p fbv-v2 -f docker-compose.yml -f $FBV_OVERRIDE --profile frontend-backend-verdict-agent/single-gpu"
$DC config -q && echo config OK                                 # fails loudly if NVIDIA_API_KEY is not exported
$DC up -d --no-deps frontend-backend-verdict-agent-single-gpu   # "Recreate"; never --build (the image tag is the run's image)
until [ "$(docker inspect -f '{{.State.Health.Status}}' $FBV_C)" = healthy ]; do sleep 5; done; curl -s localhost:7860/health; echo
# NLTK punkt_tab lives in the container layer and is gone after a recreate; fetch it once, before the first session:
docker exec $FBV_C .venv/bin/python -c "import nltk; nltk.download('punkt_tab')"
```

Check (expected values):

```bash
fbv_env | grep -E '^(FBV_PROFILE|FBV_PIPECAT_SETTINGS|PIPELINE_TLS|FBA_VOICE_EVENT_LOG|FBA_FILLER_LOG|UVICORN_WS_PING_INTERVAL|UVICORN_WS_PING_TIMEOUT)='
#   FBV_PROFILE=<chosen>  FBV_PIPECAT_SETTINGS=eval.yaml  PIPELINE_TLS=false
#   FBA_VOICE_EVENT_LOG=/app/logs/fbv_voice_events.jsonl  FBA_FILLER_LOG=/app/logs/fbv_filler.jsonl
#   UVICORN_WS_PING_INTERVAL=0  UVICORN_WS_PING_TIMEOUT=20      (the profiles require exactly these; else close 1011)
docker inspect $FBV_C --format '{{range .Mounts}}{{.Destination}} {{end}}'   # /app/src /app/examples_registry.yaml /app/logs
docker image inspect nemotron-voice-agent:fbv-v2 --format '{{.Id}} {{.Created}}'   # same id as before (2b0ee970e0d7…)
```

**After any agent code, prompt or config change** (the `./src` mount is re-read at process start): archive (§9),
`$DC up -d --force-recreate --no-deps …` (or `docker restart fbv-v2`), punkt_tab again, then a **new campaign** (§3.2).
`tau.yaml` is pinned by sha256 in the agent's tests; a prompt change needs a new pin and an A/B run (agent README).

**Not recommended — running without the recreate:** you would have no event log (Pass^1 and tau2 interaction metrics
only) and you would need a CA bundle with the container's per-start self-signed cert for tau2. Don't do it unless the
user explicitly accepts both.

---

## 3. tau2 setup, the campaign, and the run helper [tau2]

### 3.1 One-time tau2 setup (already done on this host)

As in the FBA runbook §2: `uv sync --extra voice --extra knowledge --extra dev` (`--inexact` keeps other extras),
portaudio/pyaudio, **I0** (`misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py` and `tau2_ihub.py`; every run goes
through `tau2_ihub.py`, never plain `tau2 run`), and `$TAU2/.env` with the Hub key (`OPENAI_API_KEY=sk-…`),
`TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2`, `TAU2_JUDGE_BASE_URL`, `TAU2_USER_TTS_*`. `fbv_run` overrides the judge
and review model on its command line, so `.env` stays as it is for both gpt-5.2 and gpt-5.5. `banking_knowledge` uses
`--retrieval-config bm25` (`TAU3_RETRIEVAL` overrides; FDH runbook §2.4). Quick check:

```bash
cd $TAU2 && uv run tau2 check-data && ls misc/prototypes/fba_voice_eval/tau2_ihub*.py
grep -E '^TAU2_(JUDGE_MODEL|JUDGE_BASE_URL|USER_TTS_MODEL|USER_TTS_BASE_URL)=' .env
```

The FBA runbook §2.3 endpoint check (user LLM, judge, decision, review, user TTS) is worth repeating at the start of a
campaign; with gpt-5.5 change the `user LLM` model there to `openai/openai/openai/gpt-5.5`.

### 3.2 New campaign

One dump folder per campaign: same container, image, profile, agent working tree and user/judge model throughout.

```bash
echo "$(date -u +%Y-%m-%d_%H-%M-%SZ)_fbv-v2-<arm>-<gpt52|gpt55>" > $FBV_CONSOLES/CAMPAIGN_FBV   # e.g. …_fbv-v2-vsh-gpt55
export CAMPAIGN=$(cat $FBV_CONSOLES/CAMPAIGN_FBV); echo $CAMPAIGN
mkdir -p $DUMP/tau-3-voice/$CAMPAIGN
```

Before replacing `CAMPAIGN_FBV`, make sure the previous campaign is archived (§9).

### 3.3 The run helper

Paste into the shell (after the Shell setup):

```bash
# usage: FBV_USER_JUDGE=gpt52|gpt55 [TAU3_TAG=smoke] [TAU3_RETRIEVAL=bm25] fbv_run <domain> [extra tau2 args...]
fbv_run() {
  [ $# -ge 1 ] || { echo "usage: fbv_run <domain> [tau2 args...]" >&2; return 2; }
  local domain=$1; shift
  local um
  case ${FBV_USER_JUDGE:-} in
    gpt52) um=openai/azure/openai/gpt-5.2;;
    gpt55) um=openai/openai/openai/gpt-5.5;;      # LiteLLM openai/ prefix + Hub name openai/openai/gpt-5.5
    *) echo "FBV_USER_JUDGE is not set. ASK the user: gpt52 (azure/openai/gpt-5.2) or gpt55 (openai/openai/gpt-5.5)" >&2; return 2;;
  esac
  local arm; arm=$(fbv_arm) || return 2
  [ -n "$FBV_KEY" ] || { echo "FBV_KEY empty: REALTIME_API_KEY not found in $AGENT/.env" >&2; return 2; }
  local tag=${TAU3_TAG:-}; [ "$FBV_USER_JUDGE" = gpt55 ] && tag=${tag:+$tag-}gpt55
  local name=fbv_${arm}_${domain}_regular${tag:+_$tag}
  local model=pine-${name//_/-}                                   # tau2 needs pine-; it is only tau2's run label here
  local conc=${TAU3_CONCURRENCY:-1}
  local extra=(); [ "$domain" = banking_knowledge ] && extra=(--retrieval-config "${TAU3_RETRIEVAL:-bm25}")
  mkdir -p "$FBV_CONSOLES"
  date -Is >> "$FBV_CONSOLES/$name.start"                        # one line per (re)start; line 1 = first start
  echo "== $name  label=$model  url=$FBV_URL?model=$FBV_MODEL  container=$FBV_C  profile=$(fbv_profile)  user/judge/review=$um  max_concurrency=$conc  speech_complexity=regular  turn-taking=tau2 defaults  campaign=${CAMPAIGN:-unset}  ${extra[*]}" \
    | tee -a "$FBV_CONSOLES/$name.log"
  [ "$conc" = 1 ] || echo "WARN max_concurrency $conc (campaign uses 1): approved by the user?" | tee -a "$FBV_CONSOLES/$name.log"
  ( cd "$TAU2" && export TAU2_JUDGE_MODEL="$um" TAU2_REVIEW_MODEL="$um" \
      PINE_REALTIME_BASE_URL="$FBV_URL?model=$FBV_MODEL&x_tau_run=$name" PINE_API_KEY="$FBV_KEY" && \
    uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain "$domain" --audio-native \
      --audio-native-provider openai --audio-native-model "$model" \
      --speech-complexity regular \
      --user-llm "$um" --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
      --review-model "$um" \
      --task-split-name base --num-trials 1 --max-concurrency "$conc" \
      --save-to "$name" --verbose-logs "${extra[@]}" "$@" ) 2>&1 | tee -a "$FBV_CONSOLES/$name.log"
  local rc=${PIPESTATUS[0]}
  date -Is > "$FBV_CONSOLES/$name.end"
  return "$rc"
}
```

- Run names: `fbv_<arm>_<domain>_regular[_<tag>]`, e.g. `fbv_vsh_airline_regular_gpt55`, `fbv_vsh_mock_regular_smoke-gpt55`.
  gpt-5.5 runs always get `gpt55` in the tag, so they never resume or overwrite a gpt-5.2 run. No `_` in tags.
- Extra arguments go last and win (`--num-tasks 5`, `--task-ids …`, `--auto-resume`). **Never** pass
  `--wait-to-respond-*`, `--silence-annotation-threshold`, `--speech-complexity` or `--user-llm` here (Rules).
- The same name resumes an existing run in `data/simulations/` (tau2 `--save-to`); add `--auto-resume` to rerun
  infrastructure errors.

---

## 4. Preflight [tau2 + agent]

Ready when every line holds:

```bash
docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' $FBV_C          # healthy restarts=0
curl -s localhost:7860/health; echo                                                     # {"status":"ok"}
fbv_env | grep -E '^(FBV_PROFILE|PIPELINE_TLS|FBA_VOICE_EVENT_LOG)='                     # chosen profile, false, logs/…
fbv_arm                                                                                 # e.g. vsh
docker ps --format '{{.Names}}' | grep -x $FBV_SPEECH                                    # nemo-speech running
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -vE "^($FBV_C|$FBV_SPEECH)\b"        # other agents still up?
echo "campaign=$CAMPAIGN"; git -C $AGENT log --oneline -1; git -C $AGENT status --short | wc -l; git -C $TAU2 log --oneline -1
```

- **Other agents on the same nemo-speech and Hub key** (on 2026-10-15 `fdh-voice`, the FDH Pipecat adapter, port
  8776, was up): an idle one costs nothing, but any traffic on it during a run shares ASR/TTS and Hub quota. **Ask the
  user** whether to stop it for the reportable runs (only after its own runs are archived), and note the answer in the
  run card.
- Record the provenance for the run card: agent `HEAD` (`fe7622d` on 2026-10-15) **plus a dirty tree** (~190 entries;
  `core/`, `adapters/` and the deploy guide are untracked), tau2 `HEAD`, image id.

Handshake probe (one session, no audio, no LLM call; checks URL, bearer, TLS off and the model trick, and that the
event log is written):

```bash
cd $TAU2 && FBV_KEY="$FBV_KEY" uv run python - <<'PY'
import asyncio, json, os, websockets
url = f"{os.environ['FBV_URL']}?model={os.environ['FBV_MODEL']}&x_tau_run=preflight?model=pine-fbv-preflight"
async def main():
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {os.environ['FBV_KEY']}"}) as ws:
        ev = json.loads(await asyncio.wait_for(ws.recv(), 30))
        print(ev["type"], ev.get("session", {}).get("id"), ev.get("session", {}).get("model"), ev.get("error", ""))
asyncio.run(main())
PY
#   session.created sess_… nvidia/nemotron-realtime-frontend-backend-verdict
tail -1 $FBV_EV | python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); print(r["kind"], r["session_id"])'
```

`model_not_available` = the base-URL trick is missing; close 1008 `realtime authentication failed` = wrong
`FBV_KEY`; an SSL error = the container still has TLS on (§2.1); no event-log line = `FBA_VOICE_EVENT_LOG`/mount
missing. The probe's session is outside any run's time span and its id never appears in a `task.log`, so it does not
enter a report.

---

## 5. Smoke runs [tau2] — only after the user says go and has chosen the user/judge model

```bash
export FBV_USER_JUDGE=<gpt52|gpt55>        # the user's answer, nothing else
TAU3_TAG=smoke fbv_run mock --num-tasks 1
TAU3_TAG=smoke fbv_run airline --num-tasks 1
for d in retail telecom banking_knowledge; do TAU3_TAG=smoke fbv_run $d --num-tasks 1; done
```

Then the report (§7) with `tag=smoke` (`smoke-gpt55` for gpt-5.5) and the five domains, and archive (§9).

### 5.1 Gate

`RUN` is the run name, `SINCE=$(head -1 $FBV_CONSOLES/$RUN.start)`, `R=$TAU2/data/simulations/$RUN`.

| Check | Command | Pass when |
|---|---|---|
| I0 active, right user/judge model | `grep -E 'OVERRIDE' $FBV_CONSOLES/$RUN.log` | `USER TTS OVERRIDE` (gpt-4o-mini-tts), `USER DECISION LLM OVERRIDE` (gpt-4.1), `REVIEW LLM OVERRIDE` = the chosen model; the `==` header line names it too |
| Judge really used the chosen model | `cat $R/artifacts/*/sim_*/llm_debug/*nl_assertions_eval* 2>/dev/null \| grep -o '"model": "[^"]*"' \| sort \| uniq -c` (and `*user_streaming_response*`) | only the chosen model |
| No failed LLM calls | `grep -cE "AuthenticationError\|Error in (backchannel\|interruption) decision\|ELEVENLABS_API_KEY not found\|Missing Anthropic" $FBV_CONSOLES/$RUN.log` | `0` (tau2 swallows these and runs a degraded user) |
| Connected to the right model | `grep -rhoE 'session created \(session_id=sess_[0-9a-f]+\)' $R/artifacts --include=task.log \| wc -l` | = tasks run; no `model_not_available`/`Session configuration failed` in the console log |
| No infra errors, reward | metrics box at the end of the console log; FBA runbook §5.1 `Results.load` one-liner | `Infra Errors` 0; `user_stop`/`agent_stop`; a reward per task |
| Default turn taking | header line `turn-taking=tau2 defaults`; `grep -E 'wait.to.respond\|silence.annotation' $FBV_CONSOLES/$RUN.log` | no non-default flag |
| Agent saw the run | `for s in $(grep -rhoE 'sess_[0-9a-f]{20,}' $R/artifacts --include=task.log \| sort -u); do grep -c "\"session_id\": \"$s\"" $FBV_EV; done` | every session has records |
| Profile / prompts | `grep '"session_start"' $FBV_EV \| tail -1` | `adapter pipecat`, `prompts.catalog tau.yaml`, sha256 `a29ee978…`, `backend_history` as the profile says (`full` for `…history`), `normalization.transcript true`, Pipecat `stop_secs 0.8` |
| Server-owned VAD | `docker logs --since "$SINCE" $FBV_C 2>&1 \| grep -m1 'Setting VAD params'` | `stop_secs=0.8` |
| Filler behaves as the profile says | `tail -3 $FBV_FL` | `"mode": "speak"` for `…speak*` profiles (`log_only` for the silent ones) |
| No agent failures | `docker logs --since "$SINCE" $FBV_C 2>&1 \| grep -Ei " failed\|error\|traceback\|\b401\b\|\b429\b\|server_busy\|1011\|1013" \| grep -v 'not in tts.voice_map' \| head` | nothing relevant (the per-session voice warning is expected) |
| Container did not restart | `docker inspect $FBV_C --format '{{.RestartCount}} {{.State.StartedAt}}'` | `0`, started before `$SINCE` |
| Listen | `$R/artifacts/task_*/sim_*/audio/both.wav` (if present) or the agent turns in `task.log` | agent speaks, filler audible, no long dead air, no user cut off mid-ID |
| Report | §7 | `fba_voice_metrics` exit 1 only from C6; C1 (1:1 join), C2 (per-role usage) PASS; exact-join `0 mismatch` |

**First smoke on this stack:** the metrics script has only run on the prototype's and FDH's logs. Read the smoke
report's join, C-checks and the per-turn CSV before the full runs; stop and tell the user about anything odd. Use the
airline smoke's mean simulation `duration` to estimate §6's wall times.

---

## 6. Reportable runs [tau2]

Full `base` split, 1 trial, `regular`, concurrency 1, in tmux (pass the keys into the session; the shell needs the
Shell setup and `fbv_run`):

```bash
tmux new -s fbv-v2 -e OPENAI_API_KEY="$OPENAI_API_KEY"
# inside tmux: Shell setup + fbv_run definition, then
export FBV_USER_JUDGE=<gpt52|gpt55>   # the user's answer
export TAU3_TAG=                      # empty for the reportable runs (gpt55 is appended automatically)
out=$FBV_CONSOLES/fbv_campaign_$(fbv_arm).out
for d in airline retail telecom banking_knowledge; do
  echo "== $(date -Is) start $d" | tee -a $out; fbv_run $d; echo "== $(date -Is) end $d exit=$?" | tee -a $out
done
```

| Domain | Tasks (`base`) | Wall time at concurrency 1 (re-estimate from the smoke) |
|---|---|---|
| airline | 50 | ~5–8 h (FBA prototype: 5–10 min per task) |
| retail | 114 | ~10–19 h |
| telecom | 114 | ~10–19 h |
| banking_knowledge | 97 | ~8–16 h |

Status while running:

```bash
RUN=$(ls -t $FBV_CONSOLES/fbv_*.start | head -1 | xargs basename | sed 's/\.start$//'); echo $RUN
tail -c 3000 $FBV_CONSOLES/$RUN.log | grep -a 'Status:' | tail -1
grep -rah 'FINISHED SIMULATION' --include=task.log $TAU2/data/simulations/$RUN/artifacts | wc -l   # if Status: freezes
grep -c '"kind": "session_start"' $FBV_EV; docker inspect -f '{{.State.Health.Status}} {{.RestartCount}}' $FBV_C
```

- **Freeze the stack for the whole campaign:** no `up -d`, restart, rebuild, profile change or agent-code edit in
  `[agent]/src` (it is mounted live: an edit changes the running code at the next process start, and the profile and
  prompt files are read on the first session). If something must change: stop, archive (§9), new campaign.
- **Nothing else on port 7860** during a run (no browser, no manual Realtime client): its sessions would land in the
  run's time span.
- **Archive each domain right after it finishes** (§9) so its `docker logs` (50 MB × 5 rotation) are still there.
- Resume: rerun the same `fbv_run <domain>` with the same `FBV_USER_JUDGE`/`TAU3_TAG`; `-- --auto-resume` is just
  `fbv_run <domain> --auto-resume`.

---

## 7. Compute the metrics [tau2]

```bash
cd $TAU2
tag=smoke; doms=(mock airline retail telecom banking_knowledge)    # reportable: tag=""; doms=(airline retail telecom banking_knowledge)
[ "$FBV_USER_JUDGE" = gpt55 ] && tag=${tag:+$tag-}gpt55
arm=$(fbv_arm); out=$FBV_METRICS/fbv_${arm}_regular${tag:+_$tag}; mkdir -p $out $FBV_METRICS/_setup

# 1. setup record (the model columns of the results table), from the container's own log lines
L=$(docker logs $FBV_C 2>&1)
fe=$(sed -n 's/.*verdict agent ready (.* frontend=\([^,]*\), backend=.*/\1/p' <<<"$L" | tail -1)
be=$(sed -n 's/.*verdict agent ready (.* backend=\([^,]*\), pipecat=.*/\1/p' <<<"$L" | tail -1)
asr=$(sed -n 's/.*ASR asr: .* model=\([^ ]*\) .*/\1/p' <<<"$L" | tail -1)
tts=$(sed -n 's/.*TTS tts: .* model=\([^ ]*\) voice=\([^ ]*\) .*/\1 (voice \2)/p' <<<"$L" | tail -1)
cat > $FBV_METRICS/_setup/fbv-$arm.json <<JSON
{"backend_llm": "$be", "backend_reasoning": "on (enable_thinking, reasoning_budget 1024)",
 "frontend_llm": "$fe (FBV, profile $(fbv_profile))", "frontend_reasoning": "off (enable_thinking false)",
 "asr": "$asr (NeMo Speech, streaming, Pipecat NvidiaForceEouSTTService)", "tts": "$tts (NeMo Speech, Pipecat per_sentence)"}
JSON
cat $FBV_METRICS/_setup/fbv-$arm.json
# 2026-10-15: nemotron-3.5-lightning | nemotron-3-ultra | nemotron-speech-streaming-en-0.6b | magpie-tts-multilingual (voice John)
# Reasoning settings are from core/config/agent.yaml (frontend enable_thinking false; backend true, budget 1024): re-check if it changed.

# 2. metrics: every session_start.model is the real model id, so select by it (--model) plus each run's time span
runs=() mods=() dirs=()
for d in "${doms[@]}"; do r=$TAU2/data/simulations/fbv_${arm}_${d}_regular${tag:+_$tag}
  [ -f $r/results.json ] || { echo "skip $r"; continue; }
  runs+=(--run "$arm=$r"); mods+=(--model "$(basename $r)=$FBV_MODEL"); dirs+=($r); done
TAU2_JUDGE_MODEL=$( [ "$FBV_USER_JUDGE" = gpt55 ] && echo openai/openai/openai/gpt-5.5 || echo openai/azure/openai/gpt-5.2 ) \
uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py "${runs[@]}" "${mods[@]}" \
  --event-log "$arm=$FBV_EV" --setup "$arm=$FBV_METRICS/_setup/fbv-$arm.json" --out $out > $out/console.txt 2>&1
echo "metrics exit=$? (1 expected when only C6 fails: spoken filler)"

# 3. checks summary and the exact join against tau2's own task.log session ids
fbv_logs checks $out/fba_voice_metrics.json --allow-fail C6 --join $out/join.csv --event-logs $FBV_EV | tee $out/checks.txt
fbv_logs exact-join "${dirs[@]}" --join $out/join.csv | tee $out/exact_join.txt
cat $out/fba_voice_results_table.md
```

Expected: C1 PASS (1:1 join), C2 PASS (per-role usage), C3 PASS, C4 PASS or WARN (barge-in undercount in tau2), C5
PASS/WARN (read the failures), **C6 FAIL by design** for a `…speak*` profile (filler mode `speak`; PASS for silent
profiles), C7 PASS, C8 PASS (WARN lists agent-caused endings); exact-join `0 mismatch`. `--model` is keyed by the run
folder name (the script also accepts the arm name); sessions are then picked by that model and the run's time span,
which is why nothing else may use port 7860 during a run. The judge env var only labels
the results table; the judging itself happened during the run. A separate tau2-only view, if wanted: FBA runbook §7.1
(`compute_metrics`, `tau2 submit interaction-metrics`).

---

## 8. How to read the numbers

FBA runbook §8 applies (tau2's clock runs at ~half real time, so use *mean realtime response latency* for the real
answer delay; tokens per simulation; non-official user voices; S_VT approximate), plus:

- **Audible filler** (default profile): the user hears and can answer the filler. Pass^1 and the interaction metrics
  are not comparable with silent-filler arms; the filler shows up in L_R (an early agent utterance).
- **Turn taking is Pipecat's, measured in input audio** (800 ms Silero stop, up to 2.2 s more for the force-EOU final);
  tau2's 500 ms request is ignored. Spelled IDs across pauses > 800 ms still split (identifier normalization helps the
  lookup, not the split).
- **Barge-in during backend work goes through the frontend verdict**: report the `barge_in_verdict` counts (and the
  `same_query` share) next to the interaction metrics (`grep -c '"barge_in_verdict"'` on the run's events).
- **Tool-continuation resume:** user speech while client tools are out resumes the wait (`wait_input`, `wait_resumed`
  events); a user who talks during a tool call is not a new turn.
- **Backend history on:** backend prompt tokens grow over a task (`backend_context` events); expect higher BE tokens
  and later-turn latency than history-off arms.
- **Single container, concurrency 1:** latencies include nothing from parallel sessions; don't compare with FDH's
  concurrency-4 numbers.
- **User/judge model:** name it next to every number; gpt-5.2 and gpt-5.5 results are separate series (Rules).

---

## 9. Archive everything [tau2 → dump]

Mandatory after each run (smoke, stopped and invalid ones too), right after each domain, and before any recreate.
It copies, never deletes, the shared source logs.

```bash
RUN=fbv_vsh_airline_regular_gpt55                        # repeat per run
R=$TAU2/data/simulations/$RUN; D=$DUMP/tau-3-voice/$CAMPAIGN/$RUN
SINCE=$(head -1 $FBV_CONSOLES/$RUN.start); UNTIL=$(cat $FBV_CONSOLES/$RUN.end 2>/dev/null || date -Is)
mkdir -p $D/{tau2,metrics,agent/raw,agent/config,provenance}

# 1. tau2: results, trajectories, audio, task logs, llm_debug; console log and run window
rsync -a $R/ $D/tau2/$RUN/
cp $FBV_CONSOLES/$RUN.{log,start,end} $D/tau2/ 2>/dev/null

# 2. agent event/filler logs: this run's sessions (exact, from tau2's task.log), plus the full raw files
grep -rhoE 'session created \(session_id=sess_[0-9a-f]+\)' $R/artifacts --include=task.log \
  | grep -oE 'sess_[0-9a-f]+' | sort -u > $D/agent/sessions.txt
python3 - $FBV_EV $FBV_FL $D/agent/sessions.txt $D/agent <<'PY'
import json, sys
ev, fl, ids, out = sys.argv[1:]
sids = {l.strip() for l in open(ids) if l.strip()}
for src, dst in ((ev, "events.jsonl"), (fl, "filler.jsonl")):
    n = 0
    try:
        with open(src) as f, open(f"{out}/{dst}", "w") as o:
            for line in f:
                try:
                    if json.loads(line).get("session_id") in sids: o.write(line); n += 1
                except json.JSONDecodeError: pass
    except FileNotFoundError: pass
    print(f"{dst}: {n} records for {len(sids)} sessions")
PY
cp $FBV_EV $FBV_FL $D/agent/raw/ 2>/dev/null

# 3. container and speech logs for the run window, health, redacted container definition
docker logs --since "$SINCE" --until "$UNTIL" $FBV_C > $D/agent/docker_logs.txt 2>&1
docker logs --since "$SINCE" --until "$UNTIL" $FBV_SPEECH > $D/agent/nemo_speech_logs.txt 2>&1
curl -s localhost:7860/health > $D/agent/health.json
docker inspect $FBV_C | python3 -c 'import json,re,sys; d=json.load(sys.stdin)
for c in d: c["Config"]["Env"]=[re.sub(r"^([^=]*(KEY|TOKEN|SECRET|PASS)[^=]*)=.*", r"\1=<redacted>", e) for e in c["Config"]["Env"]]
print(json.dumps(d, indent=1))' > $D/agent/container_inspect.json
fbv_env | grep -E '^(FBV_|FBA_|PIPELINE_|EXAMPLE_SELECTION|REALTIME_SERVICE_PLATFORM|UVICORN_)' > $D/provenance/agent_env.txt
grep -E 'verdict agent ready|prompts .*sha256|ASR asr:|TTS tts:|Setting VAD params' $D/agent/docker_logs.txt | head -5 > $D/provenance/agent_ready_lines.txt

# 4. the agent config and code actually served (the example is mostly untracked in git)
cp -r $AGENT/src/examples/frontend_backend_verdict/core/config $D/agent/config/core_config
cp -r $AGENT/src/examples/frontend_backend_verdict/adapters/pipecat/settings $D/agent/config/pipecat_settings
cp $AGENT/src/examples/frontend_backend_verdict/{services.local.yaml,prompts.yaml} $AGENT/examples_registry.yaml \
   $AGENT/docker-compose.yml $FBV_OVERRIDE $D/agent/config/
tar -czf $D/provenance/agent_fbv_src.tgz -C $AGENT --exclude __pycache__ \
  src/examples/frontend_backend_verdict src/examples/shared src/realtime src/server.py \
  docs/how-to/deploy-frontend-backend-verdict-agent.md tests/unit/frontend_backend_verdict
git -C $AGENT ls-files --others --exclude-standard -z | tar -czf $D/provenance/agent_untracked.tgz -C $AGENT --null -T -
sha256sum $AGENT/src/examples/frontend_backend_verdict/core/config/prompts/tau.yaml > $D/provenance/tau_prompt_sha256.txt
docker image inspect nemotron-voice-agent:fbv-v2 --format '{{.Id}} {{.Created}}' > $D/provenance/image.txt

# 5. provenance of both repos, tau2 eval helpers, key names only
for X in AGENT TAU2; do d=${!X}; git -C $d rev-parse HEAD > $D/provenance/${X,,}_commit.txt
  git -C $d branch --show-current >> $D/provenance/${X,,}_commit.txt
  git -C $d status --short > $D/provenance/${X,,}_status.txt; git -C $d diff > $D/provenance/${X,,}_uncommitted.diff; done
cp $TAU2/misc/prototypes/fba_voice_eval/{tau2_ihub_overrides.py,tau2_ihub.py,fba_voice_metrics.py} $D/provenance/
cp $TAU2/misc/examples/fbv-v2-tau3-runbook.md $D/provenance/
grep -E '^(TAU2_JUDGE_(MODEL|BASE_URL|JSON_MODE)|TAU2_USER_TTS_(MODEL|BASE_URL))=' $TAU2/.env > $D/provenance/tau2_endpoints.txt
echo "user/judge/review model used: $FBV_USER_JUDGE (see the console log header; tau2_endpoints.txt is the .env default)" \
  > $D/provenance/user_judge_model.txt
sed -n 's/^\([A-Z0-9_]*\)=.*/\1/p' $TAU2/.env > $D/provenance/tau2_env_keys.txt
du -sh $D; wc -l < $D/agent/sessions.txt
```

`sessions.txt` must have one session per simulation (retries add more); `events.jsonl: 0 records` means the event log
was off: say so in the run card.

Once per campaign, after the reports (§7):

```bash
rsync -a $FBV_METRICS/fbv_* $FBV_METRICS/_setup $DUMP/tau-3-voice/$CAMPAIGN/_reports/
ls $DUMP/tau-3-voice/$CAMPAIGN/_reports/          # only this campaign's fbv_* reports (remove older ones from the copy)
```

Write the run card `$DUMP/tau-3-voice/$CAMPAIGN/README.md`: date, container `fbv-v2` (image id, Compose files incl.
the eval override, `PIPELINE_TLS=false`), profile and its arm name, Pipecat settings `eval.yaml`, prompt catalog
sha256, frontend/backend/ASR/TTS models, **user simulator / judge / review model (the user's choice)**, user TTS and
decision models (I0), turn-taking thresholds (tau2 defaults: 5.0 / 1.0 / 4.0 s), speech complexity `regular`,
`max_concurrency 1`, `bm25` for banking_knowledge, domains, split `base`, trials, agent commit + branch + dirty
state, tau2 commit, any other container that was running, the results table from `fba_voice_results_table.md`, the
failed checks (C6 by design), and anything unusual.

**Before committing in [dump]:**

```bash
cd $DUMP
grep -rIlE 'sk-[A-Za-z0-9_-]{20,}|nvapi-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{16,}' tau-3-voice/$CAMPAIGN | head   # must print nothing
grep -rIl -- "$FBV_KEY" tau-3-voice/$CAMPAIGN | head                                                               # must print nothing
du -sh tau-3-voice/$CAMPAIGN; git lfs track | grep -q wav || { git lfs install && git lfs track "tau-3-voice/**/*.wav" && git add .gitattributes; }
git add tau-3-voice/$CAMPAIGN && git commit -m "tau-3-voice: $CAMPAIGN fbv-v2 ($(fbv_arm), <domains>, regular, <gpt52|gpt55>)"
# git push only when the user asks
```

Never copy any `.env`. The event logs hold transcripts and tool payloads (agent README): keep them in the dump repo
only. Files the container wrote under `$AGENT/logs` are owned by root: `sudo chown -R "$USER" $AGENT/logs` before
moving or deleting them (copying works without it).

**Layout:**

```
voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/
├── README.md                         run card
├── _reports/                         fbv_<arm>_regular[_tag]/ (report, CSVs, checks, exact join), _setup/
└── fbv_<arm>_<domain>_regular[_tag]/
    ├── tau2/<run>/                   results.json, simulations/, artifacts/ (audio, task.log, llm_debug)
    ├── tau2/<run>.log|.start|.end    console output and run window
    ├── agent/sessions.txt            this run's session ids (from task.log)
    ├── agent/events.jsonl, filler.jsonl   this run's agent records
    ├── agent/raw/                    full event/filler logs at archive time
    ├── agent/docker_logs.txt, nemo_speech_logs.txt, health.json, container_inspect.json (redacted)
    ├── agent/config/                 core config (profiles, prompts, voice_agent.yaml, agent.yaml), Pipecat settings,
    │                                 services.local.yaml, registry, docker-compose.yml, the eval override
    └── provenance/                   commits, status, diffs, agent_fbv_src.tgz, agent_untracked.tgz, image id,
                                      prompt sha256, env (no secrets), tau2 helpers, this runbook, key names
```

---

## 10. Stop / restore [agent] — ask first

Only after archiving, and only if the user wants it:

```bash
cd $AGENT
docker compose -p fbv-v2 -f docker-compose.yml -f $FBV_OVERRIDE --profile frontend-backend-verdict-agent/single-gpu \
  stop frontend-backend-verdict-agent-single-gpu          # keeps the container; `rm` afterwards to remove it
```

`fbv-v2` has `restart: unless-stopped`: it comes back after a reboot or Docker restart unless stopped. Leave
`nemotron-voice-agent-nemo-speech-1` alone (other stacks use it). To go back to the demo mode (TLS on, no logs),
recreate with the original override from §1 — after archiving.

---

## 11. Troubleshooting

FBA runbook §11 covers the tau2/I0 side (pyaudio, `ELEVENLABS_API_KEY not found`, Azure prefix, TTS 404, slow
airline runs, C4 WARN). Specific to `fbv-v2`:

| Symptom | Check |
|---|---|
| Every task infra error, `CERTIFICATE_VERIFY_FAILED` / `SSL` in task.log | the container still serves TLS: §2.1 (`PIPELINE_TLS=false`), and `FBV_URL` must be `ws://` |
| `model_not_available` / `Model 'pine-…' is not available` | `PINE_REALTIME_BASE_URL` lacks `?model=nvidia/nemotron-realtime-frontend-backend-verdict&x_tau_run=…` (run through `fbv_run`) |
| Handshake closed with 1008 `realtime authentication failed` | `FBV_KEY` (= `REALTIME_API_KEY` in `[agent]/.env`) wrong or empty |
| `server_error`, close **1011**, log `WebSocket keepalive differs from the profile` | `UVICORN_WS_PING_INTERVAL`/`_TIMEOUT` not `0`/`20` (someone set them in `.env` or the shell); recreate without them |
| `server_error`, close 1011, log names `FBV_PROFILE` | unknown profile name; set a served one (§2.1) |
| `server_busy`, close **1013** | > 8 sessions, or `speech warm-up failed` (nemo-speech down/unreachable: `docker logs $FBV_SPEECH`, `extra_hosts nemo-speech:host-gateway`) |
| First session after a recreate hangs ~120 s or fails | NLTK `punkt_tab` download: run the §2.1 `nltk.download` line |
| Every turn errors with `agent_error` | `NVIDIA_API_KEY` empty/wrong in the container (it is empty in `[agent]/.env`; it must come from the shell at `up`), or no Hub access |
| `config -q`: `NVIDIA_API_KEY must be set` | export the Hub key in the shell before §2.1 |
| No records in `$FBV_EV` | `FBA_VOICE_EVENT_LOG` unset or `/app/logs` not mounted (§2.1 checks) |
| C1 join fails / exact-join mismatch | another client used port 7860 during the run, or concurrency > 1; check `sessions.txt` vs `session_start` times |
| `fbv_run`: `FBV_USER_JUDGE is not set` | intended: ask the user gpt-5.2 or gpt-5.5 |
| `fbv_run`: `not a tau3_eval* profile` | the container runs `default` (generic prompts) or an unknown profile: ask the user, recreate (§2.1) |
| Old run resumed instead of a new one | same run name in `data/simulations/`; use a `TAU3_TAG` |
| `RuntimeError: Not connected to API` mid-run | tau2 stalled; check the container keepalive (`UVICORN_WS_PING_INTERVAL=0`), then rerun with `--auto-resume` |
| `Permission denied` under `$AGENT/logs` | root-owned: `sudo chown -R "$USER" $AGENT/logs` |
| Long dead air, then the task ends | backend latency against tau2's fixed 40 s inactivity limit; an agent result. Don't change tau2 |

---

## 12. What to record when you report

The FBA runbook §12 items, with: container `fbv-v2`, image `nemotron-voice-agent:fbv-v2` (id, created), Compose files
(`docker-compose.yml` + the eval override: `PIPELINE_TLS=false`, event/filler log), the repository Realtime gateway and
model id `nvidia/nemotron-realtime-frontend-backend-verdict`, `FBV_PROFILE` and arm name, `FBV_PIPECAT_SETTINGS=eval.yaml`
(800 ms audio-time Silero stop), Tau prompt catalog sha256, frontend/backend/ASR/TTS models and reasoning settings,
**the user simulator / judge / review model the user chose (gpt-5.2 or gpt-5.5)**, tau2's default turn-taking
thresholds (unchanged), `regular`, `max_concurrency 1`, `bm25` for banking_knowledge, agent commit + branch + dirty /
untracked state, tau2 commit, other agents running on the shared nemo-speech, the filler being audible (C6 FAIL by
design), and the dump path `voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/`.
