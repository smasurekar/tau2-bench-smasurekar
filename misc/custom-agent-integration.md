# Integrating a Custom Agent into τ³-bench

**Status:** reference/runbook · **Date:** 2026-09-18 · **Repo:** `tau2-bench-smasurekar` @ `caca045` (v1.0.1)

**Short answer: yes, this is supported.** Custom agents are a first-class, documented extension
point — not a workaround. The framework treats the agent as a pluggable object behind a
two-method interface, resolved by name through a registry. `LLMAgent` (the default,
`--agent llm_agent`) is simply one implementation of that interface; yours can be another.

This doc is agent-agnostic. It covers what the runtime actually does, the interface contract,
which integration pattern fits your case, how to register and run, and the traps that produce
silently wrong scores.

---

## 1. First decide whether you need a custom agent

The most important decision in this doc, and the easy one to get wrong in the expensive
direction. Ask:

> **Does your model serve behind an OpenAI-compatible `/v1/chat/completions` endpoint that
> accepts a `tools=[...]` parameter and returns structured `tool_calls`?**

| Situation | What you need | Effort |
|-----------|---------------|--------|
| Yes, and you want to measure **the model** | **No custom agent.** Point the default agent at it: `--agent-llm 'openai/<model>' --agent-llm-args '{"api_base": "...", "api_key": "..."}'` | Zero code |
| The endpoint is text-in/text-out, or emits tool calls as text the server does not parse | Custom agent — **Pattern C** (§5) | ~150 lines |
| You want to evaluate a **scaffold** (planner, memory, retries, sub-agents) as a unit | Custom agent — **Pattern B** (§5) | Depends on scaffold |
| You want the default behaviour with one thing changed (prompt, sampling, a pre/post step) | Custom agent — **Pattern A** (§5) | ~20 lines |

> **Verify before you build.** Run the default agent against `mock` first (§7, Rung 0). If it
> works, you may need no custom agent at all. If it fails, the transcript tells you exactly
> which pattern you need and what format the model actually emits — which is the input to §5.

---

## 2. How the runtime works — the protocol

Worth reading before writing code, because several of the gotchas in §8 are direct
consequences of this design.

**It is entirely Python, in-process.** There is no wire protocol between the user simulator and
the agent — no HTTP, no sockets, no serialization, no IPC between them. `Orchestrator.run()`
is a loop calling methods on Python objects that live in the same process.

`src/tau2/orchestrator/orchestrator.py:819` is a three-role state machine. The orchestrator
holds `self.message`, `self.from_role`, `self.to_role` with role ∈ `{AGENT, USER, ENV}`, and
each `step()` delivers the current message to the current target:

```python
# USER/ENV -> AGENT
agent_msg, self.agent_state = self.agent.generate_next_message(self.message, self.agent_state)

# AGENT/ENV -> USER
user_msg, self.user_state = self.user.generate_next_message(self.message, self.user_state)

# AGENT/USER -> ENV
tool_results = self._execute_tool_calls(self.message.tool_calls)
```

That is the whole protocol: two method signatures and a Pydantic object passed between them.

```
                  ┌─────────────────────────────────────┐
                  │            Orchestrator             │
                  │   (from_role, to_role, message)     │
                  └──┬──────────────┬───────────────┬───┘
       generate_next_│message       │               │ get_response()
                     ▼              ▼               ▼
              ┌────────────┐  ┌──────────┐   ┌─────────────┐
              │   Agent    │  │ UserSim  │   │ Environment │
              │  (yours)   │  │          │   │ pure Python │
              └─────┬──────┘  └────┬─────┘   └─────────────┘
                    │              │              no LLM
                    │ HTTP         │ HTTP         no network
                    ▼              ▼
              your endpoint   user-sim endpoint
```

Network calls are **vertical, never horizontal.** The agent talks to its endpoint, the user
simulator talks to its endpoint, and they never talk to each other. Everything crossing the
middle of that diagram is a Python object reference.

