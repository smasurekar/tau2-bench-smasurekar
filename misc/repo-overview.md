# τ³-bench (tau2-bench) — Repo Overview

**Purpose:** ramp-up map of this repo. What the pieces are called, what they do, what can be
evaluated, and how scoring works.

**Accurate as of:** this working tree (`dev/smasurekar/tau-bench-runs`, v1.0.x). Terminology
below uses the repo's own names.

---

## 1. What this benchmark actually is

τ-bench simulates a **customer-service phone/chat call** and scores the agent on the *outcome*.

A run of one task looks like this:

```
          ┌──────────────┐  message   ┌────────────────┐
          │ User         │ ─────────► │ Agent          │
          │ Simulator    │ ◄───────── │ (system under  │
          │ (an LLM      │  message   │  test)         │
          │  role-playing│            └───────┬────────┘
          │  a customer) │                    │ tool calls
          └──────────────┘                    ▼
                                      ┌────────────────┐
                                      │ Environment    │
                                      │ (domain DB +   │
                                      │  tools+policy) │
                                      └────────────────┘
```

The **Orchestrator** drives this loop. When the conversation ends, the **Evaluator** compares the
final database state (and what the agent said) against the task's expected outcome and emits a
**reward** in `[0, 1]`. Aggregating rewards across tasks and trials gives **avg_reward** and
**pass^k**.

Key idea: the agent is *not* graded on following a script. It is graded on **end state**.

---

## 2. Components (repo names)

| Component | Where | What it is |
|---|---|---|
| **Domain** | `src/tau2/domains/<name>/`, data in `data/tau2/domains/<name>/` | A business scenario: a DB (`db.json`/`db.toml`), a **policy** (`policy.md`) the agent must obey, **tools** (API functions), optional **user tools**, and **tasks**. |
| **Environment** | `src/tau2/environment/` | Runtime wrapper around a domain's DB + toolkits. Executes tool calls, exposes `get_tools()`, `get_policy()`, `get_db_hash()`. |
| **Task** | `data/tau2/domains/*/tasks.json`, schema in `src/tau2/data_model/tasks.py` | One scenario instance: user instructions/persona, initial state, and `evaluation_criteria`. |
| **Agent** | `src/tau2/agent/` | **The system under test.** Two base classes: `HalfDuplexAgent` (turn-based text) and `FullDuplexAgent` (tick-based voice). |
| **User Simulator** | `src/tau2/user/` | An LLM role-playing the customer, driven by the task's instructions. Emits `###STOP###` / transfer / out-of-scope signals to end the call. |
| **Orchestrator** | `src/tau2/orchestrator/` | Runs the conversation loop. `Orchestrator` (half-duplex) or `FullDuplexOrchestrator` (full-duplex). |
| **Evaluator** | `src/tau2/evaluator/` | Computes the reward. Four scoring evaluators + several LLM **judges** (see §6). |
| **Metrics** | `src/tau2/metrics/` | `agent_metrics.py` → avg_reward, **pass^k**, cost, DB-match, auth, termination stats. `voice_interaction_metrics.py` → voice latency/responsiveness panel. |
| **Runner** | `src/tau2/runner/` | Batch execution: concurrency, retries, checkpoint/resume, hallucination-retry loop. |
| **Registry** | `src/tau2/registry.py` | Name → constructor map for domains, tasks, agents, users. This is how `--agent`, `--domain`, `--user` resolve. |
| **CLI** | `src/tau2/cli.py`, docs in `docs/cli-reference.md` | `tau2 run` / `view` / `play` / `domain` / `evaluate-trajs` / `submit` / `check-data`. |
| **Gym** | `src/tau2/gym/` | Gymnasium-compatible env for RL/training and `tau2 play` (human-in-the-loop). |
| **API service** | `src/tau2/api_service/` | Small FastAPI wrapper (`/api/v1/run_domain`, `/get_tasks`, `/get_options`) to trigger runs over HTTP. |
| **Knowledge module** | `src/tau2/knowledge/` | RAG pipeline (BM25, embeddings, reranker, sandboxed shell) used only by `banking_knowledge`. |
| **Voice module** | `src/tau2/voice/` | `audio_native/` provider adapters, `synthesis/` (TTS), `transcription/` (STT), audio effects. |

