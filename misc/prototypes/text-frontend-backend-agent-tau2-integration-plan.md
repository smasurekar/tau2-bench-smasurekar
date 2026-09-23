# Plan — Frontend/Backend Prototype Agent on τ²-bench (text-in / text-out)

**Status:** implemented (offline-verified; no live endpoint run yet) · **Date:** 2026-09-23 · **Revision:** 2
**τ² repo:** `tau2-bench-smasurekar` @ `3773ffd` · **Prototype repo:** `nemotron-voice-agent-smasurekar` @ `5103650`
(+ uncommitted WIP in `delegation.py` / `prompts.yaml`, see §0 prerequisite P1)
**Code:** `tau2-fba/` · **Runbook:** [`text-frontend-backend-agent-tau2-runbook.md`](text-frontend-backend-agent-tau2-runbook.md)

> **Revision 2: built as specified, with these deviations found during implementation.**
> 1. **API key hygiene (new).** τ²'s `generate()` writes every call's kwargs into
>    `llm_debug/*.json` under `--verbose-logs`, so a per-call `api_key` landed on disk. This was
>    reproduced with a sentinel key. The adapter now passes the key per call only when it differs
>    from `$OPENAI_API_KEY`, which LiteLLM reads by itself. The driver refuses `--verbose-logs`
>    when it would have to pass the key (`config.passes_explicit_key`).
> 2. **`Results.load` on a directory.** τ² treats any directory as the voice "dir" format, and a
>    text run directory loads as **zero simulations** with no error. `tau2_fba.metrics.load`
>    resolves a text-run directory to its `results.json`, and `fba_report.py` refuses empty runs.
> 3. **`--task-split-name` defaults to `base`** in the driver. With no split, telecom silently
>    runs its full 2285-task set.
> 4. **Added:** `tools/inspect_fba_surface.py` (per-role tool and prompt inspection without LLM
>    calls) and an automatic **Checks** section in every report, which turns the §11 gates into
>    warnings. The native `llm_agent` path also reads reasoning and cached tokens from τ²'s
>    `raw_data`, so the three arms' token tables are comparable.
> 5. Verified: `raw_data`, `usage`, and `generation_time_seconds` do persist into `results.json`
>    (§8.1). 28 offline tests pass, including all three arms end to end through τ²'s real
>    `run_domain`. `git diff -- src/ tests/ uv.lock pyproject.toml` is empty.
> 6. §13.1 settled: arm C (`llm_agent`) is **optional**. It needs no code, and the report
>    supports it.

**Inputs this plan is built on**

| What | Where |
|---|---|
| Agent under test | `nemotron-voice-agent-smasurekar/src/prototypes/text_frontend_backend_agent/` |
| Its design / runbook | `nemotron-voice-agent-smasurekar/misc/prototypes/text-frontend-backend-agent-{prototype-plan,runbook}.md` |
| τ² custom-agent contract | `misc/custom-agent-integration.md` (this repo) |
| Precedent (same pattern, already working) | `tau2-hermes/` + `misc/hermes-agent-integration.md` |

---

## 0. Summary

**What gets built:** a sidecar package, `tau2-fba/`, in this repo. It follows the `tau2-hermes/`
precedent: it registers two agent factories at runtime (Path B, `custom-agent-integration.md` §6)
and calls τ²'s own `run_domain()`. Nothing under `src/tau2/` or `tests/` changes. The prototype
repo needs no code change either (one prerequisite commit, P1).

| Requirement | How it is met | § |
|---|---|---|
| τ² domain tools go to the **backend only**; the frontend gets only the delegation tool | Tools are injected into the prototype's backend `ToolRegistry`. The frontend is hard-wired to `FRONTEND_TOOLS = (call_backend,)`. Both are checked at construction and **on every LLM call** by a tool-surface guard that fails loudly. | §4 |
| Evaluate **both variants** | Two registered agents, `fba_paired` (`mode: frontend_backend`) and `fba_backend_only` (`mode: backend_only`). They share one factory and one config, and the backend model and args are held identical. | §7 |
| Pass^1–Pass^4 | τ²'s own `compute_metrics()`, unmodified, over `--num-trials 4` runs | §8.2 |
| Mean per-turn LLM latency | Backend LLM latency summed per user turn, then averaged over turns; same definition in both arms | §8.3 |
| Frontend filler latency | Wall time from the user message reaching the agent to the frontend's `call_backend` decision (the moment `filler_text` exists). Reported alongside a cross-arm **time-to-first-response**. | §8.4 |
| Avg token usage per task, frontend and backend | Per-role token counts on every returned `AssistantMessage`, summed per simulation and averaged per task | §8.5 |
| No significant τ² changes | **Zero** τ² source edits. Metrics travel in fields τ² already persists (`raw_data`, `usage`, `cost`, `generation_time_seconds`) and are aggregated by an offline report script. | §3, §8.1 |

### Prerequisites (before any measured run)

- **P1 — Commit the prototype WIP.** The working tree of the prototype repo has uncommitted edits
  that make `filler_text` a **required** `call_backend` argument (`delegation.py`, `prompts.yaml`,
  `tests/unit/prototypes/test_delegation_args.py`). The filler-latency metric depends on this: on
  committed `5103650`, `filler_text` is optional, so many delegations would have no filler to time.
  Commit it, then pin that SHA. The driver refuses to run from a dirty prototype tree unless
  `--allow-dirty-prototype` is passed (§6.4).
