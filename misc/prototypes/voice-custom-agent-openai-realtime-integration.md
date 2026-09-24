# Custom voice agent on τ³-bench through the OpenAI Realtime protocol

**Date:** 2026-09-23 · **Status:** design doc; nothing in this doc has been built or run yet ·
**Related:** [`text-frontend-backend-agent-tau2-runbook.md`](text-frontend-backend-agent-tau2-runbook.md)
(the text-to-text version of the same agent)

This doc covers two things:

1. **Integration:** how to evaluate a custom voice agent that runs as its own server and speaks
   the OpenAI Realtime WebSocket protocol. τ³-bench connects to it as if it were OpenAI, so no
   tau2 code changes are needed.
2. **Example:** how to write a minimal mock Realtime server, used to check the wiring before
   the real agent is plugged in.

Every command runs from the repo root:

```bash
cd /localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar
```

---

## 1. How it fits together

```
 τ³-bench process                                             your server (any language)
┌────────────────────────────────────────────────┐          ┌──────────────────────────────┐
│ FullDuplexOrchestrator  (one tick = 200 ms)    │          │  Realtime WebSocket endpoint │
│  ├─ VoiceStreamingUserSimulator                │          │                              │
│  │    user LLM → ElevenLabs TTS → μ-law 8 kHz  │          │  frontend / backend / tools  │
│  └─ DiscreteTimeAudioNativeAgent               │   wss    │  logic: whatever you like    │
│       └─ DiscreteTimeOpenAIAdapter ────────────┼─────────►│                              │
│            └─ OpenAIRealtimeProvider           │◄─────────┼─ audio + transcript + tool   │
│                                                │          │  calls (Realtime events)     │
│ Environment (domain DB) ◄─ runs tool calls ────┤          └──────────────────────────────┘
│ Evaluators + interaction metrics (offline)     │
└────────────────────────────────────────────────┘
```

- The server is **completely independent**. Unlike `tau2-fba/`, which subclasses
  `HalfDuplexAgent` inside tau2's process, the server doesn't import tau2 or inherit anything
  from it. The only contract is the WebSocket protocol.
- The endpoint switch already exists. If the model name starts with `pine-`, tau2's OpenAI
  provider connects to `PINE_REALTIME_BASE_URL` with `PINE_API_KEY` instead of api.openai.com
  (`src/tau2/voice/audio_native/openai/provider.py:38-41`, `:139-149`).

---

## 2. Artifacts and references

### 2.1 To be created (planned)

| Artifact | Path | Purpose |
|---|---|---|
| This doc | `misc/prototypes/voice-custom-agent-openai-realtime-integration.md` | Design and steps |
| Mock Realtime server | `misc/prototypes/mock_realtime_server/server.py` *(not created yet)* | Minimal example server (§4) |
| Mock server README | `misc/prototypes/mock_realtime_server/README.md` *(not created yet)* | How to start it |
| Run outputs | `data/simulations/<run_name>/` | Written by `tau2 run` (§5) |

### 2.2 tau2 code this relies on (read-only)

| What | Path |
|---|---|
| Endpoint switch (`pine-` prefix, `PINE_REALTIME_BASE_URL`, `PINE_API_KEY`) | `src/tau2/voice/audio_native/openai/provider.py:38-41`, `:139-149` |
| WebSocket connect: `f"{base_url}?model={model}"`, `Authorization: Bearer` | `src/tau2/voice/audio_native/openai/provider.py:198-201` |
| Handshake: waits for `session.created`, sends `session.update`, waits for `session.updated` | `src/tau2/voice/audio_native/openai/provider.py` (`connect`, `configure_session`) |
| Tool schema format sent to the server | `src/tau2/voice/audio_native/openai/provider.py` (`_format_tools_for_api`) |
| Audio format mapping (`audio/pcmu`, `audio/pcma`, `audio/pcm`) | `src/tau2/voice/utils/openai_utils.py` (`audio_format_to_openai`) |
| Event models (every event type and field tau2 parses) | `src/tau2/voice/audio_native/openai/events.py` |
| How each event is used per tick (audio, transcript, barge-in, tool calls, usage) | `src/tau2/voice/audio_native/openai/discrete_time_adapter.py:234-345` |
| Adapter base class (tick template, buffering, transcript distribution) | `src/tau2/voice/audio_native/adapter.py:39` |
| Adapter factory (`provider="openai"`) | `src/tau2/voice/audio_native/adapter.py:382` (`create_adapter`) |
| Voice agent (wraps the adapter, one call per tick) | `src/tau2/agent/discrete_time_audio_native_agent.py:414` (`get_next_chunk`) |
| Full-duplex orchestrator (tick loop, tool execution) | `src/tau2/orchestrator/full_duplex_orchestrator.py` |
| Voice user simulator (TTS, noise, interruptions) | `src/tau2/user/user_simulator_streaming.py` |
| Full-duplex evaluators (score `simulation.ticks`) | `src/tau2/evaluator/evaluator.py:140-153` |
| Interaction metrics | `src/tau2/metrics/voice_interaction_metrics.py` |
| Defaults (`DEFAULT_OPENAI_*`, providers, tick settings) | `src/tau2/config.py:172-270` |
| CLI flags (`--audio-native-provider`, `--audio-native-model`, `--tick-duration`, …) | `src/tau2/cli.py:258-310` |
| Provider conformance tests | `tests/test_voice/test_audio_native/test_provider_suite.py` |
| User persona voice IDs (`TAU2_VOICE_ID_*`) | `src/tau2/data_model/voice_personas.py` |