### Agent variants registered out of the box

| `--agent` name | Class | Use |
|---|---|---|
| `llm_agent` (default) | `LLMAgent` | Standard text agent: LLM + tool calling. |
| `llm_agent_gt` | `LLMGTAgent` | "Ground-truth" agent — told the reference actions. Used as an upper bound / debugging. |
| `llm_agent_solo` | `LLMSoloAgent` | **Solo mode**: no user at all. Agent gets a "ticket" and must solve it with tools only, then call `done`. |
| `discrete_time_audio_native_agent` | `DiscreteTimeAudioNativeAgent` | Voice agent, connects to a realtime audio API. |

### User variants

`user_simulator` (default LLM customer), `dummy_user` (no-op, for solo/debug),
`voice_streaming_user_simulator` (TTS + audio effects + turn-taking, for voice runs).

---

## 3. Benchmarks / domains available

Everything ships in **one** repo and one CLI. The names you've heard map like this:

| Name you hear | In this repo | What it is |
|---|---|---|
| **τ-bench / τ²-bench** (text) | `--domain airline|retail|telecom` | The original text, turn-based customer-service benchmark. `telecom` adds **user tools** (dual-control: the *user* also has a device the agent must walk them through). |
| **τ³ / banking knowledge** | `--domain banking_knowledge` | New knowledge-retrieval domain: 700+ docs + a transactional DB. Agent must retrieve the right policy doc. Requires `--retrieval-config`. |
| **τ-voice** | any domain + `--audio-native` | Not a separate domain — it's a **mode**. Same domains, same tasks, run full-duplex over realtime audio. |
| `mock` | `--domain mock` | Tiny 10-task domain for smoke tests. |

Task counts (`tasks.json`, `base` split):

| Domain | Tasks | Splits available |
|---|---|---|
| `airline` | 50 | `base` 50, `train` 30, `test` 20 |
| `retail` | 114 | `base` 114, `train` 74, `test` 40 |
| `telecom` | 114 (`base`); `telecom_full` = 2285, `telecom_small` = 20 | `base`, `train`, `test`, `small`, `full` |
| `banking_knowledge` | 97 | no split file (all tasks) |
| `mock` | 10 | `base` 10 |

> Use the default `base` split for anything you'll compare against published numbers.
> `telecom-workflow` is the same domain with a workflow-style policy instead of the manual.

---

## 4. What can be evaluated ("eval subject")

The repo's term is **"the system under test"** / **the agent**. Three practical shapes:

| Eval subject | How it plugs in | Mode |
|---|---|---|
| **A plain LLM** | Nothing to build. `--agent llm_agent --agent-llm <model>`. The repo's own scaffold (system prompt + tool schemas) wraps it. This is what the leaderboard calls a **standard** submission. | Text, half-duplex |
| **A text2text agent / scaffold** (planner, router, memory, sub-agents, custom tools) | Write a `HalfDuplexAgent` subclass + factory, register it, run `--agent my_agent`. Leaderboard calls this a **custom** submission. | Text, half-duplex |
| **A voice2voice agent** (speech-in / speech-out) | `--audio-native` with an existing provider adapter, or write a new `DiscreteTimeAdapter` under `src/tau2/voice/audio_native/`. | Voice, full-duplex |

Also supported, though not "agents":
- **User simulators** can themselves be swapped (`--user`) and are reviewed by a judge.
- **Retrieval pipelines** can be compared head-to-head on `banking_knowledge` via `--retrieval-config`
  (`bm25`, `openai_embeddings`, `qwen_embeddings`, `grep_only`, `terminal_use`, `alltools`, …).
- **RL training loops** via the Gym interface (`src/tau2/gym/`, train/test splits).

---

## 5. Protocol each eval subject must speak

This is the concrete integration contract.

### 5a. Plain LLM — OpenAI-compatible `/v1/chat/completions`

All LLM traffic goes through **LiteLLM** (`src/tau2/utils/llm_utils.py:generate()` →
`litellm.completion(...)`). Requirements:

- Serve `POST <base>/chat/completions`, OpenAI wire format.
- **Must support native tool calling**: accept a `tools=[{"type":"function", ...}]` parameter and
  return structured `tool_calls` in the response (`id`, `function.name`, `function.arguments` as
  a JSON string). Tool-calls-as-plain-text will not be parsed — that case needs a custom agent.
- Must accept `tool` role messages back (tool results), and multiple tool calls per assistant turn.

Wiring it up — no code:

```bash
tau2 run --domain airline \
  --agent-llm 'openai/<your-model>' \
  --agent-llm-args '{"api_base": "https://your-host/v1", "api_key": "sk-...", "temperature": 0.0}' \
  --user-llm gpt-4.1
```

`--agent-llm-args` is passed straight through to `litellm.completion`, so `api_base`, `api_key`,
`temperature`, `max_tokens`, etc. all work. Non-OpenAI providers use their LiteLLM prefix
(`anthropic/…`, `vertex_ai/…`, `bedrock/…`).

### 5b. Text2text agent — the Python interface (not a network protocol)

There is **no HTTP contract for agents**. A custom agent is a Python class the framework calls
in-process:

```python
class MyAgent(HalfDuplexAgent[MyState]):
    def __init__(self, tools: list[Tool], domain_policy: str): ...
    def get_init_state(self, message_history=None) -> MyState: ...
    def generate_next_message(
        self, message: UserMessage | ToolMessage | MultiToolMessage, state: MyState
    ) -> tuple[AssistantMessage, MyState]: ...
```

Then a factory `create_my_agent(tools, domain_policy, **kwargs)` registered in `registry.py` via
`registry.register_agent_factory(create_my_agent, "my_agent")`. Run with `--agent my_agent`.

Rules that matter:
- The **environment executes the tools**, not you. You emit `AssistantMessage.tool_calls`; the
  orchestrator runs them and hands back `ToolMessage`/`MultiToolMessage`.
- The agent is given `domain_policy` and must obey it — the judge/evaluator assumes it was in the
  prompt.
- State is returned, not mutated in place (the runner may re-run/branch).
- Your HTTP agent, if you have one, lives *inside* `generate_next_message` — you're free to call
  any remote service there.

See `examples/agents/` (`minimal_text_agent.py`, `react_agent.py`, `custom_agent_eval.py`) and
`misc/custom-agent-integration.md` in this repo.

### 5c. Voice2voice agent — realtime audio WebSocket

Voice runs are **tick-based** (default `--tick-duration 0.2` s). Each tick the harness sends the
user's audio chunk and reads back the agent's audio chunk; both sides may speak at once.
Audio on the wire is telephony-grade **G.711 μ-law, 8 kHz mono** toward the agent
(`DEFAULT_TELEPHONY_RATE = 8000`); providers' native rates (16 kHz in / 24 kHz out) are converted
by `StreamingTelephonyConverter`.

Provider adapters already implemented (`src/tau2/voice/audio_native/`):

| `--audio-native-provider` | Protocol | Default model |
|---|---|---|
| `openai` | **OpenAI Realtime API** over WSS (`wss://api.openai.com/v1/realtime?model=…`) | `gpt-realtime-1.5` |
| `openai_live` | OpenAI Live | `gpt-live-1-diamond-alpha` |
| `gemini` | Google **Gemini Live** | `gemini-3.1-flash-live-preview` |
| `xai` | xAI Grok Voice realtime WSS | `grok-voice-think-fast-2.0` |
| `nova` | Amazon **Nova Sonic** (Bedrock) | `amazon.nova-2-sonic-v1:0` |
| `qwen` | Alibaba Qwen Omni realtime WSS | `qwen3.5-omni-plus-realtime` |
| `livekit` | **Cascaded** STT→LLM→TTS pipeline | configurable |

**If your voice agent already speaks the OpenAI Realtime protocol**, there is a ready escape
hatch: models named `pine-*` reuse the OpenAI adapter but read the endpoint from
`PINE_REALTIME_BASE_URL` + `PINE_API_KEY` (`.env.example`). That is the closest thing to a
"bring your own realtime endpoint" path today.

