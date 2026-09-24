# Integration plan: Voice Frontend/Backend Agent on τ³-bench (voice, full-duplex)

**Date:** 2026-09-24 · **Status:** implemented (2026-09-24), uncommitted in both repos. I0, I1 and I2 are built and tested, I3 (`fba-voice-bo`) is running, and `mock` smoke runs of both arms pass every check (§2.1) ·
**Runbook:** [`voice-frontend-backend-agent-tau3-runbook.md`](voice-frontend-backend-agent-tau3-runbook.md) ·
**Generic Realtime integration doc:** [`voice-custom-agent-openai-realtime-integration.md`](voice-custom-agent-openai-realtime-integration.md)

This plan covers what has to be added so that a τ³ voice run against the prototype
(`nemotron-voice-agent-smasurekar/src/prototypes/voice_frontend_backend_agent`, running as the
`fba-voice` container) produces every metric the evaluation needs. **`src/tau2/` is not modified.**
Everything new is either a small change in the agent repo or a post-processing script under
`misc/prototypes/` in this repo.

| Tag | Directory |
|---|---|
| **[agent]** | `/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar` |
| **[tau2]** | `/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar` |
| **[dump]** | `/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump` |

---

## 1. Verdict

| Area | Integration needed? | Why |
|---|---|---|
| User simulator TTS on the Inference Hub (`openai/openai/gpt-4o-mini-tts`) instead of ElevenLabs | **Yes: I0** (runtime override, no `src/tau2` change) | tau2 supports only ElevenLabs for the user's voice (`src/tau2/voice/synthesis/synthesize.py:22`). I0 swaps the one function that calls ElevenLabs when the process starts (§4.0). It was tested inside tau2 on all 7 personas (§2.1). |
| User simulator LLM and judge LLM on the Inference Hub (`azure/openai/gpt-5.2`) | **No** | Plain configuration: `--user-llm openai/azure/openai/gpt-5.2` plus `api_base`, and `TAU2_JUDGE_*`. Tested through tau2's own `generate()` (§2.1). |
| tau2's **hardcoded** LLM calls: the voice user simulator's backchannel and interruption decisions (`gpt-4.1`), and the post-run user-simulator hallucination check (`claude-opus-4-5`) | **Yes: part of I0** | These calls ignore `--user-llm-args` and send no `api_base`, so with only a Hub key they fail. tau2 then **silently** degrades: the simulated user never backchannels or interrupts, and hallucinated conversations are never detected or re-run. This was observed in the first smoke run (§2.1). I0 routes them to the Hub (§4.0). |
| Wire protocol (tau2 ↔ agent) | **No** | The server speaks OpenAI Realtime GA. tau2 reaches it with any `pine-*` model name through `PINE_REALTIME_BASE_URL` (`src/tau2/voice/audio_native/openai/provider.py:38-41`, `:139-149`). The prototype ships Gate A/B checks against tau2's real provider (`cli/tau2_gates/`). |
| Agent behaviour for τ³ | **No** | `fba-voice` runs `config/profiles/tau3_eval.yaml`: paired mode, `filler.mode: log_only` (filler never reaches tau2's audio/transcript), `tools.source: client` (tau2 executes tools against its domain DB), no greeting, and tau2's greeting seeded into history. |
| Backend-only arm | **No code**, ops only | `config/profiles/backend_only.yaml` exists. It needs a second container on another port (§4.3). |
| Pass^1, Responsiveness, Latency, Interrupts, Selectivity | **No** | Computed by tau2 from the tick trajectories (`tau2.metrics.agent_metrics.compute_metrics`, `tau2 submit interaction-metrics`). |
| Per-turn backend LLM latency | **No for a derived value; yes for an exact one** | Derivable from `agent_turn_done.latency_ms` minus the filler record's `frontend_latency_ms` (§3.3). The exact per-role value comes from I1 (built). |
| Frontend filler voice latency | **No agent or tau2 change**; needs the metrics script (I2) | Every delegation already writes a `filler_timing` record with `filler_ready.since_turn_end_ms`; the endpointing delay comes from `speech_stopped` (§3.4). |
| Tokens per task, **frontend vs backend** | **Yes: I1** (built) | Before I1 the server logged only combined tokens (`agent_turn_done.input_tokens/output_tokens`, `step_usage.total_tokens`, `response.done.usage`). The per-role split exists in memory (`UsageTotals.frontend/.backend`) but is dropped in `agent/runner.py:_reply`. |
| Joining server logs to tau2 tasks | **Yes: part of I2** | Server sessions carry no tau2 task id. They are joined by the model tag, tool `call_id`s and time windows (§3.2). |
| Archiving to the dump repo | Ops only | Commands in the runbook, §9. |

So there are **three build items**:

- **I0:** runs tau2's voice user simulator entirely on the Inference Hub: its TTS, plus the hardcoded
  decision and review LLM calls. Two small files in `misc/prototypes/fba_voice_eval/` (~150 lines).
  **Built**, 16 offline tests (§4.0).
- **I1:** per-role usage and latency in the agent's event log (agent repo, ~50 lines). **Built**, 2
  new unit tests, live in both containers (§4.1).
- **I2:** a metrics script in this repo that joins tau2 results with the agent's event log (~850
  lines). **Built**, 9 offline tests (§4.2).

None of them changes `src/tau2`.

**Inference Hub endpoints used on the tau2 side.** One `sk-...` key is used for all three: the shell's
`OPENAI_API_KEY`, or the optional `TAU2_USER_TTS_API_KEY` / `TAU2_JUDGE_API_KEY`.

| Role | Base URL | Hub model id | How tau2 refers to it |
|---|---|---|---|
| User simulator LLM | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-5.2` | `--user-llm openai/azure/openai/gpt-5.2`, `api_base` in `--user-llm-args` |
| Judge LLM (NL assertions) | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-5.2` | `TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2`, `TAU2_JUDGE_BASE_URL` |
| User simulator TTS | `https://inference-api.nvidia.com/v1` (the SDK appends `/audio/speech`) | `openai/openai/gpt-4o-mini-tts` | I0: `TAU2_USER_TTS_MODEL`, `TAU2_USER_TTS_BASE_URL` |
| User simulator backchannel / interruption decisions | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-4.1` (tau2's own default model, `VOICE_USER_SIMULATOR_DECISION_MODEL = "gpt-4.1"`, served by the Hub) | I0: `TAU2_USER_DECISION_MODEL`, `TAU2_USER_DECISION_BASE_URL` |
| User-simulator hallucination check (and `--auto-review`) | `https://inference-api.nvidia.com/v1` | `azure/openai/gpt-5.2` (the judge model; tau2's default is `claude-opus-4-5`) | `--review-model openai/azure/openai/gpt-5.2`, and I0: `TAU2_REVIEW_MODEL`, `TAU2_REVIEW_BASE_URL` |

