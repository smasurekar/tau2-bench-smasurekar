# Runbook: τ³ voice evaluation of the Voice Frontend/Backend Agent

**Date:** 2026-09-24 · **Status:** runbook. I0, I1 and I2 are built and tested, `fba-voice-bo` is running, and the §5 smoke runs of both arms passed the §5.1 gate (2026-09-24) ·
**Integration plan (read first for metric definitions):** [`voice-frontend-backend-agent-tau3-integration-plan.md`](voice-frontend-backend-agent-tau3-integration-plan.md) ·
**Agent runbook (container setup):** `nemotron-voice-agent-smasurekar/misc/prototypes/voice-frontend-backend-agent-runbook.md` ·
**Generic Realtime integration:** [`voice-custom-agent-openai-realtime-integration.md`](voice-custom-agent-openai-realtime-integration.md)

This runbook goes from the already running `fba-voice` container to a τ³ voice report for both
agent variants, **paired** (frontend + backend) and **backend-only**. It ends with every log and
artifact archived in the dump repository. `src/tau2/` is not modified.

> **Everything from every run goes to
> `/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice/`**
> (repo `voice-agent-evaluation-dump`, folder `tau-3-voice`; `$DUMP/tau-3-voice/<CAMPAIGN>/<run>/`
> in §9): tau2 results, trajectories and audio, the agent's event and filler logs, container logs,
> configs, git provenance, console output and computed metrics. A run is not finished until it has
> been archived.

## Where commands run

| Tag | Directory |
|---|---|
| **[tau2]** | `/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar` |
| **[agent]** | `/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar` |
| **[dump]** | `/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump` |

Set these once per shell. Later steps use them:

```bash
export TAU2=/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar
export AGENT=/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar
export DUMP=/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump
export IHUB=https://inference-api.nvidia.com/v1
export CAMPAIGN=$(date +%Y-%m-%d)_fba-voice          # one folder in the dump per evaluation campaign
```

---

## 0. What you are measuring

| Metric | Source | Needs |
|---|---|---|
| **Pass^1** | tau2 `compute_metrics` | nothing extra |
| **Latency** (L_R response, L_Y yield) | `tau2 submit interaction-metrics` | nothing extra |
| **Responsiveness** (R_R response rate, R_Y yield rate) | same | nothing extra |
| **Interrupts** (I_A agent-interrupts-user rate) | same | nothing extra |
| **Selectivity** (S_BC backchannel, S_VT vocal tic, S_ND non-directed) | same | `--speech-complexity regular` (empty under `control`) |
| **Mean per-turn backend LLM latency (s)**: backend LLM time summed over every tool round of a user turn, averaged over turns with backend work. Same definition in both arms. | agent event log (`agent_turn_done`, `filler_timing`) | script **I2**. Exact per-role value needs **I1**; without I1 it is derived. |
| **Frontend filler voice latency** (paired only): user stops speaking → the user would hear the filler. Filler stays silent to tau2 (`log_only`), so the latency is reconstructed from the agent's logs. | agent event log (`speech_stopped`, `filler_timing`) | script **I2** (no tau2 change) |
| **Avg. tokens per task, frontend and backend** | agent event log (`agent_turn_done`) | **I1** for the split, **I2** to aggregate |