### 2.3 Docs

| Doc | Path |
|---|---|
| Voice overview (**outdated**: lists 3 of the 7 providers and says Gemini uses `GOOGLE_API_KEY`; the code reads `GEMINI_API_KEY`) | `src/tau2/voice/README.md` |
| Audio-native architecture, adding providers | `src/tau2/voice/audio_native/README.md` |
| Orchestrators (half- and full-duplex) | `src/tau2/orchestrator/README.md` |
| Interaction metric definitions | `docs/interaction-metrics.md` |
| Creating ElevenLabs voices for the user simulator | `docs/voice-personas.md` |
| CLI reference | `docs/cli-reference.md` |
| Inference Hub setup (text models for the user simulator and judge) | `misc/inference-hub-benchmark.md` |
| τ-Voice paper | https://arxiv.org/abs/2603.13686 |
| OpenAI Realtime API reference (the protocol being copied) | https://platform.openai.com/docs/api-reference/realtime |

---

## 3. The protocol contract

The server must speak the **GA** Realtime schema, which uses event names like
`response.output_audio.delta`. The older beta names, like `response.audio.delta`, are not parsed.

### 3.1 Connection

- **URL:** tau2 opens `{PINE_REALTIME_BASE_URL}?model={model}`, e.g.
  `ws://localhost:8765/v1/realtime?model=pine-mock`. The `pine-` prefix is included in the
  model name as sent.
- **Header:** `Authorization: Bearer {PINE_API_KEY}`.
- **One WebSocket per simulation.** tau2 opens a fresh connection per task and closes it at the
  end. With `--max-concurrency > 1` the server gets several connections at once.

### 3.2 Handshake (strict ordering)

| Step | Direction | Event | Notes |
|---|---|---|---|
| 1 | server → tau2 | `session.created` | **Must be the first message.** tau2 raises an error otherwise. Include `session.id`. |
| 2 | tau2 → server | `session.update` | Carries the policy, tools, audio format and VAD settings (below). |
| 3 | server → tau2 | `session.updated` | tau2 blocks until this arrives. An `error` event here fails the run. |

What the `session.update` payload contains:

```jsonc
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "instructions": "<domain policy + agent system prompt>",   // use this, don't hardcode
    "output_modalities": ["audio"],
    "tools": [{"type": "function", "name": "...", "description": "...", "parameters": {...}}],
    "audio": {
      "input":  {"format": {"type": "audio/pcmu"},             // 8 kHz G.711 μ-law by default
                 "transcription": {"model": "gpt-4o-transcribe"},
                 "noise_reduction": {"type": "near_field"},
                 "turn_detection": {"type": "server_vad", ...}},   // or semantic_vad
      "output": {"format": {"type": "audio/pcmu"}, ...}
    }
    // "reasoning": {"effort": "..."} is only present if a reasoning effort is set
  }
}
```

A custom server can ignore `transcription`, `noise_reduction` and the VAD tuning values, but
it must follow `instructions`, `tools` and the audio `format`.

### 3.3 Messages the server receives, per tick

| Event | When | What the server should do |
|---|---|---|
| `input_audio_buffer.append` (`audio`: base64) | Every tick: 200 ms of user audio, 1,600 bytes of μ-law | Feed it to your VAD / speech-to-text |
| `conversation.item.create` with `item.type = "function_call_output"` (`call_id`, `output`) | After tau2 has run a tool call you emitted | Give the result to your agent logic |
| `response.create` | Right after a tool result batch | Continue the response, i.e. speak the answer |
| `conversation.item.truncate` (`item_id`, `content_index`, `audio_end_ms`) | The user barged in while the agent was talking | Stop generating that item and drop anything not yet sent |

### 3.4 Messages the server sends