### Routing is decided by one predicate

After each agent turn, `msg.is_tool_call()` decides where the message goes:

- `True` → `to_role = ENV`. The environment executes it synchronously in Python
  (`environment.get_response(tool_call)` — a real function over a real backing store, no model
  involved) and returns a `ToolMessage` straight back to the agent. One result arrives as
  `ToolMessage`; several arrive wrapped in a `MultiToolMessage`.
- `False` → `to_role = USER`. The text goes to the user simulator.

**This single predicate is why a tool call left as text inside `content` scores zero**: it is
not a parsing warning, it is a routing decision. The message gets delivered to the user
simulator, which reads your raw JSON as if it were customer-facing prose.

### The one genuine translation: `flip_roles()`

The user simulator is itself an LLM and needs the conversation from *its* point of view
(`src/tau2/user/user_simulator.py:232`):

```python
messages = state.system_messages + state.flip_roles()
```

Your agent's `AssistantMessage` becomes `role: "user"` in the simulator's own API call, and its
reply is re-typed as a `UserMessage`. Both sides believe they are the assistant. That is the
only impedance matching in the system.

### What this means for your agent

Whatever your agent does internally — a custom text format, five LLM calls per turn, a
retrieval step — is **invisible to everyone else**. By the time a message leaves
`generate_next_message()`, it must be a well-formed `AssistantMessage`, and that object is all
the orchestrator, the user simulator, the environment, and the evaluator ever see. The
contract is the object, not the text.

---

## 3. The interface contract

From `src/tau2/agent/base_agent.py` and `src/tau2/agent/AGENTS.md`:

```python
class HalfDuplexAgent(HalfDuplexParticipant[...], ABC, Generic[AgentState]):
    def __init__(self, tools: list[Tool], domain_policy: str): ...
```

| Eval type | Base class | Method you implement |
|-----------|-----------|----------------------|
| Text (turn-based) | `HalfDuplexAgent` | `generate_next_message(message, state) -> (AssistantMessage, State)` |
| Voice (tick-based) | `FullDuplexAgent` | `get_next_chunk(state, participant_chunk, tool_results) -> (AssistantMessage, State)` |

Both also require `get_init_state(message_history=None) -> State`, called once per task.

For text evals you implement exactly two methods. `message` arrives as
`UserMessage | ToolMessage | MultiToolMessage`; you return exactly one `AssistantMessage`.

**Three rules that are enforced, not advisory:**

1. **Either `content` OR `tool_calls`, never both.** `check_communication_error()`
   (`orchestrator.py:708`) raises `AgentError` and terminates the task with `AGENT_ERROR` when
   a message carries both, or when it is empty. `msg.validate()` runs on every returned
   message.
2. **`MultiToolMessage` must be unpacked.** It holds `.tool_messages` (a list) and arrives
   whenever the previous turn emitted several tool calls. Every reference implementation
   handles it with `state.messages.extend(message.tool_messages)`. Forgetting it silently drops
   tool results, and the agent loops until `max_steps`.
3. **If you add mixins, MRO order matters**: config mixins → capability mixins → protocol base
   class last. Getting it wrong makes `__init__` arguments silently disappear
   (`src/tau2/agent/AGENTS.md`).

---

## 4. The plug-in mechanism

```
tau2 run --agent my_agent
            |
            v
  registry.get_agent_factory("my_agent")           src/tau2/registry.py
            |
            v
  factory(tools=..., domain_policy=..., llm=..., llm_args=..., task=...,
          audio_native_config=..., audio_taps_dir=...)      src/tau2/runner/build.py:117
            |
            v
  your agent instance  ->  orchestrator drives generate_next_message() in a loop
```

Two facts this imposes on you, both verified in source:

1. **`build_agent` always passes all seven kwargs** (`src/tau2/runner/build.py:117`). Your
   factory *must* accept `**kwargs` or it will `TypeError` on `audio_native_config` even though
   you never use it.