**I0** (the user simulator's TTS and hardcoded LLM calls on the Inference Hub) is **required before
any run**. **I1** (per-role usage in the agent's event log, agent repo) and **I2**
(`misc/prototypes/fba_voice_eval/fba_voice_metrics.py`) are built and tested (integration plan §4).
The raw logs are archived with every run, so I2 can be rerun on them later. Only the per-role token
split needs I1 to be in place *before* a run; I1 is live in `fba-voice` and `fba-voice-bo` since
2026-09-24.

**Arms and run names.** Every run uses its own `pine-` model tag. The agent logs the tag in
`session_start.model`, which is how the logs of different runs are told apart in the shared log files.

| Arm | Container | Port | Agent profile | Event log (host path under [agent]) |
|---|---|---|---|---|
| `paired` | `fba-voice` (already running) | 8765 | `profiles/tau3_eval.yaml` | `logs/fba_voice_events.jsonl`, `logs/fba_filler.jsonl` |
| `bo` (backend-only) | `fba-voice-bo` (§4) | 8767 | `profiles/backend_only.yaml` | `logs/fba_voice_bo_events.jsonl`, `logs/fba_voice_bo_filler.jsonl` |

Run name = model tag suffix: `fba_voice_<arm>_<domain>_<complexity>`, e.g.
`fba_voice_paired_airline_regular` ↔ `--audio-native-model pine-fba-voice-paired-airline-regular`.

---

## 1. Check the running agent [agent]

```bash
curl -s localhost:8765/health; echo                  # {"status":"ok",...}
docker inspect fba-voice --format '{{json .Config.Cmd}}'
#   must contain profiles/tau3_eval.yaml   (paired, filler log_only, client tools, no greeting)
docker inspect fba-voice --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(FRONTEND|BACKEND)_LLM_|^FBA_'          # models and log paths; never print the whole env (it holds keys)
git -C $AGENT log --oneline -1 && git -C $AGENT status --short    # record the commit; a dirty tree must be noted in the report
docker ps --filter name=nemo-speech --format '{{.Names}} {{.Status}}'   # nemotron-voice-agent-nemo-speech-1 Up …
```

Expected for the paired arm: `FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning` (reasoning off),
`BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra` (reasoning on, budget 1024),
`FBA_VOICE_EVENT_LOG=logs/fba_voice_events.jsonl`, `FBA_FILLER_LOG=logs/fba_filler.jsonl`.

If `fba-voice` is not running, start it with the agent runbook, §1.

## 2. One-time tau2 setup [tau2]

### 2.1 Install

```bash
cd $TAU2
sudo apt install portaudio19-dev ffmpeg   # first: pyaudio builds against portaudio, and tau2 imports
                                          # pyaudio at run time (src/tau2/voice/utils/audio_io.py:7)
uv sync --extra voice --extra dev         # add any other extras you use (e.g. --extra knowledge), or
                                          # uv sync removes them; `--inexact` keeps what's installed
```

Without pyaudio, every task ends as an infrastructure error with `No module named 'pyaudio'`. This
was observed on 2026-09-24.

**I0** runs tau2's voice user simulator entirely on the Inference Hub (integration plan §4.0). It is
already in place (uncommitted):

| File | What it does |
|---|---|
| `misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py` | Replaces the user's ElevenLabs TTS with Hub TTS. Routes tau2's **hardcoded** LLM calls to the Hub: the user's backchannel/interruption decisions (tau2 hardcodes `gpt-4.1` at api.openai.com) and the post-run hallucination check (tau2 defaults to `claude-opus-4-5`). |
| `misc/prototypes/fba_voice_eval/tau2_ihub.py` | Launcher: installs the overrides, then runs tau2's own CLI with the same arguments |

```bash
ls misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py misc/prototypes/fba_voice_eval/tau2_ihub.py
# if missing (e.g. a fresh checkout): copy both files verbatim from the integration plan, §4.0
```

All `tau2 run` commands in this runbook go through `tau2_ihub.py`. **Never use plain `uv run tau2 run`
for this evaluation.** With only a Hub key, tau2 would fail these calls, catch the errors, and silently
run a degraded user: one that never backchannels or interrupts, with no hallucination re-runs.

### 2.2 Endpoints, models and keys

All three roles on the tau2 side use the NVIDIA Inference Hub with **one `sk-...` key**:

| Role | Base URL | Hub model | How it is passed to tau2 |
|---|---|---|---|
| User simulator LLM | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-5.2` | `--user-llm openai/azure/openai/gpt-5.2 --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1"}'` (in the §5 helper) |
| Judge LLM | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-5.2` | `TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2`, `TAU2_JUDGE_BASE_URL` in `.env` |
| User simulator TTS | `https://inference-api.nvidia.com/v1` (the SDK adds `/audio/speech`) | `openai/openai/gpt-4o-mini-tts` | `TAU2_USER_TTS_MODEL`, `TAU2_USER_TTS_BASE_URL` in `.env`, read by I0 |
| User backchannel / interruption decisions | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-4.1` (the same model tau2 hardcodes) | I0 (`TAU2_USER_DECISION_MODEL`, `TAU2_USER_DECISION_BASE_URL` to change it) |
| User-simulator hallucination check | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-5.2` (the judge model) | `--review-model openai/azure/openai/gpt-5.2` (in the §5 helper) plus I0 (`TAU2_REVIEW_MODEL`, `TAU2_REVIEW_BASE_URL`) |

The LLM ids get LiteLLM's `openai/` prefix (`openai/azure/openai/gpt-5.2`). It means "OpenAI-compatible
endpoint at `api_base`". Without it, LiteLLM reads `azure/...` as a direct Azure call.

`$TAU2/.env` should contain the following. The judge lines are already there.

```bash
# Inference Hub key ("sk-..."). Used by the user LLM, the judge and the user TTS.
OPENAI_API_KEY=sk-...                  # skip if already exported in the shell (it is, at the time of writing)

# Judge LLM (NL assertions)
TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1
TAU2_JUDGE_JSON_MODE=0
# TAU2_JUDGE_API_KEY=sk-...            # optional; defaults to OPENAI_API_KEY

# User simulator TTS (I0). These are also I0's defaults; set them explicitly for the record.
TAU2_USER_TTS_BASE_URL=https://inference-api.nvidia.com/v1
TAU2_USER_TTS_MODEL=openai/openai/gpt-4o-mini-tts
# TAU2_USER_TTS_API_KEY=sk-...         # optional; defaults to OPENAI_API_KEY
# The two lines below are also I0's defaults (the same key is used)
TAU2_USER_DECISION_MODEL=openai/azure/openai/gpt-4.1
TAU2_REVIEW_MODEL=openai/azure/openai/gpt-5.2

# The agent under test. PINE_REALTIME_BASE_URL is overridden per arm on the command line (§5, §6).
PINE_REALTIME_BASE_URL=ws://localhost:8765/v1/realtime
PINE_API_KEY=unused                    # the agent does not enforce a bearer (server.require_bearer: false)
```

Not needed and left unset: `ELEVENLABS_API_KEY`, `TAU2_VOICE_ID_*` (I0 maps tau2's built-in persona
voice ids to Hub voices), `DEEPGRAM_API_KEY`. tau2 calls `load_dotenv()` without override, so a variable
set on the command line takes precedence over `.env`. §5 and §6 rely on this to point each arm at its
own port.

**About the TTS snippet.** Calling `client.chat.completions.create(...)` with
`base_url=".../v1/audio/speech"` returns **404**. The base URL must stop at `/v1`, and a TTS model is not
a chat model (with `/v1` the Hub answers `This is not a chat model`). The working call, which I0 uses, is:

```python
from openai import OpenAI

client = OpenAI(api_key="sk-...", base_url="https://inference-api.nvidia.com/v1")
response = client.audio.speech.create(
    model="openai/openai/gpt-4o-mini-tts",
    voice="ash",                        # alloy, ash, coral, echo, nova, onyx, sage, shimmer, ...
    input="Hi, I'd like to change my flight reservation, please.",
    instructions="A calm middle-aged man from the American Midwest, on the phone.",  # optional style
    response_format="pcm",              # raw PCM16, 24 kHz, mono; "wav" and "mp3" also work
)
open("hello.pcm", "wb").write(response.read())
```

### 2.3 Check every endpoint (about a minute, a few cents)

This calls each Hub model exactly the way tau2 does, including the two call sites I0 reroutes.

```bash
cd $TAU2
uv run tau2 check-data
uv run python - misc/prototypes/fba_voice_eval <<'PY'
import os, sys, time, wave
sys.path.insert(0, sys.argv[1])
import tau2_ihub_overrides as ihub  # installs the overrides (and tau2 has loaded .env)
import tau2.evaluator.hallucination_reviewer as reviewer
import tau2.user.user_simulator_streaming as user_streaming
from tau2.config import DEFAULT_LLM_NL_ASSERTIONS_ARGS, VOICE_USER_SIMULATOR_DECISION_MODEL
from tau2.data_model.message import SystemMessage, UserMessage
from tau2.data_model.voice import ElevenLabsTTSConfig
from tau2.data_model.voice_personas import ALL_PERSONAS
from tau2.utils.llm_utils import generate
from tau2.voice.synthesis.synthesize import synthesize_voice

msgs = [SystemMessage(role="system", content='Reply with {"ok": true} only.'),
        UserMessage(role="user", content="ping")]
judge = os.environ["TAU2_JUDGE_MODEL"]
checks = [  # (label, generate as tau2's calling module sees it, kwargs as tau2 passes them, model hit)
    ("user LLM", generate, dict(model="openai/azure/openai/gpt-5.2", temperature=0.0,
                                api_base="https://inference-api.nvidia.com/v1"), "openai/azure/openai/gpt-5.2"),
    ("judge LLM", generate, dict(model=judge, **DEFAULT_LLM_NL_ASSERTIONS_ARGS), judge),
    ("decision", user_streaming.generate, dict(model=VOICE_USER_SIMULATOR_DECISION_MODEL,
                                               call_name="backchannel_decision"), ihub.DECISION_MODEL),
    ("review", reviewer.generate, dict(model="claude-opus-4-5",
                                       call_name="llm_judge_hallucination_check"), ihub.REVIEW_MODEL),
]
for name, fn, kwargs, hub_model in checks:
    t = time.time()
    reply = fn(messages=msgs, **kwargs)
    routed = f"{kwargs['model']} -> " if kwargs["model"] != hub_model else ""
    print(f"{name:10s} OK  {routed}{hub_model}  {time.time() - t:.1f}s  {reply.content!r}")
for persona in ["matt_delaney", "priya_patil"]:
    t = time.time()
    cfg = ElevenLabsTTSConfig(voice_id=ALL_PERSONAS[persona].elevenlabs_voice_id)
    audio = synthesize_voice("Hi, I need to change my flight, please.", "elevenlabs", cfg)
    path = f"/tmp/tau2_user_tts_{persona}.wav"
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(audio.format.sample_rate)
        w.writeframes(audio.data)
    print(f"user TTS   OK  {ihub.MODEL}  {persona}  {time.time() - t:.1f}s  "
          f"{audio.format.sample_rate} Hz  -> {path}")
PY
```

Expected output (measured 2026-09-24). `decision` and `review` show the model tau2 asked for and the Hub model I0 sent the call to:

```
user LLM   OK  openai/azure/openai/gpt-5.2  4.8s  '{"ok": true}'
judge LLM  OK  openai/azure/openai/gpt-5.2  1.1s  '{"ok": true}'
decision   OK  gpt-4.1 -> openai/azure/openai/gpt-4.1  1.0s  '{"ok": true}'
review     OK  claude-opus-4-5 -> openai/azure/openai/gpt-5.2  1.1s  '{"ok": true}'
user TTS   OK  openai/openai/gpt-4o-mini-tts  matt_delaney  1.2s  16000 Hz  -> /tmp/tau2_user_tts_matt_delaney.wav
user TTS   OK  openai/openai/gpt-4o-mini-tts  priya_patil  2.6s  16000 Hz  -> /tmp/tau2_user_tts_priya_patil.wav
```

`synthesize_voice(..., "elevenlabs", ...)` is tau2's own entry point. The word "elevenlabs" is only
tau2's provider label; with I0 installed the call goes to the Hub. Listen to the two WAVs: the second
voice should have an Indian English accent.

## 3. Check that I1 (per-role usage) is live [agent]

I1 is in the agent repo's working tree (integration plan §4.1) and was applied on 2026-09-24 by
restarting `fba-voice`. After any agent code change, or if `fba-voice` was recreated from an older
tree, re-apply it:

```bash
cd $AGENT
uv run pytest tests/unit/prototypes -q     # 209 passed on 2026-09-24
docker restart fba-voice                    # the ./src bind mount is re-read at process start
until curl -sf localhost:8765/health >/dev/null; do sleep 5; done; curl -s localhost:8765/health; echo
```

Restart `fba-voice-bo` too if it is already running (§4). A run made without I1 still works; its
report says *derived* backend latency and *combined* tokens (check C2 = WARN).

## 4. Start the backend-only container [agent]

This is the command from agent runbook §1 with a different container name, host port, profile and log
files. (Started 2026-09-24; skip if `curl -s localhost:8767/health` already answers `ok`.)

```bash
cd $AGENT && mkdir -p logs
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --name fba-voice-bo \
  -p 8767:7860 \
  -v "$PWD/logs:/app/logs" \
  -e PYTHONPATH=/app/src \
  -e PIPELINE_TLS=false \
  -e FRONTEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning \
  -e BACKEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra \
  -e FBA_VOICE_EVENT_LOG=logs/fba_voice_bo_events.jsonl \
  -e FBA_FILLER_LOG=logs/fba_voice_bo_filler.jsonl \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_frontend_backend_agent.server \
    --config src/prototypes/voice_frontend_backend_agent/config/profiles/backend_only.yaml \
    --port 7860

until curl -sf localhost:8767/health >/dev/null; do sleep 5; done; curl -s localhost:8767/health; echo
```

`backend_only.yaml` extends the base config, whose defaults are the τ³ settings (no greeting, client
tools, filler `log_only`). In backend-only mode no filler is produced and the `FRONTEND_*` lines are
unused.

## 5. Smoke run: mock domain, one task per arm [tau2]

A helper that makes each run consistent. Paste it into the shell:

```bash
tau3_run() {   # usage: [TAU3_TAG=smoke] tau3_run <arm: paired|bo> <domain> <complexity: control|regular> [extra tau2 args...]
  local arm=$1 domain=$2 cx=$3; shift 3
  local port; case $arm in paired) port=8765;; bo) port=8767;; *) echo "bad arm"; return 1;; esac
  local name=fba_voice_${arm}_${domain}_${cx}${TAU3_TAG:+_$TAU3_TAG}
  local model=pine-fba-voice-${arm}-${domain}-${cx}${TAU3_TAG:+-$TAU3_TAG}
  mkdir -p $TAU2/data/simulations/_consoles
  date -Is >> $TAU2/data/simulations/_consoles/${name}.start     # one line per (re)start; line 1 = first start
  ( cd $TAU2 && PINE_REALTIME_BASE_URL=ws://localhost:${port}/v1/realtime PINE_API_KEY=unused \
    uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain "$domain" --audio-native \
      --audio-native-provider openai --audio-native-model "$model" \
      --speech-complexity "$cx" \
      --user-llm openai/azure/openai/gpt-5.2 \
      --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
      --review-model openai/azure/openai/gpt-5.2 \
      --task-split-name base --num-trials 1 --max-concurrency 1 \
      --save-to "$name" --verbose-logs "$@" ) 2>&1 | tee -a $TAU2/data/simulations/_consoles/${name}.log
  date -Is > $TAU2/data/simulations/_consoles/${name}.end
}
```

The helper calls `tau2_ihub.py` (tau2 plus I0), not `tau2`, so the user's voice comes from the
Inference Hub. The user LLM is `azure/openai/gpt-5.2` on the Hub, and the judge comes from `.env` (§2.2).
The console log and the start/end timestamps are kept outside the run directory, so a resumed run
never overwrites them. They are archived in §9.

```bash
tau3_run paired mock control --num-tasks 1
tau3_run bo     mock control --num-tasks 1
```

### 5.1 Gate: check the smoke run before spending more

| Check | Command / where | Pass when |
|---|---|---|
| I0 active | `grep -E "OVERRIDE" data/simulations/_consoles/fba_voice_paired_mock_control.log` | three lines: `USER TTS OVERRIDE` (gpt-4o-mini-tts), `USER DECISION LLM OVERRIDE` (gpt-4.1), `REVIEW LLM OVERRIDE` (gpt-5.2) |
| **No failed LLM calls** | `grep -cE "AuthenticationError\|Error in (backchannel\|interruption) decision\|ELEVENLABS_API_KEY not found" data/simulations/_consoles/fba_voice_paired_mock_control.log` | `0`. tau2 **catches** these and keeps running with a degraded user, so a finished run alone proves nothing |
| Hallucination check ran | `hallucination_check` in the simulation (`results.json` / `simulations/`) | present, with reasoning text (not an error) |
| No infrastructure errors | the metrics box at the end of the console log | `Infra Errors` absent or 0 (`No module named 'pyaudio'` means §2.1 was skipped) |
| Run finished, reward | `uv run python -c "from pathlib import Path; from tau2.data_model.simulation import Results; r=Results.load(Path('data/simulations/fba_voice_paired_mock_control')); [print(s.task_id, s.termination_reason, s.reward_info.reward if s.reward_info else None, round(s.duration)) for s in r.simulations]"` | `user_stop` or `agent_stop`; a reward is present |
| Listen to it | `data/simulations/<run>/artifacts/task_*/sim_*/audio/both.wav` | the agent answers in speech; no long dead air |
| Agent saw the run | `grep -c '"model": "pine-fba-voice-paired-mock-control"' $AGENT/logs/fba_voice_events.jsonl` | ≥ 1 |
| Tool calls went through tau2 | `grep -E 'tool_output_in' $AGENT/logs/fba_voice_events.jsonl \| tail -3` | the `call_id`s appear in the simulation's ticks |
| Filler was logged and stayed silent (paired) | `tail -2 $AGENT/logs/fba_filler.jsonl` | `"mode": "log_only"`, `"spoken": false` |
| No filler (backend-only) | `wc -l $AGENT/logs/fba_voice_bo_filler.jsonl 2>/dev/null` | the file is missing or empty |
| I1 in place | `grep agent_turn_done $AGENT/logs/fba_voice_events.jsonl \| tail -1` | has `"step"`, `"frontend"` and `"backend"` keys |
| No agent failures | `docker logs --since "$(head -1 data/simulations/_consoles/fba_voice_paired_mock_control.start)" fba-voice 2>&1 \| grep -Ei "failed\|error\|401" \| head` | nothing relevant |
| Metrics script | §7.2 with `DOMAIN=mock CX=control` | exit code 0; checks C1–C8 all PASS |

**Smoke result, 2026-09-24** (`mock`, `control`, 1 task per arm; report in
`data/simulations/_metrics/fba_voice_mock_control/`): both arms passed every row above and all of
C1–C8. Paired: reward 1.0, L_R 4.80 s, backend turn latency 3.81 s (exact), filler voice latency
2.49 s (projected), 7993 FE and 9729 BE tokens. Backend-only: reward 0.0 (the agent created a task named
"Meeting" after the user paused mid-title; an agent result, not a harness fault), L_R 2.07 s, backend
turn latency 1.71 s, 10505 BE tokens. Both runs show many turns cancelled by the agent's own turn
detection (the user's `[pause]`s split an utterance), which is worth watching in the real runs.

Before the `regular` runs, do a one-task `regular` smoke run per arm, tagged so it doesn't take the
reportable run's name (`TAU3_TAG=smoke tau3_run paired airline regular --num-tasks 1`, then the same
with `bo`; results in `fba_voice_<arm>_airline_regular_smoke`). Listen to `both.wav` for the accented persona voice and for any vocal-tic inserts.
The tics are rendered as sound-words ("ahem… hkh-hkh", "ah-choo", "sniff"), because gpt-4o-mini-tts
reads `[cough]` aloud as a word (integration plan §2.1). Their time positions are in
`artifacts/task_*/sim_*/audio/user_labels.txt`.

**`regular` smoke result, 2026-09-24** (airline task 0, `_smoke` tag, report in
`data/simulations/_metrics/fba_voice_airline_regular_smoke/`): both arms reward 1.0, gate passed,
C4 = WARN only (tau2 undercounts tokens of responses cut off by barge-in; the agent count is used).
The logs showed that in-sentence vocal tics were being dropped by I0; that is fixed (integration plan
§2.1), after these two smoke runs.

Then run `tau3_run paired airline control --num-tasks 5` as a go/no-go on a real domain. Use the mean
`duration` of its simulations to estimate the wall time of the full runs. Every run is real time, and
concurrency is 1.

## 6. Reportable runs [tau2]

The reportable condition is **`regular`**, because Selectivity needs it. Run `control` as well if a
clean-speech baseline is wanted. Use the full `base` split (no `--num-tasks`) and **1 trial**, since
Pass^1 is the target. Keep `--max-concurrency 1`: every latency depends on it. Run the two arms back to
back on the same day so that endpoint load is comparable.

Runs take hours, so start them in `tmux` or `screen`:

```bash
for domain in airline retail telecom; do
  tau3_run paired "$domain" regular
  tau3_run bo     "$domain" regular
done
```

- A run that stops can be resumed: rerun the same `tau3_run` line. The same `--save-to` resumes, and
  the helper appends a new `.start` stamp.
- Don't change the agent config, models or code during a campaign. If something must change, start a
  new `CAMPAIGN`.
- (Optional) Filler validation run (integration plan §3.4): a third container on port 8768 with
  `filler.mode: speak`, and 5 airline tasks. It is for latency validation only and is **never** part of
  Pass^1.

## 7. Compute the metrics [tau2]

### 7.1 Available today (tau2 only)

```bash
cd $TAU2
RUN=fba_voice_paired_airline_regular
mkdir -p data/simulations/_metrics/$RUN

# Pass^1
uv run python -c "
from pathlib import Path
from tau2.data_model.simulation import Results
from tau2.metrics.agent_metrics import compute_metrics
m = compute_metrics(Results.load(Path('data/simulations/$RUN')))
print(m.model_dump_json(indent=2))" | tee data/simulations/_metrics/$RUN/agent_metrics.json

# Latency, Responsiveness, Interrupts, Selectivity
uv run tau2 submit interaction-metrics data/simulations/$RUN \
  --output data/simulations/_metrics/$RUN/interaction_metrics.json
```

Pass^1 is `pass_hat_ks["1"]`. `infra_error_count` must be 0, or it must be reported.

### 7.2 Full report (I2)

First record the agent-side setup of each arm. tau2 doesn't store it, so the script takes it from a
small JSON file per arm. The models are read from the running containers; the reasoning settings come
from the text agent's `config/agent.yaml` (frontend `enable_thinking: false`; backend
`enable_thinking: true`, `reasoning_budget: 1024`); re-check them if that file changed.

```bash
cd $TAU2
mkdir -p data/simulations/_metrics/_setup
for arm in paired bo; do
  c=fba-voice; [ $arm = bo ] && c=fba-voice-bo
  env=$(docker inspect $c --format '{{range .Config.Env}}{{println .}}{{end}}')
  be=$(sed -n 's/^BACKEND_LLM_MODEL=//p' <<<"$env"); fe=$(sed -n 's/^FRONTEND_LLM_MODEL=//p' <<<"$env")
  asr=$(docker logs $c 2>&1 | sed -n 's/.* - ASR asr: .* model=\([^ ]*\) .*/\1/p' | tail -1)
  tts=$(docker logs $c 2>&1 | sed -n 's/.* - TTS tts: .* model=\([^ ]*\) voice=\([^ ]*\) .*/\1 (voice \2)/p' | tail -1)
  cat > data/simulations/_metrics/_setup/$arm.json <<JSON
{"backend_llm": "$be", "backend_reasoning": "on (enable_thinking, reasoning_budget 1024)",
 "frontend_llm": "$fe", "frontend_reasoning": "off (enable_thinking false)",
 "asr": "$asr (NeMo Speech, streaming)", "tts": "$tts (NeMo Speech)"}
JSON
  cat data/simulations/_metrics/_setup/$arm.json
done
```

For the backend-only arm the script prints the frontend columns as `none (backend-only)` / `n/a`
whatever the file says. Then run the report:

```bash
cd $TAU2
DOMAIN=airline; CX=regular
uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py \
  --run paired=data/simulations/fba_voice_paired_${DOMAIN}_${CX} \
  --run bo=data/simulations/fba_voice_bo_${DOMAIN}_${CX} \
  --event-log paired=$AGENT/logs/fba_voice_events.jsonl \
  --event-log bo=$AGENT/logs/fba_voice_bo_events.jsonl \
  --setup paired=data/simulations/_metrics/_setup/paired.json \
  --setup bo=data/simulations/_metrics/_setup/bo.json \
  --out data/simulations/_metrics/fba_voice_${DOMAIN}_${CX}
echo "exit=$?"     # 1 = a check FAILED (listed on stderr and in the report)
```

`--run` can be repeated (e.g. one per domain and arm); the arm names `bo`/`backend_only` select the
backend-only rules. Each run's sessions are picked from the shared log by its model tag and time span,
so the log files don't need filtering first. Usage details: `misc/prototypes/fba_voice_eval/README.md`.

The script produces `fba_voice_report.md`: the §7.3 results table, Pass^1, the tau2 interaction metrics, mean/p90 backend
turn latency, filler voice latency (projected) with filler-would-be-heard share, projected time to first
audio, and FE/BE tokens per task. It also writes per-task and per-turn CSVs, the session-to-task join,
and checks C1–C8. The definitions are in integration plan §3. After archiving (§9), the script can be
rerun on the archived copies of the logs instead of `$AGENT/logs`.

### 7.3 Results table (required for every report)

Every report must include this table: one row per arm × domain × speech complexity, with the columns
in this order. The script writes it to `fba_voice_results_table.md` and `fba_voice_results_table.csv`,
and as the first section of `fba_voice_report.md`.

| Column | Value | Source |
|---|---|---|
| Backend LLM | backend model (`BACKEND_LLM_MODEL`) | `--setup` (from the container) |
| Backend Reasoning | e.g. `on (enable_thinking, reasoning_budget 1024)` | `--setup` (text agent `agent.yaml`) |
| Frontend LLM | frontend model (`FRONTEND_LLM_MODEL`); `none (backend-only)` for the `bo` arm | `--setup` |
| Frontend Reasoning | e.g. `off (enable_thinking false)`; `n/a` for `bo` | `--setup` |
| Voice Agent ASR | the agent's ASR model | `--setup` (container log `ASR asr: ... model=`) |
| Voice Agent TTS | the agent's TTS model and voice | `--setup` (container log `TTS tts: ... model=`) |
| User simulator LLM | `--user-llm` of the run | tau2 `results.json` |
| Judge LLM | `TAU2_JUDGE_MODEL` | `.env` / environment when the script runs |
| Tau TTS | the user simulator's TTS | run console log (`USER TTS OVERRIDE`, I0) |
| Pass^1 | pass^1 over the run's tasks (1 trial) | tau2 `compute_metrics` |
| Responsiveness | `R_R` response rate · `R_Y` yield rate | tau2 interaction metrics |
| Latency | `L_R` response latency · `L_Y` yield latency (s) | tau2 interaction metrics |
| Interrupts | `I_A` agent-interrupts-user rate | tau2 interaction metrics |
| Selectivity | `S_BC` backchannel · `S_VT` vocal tic · `S_ND` non-directed | tau2 interaction metrics (`regular` only) |
| Mean realtime response latency (s) | mean over answered user turns of *end of the user's speech → the agent's first answer audio*, in real (wall-clock) time, **minus the time the agent waited for tau2 to return tool results**. It includes VAD endpointing, ASR, every frontend and backend LLM step, and TTS first audio: everything the agent itself spends. | agent event log (I2) |
| Frontend Mean Per-turn Latency (s) | mean over turns in which the frontend ran of the frontend LLM time in that turn (`agent_turn_done.frontend.latency_ms`, I1); `n/a` for `bo` | agent event log (I1 + I2) |

The script adds four more columns after these: backend mean per-turn LLM latency, frontend filler voice
latency (projected), and FE/BE tokens per task. Report them too; they are the metrics in §0.

**Why not just tau2's `L_R`, or the raw wall-clock answer time?** tau2's simulated clock runs slower
than real time: each 200 ms tick takes about 400–450 ms of wall time in these runs (the report's
`sim_time_to_wall_time`, about 0.45–0.5), and single ticks can stall for seconds. The agent's LLMs
keep running in real time while tau2's clock is stopped, so **`L_R` understates** how long a real user
would wait. The raw wall-clock answer time **overstates** it, because it also includes tau2's slow
tool round trips (up to 8.8 s for one tool result in the `mock` smoke run, against ~50 ms at other
times). *Mean realtime response latency* removes the tool waits and keeps everything the agent does,
so it is the best estimate of the real-time response latency. Report all three; the report's latency
detail lists the raw wall-clock time and the tool waits next to it.

## 8. How to read the numbers

- **tau2's clock runs at about half of real time** in this setup (see §7.3). Compare tau2's latency
  metrics (`L_R`, `L_Y`) only between the arms of this campaign, and use *Mean realtime response
  latency* for the real-time answer delay.

- **L_R vs filler voice latency.** L_R is when the user hears the **answer**. In τ³ the filler is
  silent, so tau2 never hears it. Filler voice latency is when the user **would** hear the filler in
  production (`filler.mode: speak`). Its TTS part is estimated from the answer TTS of the same run
  (median time from the answer text being ready to its first audio; about 80 ms in the smoke runs),
  which is why it is labelled *projected*.
- **Filler would be heard.** A filler is only spoken if the backend is still busy after
  `speak_after_ms` (300 ms). A low share means the backend usually answers first.
- **Backend turn latency** is backend LLM time only (no ASR, TTS or tool execution). In the paired arm,
  turns the frontend answered alone are excluded, and their count is shown. If the report says
  *derived*, I1 was missing and the value includes a few ms of overhead.
- **Tokens** are per simulation (one task × one trial), averaged. Reasoning tokens are included in
  completion tokens. Tokens of LLM calls cancelled by barge-in aren't recorded anywhere; the count of
  such steps is shown next to the totals.
- **Not comparable to the leaderboard's model rows as-is.** This is a cascade scaffold (ASR → two LLMs
  → TTS) with named models. Always name the frontend, backend, ASR and TTS models, the speech
  complexity, and `max_concurrency` next to any number.