| Event | Required fields | What tau2 does with it |
|---|---|---|
| `response.created` | `response.id` | Logged |
| `response.output_item.added` | `item.id` | Logged |
| `response.output_audio.delta` | `item_id`, `delta` (base64, **same format as the session**) | Agent audio: played to the user and recorded in the trajectory |
| `response.output_audio_transcript.delta` | `item_id` (same as the audio), `delta` | **The agent's words for evaluation.** Spread across ticks in proportion to the audio played |
| `response.output_audio.done` / `response.output_audio_transcript.done` | `item_id` | Logged |
| `response.function_call_arguments.done` | `call_id`, `name`, `arguments` (JSON string) | Becomes a tool call that tau2 runs against the domain DB |
| `input_audio_buffer.speech_started` | `audio_start_ms` (ms since the session's first input audio) | **Barge-in.** tau2 cuts agent audio from that point and sends `conversation.item.truncate` |
| `input_audio_buffer.speech_stopped` | `audio_end_ms` | Logged as a VAD event |
| `response.done` | `response.id`; optional `usage` | Usage/cost accounting (optional) |
| `error` | `error.message` | Fails the session if it arrives during the handshake |

Anything else is parsed where it's known and otherwise ignored.

### 3.5 Rules the server must follow

1. **tau2 executes tools, the server doesn't.** Emit `response.function_call_arguments.done`
   and wait for the `function_call_output`. If the server acts against its own backend,
   tau2's domain DB never changes and DB-checked tasks fail.
2. **Use the tools and policy from `session.update`.** They change per domain (`mock`,
   `airline`, `retail`, …). Tool names and argument schemas must match exactly.
3. **Always send a transcript with the audio**, under the same `item_id`. Audio without a
   transcript makes the agent look silent to the evaluators.
4. **Send audio at no more than real-time speed.** tau2 plays at most one tick (200 ms) of
   agent audio per tick and buffers the rest. That's fine, but a barge-in discards the buffer.
5. **Real latency counts.** Response latency, yield latency and interruption rates are
   computed from tick timing (`docs/interaction-metrics.md`).
6. **Keep no state across sessions.**
7. **Don't greet.** tau2 inserts a text-only opening greeting ("Hi! How can I help you
   today?") into the trajectory itself (`src/tau2/orchestrator/full_duplex_orchestrator.py:197`,
   `src/tau2/agent/discrete_time_audio_native_agent.py:723`). The server only has to respond
   to the user's speech.

---

## 4. Example: a minimal mock Realtime server

The goal is the smallest server that makes a full `tau2 run` complete, so the wiring can be
tested before any real agent logic exists. It doesn't understand speech. It detects when the
user stops talking and replies with a fixed sentence.

**Planned location:** `misc/prototypes/mock_realtime_server/server.py`, a single file whose
only dependency is `websockets`. `websockets` is already in the `voice` extra.

### Step 1: accept the connection and do the handshake

- Listen on `ws://0.0.0.0:8765/v1/realtime`. Ignore the `model` query parameter; optionally
  check the `Authorization` header.
- Immediately send `{"type": "session.created", "session": {"id": "sess_<uuid>"}}`.
- On `session.update`, store `session.tools` and `session.instructions` for the example, then
  send `{"type": "session.updated", "session": {...echo...}}`.

### Step 2: a crude voice activity detector on incoming audio

- On each `input_audio_buffer.append`, base64-decode the audio and convert μ-law to linear PCM.
  Python 3.13 removed `audioop`; use `numpy` or a 256-entry lookup table.
- Compute the RMS energy per chunk. Above a threshold means speech.
- Track the elapsed input time: bytes ÷ 8,000 bytes/s at 8 kHz μ-law.
- On a silence → speech transition, send `input_audio_buffer.speech_started`
  (`audio_start_ms` = the elapsed input time). If the agent is mid-reply, stop sending audio.
- After ~600 ms of silence following speech, send `input_audio_buffer.speech_stopped` and
  start a reply.

### Step 3: reply with audio and a transcript

For each reply, create new `response_id` and `item_id` values, then send in order:

1. `response.created`, then `response.output_item.added`.
2. `response.output_audio_transcript.delta` with the full sentence, e.g. "I can help with
   that. Could you tell me more?"
3. `response.output_audio.delta` chunks: ~100 ms each (800 bytes μ-law), one every 100 ms so
   playback stays near real time. tau2 counts any agent audio bytes it receives as speech
   (`src/tau2/agent/discrete_time_audio_native_agent.py:645-649`), so even silence works. A
   quiet tone is better because it makes the reply audible in `both.wav`. The real agent sends
   TTS output here.
4. `response.output_audio.done`, `response.output_audio_transcript.done`,
   `response.output_item.done`, and `response.done` (without `usage`).

### Step 4 (optional): one tool call round trip

- On the first reply, instead of speaking, pick a read-only tool from the stored `tools` list
  (e.g. a `get_*` tool in `mock`) and send `response.function_call_arguments.done` with a fresh
  `call_id` and `arguments` built from the tool's JSON schema.