The `openai/` prefix on the LLM ids tells LiteLLM to use the OpenAI-compatible protocol against
`api_base`. Without it, LiteLLM reads `azure/...` as "call Azure directly".

---

## 2. What was checked

| Checked | Finding |
|---|---|
| `docker inspect fba-voice` | Image `nemotron-voice-agent:latest`, command `server --config .../profiles/tau3_eval.yaml --port 7860`, host port 8765, `FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning`, `BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra`, `FBA_VOICE_EVENT_LOG=logs/fba_voice_events.jsonl`, `FBA_FILLER_LOG=logs/fba_filler.jsonl`. `./src` is bind-mounted **read-only** at `/app/src`, so a code change is picked up by restarting the process (`docker restart fba-voice`); no image rebuild is needed. `./logs` is bind-mounted read-write. |
| `fba-voice-web` (port 8766) | The browser demo (`browser_demo.yaml`, internal demo tools). **Not used for τ³.** |
| Agent git state | `44f33dc`, clean working tree at the time of writing. |
| Existing event log (`[agent] logs/fba_voice_events.jsonl`) | Kinds seen: `session_start` (with `model`), `session_updated`, `speech_started/stopped`, `asr_final`, `agent_turn_start/done`, `turn_latency`, `delegation`, `filler`, `filler_timing`, `direct_answer`, `backend_tool_calls`, `tool_calls_out`, `tool_output_in` (with `call_id`), `backend_final`, `step_usage`, `response_done`, `barge_in`, `truncate`, `thinking_cancelled`, `history_repaired`, `frontend_repair`, `frontend_contract_violation`, `session_end`. |
| tau2 per-simulation data | `SimulationRun` has `task_id`, `trial`, `start_time`/`end_time` (naive local-time ISO), `duration`, `agent_usage` (from `response.done.usage`, combined FE+BE), and `ticks[].agent_tool_calls[].id`, which equals the agent's backend `call_id`. |
| tau2 tick pacing | Each tick lasts **at least** 200 ms of wall time (`src/tau2/voice/audio_native/adapter.py:295-299`), so the agent's input-audio clock and wall clock stay close. Latencies from the two sides are comparable. |
| `.env` in [tau2] | Holds `TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2`, `TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1`, `TAU2_JUDGE_JSON_MODE=0`. The shell has `OPENAI_API_KEY` (an Inference Hub `sk-` key). ElevenLabs is not used (I0), so **no** `ELEVENLABS_API_KEY` or `TAU2_VOICE_ID_*` is needed. `PINE_*` are set per run by the runbook helper. See runbook §2. |
| tau2 venv | The `voice` extra needs the system package `portaudio19-dev` to build `pyaudio`. **pyaudio is required at run time:** `src/tau2/voice/utils/audio_io.py:7` imports it, and a run without it ends every task as an infrastructure error (`No module named 'pyaudio'`, observed 2026-09-24). Installed on 2026-09-24 (`portaudio19-dev`, `ffmpeg`, `uv sync --inexact --extra voice --extra dev`). |
| Every LLM call in a voice run (by `call_name`) | Default path: `user_streaming_response` (`--user-llm`), `backchannel_decision` and `interruption_decision` (**hardcoded** `gpt-4.1`, no `api_base`), `nl_assertions_eval` (`TAU2_JUDGE_*`), `llm_judge_hallucination_check` (`--review-model`, default `claude-opus-4-5`, **no `api_base`**; on by default in full-duplex, `--hallucination-retries 3`). Only with `--auto-review`: `llm_judge_*review`, `classify_authentication` (same review model). The agent side makes no tau2 LLM calls. |

### 2.1 Endpoint tests (2026-09-24, with the shell's `sk-` key)