- **Non-official user simulator.** The user speaks with `openai/openai/gpt-4o-mini-tts` (Hub voices,
  with accents from tau2's persona prompts), not tau2's ElevenLabs voices. Its LLM is
  `azure/openai/gpt-5.2`, and so is the judge. Results are comparable **between the arms of this
  campaign**, not to leaderboard numbers or to ElevenLabs-based runs.
- **S_VT (vocal tics) is approximate.** The tics are sound-words (a standalone tic is its own
  utterance; a tic inside a sentence is rendered in place, e.g. "five, (ah... ah-choo!) seven"), so the
  agent's ASR may hear them as speech and the agent may respond. S_BC and S_ND are unaffected, because
  those are spoken words anyway. Runs started before 2026-09-24 07:29 UTC, including the two
  `_smoke` runs, dropped in-sentence tics entirely.

## 9. Archive everything in the dump repo [dump]

Run this after each run, or once per campaign. It copies without deleting from the source (the source
logs are shared across runs). Run it once per `RUN`:

```bash
RUN=fba_voice_paired_airline_regular            # repeat for every run of the campaign
ARM=$(echo $RUN | cut -d_ -f3)                   # paired | bo
CONTAINER=$([ "$ARM" = paired ] && echo fba-voice || echo fba-voice-bo)
EVLOG=$([ "$ARM" = paired ] && echo fba_voice_events.jsonl || echo fba_voice_bo_events.jsonl)
FLOG=$([ "$ARM" = paired ] && echo fba_filler.jsonl || echo fba_voice_bo_filler.jsonl)
MODEL=pine-$(echo $RUN | tr '_' '-')
DEST=$DUMP/tau-3-voice/$CAMPAIGN/$RUN
mkdir -p $DEST/{tau2,agent/raw,agent/config,metrics,provenance}

# 1. tau2 results, trajectories, audio, task logs, console output
rsync -a $TAU2/data/simulations/$RUN/ $DEST/tau2/$RUN/
cp $TAU2/data/simulations/_consoles/$RUN.{log,start,end} $DEST/tau2/ 2>/dev/null
rsync -a $TAU2/data/simulations/_metrics/$RUN/ $DEST/metrics/ 2>/dev/null

# 2. Agent logs: this run's sessions (filtered by model tag), plus the full raw files
python3 - "$AGENT/logs/$EVLOG" "$AGENT/logs/$FLOG" "$MODEL" "$DEST/agent" <<'PY'
import json, sys
ev, fl, model, out = sys.argv[1:]
recs = [json.loads(l) for l in open(ev) if l.strip()]
sids = {r["session_id"] for r in recs if r.get("kind") == "session_start" and r.get("model") == model}
with open(f"{out}/events.jsonl", "w") as f:
    for r in recs:
        if r.get("session_id") in sids: f.write(json.dumps(r, ensure_ascii=False) + "\n")
try:
    with open(fl) as src, open(f"{out}/filler.jsonl", "w") as f:
        for l in src:
            if l.strip() and json.loads(l).get("session_id") in sids: f.write(l)
except FileNotFoundError:
    pass
print(f"{len(sids)} sessions for {model}")
PY
cp $AGENT/logs/$EVLOG $DEST/agent/raw/ ; cp $AGENT/logs/$FLOG $DEST/agent/raw/ 2>/dev/null
docker logs --since "$(head -1 $TAU2/data/simulations/_consoles/$RUN.start)" $CONTAINER \
  > $DEST/agent/docker_logs.txt 2>&1
docker logs --since "$(head -1 $TAU2/data/simulations/_consoles/$RUN.start)" nemotron-voice-agent-nemo-speech-1 \
  > $DEST/agent/nemo_speech_logs.txt 2>&1

# 3. Agent config actually used, and the container definition with secrets redacted
cp $AGENT/src/prototypes/voice_frontend_backend_agent/config/voice_agent.yaml \
   $AGENT/src/prototypes/voice_frontend_backend_agent/config/prompts.voice.yaml \
   $AGENT/src/prototypes/voice_frontend_backend_agent/config/profiles/*.yaml \
   $AGENT/src/prototypes/text_frontend_backend_agent/config/agent.yaml \
   $AGENT/src/examples/frontend_backend_agent/services.local.yaml  $DEST/agent/config/
docker inspect $CONTAINER | python3 -c '
import json, re, sys
d = json.load(sys.stdin)
for c in d:
    c["Config"]["Env"] = [e if not re.search("KEY|TOKEN|SECRET|PASS", e.split("=")[0]) else e.split("=")[0] + "=<redacted>" for e in c["Config"]["Env"]]
json.dump(d, sys.stdout, indent=2)' > $DEST/agent/container_inspect.json

# 4. Provenance: exact code of both repos, key names (never values)
for R in AGENT TAU2; do
  git -C ${!R} rev-parse HEAD            >  $DEST/provenance/${R,,}_commit.txt
  git -C ${!R} status --short            >  $DEST/provenance/${R,,}_status.txt
  git -C ${!R} diff                      >  $DEST/provenance/${R,,}_uncommitted.diff
done
sed -n 's/^\([A-Z0-9_]*\)=.*/\1/p' $TAU2/.env > $DEST/provenance/tau2_env_keys.txt
grep -E '^(TAU2_JUDGE_(MODEL|BASE_URL|JSON_MODE)|TAU2_USER_TTS_(MODEL|BASE_URL))=' $TAU2/.env \
  > $DEST/provenance/tau2_endpoints.txt                      # models and URLs only, no keys
cp $TAU2/misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py $TAU2/misc/prototypes/fba_voice_eval/tau2_ihub.py \
   $DEST/provenance/                                          # the user-TTS override actually used
grep -E '^(FRONTEND|BACKEND)_LLM_|^FBA_' <(docker inspect $CONTAINER --format '{{range .Config.Env}}{{println .}}{{end}}') \
  > $DEST/provenance/agent_llm_env.txt

du -sh $DEST
```

Once per campaign, after all runs are in, archive the comparison report from §7.2:

```bash
rsync -a $TAU2/data/simulations/_metrics/ $DUMP/tau-3-voice/$CAMPAIGN/_reports/
```

Write `$DUMP/tau-3-voice/$CAMPAIGN/README.md`, a run card with the date, arms, domains, speech
complexity, models (frontend, backend, ASR, TTS, user simulator, judge), commits of both repos,
`max_concurrency`, anything unusual, and the headline table from `fba_voice_report.md`.

**Layout:**

```
voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/
├── README.md                       run card
├── _reports/                       §7.2 outputs across arms (fba_voice_report.md, CSVs, JSON)
└── fba_voice_<arm>_<domain>_<cx>/
    ├── tau2/<run>/                 results.json, simulations/, artifacts/ (audio, task.log, labels)
    ├── tau2/<run>.log|.start|.end  console output and run window
    ├── metrics/                    agent_metrics.json, interaction_metrics.json
    ├── agent/events.jsonl          this run's agent sessions only
    ├── agent/filler.jsonl          this run's filler timing records (paired)
    ├── agent/raw/                  full event/filler logs as of archiving
    ├── agent/docker_logs.txt       agent container logs for the run window (agent failures live here)
    ├── agent/nemo_speech_logs.txt  ASR/TTS server logs for the run window
    ├── agent/config/               profiles, voice_agent.yaml, agent.yaml, prompts, speech catalog
    ├── agent/container_inspect.json   (secrets redacted)
    └── provenance/                 commits, status, uncommitted diffs, env key names
```

**Before committing to the dump repo:** audio makes runs large (`du -sh` above). If the campaign is
more than about 100 MB, set up Git LFS for audio first:

```bash
cd $DUMP
git lfs install && git lfs track "tau-3-voice/**/*.wav" && git add .gitattributes
git add tau-3-voice/$CAMPAIGN
git commit -m "tau-3-voice: $CAMPAIGN fba-voice paired + backend-only"
# git push   (pushes to gitlab-master; do this only when you intend to publish the campaign)
```

Never copy `.env` files. The steps above record key names only and redact the container's environment.

## 10. Stop [agent]

```bash
docker stop fba-voice-bo          # --rm removes it
# fba-voice and nemo-speech: stop per the agent runbook §5 only if nothing else needs them
```

## 11. Troubleshooting

| Symptom | Check |
|---|---|
| tau2 connects to api.openai.com / 401 from OpenAI | the model name must start with `pine-`; `PINE_REALTIME_BASE_URL` must be set (the helper sets it) |
| `Session configuration failed` | the agent rejected `session.update`; read `docker logs fba-voice` |
| Sessions from a run missing in the agent log | wrong port for the arm (8765 paired, 8767 backend-only) or the container was restarted with another `FBA_VOICE_EVENT_LOG` |
| Every turn fails; `agent turn N failed` or `401` in `docker logs` | `NVIDIA_API_KEY` in [agent] `.env` is missing or is not an Inference Hub `sk-...` key |
| `ELEVENLABS_API_KEY not found` | the run used `uv run tau2 run` instead of `tau2_ihub.py`, so I0 was not installed |
| `Incorrect API key provided: sk-…` with `Error in backchannel decision` / `interruption decision` | the decision calls went to api.openai.com: I0 was not loaded (plain `tau2 run`). The run finishes anyway, but its user never backchannels or interrupts. Delete it and rerun through `tau2_ihub.py` |
| `Missing Anthropic API Key` after a simulation | the hallucination check used tau2's default `claude-opus-4-5`: I0 was not loaded, or `--review-model` was dropped |
| Every task is an infrastructure error: `No module named 'pyaudio'` | `sudo apt install portaudio19-dev`, then `uv sync --extra voice --extra dev` (§2.1) |
| User TTS: `404 Not Found` | `TAU2_USER_TTS_BASE_URL` must end at `/v1`, not `/v1/audio/speech`; TTS uses `audio.speech.create`, not chat completions |
| User TTS: `This is not a chat model` | something sends the TTS model to `/chat/completions`; only I0 should use it |
| `403 key not allowed to access model` | the `sk-` key isn't enabled for that model on the Hub (seen for `gpt-4o-transcribe` and `whisper-1`; `gpt-4o-mini-tts` and `azure/openai/gpt-5.2` work) |
| User LLM or judge calls go to Azure / fail with Azure auth errors | the `openai/` prefix is missing: use `openai/azure/openai/gpt-5.2` with `api_base` |
| Selectivity metrics empty | the run used `--speech-complexity control` |
| Long dead air, then the simulation ends early | backend latency vs tau2's inactivity limit (`DEFAULT_AUDIO_NATIVE_MAX_INACTIVE_SECONDS = 40`); compare with the backend turn p90 in the report. Report it as an agent result; don't change tau2. |
| Filler audible in `both.wav` | the container is not running `tau3_eval.yaml` / `backend_only.yaml` (`filler.mode: speak`); the run is invalid for Pass^1 |
| Report says backend latency *derived* / tokens *combined only* | I1 not applied before the run (§3); expected for runs made before it existed |
| The tau2 process dies with `RuntimeError: Not connected to API` (the agent log shows `session closed` with no error) | a long tau2 freeze (a slow user TTS/LLM call inside its tick loop) stopped tau2 from reading the socket, so the agent server's WebSocket keepalive closed it. Fixed on 2026-09-24: `server.ws_ping_interval_s: 0` in `tau3_eval.yaml`/`backend_only.yaml` (agent) and a 15 s timeout on I0's TTS client. If it happens again, check that both containers were restarted after the fix, then rerun the same `tau3_run` command with `--auto-resume`: finished tasks are kept and infrastructure errors are rerun |
| Airline runs are slow (5–10 min per task) | expected: tau2 freezes its clock during the simulated user's interruption/backchannel decisions and interruption TTS (2–15 s each on the Hub), about 35–47% of each run. There is no faster Hub decision model for this key (`gpt-4.1-mini` is slower, `gpt-4.1-nano` is not enabled) |
| C4 = WARN, agent tokens > tau2 `agent_usage` | expected when the user barged in: tau2 never receives the usage of a response that was cut off. The report uses the agent's (complete) count. C4 = FAIL means the agent's log is missing usage |
| Join check C1 fails | concurrency > 1, or several runs used the same model tag; rerun with a unique tag |
| `/health` stays 503 after a restart | nemo-speech is down or unreachable; see agent runbook P3/P4 |

## 12. What to record when you report

Frontend, backend, ASR and TTS models of the agent; the user simulator LLM (`azure/openai/gpt-5.2`),
the user simulator TTS (`openai/openai/gpt-4o-mini-tts`, via I0), the user's backchannel/interruption
decision model (`azure/openai/gpt-4.1`, via I0), the judge and the hallucination-check model
(`azure/openai/gpt-5.2`), all on the Inference Hub; reasoning settings (frontend off, backend
on with budget 1024); speech complexity; domains and split; trials; `max_concurrency`; commits of both
repos and whether either was dirty; whether I1 was in place; the filler TTS estimate being *projected*;
failed checks; and the dump path `voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/`.