- **P2 — Environment.** `uv sync --extra dev` in this repo, plus `uv pip install websockets`
  (`custom-agent-integration.md` §8 #11). The prototype is imported from source via `PYTHONPATH`
  (§6.4), with no extra packages. Its runtime dependencies (`pyyaml`, `loguru`, and `openai` via
  `litellm`) are already τ² dependencies.

---

## 1. What is being integrated (the relevant slice of the prototype)

The prototype already exposes the exact seam τ² needs, and says so in its own design doc (prototype
plan §14, `llm.py` docstring, `assemble_agent` docstring):

```python
agent = assemble_agent(config, tools=[ToolSpec(...)], event_sink=sink,
                       frontend_client=..., backend_client=...)   # injectable ChatClients
session = SessionState.from_message_history(msgs, backend_only=...)
turn, session = await agent.send(user_text, session)               # AgentTurn
turn, session = await agent.send_tool_results([ToolResult(...)], session)
# AgentTurn: final_text XOR tool_calls (same rule as τ²'s check_communication_error),
#            usage: UsageTotals(frontend=RoleTotals, backend=RoleTotals)  -- per outward step
```

| Prototype property | Consequence for τ² |
|---|---|
| `backend.tools.execution: external` suspends the backend turn and returns `AgentTurn(tool_calls=…)` | Maps 1:1 onto τ²'s "return a tool call, get re-entered with a `ToolMessage`" protocol. No threads, no bridge. This is much simpler than Hermes. |
| Provider tool-call ids are preserved (`protocol.ensure_ids`) | τ² echoes `ToolCall.id` into `ToolMessage.id`, so correlation is exact |
| `AgentTurn.usage` covers **only the LLM calls made in that step** (`send_tool_results` starts from `UsageTotals()`) | Attaching it to each returned `AssistantMessage` sums correctly, with no double counting |
| Frontend filler goes **only** to the `EventSink`, never onto `AgentTurn` | The filler can never reach the τ² user simulator. We read its timing from the sink. |
| `mode: backend_only`: stateful backend with its own history, no frontend | This is the second arm, with no other code path |
| Paired mode: **backend is stateless per delegation** (fresh `History()` each `call_backend`) | This is the design under test, not a bug. Expect the backend to re-read data (e.g. `get_user_details`) on every delegated turn. See §10 R3. |

---

## 2. Architecture

```
 τ² Orchestrator (unmodified)
   │  generate_next_message(UserMessage | ToolMessage | MultiToolMessage, state)
   ▼
 ┌──────────────────── tau2_fba.FBAHalfDuplexAgent (HalfDuplexAgent) ─────────────────────┐
 │  state: SessionState (prototype) + TurnTracker (timing / accounting for the open turn)  │
 │                                                                                         │
 │  UserMessage          ──► asyncio.run(agent.send(text, session))                        │
 │  Tool/MultiToolMessage ─► asyncio.run(agent.send_tool_results([ToolResult…], session)) │
 │  AgentTurn            ──► AssistantMessage(content XOR tool_calls, usage, cost,        │
 │                                            generation_time_seconds, raw_data["fba"])   │
 │                                                                                         │
 │   ┌────────── prototype FrontendBackendAgent (unmodified) ──────────┐                   │
 │   │  FrontendAgent ── tools = [call_backend] ONLY                    │   StepSink        │
 │   │        │ Delegate(query, filler)             ─── events ───────► (per-instance,    │
 │   │        ▼                                                         │    drained each  │
 │   │  BackendAgent ── tools = τ² domain tools ONLY (external exec)    │    step)         │
 │   └────────┬───────────────────────────────┬──────────────────────────┘                  │
 │            │ frontend ChatClient           │ backend ChatClient                          │
 │   ┌────────▼───────────────────────────────▼─────────┐                                  │
 │   │ tau2_fba.Tau2ChatClient(role=…) — tool-surface   │                                  │
 │   │ guard + prototype⇄τ² message conversion           │                                  │
 │   └────────────────────────┬──────────────────────────┘                                  │
 └────────────────────────────┼──────────────────────────────────────────────────────────┘
                              ▼
               tau2.utils.llm_utils.generate()  → LiteLLM → Inference Hub
         (retries, llm_debug logs, usage, cost, generation_time_seconds for free)
```

### One paired turn, as τ² sees it

```
User ─"change my flight"─► agent.send()
                             frontend LLM → call_backend(query, filler)   [t_decision; filler logged]
                             backend LLM  → get_reservation_details(...)  → AgentTurn(tool_calls)
◄──── AssistantMessage(tool_calls=[get_reservation_details]) ── raw_data.fba.step{frontend+backend}
ENV executes, ToolMessage ─► agent.send_tool_results()
                             backend LLM  → final text                    → AgentTurn(final_text)
◄──── AssistantMessage(content="…") ── raw_data.fba.step{backend} + raw_data.fba.turn{summary}
```

In `backend_only` the frontend line disappears, and everything else is identical. `call_backend`
never appears in the τ² trajectory. Only domain tool calls and final texts do.

---

## 3. Footprint: τ² stays the unmodified measuring instrument

```
tau2-bench-smasurekar/
├── src/tau2/, tests/                      ← UNCHANGED (git diff empty)
├── misc/prototypes/
│   ├── text-frontend-backend-agent-tau2-integration-plan.md   ← this file
│   └── text-frontend-backend-agent-tau2-runbook.md            ← written after bring-up (Phase 5)
└── tau2-fba/                              ← NEW, additive only, mirrors tau2-hermes/
    ├── README.md
    ├── pyproject.toml                     (dependencies = [], like tau2-hermes)
    ├── conftest.py                        (import path for tests, like tau2-hermes)
    ├── run_fba_eval.py                    driver: register + run_domain()          (~150 lines)
    ├── fba_report.py                      offline metrics CLI over results.json    (~60 lines)
    ├── tau2_fba/
    │   ├── __init__.py                    register() — idempotent, both names
    │   ├── prototype_import.py            PYTHONPATH/import guard + SHA/dirty provenance
    │   ├── client.py                      Tau2ChatClient + message conversion + surface guard (~180)
    │   ├── config.py                      prototype agent.yaml → τ² overrides → Config      (~100)
    │   ├── agent.py                       FBAHalfDuplexAgent + factories + TurnTracker      (~250)
    │   ├── metrics.py                     per-turn / per-task aggregation, report tables    (~250)
    │   └── domains.yaml                   per-domain persona + capability list (§6.3)
    └── tests/                             offline, fake clients, no network            (§9)
```

Why Path B (runtime registration + driver) and not the two-line `registry.py` edit (Path A): the
requirement is no significant τ² changes, and `tau2-hermes` already set this pattern here. The
cost is that the `tau2 run --agent …` CLI flag can't be used. That's acceptable, since the driver
exposes the flags we need, and `tau2 view` works on the results regardless.

---

## 4. Tool routing (requirement 1)

| Surface | Frontend LLM | Backend LLM |
|---|---|---|
| Tools in the API request | **exactly** `[call_backend]`, from prototype `delegation.FRONTEND_TOOLS` | **exactly** the τ² domain tools (`environment.get_tools()`, passed to our factory by `build_agent`), as `ToolSpec(callable=None)` |
| System prompt | prototype `frontend` prompt with `{persona}`, `{capabilities}`, `{agent_name}`. **No policy and no tool names.** | prototype `backend` prompt with `{domain_policy}` = τ² `environment.get_policy()` |
| Who executes tools | n/a (`call_backend` is handled inside the prototype) | τ²'s environment (`execution: external`) |

**Mechanism.** The adapter converts each τ² `Tool` with
`ToolSpec(name=f["name"], description=f["description"], parameters=f["parameters"])`, where
`f = tool.openai_schema["function"]`. It passes the list to `assemble_agent(tools=…)`, which builds
the backend's `ToolRegistry`. The frontend is never given that list. `FrontendAgent.decide()`
always sends `FRONTEND_TOOLS`, and `_interpret()` rejects any non-`call_backend` call.

**Enforcement, so a misconfiguration fails loudly instead of quietly lowering the score** (the same
idea as Hermes' `check_tool_surface`):

1. *At construction:* assert `{s.name for s in registry schemas} == {t.name for t in tau2 tools}`,
   `"call_backend"` not in the domain tool names (a name collision would be ambiguous), and
   `config.backend.tools.execution == "external"` (forced by the adapter, never read from YAML).
2. *On every LLM call* (`Tau2ChatClient.complete`): the frontend client asserts that the tool
   names are `{"call_backend"}`. The backend client asserts that they equal the domain tool set.
   On a violation it raises `RuntimeError`. This is a harness bug, so it should go to τ²'s
   retry/infra path, not be scored.
3. *Prompt isolation:* a test asserts that the rendered frontend system prompt contains no domain
   tool name and no line of the domain policy.
4. *Trajectory check* (runbook gate): no `AssistantMessage` in `results.json` has a `call_backend`
   tool call, and no assistant `content` equals any logged `filler_text`.

---

## 5. The adapter (`tau2_fba/agent.py`)

### 5.1 Message mapping

| τ² input | Adapter action |
|---|---|
| `UserMessage` | Open a new turn in `TurnTracker` (`t_user = time.time()`, `turn_index += 1`), then `send(content, session)` |
| `ToolMessage` | `send_tool_results([ToolResult(tool_call_id=m.id, content=m.content or "", is_error=m.error)])` |
| `MultiToolMessage` | Same, over `m.tool_messages`, as **one** batch (the prototype validates completeness and reorders to emission order) |

| Prototype output | Returned `AssistantMessage` |
|---|---|
| `AgentTurn(final_text=t)` | `content=t, tool_calls=None`, and closes the turn (writes `raw_data.fba.turn`, §8.1) |
| `AgentTurn(tool_calls=cs)` | `content=None`, `tool_calls=[ToolCall(id=c.id, name=c.name, arguments=c.arguments, requestor="assistant")]` |

Both carry `usage`, `cost`, `generation_time_seconds` and `raw_data["fba"]["step"]` for **this
step's** LLM calls (§8.1).

### 5.2 State and lifecycle

- `FBAState(BaseModel, arbitrary_types_allowed)` holds `session: SessionState` and a small
  `TurnTracker` (turn index, `t_user`, per-turn running role totals, decision, filler, event counts).
- `get_init_state(message_history)` converts τ² messages to prototype `Message`s and calls
  `SessionState.from_message_history(msgs, backend_only=…)`. On a normal run τ² passes one message,
  the greeting `"Hi! How can I help you today?"` (`orchestrator.py:627`). In paired mode that
  becomes a leading group in `frontend_history`; in backend-only mode it goes into
  `backend_history`. Tasks with a seeded history replay under the prototype's documented, lossy
  rule (prototype plan §6.3).
- **One prototype agent per τ² agent instance** (τ² builds a fresh agent per simulation). So the
  per-instance `StepSink` is effectively single-session, and `CollectingSink`-style semantics are
  safe. The sink is drained after every `send`/`send_tool_results`.
- **Async bridge:** `asyncio.run(...)` per τ² step. `Tau2ChatClient.complete` is `async def` but
  calls the synchronous `generate()` directly, so no event-loop-bound resources (unlike
  `AsyncOpenAI`) survive between steps. τ²'s `ThreadPoolExecutor` workers have no running loop, so
  `asyncio.run` is legal there. There's no `stop()` cleanup beyond releasing references.
- `set_seed(seed)` adds `seed` to both clients' LiteLLM kwargs, mirroring what `llm_agent` receives.

### 5.3 Failure semantics: match `llm_agent`

| Failure | Prototype default | Adapter behaviour | Why |
|---|---|---|---|
| Frontend LLM transport error | propagates | propagates | τ² `run_with_retry` (`runner/progress.py:19`) retries. When exhausted, the sim is marked `INFRASTRUCTURE_ERROR`, excluded, and resumable. This is the same as `llm_agent`. |
| **Backend LLM transport error** | **swallowed** in `BackendAgent.step` (`backend.py:90-94`), and the user gets `"I could not complete that request right now…"` | The client records the exception, and the adapter sees `backend_error` in the drained events and **re-raises it** (`strict_transport_errors=True`, the default) | Otherwise a 429 or a timeout on the Hub is scored as an agent failure. That's a silent bias against the scaffold, and it's **worse under the higher per-turn call count of paired mode.** |
| `ToolProtocolError`, duplicate provider ids | raises | re-raised as `AgentError` | A real protocol break, so it's scored as `AGENT_ERROR` (`orchestrator.py:684`) |
| Frontend contract violation after repairs | `fallback_text` returned to user | kept, plus counted in `raw_data` | This is product behaviour, so it is measured, not hidden |
| Backend iteration cap | not enforced in `external` mode (`agent.py:159-170` returns before the cap check) | kept; bounded by τ² `max_steps` (200) | Same as `llm_agent`, which has no per-turn cap either |

`strict_transport_errors=False` is available for diagnostic runs only and is recorded in the run's
`llm_args`.

### 5.4 `Tau2ChatClient` (`tau2_fba/client.py`)

Implements the prototype's `ChatClient` protocol on top of `tau2.utils.llm_utils.generate()`. This
is what the prototype plan §8 designates, and it follows `custom-agent-integration.md` §4 / gotcha
#7. We get LiteLLM routing, `num_retries`, per-call `llm_debug` logs (`call_name="fba_frontend"` /
`"fba_backend"`), usage, cost, and timing.

```python
class Tau2ChatClient:
    def __init__(self, role, litellm_model, litellm_kwargs, expected_tools: set[str]): ...
    async def complete(self, *, messages, tools=None) -> ChatResponse:
        self._guard(tools)                                   # §4 enforcement 2
        msg = generate(model=self.model, messages=to_tau2(messages),
                       tools=[_SchemaTool(t) for t in tools or []] or None,
                       call_name=f"fba_{self.role}", **self.kwargs)
        u = (msg.raw_data or {}).get("usage") or {}
        return ChatResponse(
            content=msg.content,
            tool_calls=tuple(ToolCall(id=c.id, name=c.name,
                                      arguments_json=canonical_json(c.arguments))
                             for c in msg.tool_calls or ()),
            usage=Usage(prompt_tokens=msg.usage["prompt_tokens"],
                        completion_tokens=msg.usage["completion_tokens"],
                        total_tokens=u.get("total_tokens", 0),
                        cached_tokens=_dig(u, "prompt_tokens_details", "cached_tokens")),
            cost=msg.cost, latency_ms=msg.generation_time_seconds * 1000,
            model=..., finish_reason=...)
        # reasoning_tokens (completion_tokens_details) is kept on the client's per-call log (§8.1)
```

Conversion details that must be right:

| Prototype `Message` | τ² message for `generate()` |
|---|---|
| `system` / `user` | `SystemMessage` / `UserMessage` |
| `assistant` with tool calls | `AssistantMessage(content=None, tool_calls=[…])` |
| `assistant` text | `AssistantMessage(content=text)` |
| `tool` | `ToolMessage(id=tool_call_id, role="tool", content=…, requestor="assistant")` |
| **`assistant` with empty content and no tool calls** (only produced by the frontend repair path, `frontend.py:99-103`) | τ²'s `validate_message` would assert. Substitute the fixed placeholder `"(no reply)"`, count it in `raw_data`, and cover it with a test. This is the one place the wire differs from the prototype's own `OpenAIChatClient`, and it only happens on a repair reprompt. |

`_SchemaTool` is a three-line shim exposing `.openai_schema`, because `generate()` expects τ²
`Tool` objects (`llm_utils.py:389`) and `call_backend` is a plain dict.

---

## 6. Configuration

### 6.1 Single source of truth: the prototype's own YAML

`tau2_fba/config.py` reads the **prototype's** `config/agent.yaml` (default path under
`$FBA_PROTOTYPE_ROOT`, overridable with `--fba-config`). It interpolates `${VAR}` exactly as the
prototype does, then applies **only** these τ²-side overrides before calling the prototype's
`build_config(raw, source_dir=…)`:

| Key | Override | Reason |
|---|---|---|
| `agent.mode` | `frontend_backend` \| `backend_only` (from the registered name) | the two arms |
| `backend.stateful` | `auto` | the prototype derives it from the mode |
| `backend.tools.execution` | `external` (forced) | τ² executes tools |
| `backend.tools.on_incomplete_results` / `on_user_message_while_pending` | `error` / `error` | τ² always returns every result and never interleaves a user message. Either firing is a bug. |
| `domain.policy` | τ² `environment.get_policy()` | the policy comes from the environment (`custom-agent-integration.md` §4) |
| `agent.persona`, `agent.name`, `domain.capabilities`, `domain.unsupported_reply` | from `tau2_fba/domains.yaml[domain]` | §6.3 |
| `logging.event_sink` | ignored; the adapter injects its own `StepSink` (it can tee to JSONL with `--fba-event-log`) | timing capture |

Prompts (`prompts.yaml`), history windows (`frontend.history.max_groups=20`,
`backend.history.max_groups=40`), `delegation.max_repair_attempts`, and `fallback_text` stay
**exactly as the prototype ships them**. We measure the prototype as it is.

### 6.2 Models and reasoning, per role

The prototype `LLMConfig` (`model`, `base_url`, `api_key`, `temperature`, `max_tokens`,
`extra_body`) maps onto LiteLLM kwargs for `generate()`:

| `LLMConfig` | `generate()` |
|---|---|
| `model: nvidia/nvidia/nemotron-3-ultra` | `model="openai/nvidia/nvidia/nemotron-3-ultra"` (LiteLLM needs the `openai/` provider prefix, `inference-hub-benchmark.md` §3) |
| `base_url` / `api_key` | `api_base` / `api_key` (key from env only) |
| `temperature`, `max_tokens` | same |
| `extra_body: {chat_template_kwargs: {enable_thinking: …}, reasoning_budget: …}` | `extra_body=` passthrough |

Defaults are inherited from the prototype: **frontend** `nemotron-3.5-lightning`, reasoning
**off**; **backend** `nemotron-3-ultra`, reasoning **on**, `reasoning_budget: 1024`. On the driver,
`--agent-llm` sets the **backend** model (so τ²'s `agent_info.llm` names the model doing the task
work), and `--frontend-llm` sets the frontend model. Both default to the YAML.

> **Verify the reasoning toggle actually took effect.** τ² sets `litellm.drop_params = True`
> (`llm_utils.py:71`), which can silently drop provider params. That is why
> `misc/tools/verify_reasoning_off.py` exists. The adapter records `reasoning_tokens` **per role
> per call** in `raw_data` (§8.1), so the check reads straight off the smoke run: frontend
> reasoning tokens must be 0 on every call, and backend reasoning tokens must be > 0 on most calls.
> This is a bring-up gate (§11, Rung 2).