2. **Tools and policy come from the environment, not from you** — `environment.get_tools()` and
   `environment.get_policy()`. You receive the domain's real tool objects and its real policy
   text. You do not choose them. (In `solo_mode` the user tools are folded in as well.)

### Use `generate()`, not a raw HTTP client

`tau2.utils.llm_utils.generate()` gives you, for free: LiteLLM provider routing, `num_retries`,
cost and token accounting into the results file, per-call logging keyed by `call_name`, and
message-history validation. Call it with `tools=None` when you want plain text back. Rolling
your own `requests.post` loses all of it, and your results file will report zero cost.

---

## 5. Three implementation patterns

### Pattern A — subclass the default agent

For small deviations: a different system prompt, a pre/post-processing step, custom sampling.

```python
from tau2.agent.llm_agent import LLMAgent

class MyAgent(LLMAgent):
    @property
    def system_prompt(self) -> str:
        return "..."  # your prompt, built from self.domain_policy / self.tools

def create_my_agent(tools, domain_policy, **kwargs):
    return MyAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
    )
```

You inherit correct state handling, `MultiToolMessage` unpacking, and structured tool calls.
Override `_generate_next_message` if you need more control over the turn itself.

### Pattern B — scaffold wrapper

For evaluating a scaffold as a unit. τ² calls your agent once per turn; what you do inside is
unconstrained — multiple LLM calls, retrieval, self-critique, sub-agents. The only requirement
is that one `AssistantMessage` comes back.

`examples/agents/react_agent.py` is the canonical worked example: two LLM calls per turn
(THINK without tools, then ACT with tools), with the reasoning trace kept ephemeral and out of
conversation history.

### Pattern C — protocol adapter (non-native tool calling)

The hard case: the framework speaks structured OpenAI tool calls; your model speaks text. The
agent is the translation layer, in both directions.

```
        τ² side (structured)                 wire side (plain text)
  ────────────────────────────────    ────────────────────────────────────
  ToolMessage(result JSON)       ->   user: <tool_response>{...}</tool_response>
  AssistantMessage(tool_calls=)  <-   assistant: <tool_call>{"name":...}</tool_call>
  tools: list[Tool]              ->   system: <tools>[ ...json schemas... ]</tools>
```

**Key design decision: keep two histories.** What you *return* must carry real `ToolCall`
objects (§2 — the evaluator scores `ACTION` off the recorded trajectory). What you *send on the
wire* should contain no `role: "tool"` messages, because an endpoint that was never sent a
`tools=` parameter will often reject a `tool` role, or an assistant message bearing
`tool_calls`, outright. Flattening tool results into plain user turns sidesteps that entire
class of problem.

Complete implementation — create `src/tau2/agent/my_agent.py`:

```python
"""Protocol-adapter agent for a text-in/text-out endpoint.

Adapts a plain text completion endpoint to tau2's structured tool-call protocol
by rendering tool schemas into the system prompt and parsing tool-call blocks out
of the model's text output.

Adjust TOOL_CALL_RE and SYSTEM_PROMPT to match the exact convention your model
emits -- verify against a real transcript before running a full sweep.
"""

import json
import re
import uuid
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict

from tau2.agent.base.llm_config import LLMConfigMixin
from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import (
    APICompatibleMessage,
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool
from tau2.utils.llm_utils import generate

SYSTEM_PROMPT = """\
You are a customer service agent that helps the user according to the <policy> below.

You have access to the following tools:
<tools>
{tool_schemas}
</tools>

In each turn you may either:
- Send a plain text message to the user, OR
- Call one or more tools.
You may NOT do both in the same turn.

To call a tool, emit one JSON object per call, each wrapped in <tool_call> tags:
<tool_call>{{"name": "tool_name", "arguments": {{"arg": "value"}}}}</tool_call>

Emit nothing else on a turn that contains a tool call. Tool results are returned to
you inside <tool_response> tags.

<policy>
{domain_policy}
</policy>"""

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


class MyAgentState(BaseModel):
    """Wire-format history: system/user/assistant only, never role=tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    system_messages: list[SystemMessage]
    messages: list[APICompatibleMessage]


class MyAgent(LLMConfigMixin, HalfDuplexAgent[MyAgentState]):
    """Half-duplex agent for a text-in/text-out endpoint."""

    def __init__(
        self,
        tools: List[Tool],
        domain_policy: str,
        llm: str,
        llm_args: Optional[dict] = None,
    ):
        super().__init__(
            tools=tools,
            domain_policy=domain_policy,
            llm=llm,
            llm_args=llm_args,
        )

    @property
    def system_prompt(self) -> str:
        schemas = "\n".join(
            json.dumps(t.openai_schema["function"]) for t in self.tools
        )
        return SYSTEM_PROMPT.format(
            tool_schemas=schemas,
            domain_policy=self.domain_policy,
        )

    def get_init_state(
        self, message_history: Optional[list[Message]] = None
    ) -> MyAgentState:
        state = MyAgentState(
            system_messages=[
                SystemMessage(role="system", content=self.system_prompt)
            ],
            messages=[],
        )
        for m in message_history or []:
            self._append_to_wire(m, state)
        return state

    # ---- inbound: structured -> text -------------------------------------

    def _append_to_wire(
        self, message: ValidAgentInputMessage, state: MyAgentState
    ) -> None:
        """Flatten any inbound message into a plain user/assistant wire turn."""
        if isinstance(message, MultiToolMessage):
            for tm in message.tool_messages:
                self._append_to_wire(tm, state)
        elif isinstance(message, ToolMessage):
            state.messages.append(
                UserMessage(
                    role="user",
                    content=f"<tool_response>\n{message.content}\n</tool_response>",
                )
            )
        else:
            state.messages.append(message)

    # ---- outbound: text -> structured ------------------------------------

    def _parse(self, raw: str) -> Optional[list[ToolCall]]:
        """Extract tool calls from the model's text. None if it is a user reply."""
        blocks = TOOL_CALL_RE.findall(raw or "")
        if not blocks:
            return None
        calls = []
        for block in blocks:
            try:
                payload = json.loads(block)
                calls.append(
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:16]}",
                        name=payload["name"],
                        arguments=payload.get("arguments") or {},
                        requestor="assistant",
                    )
                )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                # Malformed call: fall through and treat the whole turn as text
                # so the conversation can recover instead of crashing the task.
                logger.warning(f"Unparseable tool_call block: {e}")
                return None
        return calls

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: MyAgentState
    ) -> tuple[AssistantMessage, MyAgentState]:
        self._append_to_wire(message, state)

        raw = generate(
            model=self.llm,
            tools=None,  # text-only endpoint: never send a tools= parameter
            messages=state.system_messages + state.messages,
            call_name="my_agent_response",
            **self.llm_args,
        )
        text = raw.content or ""

        # Record the model's verbatim output on the wire so it sees its own turns.
        state.messages.append(AssistantMessage(role="assistant", content=text))

        tool_calls = self._parse(text)
        if tool_calls:
            # content MUST be None when tool_calls is set -- never both.
            out = AssistantMessage(
                role="assistant",
                content=None,
                tool_calls=tool_calls,
                cost=raw.cost,
                usage=raw.usage,
            )
        else:
            out = AssistantMessage(
                role="assistant",
                content=text,
                cost=raw.cost,
                usage=raw.usage,
            )
        return out, state


def create_my_agent(tools, domain_policy, **kwargs):
    """Factory for the registry.

    Must accept **kwargs: build_agent() always passes llm, llm_args, task,
    audio_native_config and audio_taps_dir, regardless of whether you use them.
    """
    return MyAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
    )
```

---

## 6. Registration — two paths

This is where the one real gotcha lives.

### The gotcha

`src/tau2/cli.py:68-72`:

```python
parser.add_argument(
    "--agent",
    type=str,
    default=DEFAULT_AGENT_IMPLEMENTATION,
    choices=get_options().agents,     # <-- read from the registry at parser-build time
    ...
)
```