**Otherwise** you must write an adapter (`README` in `voice/audio_native/`): a `provider.py`
(`connect`, `send_audio`, `receive_events_for_duration`, `send_tool_result`), an `events.py`, and
a `discrete_time_adapter.py` implementing `_execute_tick()` / `_flush_pending_tool_results()`,
then register it in `adapter.py::create_adapter()`. Your agent must also accept **tool/function
definitions over the realtime session** and return tool calls — tools are still executed by the
τ-bench environment, not by your stack.

---

## 6. How evaluation works

### Step 1 — did the conversation end cleanly?

If `termination_reason` is not `AGENT_STOP` or `USER_STOP` (i.e. max steps, timeout, too many
errors, context overflow), **reward = 0** immediately, before any evaluator runs.
`INFRASTRUCTURE_ERROR` simulations are excluded from metrics entirely.

### Step 2 — the four reward components

Each task's `evaluation_criteria` declares a `reward_basis` — the list of components that count.

| `RewardType` | Evaluator | Check |
|---|---|---|
| `DB` | `EnvironmentEvaluator` | Hash of the final DB == hash of a **gold** DB built by replaying the task's reference `actions` on a fresh environment. |
| `ENV_ASSERTION` | `EnvironmentEvaluator` | All `env_assertions` hold on the final environment. |
| `COMMUNICATE` | `CommunicateEvaluator` | Every string in `communicate_info` appears (substring) in the agent's messages. |
| `NL_ASSERTION` | `NLAssertionsEvaluator` | An **LLM judge** returns true for each natural-language assertion. Marked WIP. |
| `ACTION` | `ActionEvaluator` | Every entry in `actions` was actually called by the agent. |

**Final reward = the product of the components in `reward_basis`.** Any zero → total zero.

> **The single biggest gotcha:** `evaluation_criteria.actions` is **one reference trajectory**,
> not a checklist. It exists to *derive the gold DB state*. Unless `ACTION` is in `reward_basis`,
> the agent can take a completely different path (or no tool calls at all) and still score 1.0.
> Default `reward_basis` for airline / retail / telecom is `["DB", "COMMUNICATE"]`.
> `ACTION` is used only in ~9 of the `banking_knowledge` tasks. Full write-up: `docs/evaluation.md`.

`ActionEvaluator` still runs as a **diagnostic** (`partial_action_reward`, split by read vs write
tools) even when `ACTION` isn't scored — useful for debugging, not a correctness verdict.

### Step 3 — LLM judges (diagnostics, not the score)

These are separate from the reward and mostly opt-in:

| Judge | File | Role |
|---|---|---|
| **NL-assertions judge** | `evaluator_nl_assertions.py` | Scores `nl_assertions`. Only affects reward if `NL_ASSERTION` is in `reward_basis`. Configured via `TAU2_JUDGE_MODEL` / `TAU2_JUDGE_BASE_URL` / `TAU2_JUDGE_API_KEY` env vars (no CLI flag). |
| **Conversation reviewer** | `review_llm_judge.py`, `review_llm_judge_user_only.py` | `--auto-review`: reads the whole transcript and tags **agent errors** and **user-simulator errors** with severity (`critical` / `minor`; user errors also `critical_helped` / `critical_hindered`). Feeds the error-breakdown fields in `AgentMetrics`. Default model `claude-opus-4-5` (`--review-model`). |
| **Hallucination reviewer** | `hallucination_reviewer.py` | Full-duplex only. Detects the *user simulator* inventing facts; if found, the sim is **re-run** (`--hallucination-retries`, default 3). Protects against bad user sims poisoning the score. |
| **Auth classifier** | `auth_classifier.py` | Labels whether the agent authenticated the caller (succeeded / failed / not needed). Diagnostic only. |

Note: the CLI runs with `EvaluationType.ALL` (ENV + ACTION + COMMUNICATE). NL assertions are only
invoked when the task's own `reward_basis` asks for them.

### Step 4 — metrics / leaderboard numbers