### 6.3 Per-domain persona and capabilities (`tau2_fba/domains.yaml`)

The frontend prompt decides between **Direct / Unsupported / Tool** using `{capabilities}`. With no
list configured, it renders `- (no capability list configured)`, and the frontend is likely to
refuse in-domain requests as "Unsupported". So each τ² domain needs a short, **broad** capability
list and a neutral persona:

```yaml
airline:
  name: "Airline Assistant"
  persona: "You are a customer service agent for an airline."
  capabilities:
    - "booking new flight reservations"
    - "changing, upgrading or cancelling existing reservations, including passengers and baggage"
    - "questions about flights, reservations, baggage, refunds and compensation"
    - "transferring the customer to a human agent"
retail:   { ... orders: status, cancel/modify pending, return/exchange delivered, address/payment, account lookup ... }
telecom:  { ... mobile service: account/billing, plans, data/roaming/connectivity troubleshooting ... }
mock:     { ... }
```

An unconfigured domain **fails at construction** and names this file. These strings are a
scaffold input that affects the score. They are hashed and written into the run's `llm_args`
(§6.4), must be identical across both arms, and are reviewed once by a human. The paired arm is the
only one that reads them, since the backend prompt has no `{capabilities}`.

### 6.4 Import and provenance

- `prototype_import.py` puts `$FBA_PROTOTYPE_ROOT/src` on `sys.path` (or respects an existing
  `PYTHONPATH`). If the import fails, it raises an actionable error, like Hermes' `_HERMES_MISSING`.
  We don't use `pip install -e nemotron-voice-agent[prototypes]`: that would pull `pipecat-ai` and
  the whole voice stack into τ²'s env, and `src/prototypes` isn't packaged anyway.
- Recorded into `TextRunConfig.llm_args_agent`, and therefore into `results.json` →
  `info.agent_info.llm_args`, so every run is self-describing: `fba_mode`, prototype git SHA and
  dirty flag, sha256 of the resolved `agent.yaml` / `prompts.yaml` / `domains.yaml[domain]`, both
  roles' model + sampling + `extra_body`, and `strict_transport_errors`. The API key is never
  recorded.
- A dirty prototype tree is refused unless `--allow-dirty-prototype` is passed. The flag is then
  recorded too.

---

## 7. Evaluating both variants (requirement 2)

| | Arm A — `fba_paired` | Arm B — `fba_backend_only` | Arm C — `llm_agent` baseline (recommended) |
|---|---|---|---|
| Prototype mode | `frontend_backend` | `backend_only` | n/a (τ² default agent) |
| Frontend | lightning, reasoning off | — | — |
| Backend | ultra, reasoning on | **identical** model and args | same model and args as the backend |
| History | frontend: 20 groups; backend: per-delegation | backend: 40 groups, stateful | full |
| Isolates | the full product | B vs A = **the frontend's contribution** (accuracy, latency, tokens) | B vs C = the prototype backend scaffold (its prompt) vs τ²'s default prompt |

Registration (`tau2_fba.register()`, idempotent, guarded with `registry.get_agents()`):

```python
registry.register_agent_factory(partial(create_fba_agent, mode="frontend_backend"), "fba_paired")
registry.register_agent_factory(partial(create_fba_agent, mode="backend_only"),     "fba_backend_only")
```

Two names rather than one name with a mode argument, so `results.json` records the arm in
`info.agent_info.implementation`, and a mislabeled run is impossible.

**Held fixed across every arm** (`custom-agent-eval-runbook.md` §1 "Apparatus"): user simulator
`openai/azure/openai/gpt-5.2` with its args, the judge (`TAU2_JUDGE_*` in `.env`), domain, task
split `base`, `--num-trials 4`, seed, `max_steps`, **`--max-concurrency`** (it affects latency,
§10 R6), the prototype SHA, and `domains.yaml`.