`choices` is frozen when the `tau2` CLI builds its parser, from whatever the registry holds at
that moment. A module the `tau2` package never imports is never registered, so
`--agent my_agent` fails argparse validation with an *invalid choice* error before any of your
code runs. **You cannot register from outside the package and use the `tau2 run` CLI.**

### Path A — edit the registry (recommended; enables the CLI)

Two lines in `src/tau2/registry.py`:

```python
# with the other agent imports at the top
from tau2.agent.my_agent import create_my_agent

# next to registry.register_agent_factory(create_llm_agent, "llm_agent")  (line ~297)
registry.register_agent_factory(create_my_agent, "my_agent")
```

Verify:

```bash
tau2 run --help | grep -A2 '\-\-agent '   # my_agent should appear in the choices
```

This is the path `src/tau2/agent/README.md` documents for core agents, and the only one that
gives you `--auto-resume`, `--max-concurrency`, checkpointing, and the standard results layout
with no extra code. The diff is two lines and rebases cleanly on upstream.

`register_agent_factory` also takes two optional arguments worth knowing about:

```python
registry.register_agent_factory(
    create_my_agent,
    "my_agent",
    task_filter=MyAgent.check_valid_task,   # skip tasks your agent cannot run
    metadata={"solo_mode": True},           # e.g. run without a user simulator
)
```

### Path B — driver script (no repo edits, no CLI)

Register at runtime and call the batch runner directly. The runner uses `ThreadPoolExecutor`
(`src/tau2/runner/batch.py:1005`), not process pools, so a registration made in your script is
visible to every worker — concurrency still works.

```python
# scripts/run_my_agent.py
from tau2.data_model.simulation import TextRunConfig
from tau2.registry import registry
from tau2.runner import run_domain

from tau2.agent.my_agent import create_my_agent

registry.register_agent_factory(create_my_agent, "my_agent")

run_domain(
    TextRunConfig(
        domain="airline",
        agent="my_agent",
        llm_agent="openai/<your-model>",
        llm_args_agent={
            "temperature": 0.0,
            "api_base": "https://<your-endpoint>/v1",
        },
        llm_user="openai/<user-sim-model>",
        num_trials=4,
        max_concurrency=8,
        save_to="my_agent_airline_base",
    )
)
```

Use Path B when you cannot or do not want to touch tracked files. Use Path A otherwise.

> **Do not put API keys in `src/tau2/config.py` or any tracked file.** Keep them in `.env` /
> exported env vars, consistent with the judge wiring in `misc/judge-rewire-plan.md`.

---

## 7. Bring-up ladder

Do not start with a full sweep. Each rung is a cheap gate on a specific failure mode.

| Rung | Command | Gate |
|------|---------|------|
| **0** | `tau2 run --domain mock --agent-llm 'openai/<model>' --num-tasks 2 --num-trials 1` | Does the **default** agent already work? If yes, §1 says you may not need a custom agent. |
| **1** | `tau2 run --domain mock --agent my_agent --agent-llm 'openai/<model>' --num-tasks 2 --num-trials 1` | Agent constructs, endpoint responds, no crash. |
| **2** | `tau2 view` on the Rung 1 run | **The critical inspection.** Read the transcript. Are tool calls showing as structured calls, or as raw text in an assistant message that got delivered to the user simulator? |
| **3** | `--domain airline --task-set-name test --num-trials 1` (20 tasks) | Go/no-go. A near-zero score here is a scaffold bug, not a model result. |
| **4** | `--domain airline --num-trials 4` (50 tasks, `base` split) | First reportable, judge-independent number. |
| **5** | `retail`, then `telecom` | Full sweep. `telecom` is dual-control — the agent must talk the user through steps. |

**Rung 2 is not optional.** It is the only step that catches the failure where everything
"runs" and the score is quietly zero because tool calls never became real actions.

---

## 8. Gotchas