Computed in `src/tau2/metrics/agent_metrics.py`:

- **`avg_reward`** — mean reward over all simulations.
- **`pass^k`** (`pass_hat_k`) — the headline metric, from the original τ-bench paper.
  With `n` trials of a task and `c` successes, `pass^k = C(c, k) / C(n, k)`: **the probability
  that k independently sampled trials are *all* successful**. `pass^1` ≈ plain success rate;
  higher k measures **reliability/consistency**, and drops fast for flaky agents. Reported as the
  mean across tasks. A run needs `--num-trials >= k`; the leaderboard wants **4+ trials**.
  (Success = reward within 1e-6 of 1.0 — it's all-or-nothing per task.)
- **`avg_agent_cost`** — mean USD cost per trajectory (LiteLLM cost tracking).
- Plus diagnostics: DB match/mismatch, read/write action accuracy, auth outcomes, termination
  reason counts, judge error counts by severity.

**Voice leaderboard** adds a separate **interaction-metrics panel** (`docs/interaction-metrics.md`,
`metrics/voice_interaction_metrics.py`) computed from tick data — it does *not* change the task
reward:

| Group | Metric | Direction | Meaning |
|---|---|---|---|
| Latency | `response_latency_mean` (L_R) | ↓ | Seconds from end of user turn to start of agent reply. |
| Latency | `yield_latency_mean` (L_Y) | ↓ | Seconds to stop talking after being interrupted. |
| Responsiveness | `response_rate` (R_R) | ↑ | Fraction of user turns that got a reply in time. |
| Responsiveness | `yield_rate` (R_Y) | ↑ | Fraction of interruptions where the agent yielded. |
| Interrupt | `agent_interruption_rate` (I_A) | ↓ | Agent talking over the user, per user turn. |
| Selectivity | `selectivity_backchannel` / `_vocal_tic` / `_non_directed` (S_BC / S_VT / S_ND) | ↑ | Correctly *ignoring* "mm-hmm", "um", and side conversations. |

Voice submissions must use `--speech-complexity regular` (noise, accents, interruptions) and
typically report only `pass^1`, since audio runs are expensive.

### Leaderboard submission types

- **standard** — stock scaffold, stock prompts/tools/user simulator/task set. For voice, any
  internal architecture is fine as long as it sits behind the standard τ-voice interface.
- **custom** — anything that changes the benchmark side (custom scaffold, extra tools, modified
  prompts, non-default user sim), or a model fine-tuned on τ-bench domains. Requires methodology
  notes. Flow: `tau2 submit prepare` → `tau2 submit validate` → PR. See
  `docs/leaderboard-submission.md`.

---

## 7. Minimal mental model

```
tau2 run --domain D --agent A --agent-llm M --user-llm U --num-trials N
   │
   ├─ registry → Environment(D)         (DB + tools + policy.md)
   ├─ registry → Agent A                (system under test)
   ├─ registry → UserSimulator(U)       (simulated customer, from task instructions)
   │
   ├─ Orchestrator loop  (half-duplex turns  |  full-duplex ticks)
   │     agent ⇄ user,  agent → tools → environment
   │
   ├─ Evaluator: reward = Π(components in task.reward_basis)   ∈ {0 … 1}
   │     usually  DB_hash_match × communicate_info_match
   │
   └─ Metrics: avg_reward, pass^k over N trials, cost  →  data/simulations/<run>/
```

---

## 8. Where to read next

| Question | File |
|---|---|
| How scoring really works | `docs/evaluation.md` |
| All CLI flags | `docs/cli-reference.md` |
| Building your own agent | `src/tau2/agent/README.md`, `examples/agents/` |
| Adding a domain | `src/tau2/domains/README.md` |
| Half- vs full-duplex loop | `src/tau2/orchestrator/README.md` |
| Voice setup and options | `src/tau2/voice/README.md` |
| Adding a voice provider | `src/tau2/voice/audio_native/README.md` |
| RAG configs for banking | `src/tau2/knowledge/README.md` |
| Submitting results | `docs/leaderboard-submission.md` |
| Voice interaction metrics | `docs/interaction-metrics.md` |