Arm C runs through the normal `tau2 run --agent llm_agent`. `fba_report.py` can still compute its
latency and tokens from τ²-native fields (§8.6), so all three arms appear in one table.

---

## 8. Metrics

### 8.1 Capture: what is written on every returned `AssistantMessage`

All of these are **existing** `AssistantMessage` fields, which τ² persists into `results.json`
unchanged. The only field excluded from serialization is `audio_content` (`message.py:231`), and
`Results.save` / checkpointing use `model_dump_json()`. (The docstring of
`misc/tools/verify_reasoning_off.py` says `raw_data` and `usage` aren't persisted. That doesn't
hold for this code, and the first smoke run confirms it as part of Rung 3.)

| Field | Value | Consumed by |
|---|---|---|
| `usage` | `{"prompt_tokens": FE+BE, "completion_tokens": FE+BE}` for this step (τ²'s key convention, `llm_utils.get_token_usage`) | τ²-native tooling |
| `cost` | sum of the per-call costs for this step. It is `0.0` for Hub models LiteLLM can't price (`get_response_cost`, `llm_utils.py:119`), so tokens are the resource metric. | τ² `agent_cost` / `avg_agent_cost` |
| `generation_time_seconds` | sum of the LLM latencies for this step (FE+BE) | τ²-native tooling, arm C parity |
| `raw_data["fba"]` | schema below | `fba_report.py` |

```jsonc
"raw_data": { "fba": {
  "schema": 1,
  "mode": "frontend_backend",            // or "backend_only"
  "turn_index": 3,                        // 1-based user-turn counter within the simulation
  "step_kind": "tool_calls",              // or "final"
  "step": {                               // ONLY the LLM calls made in this τ² step
    "frontend": {"calls": 1, "latency_s": 0.41, "prompt_tokens": 2710, "completion_tokens": 58,
                 "reasoning_tokens": 0, "cached_tokens": 0},
    "backend":  {"calls": 1, "latency_s": 2.95, "prompt_tokens": 4102, "completion_tokens": 311,
                 "reasoning_tokens": 244, "cached_tokens": 0},
    "per_call": [ {"role": "frontend", "latency_s": 0.41, "reasoning_tokens": 0, "finish_reason": "tool_calls"}, ... ]
  },
  "turn": {                               // ONLY on the message that closes the user turn (step_kind == "final")
    "decision": "delegate",               // delegate | direct | contract_fallback | backend_only
    "filler_text": "Let me take a look.", // "" if the frontend omitted it
    "filler_latency_s": 0.43,             // §8.4; null unless decision == delegate
    "frontend_latency_s": 0.41,           // Σ frontend call latencies in the turn (incl. repairs)
    "backend_latency_s": 7.62,            // Σ backend call latencies in the turn
    "backend_calls": 4, "backend_tool_rounds": 3,
    "first_response_latency_s": 0.43,     // §8.4
    "wall_s": 8.11,                       // t_final − t_user (includes τ² env tool execution)
    "delegation_query": "The user (id mia_li_3668) wants to ...",
    "events": {"frontend_repair": 0, "contract_violation": 0, "backend_error": 0, "empty_assistant_placeholder": 0}
  }
}}
```

Timing sources: per-call latency is `generate()`'s `generation_time_seconds`, wall-clock around
`litellm.completion` (`llm_utils.py:407-419`). Turn timestamps are adapter `time.time()` at
`UserMessage` receipt and at final return. The decision instant is the prototype's `delegation`
event timestamp (`InternalEvent.timestamp`, stamped at emission, `agent.py:258-263`), both
`time.time()` in the same process.

### 8.2 Pass^1 – Pass^4

`fba_report.py` calls τ²'s own `tau2.metrics.agent_metrics.compute_metrics(results)` and copies
`pass_hat_ks`. These are the same numbers the `tau2` CLI prints, computed as
`comb(successes, k) / comb(trials, k)` averaged over tasks (`agent_metrics.py:113`). This needs
`--num-trials 4`, because `pass_hat_k` raises when trials < k. `INFRASTRUCTURE_ERROR` sims are
resumed before reporting (`--auto-resume`). The report refuses to print pass^k if any remain, and
says why.

### 8.3 Mean per-turn LLM latency: backend turn latency

A **user turn** *u* runs from a `UserMessage` reaching the agent to the next `AssistantMessage`
with text content. It spans every tool round in between.

- `BE_u` = Σ latency of the backend LLM calls in *u* (LLM time only, excluding τ² tool execution),
  i.e. `turn.backend_latency_s`.
- **Headline:** `mean_backend_turn_latency_s = mean(BE_u over turns with backend_calls > 0)`.
  - Arm A: direct-answer and contract-fallback turns have no backend work and are **excluded**. The
    count of excluded turns is shown next to the mean, so a frontend that answers directly more
    often can't flatter the number unnoticed.
  - Arm B: every turn has backend work.
- Also reported: p50/p90/p95, `mean backend calls per turn`, `mean latency per backend LLM call`,
  and `mean end-to-end LLM latency per turn = mean(FE_u + BE_u over all turns)`, which is the fair
  total-work comparison between A and B.

### 8.4 Frontend filler latency, and time-to-first-response

- **Filler latency** (arm A, delegated turns): `filler_latency_s = t_decision − t_user`, where
  `t_decision` is the moment the frontend's `call_backend` decision (and so `filler_text`) exists.
  It includes frontend repair reprompts. It is reported as mean/p50/p90/p95/max, over delegated
  turns with non-empty filler.
  - `filler_presence_rate` = delegated turns with non-empty `filler_text` ÷ delegated turns. A
    delegation without filler means the real user would hear nothing until the backend finishes.
- **Time-to-first-response (TTFR)**, all turns, both arms. This answers "how fast would a real user
  hear *something*":

  | Arm | Turn type | TTFR_u |
  |---|---|---|
  | A | delegated with filler | `filler_latency_s` |
  | A | delegated without filler | `wall_s` (nothing to say until the backend finishes) |
  | A | direct / contract fallback | frontend decision time (the answer itself) |
  | B | every turn | `wall_s` (no filler exists, so the first thing heard is the final answer) |

  The A-vs-B TTFR delta is the number that quantifies what the frontend buys the user.

**Caveats, stated in every report:**

1. **Non-streaming measurement.** `generate()` returns the whole completion, so the filler
   "arrives" when the full `call_backend` call has been generated. A streaming frontend could
   surface it earlier. This is an upper bound on the production filler latency for the same model
   and endpoint.
2. **The argument order inflates it.** The `call_backend` schema lists `query` before
   `filler_text` (`delegation.py`). Models usually emit JSON keys in schema order, so the filler is
   generated *after* the long, self-contained query, and its latency grows with query length.
   Streaming wouldn't fix this. This is an observation for the prototype owners, not something to
   change mid-evaluation. The report includes `mean completion tokens per frontend call` so the
   effect is visible.

### 8.5 Average token usage per task, frontend and backend

For simulation *s*: `T_s[role][k] = Σ over agent messages raw_data.fba.step[role][k]`, for
`k ∈ {prompt, completion, reasoning, cached, total}` and `role ∈ {frontend, backend}`.

- **Headline:** `avg_tokens_per_task[role] = mean_s T_s[role]` over all non-infra simulations (all
  tasks × trials), for prompt / completion / total. Reasoning is shown separately, as a subset of
  completion where the provider reports it.
- **Per-task table:** mean over the 4 trials of each `task_id`, per role (CSV), so the tasks where
  the paired backend's stateless re-reads cost most are visible.
- Arm B frontend = 0 by construction. The report prints it rather than omitting it.
- User-simulator tokens are excluded (they're apparatus, not agent). They stay available in τ²'s
  own fields if ever needed.

### 8.6 The report (`fba_report.py`)

```bash
uv run python tau2-fba/fba_report.py data/simulations/<run_A> data/simulations/<run_B> [<run_C>] \
    --out misc/prototypes/results/<date>-<domain>.md --csv-dir …
```

- Loads with τ²'s `Results.load` (handles json and dir formats). Detects the arm from
  `info.agent_info.implementation`. For `llm_agent` runs it falls back to native fields: every
  agent LLM call counts as "backend", from `generation_time_seconds` and `usage`, and
  filler/frontend show as n/a.
- Output: a Markdown table with one row per arm × domain: Pass^1–4 · mean backend turn latency
  (+p90) · filler latency mean/p90 + presence rate · TTFR mean/p90 · FE/BE tokens per task · turns,
  delegation rate, direct-answer rate, contract fallbacks, backend errors, repair reprompts. It also
  writes a JSON blob of the same data, per-task CSVs, and a provenance header (both SHAs, models,
  concurrency, user sim, judge).
- It is pure post-processing and can be re-run on old results at any time.

---

## 9. Tests (`tau2-fba/tests/`, offline, no network, no credentials)

Fake `ChatClient`s script the LLMs, the same approach as the prototype's own
`tests/unit/prototypes/_fakes.py`. Real τ² environments (`mock`, `airline`) provide real `Tool`
objects and policies.

| # | Test | Guards |
|---|---|---|
| 1 | Frontend client receives exactly `{call_backend}`. Backend client receives exactly the domain tool names. The frontend system prompt has no tool name and no policy line. | R1 tool routing |
| 2 | Surface guard raises when a tool list is tampered with | §4 enforcement |
| 3 | Paired turn with 2 backend tool rounds → τ² gets 2 tool-call messages with **provider ids**, then 1 text message. No `call_backend` anywhere. | routing, id correlation |
| 4 | `MultiToolMessage` of 3 → one `send_tool_results` batch, in emission order | gotcha #2 |
| 5 | Every returned message satisfies content XOR tool_calls | gotcha #4 |
| 6 | `backend_only` makes the same turn with zero frontend calls and frontend usage 0 | arm B |
| 7 | `raw_data.fba.step` sums equal the scripted usage exactly. `turn` appears only on the final message. `filler_latency_s ≤ wall_s`. | metric capture |
| 8 | Backend client raises → adapter re-raises (strict) / returns the prototype's error text (lenient) | §5.3 |
| 9 | Frontend repair path with an empty assistant → placeholder substituted and counted, and `generate` validation passes | §5.4 |
| 10 | `get_init_state` with the τ² greeting, and with a tool-call history, in both modes | replay |
| 11 | `fba_report` on a synthetic `Results` → pass^k equals `compute_metrics`. Latency, TTFR and token aggregates match hand-computed values. Infra sims are excluded. The `llm_agent` fallback works. | reporting |
| 12 | `register()` is idempotent and exposes both names. An unconfigured domain fails at construction. | wiring |

The prototype's own 90 tests stay green, since we don't modify it. `make test` in τ² is unaffected.

---

## 10. Risks and gotchas specific to this agent

| # | Risk | Effect if ignored | Mitigation |
|---|---|---|---|
| R1 | Backend transport errors turn into user-facing text | Hub hiccups scored as agent failures, mostly in arm A | Re-raise (§5.3) and count `backend_error` in the report |
| R2 | `filler_text` optional on the committed prototype | Filler presence low, filler latency undefined for many turns | P1: commit the WIP that makes it required |
| R3 | Paired backend is stateless per delegation | More tool calls and tokens per turn. Policy steps that depend on earlier tool results must be re-derived from the frontend's query. Can cost accuracy. | This is the design under test. Arm B isolates it. The per-task token CSV and `delegation_query` in `raw_data` make it diagnosable. |
| R4 | Frontend "Unsupported" mode with a thin capability list | In-domain requests refused, score collapses | Broad `domains.yaml` lists. The report shows the direct-answer rate. The Rung 3 transcript gate. |
| R5 | `drop_params` silently drops `extra_body` | Frontend runs with reasoning on (slow filler) or backend without it | Per-role `reasoning_tokens` gate (Rung 2) |
| R6 | Latency under concurrency | Endpoint queueing inflates every latency number | Fixed and recorded `--max-concurrency`, identical across arms. Optionally a dedicated latency pass at concurrency 1–2 on a task subset. |
| R7 | LiteLLM retries inside `generate()` | A retried call's latency includes the failed attempt | Record `num_retries` and report the p95 alongside the mean. Retries also surface in `llm_debug` logs. |
| R8 | Frontend history window of 20 groups | Long telecom conversations can prune the earliest turns (e.g. user id) from the frontend's context | Keep the prototype default (it's the product). Report turns per sim and flag sims > 20 turns in the per-task CSV. |
| R9 | `generate()` `json.loads` on malformed tool args raises | Treated as a transport error (retried) rather than an agent error | Same behaviour as `llm_agent`, noted, not changed |
| R10 | Cost reads 0.0 for Hub models | `avg_agent_cost` meaningless | Tokens are the resource metric (§8.5) |
| R11 | The result is scaffold + models, not a model | Wrongly compared to leaderboard numbers | Label every result as scaffold-assisted, with both models named (`custom-agent-integration.md` §9) |

---

## 11. Bring-up ladder

| Rung | Command (sketch) | Gate |
|---|---|---|
| 0 | `uv run pytest tau2-fba/tests` | All offline tests green |
| 1 | `run_fba_eval.py --mode paired --domain mock --num-tasks 2 --num-trials 1`, then the same with `--mode backend_only` | Constructs, runs, no crash, both arms |
| 2 | Inspect `raw_data.fba.step.per_call` of Rung 1 | **Frontend reasoning_tokens = 0 on every call; backend > 0.** Filler present on delegations. `raw_data` and `usage` survived into `results.json`. |
| 3 | `tau2 view` on Rung 1 (and a 5-task airline run) | **Mandatory transcript gate:** only domain tool calls, structured. No `call_backend`, no filler text, no `"Sorry, I could not process that"`, no backend error text in the trajectory. Delegation queries are self-contained. No wrongful "Unsupported" replies. |
| 4 | airline, `base`, 50 tasks, `--num-trials 1`, both arms | Go/no-go. Near-zero pass^1 means a harness bug, not a result. |
| 5 | airline, `base`, `--num-trials 4`, both arms (+ arm C), then `fba_report.py` | **First reportable table:** Pass^1–4, latency, filler, TTFR, tokens |
| 6 | retail, then telecom, **one domain per process invocation** | Full sweep |

The driver mirrors `run_hermes_eval.py`: `--mode {paired,backend_only}`, `--domain`,
`--agent-llm` (backend), `--frontend-llm`, `--base-url`, `--api-key-env`, `--user-llm`,
`--user-llm-args`, `--num-trials`, `--task-split-name`, `--num-tasks`, `--max-concurrency`,
`--save-to` (default `fba_{mode}_{domain}`), `--auto-resume`, `--fba-config`, `--fba-event-log`,
`--lenient-transport-errors`, `--allow-dirty-prototype`.

---

## 12. Phases and deliverables

| Phase | Deliverable | Gate |
|---|---|---|
| P0 | Prototype WIP committed (P1). The SHA pinned into this plan. | clean tree |
| P1 | `prototype_import.py`, `config.py`, `domains.yaml`, `client.py` | tests 1, 2, 9 |
| P2 | `agent.py` (both factories), `__init__.register()`, `run_fba_eval.py` | tests 3–8, 10, 12. Rungs 1–3. |
| P3 | `metrics.py`, `fba_report.py` | test 11. Report produced for the Rung 1 runs. |
| P4 | Airline reportable run, all arms | Rungs 4–5 |
| P5 | Runbook `misc/prototypes/text-frontend-backend-agent-tau2-runbook.md`, `tau2-fba/README.md`, retail + telecom | Rung 6 |

The τ²-side diff at the end: `tau2-fba/**` and `misc/prototypes/**` only.
`git diff -- src/ tests/` stays empty.

---

## 13. Decisions to confirm before implementation

1. ~~**Arm C (`llm_agent` baseline, same backend model).**~~ *Settled: optional.* Not required
   for the requested metrics. Run it to separate the model from the prototype's backend prompt,
   or as a harness sanity check (runbook step 8).
2. **Filler latency definition.** The plan measures the non-streaming decision time (§8.4), which
   needs no prototype change. A true streaming time-to-filler would need a streaming `ChatClient`
   in the prototype and is out of scope unless requested.
3. **`domains.yaml` capability wording.** It needs one review by the prototype owner, since it
   affects the arm A score.
4. **Latency pass.** Report latency from the accuracy runs at the sweep's concurrency (simplest),
   or add a dedicated low-concurrency latency pass (cleaner numbers, extra cost)?