| # | Trap | Symptom | Fix |
|---|------|---------|-----|
| 1 | Factory does not accept `**kwargs` | `TypeError: unexpected keyword argument 'audio_native_config'` | `def create_my_agent(tools, domain_policy, **kwargs)` |
| 2 | `MultiToolMessage` not unpacked | Agent repeats tool calls; loops until `max_steps` | Recurse over `.tool_messages` |
| 3 | Tool calls left as text in `content` | Runs complete, `ACTION` reward 0 everywhere | Return real `ToolCall` objects (§2; Rung 2 catches this) |
| 4 | `content` **and** `tool_calls` both set | `AgentError`, task ends as `AGENT_ERROR` | Set `content=None` whenever `tool_calls` is non-empty |
| 5 | Registered outside the package, CLI used | `argparse: invalid choice: 'my_agent'` | Path A, or use Path B's driver script |
| 6 | `role: "tool"` sent to a text-only endpoint | 400 from the server, or silently degraded context | Flatten tool results into plain user turns (Pattern C) |
| 7 | Raw HTTP client instead of `generate()` | No cost/usage in results, no retries, no LLM logs | Use `tau2.utils.llm_utils.generate` |
| 8 | `ToolCall.id` left empty | Result correlation can break with parallel calls | Generate a unique id per call |
| 9 | Agent name reused | `ValueError: Agent factory my_agent already registered` | Register once; guard with `if "my_agent" not in registry.get_agents()` |
| 10 | Mixin order wrong | `__init__` arguments silently vanish | Config mixins → capability mixins → protocol base last |
| 11 | `uv sync` core-only | `ModuleNotFoundError: No module named 'websockets'` on any `tau2` command | `uv pip install websockets` or `uv sync --extra voice` (pre-existing repo issue, see `misc/inference-hub-benchmark.md` §2) |

---

## 9. Reporting and comparability

A custom agent changes what the number means, so state it explicitly in any writeup:

- **Published τ-bench leaderboard numbers all use the default `llm_agent`.** A custom-agent
  score is **not** directly comparable to them — it measures model + scaffold, not model.
- If the question is *"how good is this model?"*, the comparable configuration is the default
  agent (§1 row 1). Use a custom agent only when the model cannot be driven that way, and then
  label the result as scaffold-assisted.
- If the question is *"how good is our agent product?"*, the custom agent is exactly right —
  report both the agent and the underlying model.
- The strongest writeup runs **both** arms where possible: default agent vs. custom agent, same
  model, same domains, same `--num-trials`. That isolates the scaffold's contribution.
- Keep `--user-llm` and the judge fixed across every arm. They are measurement apparatus;
  changing them invalidates the comparison.
- Note the version: results from tau2-bench < 1.0.1 are not comparable with >= 1.0.1 on
  `banking_knowledge`.

---

## 10. References

| What | Where |
|------|-------|
| Agent developer guide | `src/tau2/agent/README.md` |
| Contributor rules (MRO, message constraints) | `src/tau2/agent/AGENTS.md` |
| Orchestrator / communication modes | `src/tau2/orchestrator/README.md` |
| Runnable examples | `examples/agents/minimal_text_agent.py`, `react_agent.py`, `custom_agent_eval.py` |
| Default agent implementation | `src/tau2/agent/llm_agent.py` |
| Registry + registration | `src/tau2/registry.py` (agent factories at ~line 297) |
| Agent construction | `src/tau2/runner/build.py::build_agent` (line 100) |
| Half-duplex step loop | `src/tau2/orchestrator/orchestrator.py:819` |
| Communication validation | `src/tau2/orchestrator/orchestrator.py:708` |
| CLI `--agent` flag | `src/tau2/cli.py:68` |
| Default agent name | `src/tau2/config.py::DEFAULT_AGENT_IMPLEMENTATION` |
| Endpoint routing / model strings | `misc/inference-hub-benchmark.md` §3 |
| Judge wiring and secret handling | `misc/judge-rewire-plan.md` |