- Wait for `conversation.item.create` (`function_call_output`) and then `response.create`. Then
  reply as in Step 3 and include part of the tool output in the transcript.

This proves that tool calls reach tau2's environment and that results come back.

### Step 5: handle barge-in and truncation

- On `conversation.item.truncate`, cancel the reply task for that `item_id` and optionally
  send `conversation.item.truncated`.
- If the reply was mid-stream, send `response.done` with `status: "cancelled"`.

### Step 6: close cleanly

- When the socket closes, cancel all tasks for that session. Don't share state between
  connections.

The mock won't score well; that's expected. It only has to show that the pipeline runs end to
end. To turn it into the real agent, replace Step 2 with real speech-to-text and endpointing,
replace Step 3's fixed text with the frontend/backend logic plus TTS, and use Step 4's pattern
for every tool the backend wants to call.

---

## 5. Evaluating a custom agent (mock or real)

### 5.1 One-time setup

```bash
uv sync --extra voice --extra dev
# System packages for pyaudio / audio I/O (needs sudo):
sudo apt install portaudio19-dev ffmpeg
```

Add these to `.env`:

```bash
# Your Realtime-compatible server
PINE_REALTIME_BASE_URL=ws://localhost:8765/v1/realtime   # wss://… for a remote host
PINE_API_KEY=anything-for-the-mock

# User simulator speech (ElevenLabs is the only supported TTS; see docs/voice-personas.md)
ELEVENLABS_API_KEY=...
TAU2_VOICE_ID_MATT_DELANEY=...     # the two control personas are enough
TAU2_VOICE_ID_LISA_BRENNER=...     # when using --speech-complexity control
```

The user simulator's text LLM runs on the Inference Hub. Pass `--user-llm` and `api_base` the
way `misc/inference-hub-benchmark.md` shows.

### 5.2 Start the server

```bash
uv run python misc/prototypes/mock_realtime_server/server.py --port 8765
```

### 5.3 Run one task

```bash
IHUB=https://inference-api.nvidia.com/v1
uv run tau2 run --domain mock --audio-native \
  --audio-native-provider openai \
  --audio-native-model pine-mock \
  --speech-complexity control \
  --user-llm openai/azure/openai/gpt-5.2 \
  --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --num-tasks 1 --num-trials 1 --max-concurrency 1 \
  --max-steps-seconds 120 --verbose-logs
```

`--audio-native-model pine-…` is what routes the connection to your server. Without the
prefix, tau2 goes to api.openai.com.

### 5.4 Check the outputs

| Check | Where |
|---|---|
| Run finished; reward and termination reason | `data/simulations/<run>/simulations/sim_0.json` |
| Listen to the conversation (stereo, user and agent) | `data/simulations/<run>/artifacts/task_*/sim_*/audio/both.wav` |
| When each side spoke (Audacity labels) | `…/audio/assistant_labels.txt`, `user_labels.txt`, `assistant_tool_calls_labels.txt` |
| Per-task log (handshake errors show up here) | `…/artifacts/task_*/sim_*/task.log` |
| Browse the run | `uv run tau2 view` |
| Interaction metrics | `uv run tau2 submit interaction-metrics data/simulations/<run>` |

### 5.5 Scale up

After one `mock` task works, go to `--domain airline` / `retail`, raise `--num-tasks`, set
`--num-trials 4` for Pass^k, try `--speech-complexity regular`, and raise
`--max-concurrency` once the server handles parallel sessions.

---

## 6. Known gaps and follow-ups

| Item | Detail |
|---|---|
| ElevenLabs is required | Only the agent side is replaced. The user simulator still needs `ELEVENLABS_API_KEY` and your own voice IDs (`src/tau2/voice/synthesis/synthesize.py:22` supports only `elevenlabs`). |
| Current key can't reach OpenAI | The `OPENAI_API_KEY` in this environment is an Inference Hub key; api.openai.com returns 401. This doesn't matter for the `pine-` route. |
| `openai_live` is not supported for self-hosted agents | Its endpoint is hardcoded (`src/tau2/voice/audio_native/openai/live_provider.py:334`, `:340`) and it uses WebRTC plus OpenAI's alpha Live events. Supporting it needs a ~15-line URL override in tau2 **and** a WebRTC server. The WebSocket route in this doc is the recommended path. |
| Conformance suite can't target `pine-` yet | `tests/test_voice/test_audio_native/test_provider_suite.py` uses the default model for `openai`. A small tweak (e.g. reading an `OPENAI_REALTIME_MODEL` env var in the `adapter` fixture) would let it test a custom server before full runs. |
| Voice README is outdated | `src/tau2/voice/README.md` should list all 7 providers and `GEMINI_API_KEY`. |