| Test | Result |
|---|---|
| TTS snippet **as given**: `OpenAI(base_url=".../v1/audio/speech").chat.completions.create(model="openai/openai/gpt-4o-mini-tts", messages=...)` | **Fails: 404 Not Found.** Two errors: the base URL must stop at `/v1`, because the SDK appends the route itself; and a TTS model is not a chat model. |
| The same with `base_url=".../v1"` | **Fails: 404**, `This is not a chat model and thus not supported in the v1/chat/completions endpoint` |
| **Correct TTS call:** `OpenAI(base_url=".../v1").audio.speech.create(model="openai/openai/gpt-4o-mini-tts", voice="ash", input=..., instructions=..., response_format="pcm")` | **Works.** It returns raw PCM16, 24 kHz, mono (confirmed with `response_format="wav"`: 24000 Hz, 1 ch, 16-bit). `wav` and `mp3` also work. It takes 1–8 s per utterance. |
| User LLM via tau2 `generate(model="openai/azure/openai/gpt-5.2", api_base=".../v1", temperature=0)` | **Works** (~2 s) |
| Judge via tau2 `generate(model=$TAU2_JUDGE_MODEL, **DEFAULT_LLM_NL_ASSERTIONS_ARGS)` | **Works** (~2.6 s), returns the JSON object |
| I0 override through tau2's own `synthesize_voice()` with `ELEVENLABS_API_KEY` unset: 7 personas, 3 vocal tics, a non-directed phrase, a `[pause]` tag | **Works.** All clips come back as `pcm_s16le 16000 Hz` (tau2's expected format). All 7 persona clips transcribe back **word for word**, as do the non-directed phrase and the `[pause]` line. |
| Vocal tics **inside** a sentence (tau2's in-turn tics, e.g. `nine, five, .[sneeze][sneeze][sneeze] seven`) | **Were dropped, now fixed.** The first I0 only rendered utterances that are nothing but tics (tau2's out-of-turn inserts); tags inside a sentence were stripped, so the agent never heard them and S_VT would have been too high. Found in the `regular` smoke runs' `user_labels.txt`. I0 now renders them in place as sound-words in parentheses, with an instruction to perform them as brief sounds. Live check through the Hub: ASR heard "995-A-Achoo-7", "Are... sniff sniff... you still there?", "hmmhmmhmmEmma_kim". The two `regular` smoke runs were made before this fix. |
| Vocal tics (`.[cough][cough][cough]` etc.) | **Approximate.** gpt-4o-mini-tts speaks tag text as words (ASR heard "sneeze sneeze sneeze", and "咳嗽", Chinese for "cough"). I0 therefore renders sound-words instead ("Ahem! Hkh-hkh…", "Ah… ah… ah-choo!", "Sniff… sniff."). ASR then hears "Ahem.", "A, a, achoo!", "嗅嗅". These are closer to sounds but aren't true non-speech. See §6. |
| Full `mock` run with I0, first attempt | Ended as an infrastructure error (`No module named 'pyaudio'`) until pyaudio was installed. |
| Full `mock` run with TTS-only I0, second attempt | **Ran to completion, but was invalid.** The logs held 47 `AuthenticationError`s: every `backchannel_decision` went to api.openai.com with the Hub key ("Incorrect API key provided"), and the hallucination check went to Anthropic ("Missing Anthropic API Key"). tau2 caught both and carried on with degraded behaviour. The run was deleted, and I0 was extended to route these calls. |
| Hub availability of tau2's decision model | `openai/azure/openai/gpt-4.1` **works** (~1.4 s, answers `YES`). `openai/openai/gpt-4.1` returns **403** (not enabled for this key). |
| Full `mock` run with complete I0 (paired arm, `control`, 1 task) | **Passed.** Console shows all three `OVERRIDE` lines and **0** `AuthenticationError`. Reward 1.0 (DB 1.0, COMMUNICATE 1.0), `user_stop`, 50 s, 128 ticks. Agent tool calls `get_users` and `create_task`; both `call_id`s are in the agent's event log (the I2 join key works). Hallucination check ran on the Hub, with reasoning text. Filler `log_only`, `spoken: false`, `since_turn_end_ms` 510. The §7.1 commands work: Pass^1 = 1.0, 0 infra errors; interaction metrics L_R 2.60 s, R_R 100%, I_A 0% (one user turn, so the other metrics have no events). Run kept as `data/simulations/fba_voice_paired_mock_control`. |
| Paired `mock` run with I0 **and I1** (`fba_voice_paired_mock_control`) | **Passed.** Reward 1.0, `user_stop`, 204 s, 3 `OVERRIDE` lines, 0 auth errors. Every `agent_turn_done` has `step`, `frontend` and `backend`. I2: all checks C1–C8 PASS; exact and derived backend latency agree (C3, 0.0%); FE + BE tokens equal tau2's `agent_usage` (C4). L_R 4.80 s, backend turn latency 3.81 s mean (p90 5.75 s), filler voice latency 2.49 s projected, 7993 FE / 9729 BE tokens. The pre-I1 run is kept as `fba_voice_paired_mock_control_preI1` (C2 = WARN, as expected). |
| Backend-only `mock` run (`fba-voice-bo`, `fba_voice_bo_mock_control`) | **Harness passed; agent failed the task.** `user_stop`, 144 s, no filler file, all checks PASS. Reward 0.0 (DB 0): the user paused mid-title, the agent's VAD committed the turn, and the backend created "Meeting" instead of "Important Meeting". L_R 2.07 s, backend turn latency 1.71 s, 10505 BE tokens. In both arms many turns were cancelled by the agent's own turn detection (a `[pause]` in the user's speech splits one utterance into several turns). |
| `regular` airline smoke, 1 task per arm (`fba_voice_<arm>_airline_regular_smoke`) | **Passed the gate.** Both reward 1.0 (task 0: the user gives up, nothing may be cancelled), 3 `OVERRIDE` lines, 0 auth errors, hallucination check on the Hub, accented persona prompt in use, vocal tics present in `user_labels.txt`. Paired 217 s, backend-only 490 s. Metrics: all checks pass except C4 = WARN (below). |
| **tau2's simulated clock vs real time** | In every run so far, tau2's ticks (200 ms of simulated time each) took 360–500 ms of wall time on average (`sim_time_to_wall_time` 0.36–0.51), and single ticks stalled for up to ~9 s (seen as a tool result arriving 8.8 s after the call while the agent's input-audio clock moved 200 ms). The agent's LLMs keep running in real time during these stalls, so tau2's `L_R`/`L_Y` understate real-time latency; the raw wall-clock answer time overstates it (it includes tau2's tool round trips). §3.6 defines the real-time latency that avoids both. Both arms are affected alike. |
| **Full airline run, first attempt (08:10 UTC)** | **Crashed after 4 tasks.** tau2 synthesizes an interrupting user's speech inside its tick loop; one Hub TTS call hung for 88 s (I0's client had the SDK default of 600 s × 3 attempts). tau2 stopped reading the socket while the agent streamed audio, the agent server's uvicorn keepalive (ping 20 s / timeout 20 s) closed the session, and the next tick raised `Not connected to API`, which ended the whole tau2 process. **Fixes:** I0's TTS client now times out after 15 s with no SDK retries (tau2's `@tts_retry` still retries 3×); the agent got `server.ws_ping_interval_s` / `ws_ping_timeout_s` (default 20 / 20), and both τ³ profiles set the interval to 0 (keepalive off). 211 agent tests and 32 tau2-side tests pass. The run was resumed with `--auto-resume` (tasks 0–3 kept; task 4 rerun). |
| Where the airline wall time goes | Normal ticks take 213 ms. Freezes of 2–24 s (up to 88 s once) come every 2 s while the agent speaks and when the user starts to speak: tau2's synchronous interruption/backchannel decisions (Hub `gpt-4.1`, 2–15 s with the full conversation as prompt) and interruption TTS. They make up 35–47% of each simulation. `gpt-4.1-mini` on the Hub is slower (median 3.5 s, max 17.6 s); `gpt-4.1-nano` is not enabled for the key. |
| C4 token cross-check on the `regular` smokes | Agent-side tokens exceeded tau2's `agent_usage` (paired 95,051 vs 80,161). The session had 13 completed agent steps but 9 `response.done`: responses the user cut off by barge-in never deliver their usage to tau2. The agent's count is the complete one and is used; C4 is now WARN in that case and FAIL only when the agent reports fewer tokens than tau2 or there was no barge-in. |
| I0 routing through tau2's own module globals | `backchannel_decision` → `'YES'`, `interruption_decision` → `'NO'`, `llm_judge_hallucination_check` (requested as `claude-opus-4-5`) → Hub `gpt-5.2`, JSON reply. A call with another `call_name` is left untouched. |
| Other audio models on this key | `gpt-4o-transcribe` and `whisper-1` return **403** ("key not allowed to access model"). `gpt-4o-mini-transcribe` works and was used for the checks above. |

---

## 3. Metric definitions

Every metric is computed per **arm** (`paired` or `backend_only`) × **domain** × **speech complexity**.

### 3.1 From tau2 alone (no integration)

| Metric | Field / source | Notes |
|---|---|---|
| **Pass^1** | `compute_metrics(Results.load(run_dir))` (`src/tau2/metrics/agent_metrics.py:202`) | reward == 1 counts as success. Infrastructure errors are reported separately. |
| **Latency** L_R, L_Y | `response_latency_mean`, `yield_latency_mean` from `tau2 submit interaction-metrics` | Seconds, from ticks. L_R is what a user hears for the **answer** (filler is silent in `log_only`). |
| **Responsiveness** R_R, R_Y | `response_rate`, `yield_rate` | |
| **Interrupts** I_A | `agent_interruption_rate` | |
| **Selectivity** S_BC, S_VT, S_ND | `selectivity_*` | Needs `--speech-complexity regular`. `control` has no backchannels, vocal tics or non-directed speech, so these come out empty. With I0, **S_VT is approximate**: the tics are TTS sound-words, not ElevenLabs v3 non-speech (§2.1). |
| Combined agent tokens per task (cross-check only) | `SimulationRun.agent_usage` | Sum of `response.done.usage`. Must equal FE + BE from I1 (check C4 in §4.2). |

### 3.2 Joining agent sessions to tau2 simulations

The agent writes one `session_id` per WebSocket connection. tau2 opens one connection per simulation
(plus retries: `DEFAULT_AUDIO_NATIVE_MAX_RETRIES = 3`).

1. **Filter by run tag.** Each run uses its own model name, e.g. `--audio-native-model
   pine-fba-paired-airline-regular`. tau2 sends it as `?model=`. The agent logs it in
   `session_start.model` and accepts any name. Only sessions whose `model` equals the run's model are
   considered. The shared log file can then hold many runs.
2. **Exact match by tool call id.** A session matches a simulation if any `tool_output_in.call_id`
   in the session equals any `ticks[].agent_tool_calls[].id` in the simulation.
3. **Time-window match** for sessions without tool calls. Pick the simulation whose
   `[start_time − 30 s, end_time + 30 s]` contains the session's `[session_start, session_end]` with
   the largest overlap. tau2 times are naive local time and agent timestamps are epoch seconds. Both
   come from the same host, so convert tau2's with the local timezone.
4. **Retries.** Several sessions can match one simulation. The simulation's metrics use the **last**
   one (the one that ran the conversation). Earlier ones are counted as `retried_sessions`.
5. **Report** unmatched sessions and unmatched simulations. At `--max-concurrency 1`, steps 2 and 3
   must give a 1:1 mapping. Anything else is a failed check (C1).

### 3.3 Mean per-turn backend LLM latency (s)

A **user turn** is `(session_id, turn_id)`. A turn can span several agent steps: the first
`respond()` and one `resume()` per tool round, all logged as `agent_turn_done` with the same
`turn_id`.

- **Definition, the same in both arms:** backend LLM time summed over every step of the turn, averaged
  over turns that did backend work. Mean, p50 and p90 are reported.
  - *Paired:* turns the frontend answered by itself (`direct_answer`) have no backend work and are
    excluded. Their count is reported.
  - *Backend-only:* every turn is backend work.
- **Exact (after I1):** `Σ agent_turn_done.backend.latency_ms` per turn. This is the sum of the
  backend's LLM call latencies as measured by the text prototype's client (`RoleTotals.latency_ms`).
- **Derived (works today, and is the fallback):**
  - backend-only: `Σ agent_turn_done.latency_ms` per turn.
  - paired: `(first step's latency_ms − filler_timing.filler_ready.frontend_latency_ms) + Σ later
    steps' latency_ms`. The first step runs the frontend and then the backend. `frontend_latency_ms`
    is the time from the step's start to the frontend's `call_backend` decision, and later steps
    are backend-only.
  - The derived value includes a few ms of Python overhead. The script reports both values and their
    difference.
- **Not counted:** steps cancelled by barge-in (`thinking_cancelled`) never log `agent_turn_done`.
  Their count is reported. Tool execution time is tau2's and is not included, because a `resume`
  step starts when the tool output arrives.
- **Also reported, not headline:** the agent-side answer latency `turn_latency.user_stop_to_first_audio_ms`
  (VAD endpoint → first answer audio), split into ASR, LLM and TTS parts. It explains where L_R goes.

### 3.4 Frontend filler voice latency (paired arm only; no tau2 change)

In `log_only` mode the filler text is produced and time-stamped but never synthesized. The
metric is the time from **the end of the user's speech** to **the moment the user would hear the
filler**. It is on the same scale as tau2's L_R, so the two can be compared directly.

For every delegated turn with non-empty filler text (`filler_timing` record, `text != ""`):

```
filler_voice_latency_ms  =  endpointing_ms  +  filler_text_ms  +  tts_first_audio_ms
```

| Term | Source | Exact / estimated |
|---|---|---|
| `endpointing_ms`: user stops speaking → VAD commits the turn | the session's last `speech_stopped` at or before `filler_timing.turn_end.audio_ms`: `audio_ms − audio_end_ms` (both on the input-audio clock) | exact |
| `filler_text_ms`: turn committed → frontend's filler text exists (includes ASR finalization and the frontend LLM call) | `filler_timing.filler_ready.since_turn_end_ms` | exact |
| `tts_first_audio_ms`: filler text → first TTS audio | per run and arm: median, over answered turns, of `turn_latency.timestamp − timestamp` of the turn's last `agent_turn_done` with `outcome == "text"` (answer text ready → first answer audio; the same TTS path, measured on answer sentences). Fallback: `first_answer_audio − backend_done` of `filler_timing` records with `outcome == "answer"`. | **estimated** (projection) |

Also reported:

| Field | Definition |
|---|---|
| `filler_text_latency` | `endpointing_ms + filler_text_ms`: the exact part, before TTS |
| `frontend_latency` | `filler_ready.frontend_latency_ms`: the frontend LLM call alone |
| `filler_present` | delegated turns with filler text ÷ delegated turns |
| `filler_would_be_heard` | share of delegated turns where the backend was still busy after `speak_after_ms` (`would_have_spoken == true`), i.e. turns where a real user would hear the filler |
| `time_to_first_audio_projected` | per turn: FVL if the filler would be heard, otherwise the answer latency (`endpointing_ms + turn_latency.user_stop_to_first_audio_ms`). This is the paired arm's "how soon does the user hear something" number. The backend-only arm's value is its answer latency. |
| `answer_latency_saved` | answer latency − FVL, over turns where the filler would be heard |

**Why not only the filler records for the TTS term** (the first version of this plan): a filler record is
written when the turn's first step ends. If the backend calls a tool, which it does in almost every tau2
turn, the record is written before any answer audio exists, so `first_answer_audio` stays null and there
are no samples. The `agent_turn_done` → `turn_latency` pair exists for every answered turn in both arms
(about 80 ms in the smoke runs).

**Optional validation arm.** A small separate run (e.g. 5 airline tasks) against a third container with
`filler.mode: speak` makes the filler audible. tau2's own L_R then measures first-sound latency, and the
agent's `filler_timing.filler_audio_start` records the real TTS time. Comparing it with the projected FVL
of the same tasks bounds the error of `tts_first_audio_ms`. **This arm is excluded from Pass^1:** in
`speak` mode the filler is sent to tau2 as agent speech and transcript, so it changes the conversation
that is graded.

### 3.5 Average token usage per task, frontend and backend

Per simulation (one task × one trial), summed over every completed agent step of the matched session.
Reported as the mean over simulations and in a per-task CSV:

| Role | Fields |
|---|---|
| frontend | calls, prompt, completion, cached prompt, total |
| backend | calls, prompt, completion, cached prompt, total |

- Needs I1. Without it, only the combined total is available, from `agent_turn_done` or tau2's
  `agent_usage`. For the backend-only arm the combined total *is* backend usage.
- Reasoning tokens are not separated. The text prototype's `Usage` has no reasoning field, so they are
  part of `completion`. The text-to-text runbook separates them because it reads LiteLLM responses; that
  path does not exist here.
- **Undercount:** LLM calls cancelled by barge-in (`thinking_cancelled`) consumed tokens that no one
  records. The count of cancelled steps is reported next to the totals.
- ASR and TTS (nemo-speech) are not token-metered and are not included.

### 3.6 Results-table latencies (added 2026-09-24)

The results table (runbook §7.3) has two more latency columns.

- **Mean realtime response latency (s)**, both arms: per answered user turn, *end of the user's speech
  → the agent's first answer audio* on the wall clock (`endpointing_ms +
  turn_latency.user_stop_to_first_audio_ms`), **minus the time spent waiting for tau2's tool results**:
  for each step after the first, `step start − previous step's end`, where a step's start is its
  `agent_turn_done.timestamp − latency_ms`. It keeps everything the agent spends (endpointing, ASR,
  every LLM step, TTS first audio) and drops the harness's tool round trips. Averaged over answered turns.
- **Frontend Mean Per-turn Latency (s)**, paired arm: per user turn in which the frontend ran, the sum of
  `agent_turn_done.frontend.latency_ms` (I1). Without I1 it falls back to the filler record's
  `frontend_latency_ms` (delegated turns) or the step's `latency_ms` (direct turns). `n/a` for
  backend-only.

Why not tau2's `L_R` alone: see the clock row in §2.1.

---

## 4. Build items

### 4.0 I0: the voice user simulator on the Inference Hub (TTS and hardcoded LLM calls) [tau2]

**Why an override works.** Every user-side synthesis goes through
`tau2.voice.synthesis.synthesize.synthesize_voice()`. That covers normal turns
(`agent/base/voice.py:139`) and out-of-turn inserts such as vocal tics and non-directed phrases
(`voice/synthesis/audio_effects/speech_generator.py:84`). `synthesize_voice()` calls
`tts_elevenlabs(text, config)`, which it looks up as a **module global at call time**. Replacing that
one global before tau2 starts routes all user speech to the Hub. tau2's retry wrapper
(`@tts_retry`) still applies. Simulations run in threads of one process (`ThreadPoolExecutor`), so
the patch covers all of them. The user's TTS runs on the user simulator's background executor
(`user/user_simulator_streaming.py:512`), not inside the tick loop. Its latency delays when the
simulated user starts speaking; it does not add to the agent's measured latency.

**Persona mapping.** tau2 passes the persona's official ElevenLabs voice id in `config.voice_id`.
`get_persona_name_by_voice_id()` maps it back to the persona. The persona gets a gender-matched
gpt-4o-mini-tts voice, and **its own voice-design prompt from tau2** (`VoicePersona.prompt`: accent,
age, pace, mood) as the `instructions` parameter. So the accented `regular` personas are still
accented. Don't set `TAU2_VOICE_ID_*`: the mapping relies on the built-in ids.

**Hardcoded LLM calls.** Each caller does `from tau2.utils.llm_utils import generate` and looks the name
up at call time, so I0 wraps `generate` **in each calling module**. The wrapper matches on the call's
`call_name`, replaces the model, and adds the Hub `api_base`:

| Module | `call_name` | tau2's model | Routed to | Effect if left unrouted with only a Hub key |
|---|---|---|---|---|
| `tau2.user.user_simulator_streaming` | `backchannel_decision`, `interruption_decision` | `gpt-4.1` (constant, no CLI flag) | `openai/azure/openai/gpt-4.1` (same model, on the Hub) | The user never backchannels (no S_BC events) and never barges in (no L_Y / R_Y events) |
| `tau2.evaluator.hallucination_reviewer` | `llm_judge_hallucination_check` | `--review-model` (`claude-opus-4-5`) | `openai/azure/openai/gpt-5.2` | Simulated-user hallucinations are never caught or re-run |
| `tau2.evaluator.review_llm_judge`, `review_llm_judge_user_only`, `auth_classifier` | `llm_judge_*`, `classify_authentication` | same | same | Only with `--auto-review`, which is off by default |

The runbook helper also passes `--review-model openai/azure/openai/gpt-5.2`, so the model recorded in
`results.json` matches the one actually called. The user LLM (`--user-llm-args`) and the NL-assertion
judge (`TAU2_JUDGE_*`) already accept an `api_base` and are not touched.

**Files** (written, ruff-clean and tested; already in place, uncommitted):

| File | Role |
|---|---|
| `misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py` | The overrides (below) |
| `misc/prototypes/fba_voice_eval/tau2_ihub.py` | Launcher: installs the overrides, logs `USER TTS OVERRIDE`, `USER DECISION LLM OVERRIDE` and `REVIEW LLM OVERRIDE` warnings, and runs tau2's own `tau2.cli:main`. Used as `uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run ...` instead of `uv run tau2 run ...`. It takes exactly the same arguments. |

`tau2_ihub_overrides.py` (ruff-clean):

```python
"""Run tau2's voice user simulator entirely on an OpenAI-compatible hub.

Import this module before tau2 runs anything. It installs two overrides:

1. TTS: replaces the ElevenLabs call that tau2.voice.synthesis.synthesize.synthesize_voice()
   makes, so every user utterance and every out-of-turn insert (vocal tics, non-directed
   phrases) is synthesized by the hub.
2. Hardcoded LLM calls: tau2 makes some LLM calls with a fixed model and no api_base, so
   with only a hub key they fail. Each is routed to the hub by its call_name:
   - backchannel_decision / interruption_decision (voice user simulator): model
     VOICE_USER_SIMULATOR_DECISION_MODEL = "gpt-4.1" at api.openai.com. On failure tau2
     answers "no", so the simulated user silently never backchannels or interrupts.
   - llm_judge_hallucination_check (on by default in full-duplex runs,
     --hallucination-retries 3): --review-model, default "claude-opus-4-5". On failure no
     hallucinated conversation is detected or re-run.
   - llm_judge_*review and classify_authentication (only with --auto-review): same model.

No file under src/tau2 is modified.
"""

import os
import re
from copy import deepcopy

from openai import OpenAI

import tau2.evaluator.auth_classifier as tau2_auth_classifier
import tau2.evaluator.hallucination_reviewer as tau2_hallucination_reviewer
import tau2.evaluator.review_llm_judge as tau2_review_llm_judge
import tau2.evaluator.review_llm_judge_user_only as tau2_review_user_only
import tau2.user.user_simulator_streaming as tau2_user_streaming
import tau2.voice.synthesis.synthesize as tau2_synthesize
from tau2.data_model.audio import AudioData, AudioEncoding, AudioFormat
from tau2.data_model.voice_personas import (
    ALL_PERSONAS,
    DEFAULT_PERSONA_NAME,
    get_persona_name_by_voice_id,
)
from tau2.voice.utils.audio_preprocessing import resample_audio

BASE_URL = os.getenv("TAU2_USER_TTS_BASE_URL", "https://inference-api.nvidia.com/v1")
MODEL = os.getenv("TAU2_USER_TTS_MODEL", "openai/openai/gpt-4o-mini-tts")
API_KEY = os.getenv("TAU2_USER_TTS_API_KEY") or os.getenv("OPENAI_API_KEY")
DECISION_MODEL = os.getenv("TAU2_USER_DECISION_MODEL", "openai/azure/openai/gpt-4.1")
DECISION_BASE_URL = os.getenv("TAU2_USER_DECISION_BASE_URL", BASE_URL)
DECISION_CALLS = ("backchannel_decision", "interruption_decision")
REVIEW_MODEL = os.getenv("TAU2_REVIEW_MODEL", "openai/azure/openai/gpt-5.2")
REVIEW_BASE_URL = os.getenv("TAU2_REVIEW_BASE_URL", BASE_URL)
REVIEW_CALLS = ("llm_judge_", "classify_authentication")  # call_name prefixes
TTS_RATE = 24000  # response_format="pcm" is 24 kHz, 16-bit, mono

# persona -> gpt-4o-mini-tts voice (gender-matched). The accent/style comes from
# the persona's own voice-design prompt in tau2 (passed as `instructions`).
VOICE_MAP = {
    "matt_delaney": "ash",
    "lisa_brenner": "coral",
    "mildred_kaplan": "sage",
    "arjun_roy": "echo",
    "wei_lin": "shimmer",
    "mamadou_diallo": "onyx",
    "priya_patil": "nova",
}
_TIC = re.compile(r"^\W*(\[(cough|sneeze|sniffle)\]\s*)+$", re.IGNORECASE)
# the same tags inside a sentence (tau2's in-turn vocal tics, e.g. "five, .[sneeze][sneeze] seven")
_INLINE_TIC = re.compile(r"\.?\s*(?:\[(cough|sneeze|sniffle)\]\s*)+", re.IGNORECASE)
_INLINE_TIC_SOUNDS = {
    "cough": " (ahem, hkh-hkh) ",
    "sneeze": " (ah... ah-choo!) ",
    "sniffle": " (sniff, sniff) ",
}
_INLINE_TIC_NOTE = (
    "\nThe parts in parentheses such as (ahem, hkh-hkh), (ah... ah-choo!) or "
    "(sniff, sniff) are a cough, a sneeze or a sniffle in the middle of speaking: "
    "perform them as real, brief sounds, not as words, then carry on speaking."
)
_PAUSE = re.compile(r"\[pause\]", re.IGNORECASE)
_TAG = re.compile(r"\[[a-z_ ]+\]", re.IGNORECASE)
# gpt-4o-mini-tts reads "[cough]" or "*cough*" as the word; sound-words get closest to a sound.
_TIC_SOUNDS = {
    "cough": (
        "Ahem! Hkh-hkh. Hkh-hkh.",
        "Clear your throat and cough. Nonverbal sounds only; no words.",
    ),
    "sneeze": ("Ah... ah... ah-choo!", "A real sneeze, not spoken words."),
    "sniffle": (
        "Sniff... sniff.",
        "Short nasal sniffing sounds, breathy, no voiced words.",
    ),
}
_client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def _request(text: str, persona: str) -> tuple[str, str]:
    """Return (input, instructions) for one utterance."""
    style = ALL_PERSONAS[persona].prompt
    tic = _TIC.match(text.strip())
    if tic:  # ElevenLabs v3 audio tags such as ".[cough][cough][cough]": render a non-word sound
        return _TIC_SOUNDS[tic.group(2).lower()]
    note = _INLINE_TIC_NOTE if _INLINE_TIC.search(text) else ""
    text = _INLINE_TIC.sub(lambda m: _INLINE_TIC_SOUNDS[m.group(1).lower()], text)
    text = _TAG.sub("", _PAUSE.sub("...", text))
    text = re.sub(r"\s+", " ", text).strip() or "..."
    return (
        text,
        f"{style}\nRead the text exactly as written, as a caller on a phone line.{note}",
    )


def ihub_tts(text, config):
    """Drop-in for tau2's tts_elevenlabs(text, config) -> AudioData (PCM16 at config's rate)."""
    persona = (
        get_persona_name_by_voice_id(config.voice_id or "") or DEFAULT_PERSONA_NAME
    )
    tts_input, instructions = _request(text, persona)
    response = _client.audio.speech.create(
        model=MODEL,
        voice=VOICE_MAP[persona],
        input=tts_input,
        instructions=instructions,
        response_format="pcm",
    )
    pcm = response.read()
    if not pcm:
        raise ValueError(f"user TTS returned empty audio for {text!r}")
    audio = AudioData(
        data=pcm,
        format=AudioFormat(encoding=AudioEncoding.PCM_S16LE, sample_rate=TTS_RATE),
    )
    target = deepcopy(config.output_audio_format)
    return (
        audio
        if target.sample_rate == TTS_RATE
        else resample_audio(audio, target.sample_rate)
    )


def _route(module, call_names, model, base_url):
    """Send ``module``'s generate() calls whose call_name starts with ``call_names`` to the hub."""
    original = module.generate

    def routed(*args, **kwargs):
        if str(kwargs.get("call_name") or "").startswith(call_names):
            kwargs["model"] = model
            kwargs.setdefault("api_base", base_url)
        return original(*args, **kwargs)

    module.generate = routed


tau2_synthesize.tts_elevenlabs = ihub_tts
_route(tau2_user_streaming, DECISION_CALLS, DECISION_MODEL, DECISION_BASE_URL)
for _module in (
    tau2_hallucination_reviewer,
    tau2_review_llm_judge,
    tau2_review_user_only,
    tau2_auth_classifier,
):
    _route(_module, REVIEW_CALLS, REVIEW_MODEL, REVIEW_BASE_URL)
```

`tau2_ihub.py`:

```python
"""`tau2` CLI with the voice user simulator's TTS and hardcoded LLM calls routed to the Inference Hub (see tau2_ihub_overrides.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tau2_ihub_overrides as ihub  # noqa: E402  (installs the overrides on import)
from loguru import logger  # noqa: E402

from tau2.cli import main  # noqa: E402

if __name__ == "__main__":
    logger.warning(
        f"USER TTS OVERRIDE: {ihub.MODEL} at {ihub.BASE_URL} "
        "(non-official user voices; not leaderboard-comparable)"
    )
    logger.warning(
        f"USER DECISION LLM OVERRIDE: {ihub.DECISION_MODEL} at {ihub.DECISION_BASE_URL} "
        f"for {', '.join(ihub.DECISION_CALLS)}"
    )
    logger.warning(
        f"REVIEW LLM OVERRIDE: {ihub.REVIEW_MODEL} at {ihub.REVIEW_BASE_URL} "
        "for the hallucination check and --auto-review"
    )
    sys.exit(main())
```

**Tests** (`misc/prototypes/fba_voice_eval/tests/test_tau2_ihub_overrides.py`, 16 tests, offline, with
the OpenAI client mocked; all pass): tag handling (tic → sound-word, `[pause]` → `...`, other tags removed); persona
lookup from each official voice id; 24 kHz → 16 kHz resampling gives the expected length; empty audio
raises; each routed `call_name` gets the Hub model and `api_base` while other calls pass through
unchanged (with `llm_utils.generate` mocked). The live check is runbook §2.3.

**Acceptance:** the runbook's §2.3 check passes, and a `mock` smoke run through `tau2_ihub.py` with
`ELEVENLABS_API_KEY` unset finishes with no infrastructure errors and **no `AuthenticationError` in
the console log**. Its `both.wav` has an intelligible user voice.

### 4.1 I1: per-role usage and latency in `agent_turn_done` [agent]

**Goal:** every completed agent step logs how many LLM calls, tokens and milliseconds the frontend
and backend used. The text prototype is not modified; it already computes all of this.

| File | Change |
|---|---|
| `src/prototypes/voice_frontend_backend_agent/agent/port.py` | Add `RoleUsage` (frozen dataclass: `calls`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens`, `latency_ms`). Add `frontend: RoleUsage` and `backend: RoleUsage` to `ReplyUsage`, both defaulting to zeros. Existing fields are unchanged, so `response.done.usage` is unchanged. |
| `src/prototypes/voice_frontend_backend_agent/agent/runner.py` | In `_reply(turn)`, fill both from `turn.usage.frontend` / `turn.usage.backend` (`RoleTotals`: `calls`, `usage.*`, `latency_ms`). |
| `src/prototypes/voice_frontend_backend_agent/engine/turn_manager.py` (`_run_agent`, ~line 387) | Add to the `agent_turn_done` record: `step` (`"respond"` if `outputs is None`, else `"resume"`), `frontend` (dict), `backend` (dict). |
| `src/prototypes/voice_frontend_backend_agent/agent/scripted.py` | No change; the defaults keep the stub agent working. |
| `src/prototypes/voice_frontend_backend_agent/README.md` | One line in the event-log description. |
| `tests/unit/prototypes/voice/test_voice_*.py` | One test: a fake agent turn with known `UsageTotals` gives an `agent_turn_done` record with the expected per-role numbers. One more: the stub agent still produces a record with zeroed roles. |

Resulting record (additions marked):

```json
{"kind": "agent_turn_done", "session_id": "sess_…", "audio_ms": 6000, "turn_id": 1,
 "latency_ms": 8027, "outcome": "text", "input_tokens": 2407, "output_tokens": 25,
 "step": "respond",
 "frontend": {"calls": 1, "prompt_tokens": 1180, "completion_tokens": 42, "cached_tokens": 0, "total_tokens": 1222, "latency_ms": 478},
 "backend":  {"calls": 1, "prompt_tokens": 1227, "completion_tokens": 183, "cached_tokens": 512, "total_tokens": 1410, "latency_ms": 7521}}
```

The change is additive: existing readers of the log keep working. Roll-out:

```bash
# [agent]
uv run pytest tests/unit/prototypes/voice -q
docker restart fba-voice          # picks up the bind-mounted ./src; same command and env as before
curl -s localhost:8765/health     # 503 "starting" until the ASR/TTS warm-up passes
```

**Acceptance:** after one `mock` smoke run, every `agent_turn_done` of the run has `frontend` and
`backend`, and `frontend.total_tokens + backend.total_tokens == input_tokens + output_tokens` for
every record.

**Status (2026-09-24): built and accepted.** As specified, with these details:

- `RoleUsage.as_record()` gives the logged dict; `latency_ms` is rounded to 0.1 ms.
- `total_tokens` falls back to `prompt + completion` when a provider reports 0.
- The test is `tests/unit/prototypes/voice/test_voice_usage_log.py`: a paired turn with two backend
  steps (`respond` with FE + BE usage, `resume` with BE only) and the scripted agent's zeroed roles.
  `_voice_fakes.SessionHarness` gained an optional `event_log` argument for it.
- All 209 prototype unit tests pass; ruff is clean. `fba-voice` was restarted and `fba-voice-bo` started
  with it; the smoke runs meet the acceptance above (checks C2 and C4 in I2).

### 4.2 I2: `fba_voice_metrics.py`, joining tau2 results with the agent log [tau2]

**Location:** `misc/prototypes/fba_voice_eval/` (not `src/tau2/`, not `tau2-fba/`, which is the
text-to-text adapter).

| File | Role |
|---|---|
| `fba_voice_metrics.py` | CLI. Computes everything in §3 for one or more runs. |
| `README.md` | Usage. |
| `tests/test_fba_voice_metrics.py` | Offline tests over a small synthetic `results.json` and event log. |
| `tests/fixtures/` | The synthetic inputs: one paired session with a direct turn, a delegated turn with two tool rounds, and a barge-in cancellation; one backend-only session; one retried session. |

**CLI:**

```bash
uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py \
  --run  paired=data/simulations/fba_voice_paired_airline_regular \
  --run  backend_only=data/simulations/fba_voice_bo_airline_regular \
  --event-log paired=<dump-or-agent>/fba_voice_events.jsonl \
  --event-log backend_only=<dump-or-agent>/fba_voice_bo_events.jsonl \
  --out  <output dir>
```

The model tag of each run is read from the run's recorded `audio_native_config`. It can be overridden with
`--model paired=pine-…`.

**Steps:**

1. `Results.load(run_dir)` for each run. Pass^1 comes from `compute_metrics`, and interaction metrics
   from `compute_interaction_metrics_block([run_dir])`, both imported from tau2 (read-only use).
2. Stream the event log. Keep only sessions whose `session_start.model` is the run's model tag. Group
   records by `session_id`.
3. Join sessions to simulations (§3.2).
4. Per turn: backend latency, exact and derived (§3.3), and the filler terms (§3.4). Per simulation:
   per-role tokens (§3.5).
5. Aggregate: mean, p50 and p90 per arm and domain.
6. Run the checks. Each failure is printed and written to the report:
   - **C1:** the join is not 1:1 (unmatched sessions or simulations).
   - **C2:** I1 fields missing, so the report falls back to combined tokens and derived latency, and
     says so.
   - **C3:** exact and derived backend latency differ by more than 5% on the median.
   - **C4:** per-role FE + BE tokens ≠ tau2 `agent_usage` for a simulation. Cancelled steps are
     tolerated when `thinking_cancelled` explains the difference.
   - **C5:** agent failures in the run (counts): `frontend_contract_violation`, `backend_error`,
     `tool_result_timeout`, `response_done` with `status: "failed"`, and `filler_timing.outcome ==
     "error"`. A failed agent turn goes to the wire as `error code=agent_error` and to `docker logs`
     as `agent turn N failed`, **not** to the event log, so the runbook archives `docker logs` too.
   - **C6:** `filler_timing.mode != "log_only"` in a Pass^1 run.
   - **C7:** a paired run whose sessions have no `filler_timing` records, or a backend-only run whose
     sessions have some.
   - **C8:** simulations that ended in an infrastructure error, or with a termination reason other than
     `user_stop` / `agent_stop`.

**Outputs** (written to `--out`):

| File | Contents |
|---|---|
| `fba_voice_report.md` | Headline table: Arm · Domain · Sims · Pass^1 · L_R · L_Y · R_R · R_Y · I_A · S_BC · S_VT · S_ND · Backend turn latency mean (p90) · Filler voice latency mean (p90) · Filler would be heard · TTFA (projected) · FE tokens per task · BE tokens per task. Then latency detail, tokens per task by role, diagnostics, checks, provenance (models, commits, model tag, concurrency). |
| `fba_voice_metrics.json` | Every number in the report, machine-readable. |
| `fba_voice_per_task.csv` | One row per simulation: task_id, trial, reward, session_id, turns, delegated turns, FE/BE tokens, mean backend latency, mean FVL. |
| `fba_voice_per_turn.csv` | One row per user turn: session, turn_id, decision (direct/delegate/backend_only), endpointing, filler text, frontend, TTS estimate, FVL, backend latency (exact, derived), answer latency, would-be-heard, outcome. |
| `interaction_metrics.json` | tau2's `interaction_metrics` block, unchanged. |
| `join.csv` | session_id ↔ (task_id, trial), match method, retried sessions. |

**Environment:** tau2's venv (`uv sync --extra voice`). No network and no keys needed.

**Status (2026-09-24): built.** `misc/prototypes/fba_voice_eval/fba_voice_metrics.py`, `README.md`, and
`tests/test_fba_voice_metrics.py` (9 tests, all pass; ruff-clean). It differs from the spec above in
these ways:

- **Filler TTS term:** measured as described in §3.4 (answer step → first answer audio). The spec's
  filler-record source has no samples on tool-using turns; it is kept as the fallback.
- **Test fixtures:** a synthetic event log (`tests/fixtures/events.jsonl`). The simulations are built
  in the test as plain `Sim` records instead of a synthetic `results.json`, because the join, the
  per-turn maths and the checks don't depend on tau2's loader. tau2's loader and metrics are exercised
  on the real smoke runs.
- **Arms:** `--run` can be repeated (one per domain and arm). An arm named `bo`, `backend_only` or
  `backend-only` gets the backend-only rules. `--model` takes an arm or a run-directory name.
- **Sessions from other runs:** besides the model tag, a session must fall within the run's span
  (±120 s), so reruns under the same tag don't break the 1:1 join. Their count is reported.
- **Check severity:** C2 (no I1 fields), C5 (agent failures) and C4 when the agent counts *more*
  tokens than tau2 in a session with a barge-in (tau2 drops the usage of responses the user cut off)
  are WARN; the others FAIL. The exit code is 1 only on a FAIL.
- **Added later (2026-09-24):** the results table (`fba_voice_results_table.md/.csv`, runbook §7.3)
  with `--setup ARM=FILE.json` for the agent-side configuration columns; the realtime response and
  frontend per-turn latencies (§3.6); the tool-wait and `sim_time_to_wall_time` diagnostics.
- **Turns** that were cancelled before the agent decided anything are reported as `undecided`.
- **p90** is nearest-rank.

### 4.3 I3: backend-only container [agent] (ops, no code)

This is the same command as the `fba-voice` start in the agent runbook §1, with three differences: name
`fba-voice-bo`, host port **8767**, and the backend-only profile with its own log files. The exact
command is in the runbook, §4. The paired arm keeps using `fba-voice` on 8765.

### 4.4 Out of scope

- Changes to `src/tau2/` (none are needed).
- Streaming the filler's first token. Filler latency is measured once the whole `call_backend` call
  has returned, which is an upper bound for a streaming frontend; the text runbook documents the same caveat.
- Load-testing `server.max_sessions`. Latency-bearing runs use `--max-concurrency 1`.

---

## 5. Order of work

| # | Step | Where | Done when |
|---|---|---|---|
| 0 | ~~Install `portaudio19-dev` and the `voice` extra; put I0 in place; add I0's offline tests~~ | [tau2] | **Done** 2026-09-24 |
| 1 | ~~Build I1, run the unit tests, `docker restart fba-voice`~~ | [agent] | **Done** 2026-09-24; acceptance met on the paired smoke run |
| 2 | ~~Build I2 with its offline tests~~ | [tau2] | **Done** 2026-09-24; all checks pass on both smoke runs |
| 3 | ~~Start `fba-voice-bo` (I3)~~ | [agent] | **Done** 2026-09-24; health `ok`, smoke run passed |
| 4 | Follow the runbook from §5.1 (`regular` smoke with a listening check, 5-task airline go/no-go), then §6–§9 | [tau2] | Report in the dump repo |

I0 is required before any run, because without it tau2 would need ElevenLabs. The runbook can be
started before I1 and I2 exist. Runs made before I1 lack only the per-role token
split. I2 can be run on them later, because the raw logs are archived with every run.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **User voices are not the official ones** (gpt-4o-mini-tts instead of ElevenLabs), so results are not comparable to the τ³ voice leaderboard or to ElevenLabs-based runs | State "user TTS: `openai/openai/gpt-4o-mini-tts` via Inference Hub" next to every number. Compare arms only with each other, under the same user TTS. |
| Vocal tics are sound-words, not true non-speech, so S_VT can be understated: the agent's ASR hears "ahem" and "achoo" as speech | Report S_VT with an "approximate (I0)" note. Listen to a few tic inserts in the smoke run (runbook §5.1). S_BC and S_ND are not affected, because they are spoken words in the official setup too. |
| Accents come from the `instructions` prompt, not from dedicated voices, so they may be milder than ElevenLabs' designed voices | This affects all arms equally. The persona prompts are tau2's own. |
| Hub TTS latency (1–8 s per utterance) or rate limits | It doesn't enter agent metrics (§4.0). Failures are retried by tau2's `@tts_retry`; persistent ones become infrastructure errors (check C8). |
| The filler TTS term is an estimate | Label it "projected" in every table, and do the optional `speak` validation run (§3.4) once per model pair. |
| Backend reasoning turns are slow, and a long silence could trip tau2's stall detection (`DEFAULT_AUDIO_NATIVE_MAX_INACTIVE_SECONDS = 40`, `src/tau2/config.py:167`) | The smoke gate reports the p90 backend turn latency before the full runs. If simulations end with inactivity errors, report it as an agent result; don't raise the threshold (that would change tau2). |
| Endpoint load changes latency | `--max-concurrency 1` for every run, and both arms run back to back on the same day. Record the start and end time of every run. |
| Shared, root-owned log files grow across runs | Runs are separated by model tag. The archive step copies each run's filtered records plus a raw copy of the full log. |
| Barge-in cancellations hide tokens and latency | Report the count of cancelled steps next to every total. |
