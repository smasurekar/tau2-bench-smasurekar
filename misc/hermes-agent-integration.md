# Integrating the Hermes Agent into τ³-bench (text-in / text-out)

**Status:** design + runbook · **Date:** 2026-09-21 · **Revision:** 3 (post-review 2)

**Revision 3** closes five gaps found by re-reading the Hermes source: Tool Search hides the
whole τ² toolset by default (H15); three core prompt blocks contradict τ² task semantics and a
fourth probes your git workspace (H16, H17); the prompt is model-gated in a way that confounds
within-arm comparison (H18); and the bridge's two timeouts expired together (H19). All are fixed
from `$HERMES_HOME/config.yaml` (§3) plus one construction-time assertion (§5) — still zero τ²
changes, and no Hermes source changes either.
**τ² repo:** `tau2-bench-smasurekar` @ `60cfc92` (v1.0.1) · **Hermes repo:** `hermes-agent-smasurekar` (`hermes-agent` 0.21.3)

Companion to `misc/custom-agent-integration.md`, which covers the τ² side of the contract
generically. This one is Hermes-specific.

**Just want to run it?** See `misc/hermes-agent-runbook.md` — step-by-step, with the
NVIDIA Inference Hub / Nemotron 3 Ultra configuration. This document is the *why*.

**The requirement:** `HermesHalfDuplexAgent(HalfDuplexAgent)` receives the domain's
`tools: list[Tool]` from τ², and the real Hermes agent inside it must *emit those tools as tool
calls* — τ²'s environment executes them, results flow back into Hermes' loop.

**Non-requirement, and a hard constraint:** τ² itself is the measuring instrument. This
integration adds **zero changes to the τ² codebase** (§6).

---

## 1. Which Hermes surface can carry τ²'s tools

| Surface | Accepts caller-supplied `tools=[...]`? | Verdict |
|---|---|---|
| OpenAI-compatible HTTP API (`/v1/chat/completions`) | **No.** The body's `tools` field is read only as a response-cache fingerprint (`gateway/platforms/api_server_openai_routes.py:561`). Docs are explicit: tool calls *"were already executed server-side by the Hermes agent … never as pending calls for the client to execute"* (`website/docs/user-guide/features/api-server.md:147`). | ✗ |
| ACP / TUI-gateway JSON-RPC | Session protocols; the agent owns its tools. | ✗ |
| MCP (Hermes as MCP *client*) | Yes, indirectly — τ² tools exposed as an MCP server. | △ Fallback (§9) |
| **Python library (`from run_agent import AIAgent`) + tool-registry injection** | **Yes.** `tools/registry.py:649 register(name, toolset, schema, handler, …)`; `toolsets.py:444 create_custom_toolset()`; `AIAgent(enabled_toolsets=[...])` restricts the agent to exactly that group. | ✓ **Recommended** |

So: **Pattern B (scaffold wrapper) from the companion doc §5, in-process.** Not Pattern C — do
not render schemas into a text prompt and regex calls back out; Hermes has native structured
tool calling and just needs to be pointed at τ²'s tools.

This also rules out the "no custom agent, just `--agent-llm`" path: Hermes' endpoint is an
*agent*, not a model proxy, so it would answer from its own terminal/browser toolset with no
access to the domain database — a guaranteed zero.

---

## 2. Architecture: two loops, one blocking bridge

Both systems want to own the tool-execution loop. τ² is a state machine — the agent *returns* a
tool call and is re-entered with the result (`orchestrator.py:819`). Hermes is a blocking loop —
`run_conversation()` calls handlers itself and returns only when the turn ends
(`agent/conversation_loop.py:1579`).

Resolution: run Hermes on a **worker thread**; the injected handler **blocks on a private
queue**. It hands the call out to τ², τ² executes it against the real environment, and the
result comes back as the handler's return value. Hermes never learns its tools are remote.

```
  τ² main thread (orchestrator)                 Hermes worker thread
  ─────────────────────────────                 ────────────────────────────
  generate_next_message(UserMessage)
        │ start thread ───────────────────────► run_conversation(text, history)
        │                                              │  LLM call
        │                                              ▼
        │                                       handler(args, session_id=…)
        │  ◄─ ("tool_call", call_id, name, args) ─ bridge.call_tool()  [BLOCKS on
        ▼                                              ┆   its own queue]
  AssistantMessage(tool_calls=[ToolCall(id=call_id)])  ┆
        │                                              ┆
  environment.get_response() → ToolMessage(id=call_id) ┆
        │                                              ┆
  generate_next_message(ToolMessage)                   ┆
        │ ── send_result(msg.id, content) ─────────────►┆
        │                                       returns result string
        │                                              │  LLM call … (loops)
        │  ◄── ("final", result_dict) ────────── run_conversation returns
        ▼
  AssistantMessage(content=final_response)  ──► user simulator
```

**Correlation is by id, not by queue order.** Every call gets a `call_id`, minted as the
`ToolCall.id`. τ²'s environment echoes it verbatim into the result
(`src/tau2/environment/environment.py:485`: `ToolMessage(id=message.id, …)`), so results route
back to the exact waiting call. Each call owns a private `Queue(maxsize=1)` that is discarded
when the call completes or times out, so a late result can never be handed to a different call.

Other consequences:

- **One τ² turn per Hermes tool call.** Custom-registered tools are not parallel-safe
  (`agent/tool_dispatch_helpers.py:159`), so Hermes executes them sequentially; each blocks in
  turn and becomes its own `AssistantMessage`. `ACTION` checks score the recorded trajectory and
  are unaffected by batching.
- **The user simulator sees only Hermes' turn-final text.** All internal reasoning, retries and
  multi-step tool work stay inside one τ² turn — the "scaffold as a unit" semantics.
- **τ² `max_steps` counts tool-call turns**, so cap Hermes' `max_iterations` (§5).
- **Handlers route by `session_id`, never by thread.** Hermes passes `task_id`/`session_id` into
  every handler as kwargs (`model_tools.py:820`). Do not use `threading.local` or a `ContextVar`.
- **`registry.dispatch()` swallows every exception** (`tools/registry.py:888`) and converts it to
  a tool-error string. An abort therefore *cannot* be signalled by raising: Hermes would keep
  looping on error strings until `max_iterations`. Teardown must use `hard_interrupt()` (§5).

---

## 3. Environment: one interpreter, both packages

`import tau2` and `import run_agent` must both work in one interpreter. Hermes exact-pins its
deps (`openai==2.24.0`, `httpx[socks]==0.28.1`, …) and ships no wheel — it is supported only
from its checkout under `uv`. τ² depends on `litellm`, whose ranges accommodate those pins. So
install **τ² into Hermes' environment**, not the reverse:

```bash
cd ~/Desktop/Swapnil/github_repos/hermes-agent-smasurekar
uv sync
uv pip install -e ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
uv pip install websockets            # companion-doc gotcha #11
uv pip install -e ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-hermes  # adapter (§6)

uv run python -c "
from run_agent import AIAgent
from tau2.registry import registry
print('both importable', len(registry.get_domains()))"
```

Both projects intersect at Python `>=3.12,<3.14`. If that verification fails, fix it before
writing any agent code; if the pins prove irreconcilable, go to §9.

### Launcher environment

Hermes needs its own credentials (`OPENROUTER_API_KEY`, or `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`)
and a configured model. τ²'s `--user-llm` and judge keys stay in τ²'s `.env`. Keep both out of
tracked files.

Give the benchmark a **dedicated Hermes home** so a sweep never touches the user's real Hermes
memory, sessions, trajectories, cron or skills (`hermes_constants.py:102`):

```bash
mkdir -p ~/.hermes-tau2
cp ~/.hermes/.env ~/.hermes/config.yaml ~/.hermes-tau2/   # provider config only

export HERMES_HOME=~/.hermes-tau2
export HERMES_YOLO_MODE=1        # MUST be exported before the process starts
```

`HERMES_YOLO_MODE` is frozen at import time by design (`tools/approval.py:45`) — a per-call read
would let anything in the process bypass approvals. Set it in the shell, never from Python. It
is a safety net only: no τ² domain tool is approval-gated.

### Benchmark `config.yaml` — required, not cosmetic

`$HERMES_HOME/config.yaml` is the only lever that reaches Hermes' prompt assembly and tool
presentation from outside its source tree (`hermes_cli/config.py:489` resolves it under
`HERMES_HOME`). Two of these settings change *what is measured*; the run is not valid without
them.

```yaml
tools:
  # REQUIRED. Without this every tau2 tool is hidden behind the tool_search /
  # tool_describe / tool_call bridge -- see H15.
  tool_search:
    enabled: off

agent:
  # Prompt hygiene -- see H16, H17, H18.
  coding_context: off               # no coding brief, no live `git status` of the cwd
  task_completion_guidance: false   # "never stop before the task is done"
  parallel_tool_call_guidance: false
  tool_use_enforcement: false       # "never end your turn without a tool call"
  execution_guidance: false
```

Pin `tool_search.enabled` to the literal `off`, not `auto`: the docs state `auto` is an alias of
`on` today and is reserved for a future budget-gated mode
(`website/docs/user-guide/features/tool-search.md`).

**Optional — replace Hermes' identity block instead of appending to it.** `_identity_parts`
(`agent/system_prompt.py:542`) uses `$HERMES_HOME/SOUL.md` *in place of* `DEFAULT_AGENT_IDENTITY`
when the file exists, but only if the soul read is enabled — with `skip_context_files=True` that
requires `load_soul_identity=True`. Writing the customer-service persona and the domain policy
into `SOUL.md` and passing `hermes_args={"load_soul_identity": True}` puts them in the identity
slot rather than bolted on after a block that says something else (H16).

The trade-off is real and is a judgment call, not a default: `SOUL.md` is per-`HERMES_HOME`
static state while `domain_policy` varies by domain, so the launcher must write it per run. Since
Hermes' registry already forces one process per domain (§6), that is one extra file write — but
it does move prompt content out of the agent module and into the filesystem. Start without it;
adopt it only if rung 3 transcripts show the persona conflict actually biting.

---

## 4. `tau2_hermes/hermes_agent.py`, part 1 — the tool bridge

The adapter lives in its **own package** (§6) so τ² stays byte-identical. Naming and layout
still follow `src/tau2/agent/README.md`: one module per agent family, state class beside the
agent, factory `create_<registry_name>`.

Hermes is imported lazily inside the functions that need it.

```python
"""Hermes agent: runs the Hermes scaffold (github.com/NousResearch/hermes-agent) as a
tau2 half-duplex agent.

Each tau2 domain Tool is registered into Hermes' tool registry with a handler that
executes nothing: it hands the call out to the tau2 orchestrator and blocks until the
orchestrator returns the matching ToolMessage. Hermes therefore emits tau2's domain
tools as real tool calls, and tau2's environment executes them.

See misc/hermes-agent-integration.md.
"""

import json
import queue
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from tau2.environment.tool import Tool

TAU2_TOOLSET = "tau2_domain"

_BRIDGES: dict[str, "ToolBridge"] = {}
_BRIDGES_LOCK = threading.Lock()

_INSTALLED: dict[str, frozenset[str]] = {}   # toolset -> tool names served by this process
_INSTALL_LOCK = threading.Lock()


@dataclass
class _PendingCall:
    """One in-flight tool call and its private, single-use response queue."""

    call_id: str
    name: str
    response: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))


class ToolBridge:
    """Rendezvous between Hermes' tool handlers and the tau2 orchestrator.

    One bridge per tau2 simulation, keyed by the Hermes session_id. Results are
    correlated by call_id -- never by queue order -- so a late or timed-out result
    can never be delivered to a different call.
    """

    def __init__(self, session_id: str, tool_timeout: float = 900.0):
        self.session_id = session_id
        self.tool_timeout = tool_timeout
        self.events: queue.Queue = queue.Queue()          # bridge -> tau2
        self._pending: dict[str, _PendingCall] = {}
        self._lock = threading.Lock()
        self._aborted = threading.Event()

    # ---- Hermes worker-thread side -------------------------------------

    def call_tool(self, name: str, args: dict) -> str:
        """Block until tau2 returns this call's result.

        Always returns a string. Raising is useless here: registry.dispatch()
        catches every exception (tools/registry.py:888) and Hermes would keep
        looping on the error text.
        """
        if self._aborted.is_set():
            return json.dumps({"error": "tau2 run aborted"})

        call = _PendingCall(call_id=f"call_{uuid.uuid4().hex[:16]}", name=name)
        with self._lock:
            self._pending[call.call_id] = call
        self.events.put(("tool_call", (call.call_id, name, args)))
        try:
            kind, payload = call.response.get(timeout=self.tool_timeout)
        except queue.Empty:
            logger.error(f"tau2 did not answer {name} (id={call.call_id}) in {self.tool_timeout}s")
            return json.dumps({"error": f"tool call timed out after {self.tool_timeout}s"})
        finally:
            # Dropped here, so any later result for this id is unroutable by construction.
            with self._lock:
                self._pending.pop(call.call_id, None)
        if kind == "abort":
            return json.dumps({"error": "tau2 run aborted"})
        return payload

    # ---- tau2 main-thread side -----------------------------------------

    def next_event(self, timeout: float) -> tuple[str, Any]:
        return self.events.get(timeout=timeout)

    def send_result(self, call_id: str, content: str) -> bool:
        """Route a ToolMessage back to its originating call. False if unmatched."""
        with self._lock:
            call = self._pending.get(call_id)
        if call is None:
            return False
        with suppress(queue.Full):
            call.response.put_nowait(("tool_result", content))
        return True

    def abort(self) -> None:
        """Release every blocked handler. Idempotent."""
        self._aborted.set()
        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for call in pending:
            with suppress(queue.Full):
                call.response.put_nowait(("abort", None))

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


def register_bridge(bridge: ToolBridge) -> None:
    with _BRIDGES_LOCK:
        _BRIDGES[bridge.session_id] = bridge


def unregister_bridge(session_id: str) -> None:
    with _BRIDGES_LOCK:
        _BRIDGES.pop(session_id, None)


def _make_handler(tool_name: str):
    """Hermes handler: handler(args: dict, **kwargs) -> str."""

    def handler(args: dict, **kwargs) -> str:
        session_id = str(kwargs.get("session_id") or "")
        with _BRIDGES_LOCK:
            bridge = _BRIDGES.get(session_id)
        if bridge is None:
            return json.dumps({"error": f"no tau2 bridge for session {session_id!r}"})
        return bridge.call_tool(tool_name, args or {})

    return handler


def install_tau2_toolset(tools: list[Tool], toolset: str = TAU2_TOOLSET) -> str:
    """Register the domain's tools into Hermes' process-global registry.

    Idempotent for the same tool set (many simulations share one process).
    Raises on a *different* tool set: Hermes' registry is process-global, so one
    process serves exactly one domain.
    """
    from model_tools import get_tool_definitions  # Hermes
    from tools.registry import registry  # Hermes
    from toolsets import create_custom_toolset  # Hermes

    names = frozenset(t.name for t in tools)
    with _INSTALL_LOCK:
        installed = _INSTALLED.get(toolset)
        if installed is not None:
            if installed != names:
                raise RuntimeError(
                    "Hermes' tool registry is process-global: this process already serves "
                    f"{len(installed)} tau2 tools and cannot switch to a different set. "
                    "Run one domain per process (see misc/hermes-agent-integration.md section 6)."
                )
            return toolset  # same domain, another simulation

        # Never shadow a Hermes builtin, and never let one shadow us: refuse instead
        # of passing override=True, which would silently replace whichever lost.
        builtins = {d["function"]["name"] for d in get_tool_definitions(quiet_mode=True)}
        clash = names & builtins
        if clash:
            raise RuntimeError(
                f"tau2 tool names collide with Hermes builtin tools: {sorted(clash)}. "
                "Resolve before running: one of the two would be shadowed."
            )

        create_custom_toolset(toolset, "tau2-bench domain tools", sorted(names))
        for tool in tools:
            fn = tool.openai_schema["function"]
            registry.register(
                name=fn["name"],
                toolset=toolset,
                schema={
                    "description": fn.get("description") or fn["name"],
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                },
                handler=_make_handler(fn["name"]),
            )
        _INSTALLED[toolset] = names
    logger.debug(f"Installed {len(names)} tau2 tools into Hermes toolset {toolset!r}")
    return toolset


def uninstall_tau2_toolset(toolset: str = TAU2_TOOLSET) -> None:
    """Process-level teardown: call once after the whole run, never from an agent's
    stop() -- concurrent simulations share these registrations."""
    from tools.registry import registry  # Hermes

    with _INSTALL_LOCK:
        for name in _INSTALLED.pop(toolset, frozenset()):
            with suppress(Exception):
                registry.deregister(name)
```

Four load-bearing details:

1. `registry.register()` takes the **function-level** schema (`description` + `parameters`) and
   injects `name` itself (`tools/registry.py:836`). τ²'s `Tool.openai_schema["function"]` is
   exactly that shape (`src/tau2/environment/tool.py:140`).
2. Registration must precede `AIAgent(...)` — `agent/agent_init.py:1076` snapshots `agent.tools`
   at construction. Late registration bumps the registry generation, which is part of the
   tool-def cache key (`model_tools.py:273`), but an already-built agent keeps its stale list.
3. Handlers must return a **string** (or Hermes' multimodal dict); anything else becomes a
   contract error (`tools/registry.py:856`). `ToolMessage.content` is already a string.
4. `registry.register()` can **fail silently**: the shadow-rejection path logs and returns
   without raising (`tools/registry.py:687`). It therefore cannot be trusted as its own proof of
   success — the assertion in `_build_hermes` (§5) is what actually verifies the tool surface.

---

## 5. `tau2_hermes/hermes_agent.py`, part 2 — the agent

Same module, continued; these imports join those in §4.

```python
from pydantic import BaseModel, ConfigDict, Field

from tau2.agent.base_agent import AgentError, HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)

AGENT_INSTRUCTION = """\
You are a customer-service agent talking to a real customer over chat.

Follow the policy below exactly. Use ONLY the tools provided to you; they are the
only way to read or change any real data. Never claim to have done something you
did not do with a tool call. Do not ask a human supervisor, and do not use a
terminal, a browser, or the filesystem.

End your turn with a message to the customer whenever you need information from
them or have finished the requested work.

<policy>
{domain_policy}
</policy>"""


def _to_hermes_history(messages: list[Message]) -> list[dict]:
    """Convert a tau2 message history to OpenAI-shaped rows for Hermes.

    Assistant tool calls and their results are preserved: dropping them would hand
    Hermes a history in which actions it already took are invisible. The filter
    matches tau2's own is_valid_agent_history_message (base_agent.py:37).
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, UserMessage) and not m.is_tool_call():
            out.append({"role": "user", "content": m.content or ""})
        elif isinstance(m, AssistantMessage) and m.is_tool_call():
            out.append({
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ],
            })
        elif isinstance(m, AssistantMessage):
            out.append({"role": "assistant", "content": m.content or ""})
        elif isinstance(m, ToolMessage) and m.requestor == "assistant":
            out.append({"role": "tool", "tool_call_id": m.id, "content": m.content or ""})
    return out


class HermesAgentState(BaseModel):
    """Hermes' own wire history, carried across tau2 turns."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    hermes_history: list[dict] = Field(default_factory=list)
    turn_active: bool = False


class HermesHalfDuplexAgent(HalfDuplexAgent[HermesAgentState]):
    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        model: Optional[str] = None,
        hermes_args: Optional[dict] = None,
        max_iterations: int = 30,
        turn_timeout: float = 900.0,
        tool_timeout: Optional[float] = None,
    ):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.model = model
        self.hermes_args = dict(hermes_args or {})
        self.max_iterations = max_iterations
        self.turn_timeout = turn_timeout
        # Strictly longer than the orchestrator's wait, so exactly one side fires on a
        # stall: tau2 raises AgentError and stop() unwinds, instead of racing the
        # handler's own timeout string back into Hermes' loop.
        self.tool_timeout = resolve_tool_timeout(turn_timeout, tool_timeout)
        # HERMES_YOLO_MODE is NOT set here: Hermes freezes it at import
        # (tools/approval.py:45), so a constructor write is too late -- and is the
        # escalation path that freeze defends against. It belongs in the shell (section 3).
        self.toolset = install_tau2_toolset(tools)
        self._session_id = f"tau2-{uuid.uuid4().hex[:12]}"
        self._bridge: Optional[ToolBridge] = None
        self._thread: Optional[threading.Thread] = None
        self._hermes: Any = None
        self._prev_cost: float = 0.0

    # ---- lifecycle -------------------------------------------------------

    def _build_hermes(self):
        try:
            from run_agent import AIAgent  # Hermes
        except ImportError as e:
            raise ImportError(
                "hermes-agent is not importable in this interpreter. See "
                "misc/hermes-agent-integration.md section 3 -- tau2 must be installed "
                "into the Hermes uv environment."
            ) from e

        hermes = AIAgent(
            model=self.model or "",
            session_id=self._session_id,
            enabled_toolsets=[self.toolset],
            ephemeral_system_prompt=AGENT_INSTRUCTION.format(domain_policy=self.domain_policy),
            max_iterations=self.max_iterations,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            save_trajectories=False,
            **self.hermes_args,
        )
        check_tool_surface({t.name for t in self.tools}, hermes)
        return hermes


def check_tool_surface(expected: set[str], hermes: Any) -> None:
    """Raise unless what Hermes will show the model is exactly tau2's tools.

        This is the single check that catches, at construction time, every way the
        exposed surface can silently diverge:
          - tool_search deferral replacing the domain tools with the search bridge
            (H15) -- the default config activates it;
          - a registration that no-opped instead of raising (tools/registry.py:687);
          - toolset filtering being bypassed, e.g. context-compressor schemas appended
            after the filter (agent/agent_init.py:2003);
          - an AIAgent built before install_tau2_toolset() (H1).
    Without it, each of these reads downstream as a bad model, not a bad harness.

    Module-level and duck-typed on purpose: the tests drive it with a stub that has a
    .tools list, so the regression test needs no Hermes import.
    """
    actual = {
        t["function"]["name"]
        for t in (getattr(hermes, "tools", None) or [])
        if isinstance(t, dict) and isinstance(t.get("function"), dict)
    }
    if actual == set(expected):
        return
    missing = sorted(set(expected) - actual)
    unexpected = sorted(actual - set(expected))
    hint = ""
    if {"tool_search", "tool_describe", "tool_call"} & set(unexpected):
        hint = (
            " Set tools.tool_search.enabled: off in $HERMES_HOME/config.yaml "
            "(see misc/hermes-agent-integration.md section 3)."
        )
    raise AgentError(
        "Hermes' tool surface does not match the tau2 domain tools. "
        f"missing={missing} unexpected={unexpected}.{hint}"
    )

    def get_init_state(
        self, message_history: Optional[list[Message]] = None
    ) -> HermesAgentState:
        self._bridge = ToolBridge(self._session_id, tool_timeout=self.tool_timeout)
        register_bridge(self._bridge)
        self._hermes = self._build_hermes()
        return HermesAgentState(hermes_history=_to_hermes_history(message_history or []))

    def set_seed(self, seed: int) -> None:
        """Called by the orchestrator whenever a seed is set (orchestrator.py:527);
        tau2's default seed is 300, so this fires on every ordinary run. The base implementation is a
        no-op that only logs. Runs before get_init_state(), so mutating hermes_args works.
        """
        overrides = dict(self.hermes_args.get("request_overrides") or {})
        overrides["seed"] = seed
        self.hermes_args["request_overrides"] = overrides

    def stop(self, message=None, state=None) -> None:
        """Tear the simulation down in an order that cannot hang.

        hard_interrupt() first so Hermes' loop unwinds instead of continuing on the
        error strings abort() produces (registry.dispatch swallows exceptions), then
        release blocked handlers, then join. Tool *registrations* are process-global
        and are NOT removed here -- concurrent simulations share them (see
        uninstall_tau2_toolset).
        """
        try:
            hard_interrupt = getattr(self._hermes, "hard_interrupt", None)
            if callable(hard_interrupt):
                hard_interrupt("tau2 simulation ended")   # agent/interrupt_control.py:209
        except Exception:
            logger.debug("Hermes hard_interrupt failed", exc_info=True)

        if self._bridge is not None:
            self._bridge.abort()
            unregister_bridge(self._session_id)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                logger.error(f"Hermes worker {self._session_id} did not exit; thread leaked")

        close = getattr(self._hermes, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Hermes close() failed", exc_info=True)
        self._hermes = None

    # ---- turn machinery --------------------------------------------------

    def _start_turn(self, user_text: str, state: HermesAgentState) -> None:
        bridge, history = self._bridge, list(state.hermes_history)

        def _run() -> None:
            try:
                result = self._hermes.run_conversation(
                    user_text, conversation_history=history, task_id=uuid.uuid4().hex
                )
                bridge.events.put(("final", result))
            except Exception as e:
                bridge.events.put(("error", e))

        self._thread = threading.Thread(target=_run, daemon=True, name=self._session_id)
        state.turn_active = True
        self._thread.start()

    def _deliver(self, message: ValidAgentInputMessage) -> None:
        """Route results back by id. tau2 echoes ToolCall.id into ToolMessage.id
        (environment.py:485), so this is exact even with several calls in flight."""
        if isinstance(message, MultiToolMessage):
            for tm in message.tool_messages:
                self._deliver(tm)
        elif isinstance(message, ToolMessage):
            if not self._bridge.send_result(message.id, message.content or ""):
                logger.warning(f"Dropped unmatched/late tool result id={message.id!r}")
        else:
            raise AgentError(f"Unexpected message type mid-turn: {type(message).__name__}")

    def _cost_delta(self, result: dict) -> Optional[float]:
        total = result.get("estimated_cost_usd")
        if total is None:
            return None
        delta = float(total) - self._prev_cost
        self._prev_cost = float(total)
        return max(delta, 0.0)

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: HermesAgentState
    ) -> tuple[AssistantMessage, HermesAgentState]:
        if isinstance(message, UserMessage):
            if state.turn_active:
                raise AgentError("User message arrived while a Hermes turn was still open")
            self._start_turn(message.content or "", state)
        else:
            self._deliver(message)

        try:
            kind, payload = self._bridge.next_event(timeout=self.turn_timeout)
        except queue.Empty:
            raise AgentError(f"Hermes turn exceeded {self.turn_timeout}s")

        if kind == "tool_call":
            call_id, name, args = payload
            # content MUST be None when tool_calls is set (orchestrator.py:708).
            return (
                AssistantMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=call_id,          # echoed back as ToolMessage.id
                            name=name,
                            arguments=args if isinstance(args, dict) else {},
                            requestor="assistant",
                        )
                    ],
                ),
                state,
            )

        if kind == "error":
            raise AgentError(f"Hermes turn failed: {payload!r}")

        result: dict = payload
        state.turn_active = False
        state.hermes_history = list(result.get("messages") or state.hermes_history)
        text = (result.get("final_response") or "").strip()
        if not text:
            logger.warning("Hermes returned an empty final response")
            text = "I'm sorry, could you please repeat that?"
        usage = {
            k: result[k]
            for k in ("input_tokens", "output_tokens", "total_tokens")
            if result.get(k) is not None
        }
        return (
            AssistantMessage(
                role="assistant",
                content=text,
                cost=self._cost_delta(result),
                usage=usage or None,
            ),
            state,
        )


def create_hermes_agent(tools, domain_policy, **kwargs):
    """Factory for tau2's registry.

    Must accept **kwargs: build_agent() always passes llm, llm_args, task,
    audio_native_config and audio_taps_dir (src/tau2/runner/build.py:117).
    """
    llm_args = dict(kwargs.get("llm_args") or {})
    return HermesHalfDuplexAgent(
        tools=tools,
        domain_policy=domain_policy,
        model=kwargs.get("llm"),
        hermes_args=llm_args.pop("hermes_args", None),
        max_iterations=llm_args.pop("max_iterations", 30),
        turn_timeout=llm_args.pop("turn_timeout", 900.0),
        tool_timeout=llm_args.pop("tool_timeout", None),
    )
```

### Argument mapping

| τ² config | Lands on | Note |
|---|---|---|
| `llm_agent="anthropic/claude-sonnet-4.6"` | `AIAgent(model=...)` | **OpenRouter-format string**, not LiteLLM. Hermes resolves the provider itself. |
| `llm_args_agent={"max_iterations": 20}` | consumed by the factory | |
| `llm_args_agent={"turn_timeout": …, "tool_timeout": …}` | consumed by the factory | `tool_timeout` defaults to `turn_timeout + 60` so the two never expire together (H19). |
| `llm_args_agent={"hermes_args": {"load_soul_identity": True}}` | `AIAgent(load_soul_identity=True)` | Only with a `SOUL.md` in `$HERMES_HOME` — replaces Hermes' identity block (§3, H16). |
| `llm_args_agent={"hermes_args": {...}}` | `AIAgent(**hermes_args)` | Any Hermes constructor kwarg (`run_agent.py:251`): `base_url`, `api_key`, `provider`, `max_tokens`, `reasoning_config`, … |
| `seed` (default `300`) | `hermes_args["request_overrides"]["seed"]` via `set_seed` | Verify the provider honours it; otherwise say so when reporting. |
| `llm_user` | unchanged | The user simulator still goes through LiteLLM. |

**Cost and usage come from Hermes, not LiteLLM.** The turn result carries session-cumulative
`estimated_cost_usd` and token counts (`agent/turn_finalizer.py:30-34,561`), hence the delta.
The companion doc's "always use `generate()`" rule cannot apply here; this is the compensating
mechanism.

### Conformance with `src/tau2/agent/README.md` / `AGENTS.md`

| House rule | This agent |
|---|---|
| Constructor `(tools: list[Tool], domain_policy: str)` | ✓ extra args keyword-only with defaults |
| State class is a Pydantic `BaseModel`, hosted in the agent module | ✓ `HermesAgentState` |
| `content` XOR `tool_calls`; tool calls never chunked | ✓ |
| Factory `create_<registry_name>`, module name == registry name | ✓ `create_hermes_agent`, `hermes_agent.py` ↔ `"hermes_agent"` |
| MRO: config mixins → capability mixins → protocol base last | n/a — single base, no mixins |
| Module lives in `src/tau2/agent/` | **Deviation, deliberate.** The adapter lives in its own package so τ² stays unmodified (§6). Naming and structure still follow the README. |
| Concrete agents do not name their protocol (`LLMAgent`, not `LLMHalfDuplexAgent`) | **Deviation, by request.** Class is `HermesHalfDuplexAgent` as specified; `HermesAgent` would be the house name. |
| "For LLM-powered agents, add `LLMConfigMixin`" | **Omitted.** It supplies `self.llm`/`llm_args` for `tau2.utils.llm_utils.generate()`, which Hermes never calls. `--agent-llm` maps to Hermes' `model` in the factory. But the mixin also carries `set_seed()` — hence the explicit override above, since the orchestrator calls it on every run (`orchestrator.py:527`, `DEFAULT_SEED = 300`). |
| `is_stop()` | Default `False` is correct here: the plain `LLMAgent` does not override it either. The `###STOP###` convention in `AGENTS.md` belongs to `LLMSoloAgent`/voice. A solo-mode variant would additionally need a `done` tool and an `is_stop` override (`llm_agent.py:329-431`). |

---

## 6. Running it — τ² stays unmodified

The adapter is a separate package; the factory is registered at runtime and the batch runner is
called directly. **No file in `tau2-bench-smasurekar` is edited.**

It lives at `tau2-hermes/` *inside* the τ² checkout, so it is versioned alongside the plan and
the runs it produces. That placement is additive, not a modification: no τ² file changes, no τ²
module imports it, and `git diff upstream/main -- src/ tests/` stays empty. The one interaction
is that τ²'s pytest config sets no `testpaths`, so a root-level `pytest` recurses here and runs
this package's 14 tests alongside τ²'s own (971 → 985 collected). They are offline and take
~0.1 s, and the two that need `hermes-agent` skip when it is not importable.
`tau2-hermes/conftest.py` is what makes that work: it puts the package on `sys.path`, so the
tests import cleanly with no `PYTHONPATH`. Deleting it does not hide them — it turns them into a
collection error. Use `--ignore=tau2-hermes` for a τ²-only run.

```
tau2-hermes/
├── conftest.py                # keeps these tests out of tau2's root pytest run
├── pyproject.toml
├── tau2_hermes/
│   ├── __init__.py
│   └── hermes_agent.py        # sections 4 + 5
├── run_hermes_eval.py
├── tools/
│   └── inspect_hermes_surface.py   # rungs 1.5 / 1.6 (section 8)
└── tests/test_hermes_agent.py # section 7
```

```python
# run_hermes_eval.py
import argparse

from tau2.data_model.simulation import TextRunConfig
from tau2.runner import run_domain

import tau2_hermes
from tau2_hermes.hermes_agent import uninstall_tau2_toolset

# tau2_hermes.register() does this, idempotently:
#   registry.register_agent_factory(create_hermes_agent, "hermes_agent")
AGENT_NAME = tau2_hermes.register()

parser = argparse.ArgumentParser()
parser.add_argument("--domain", default="airline")
parser.add_argument("--agent-llm", required=True)
parser.add_argument("--user-llm", required=True)
parser.add_argument("--num-trials", type=int, default=1)
parser.add_argument("--task-set-name", default=None)
parser.add_argument("--max-concurrency", type=int, default=1)
parser.add_argument("--save-to", default=None)
args = parser.parse_args()

try:
    run_domain(
        TextRunConfig(
            domain=args.domain,
            agent=AGENT_NAME,
            llm_agent=args.agent_llm,
            llm_user=args.user_llm,
            num_trials=args.num_trials,
            task_set_name=args.task_set_name,
            max_concurrency=args.max_concurrency,
            save_to=args.save_to or f"hermes_{args.domain}",
        )
    )
finally:
    uninstall_tau2_toolset()
```

`run_domain` (`src/tau2/runner/batch.py:1085`) is what the CLI itself calls: it validates the
config, loads and filters tasks, writes the standard results layout, and prints the metrics
table. `max_concurrency`, `auto_resume`, `task_set_name` and `save_to` are all `RunConfig`
fields, so the driver loses nothing but the `tau2 run` command line. The batch runner uses a
`ThreadPoolExecutor` (`src/tau2/runner/batch.py:1005`), so a runtime registration is visible to
every worker.

**One domain per process.** Hermes' tool registry is process-global; `install_tau2_toolset`
raises if a second, different tool set is requested (§4). Sweep several domains with several
invocations of this script, not a loop inside one.

> If you later want `tau2 run --agent hermes_agent` on the CLI, that requires editing
> `src/tau2/registry.py`, because `--agent` `choices` are frozen at parser-build time
> (`src/tau2/cli.py:68`). That is a two-line guarded edit (companion doc §6, Path A) — but it
> means the benchmark harness is no longer pristine. Prefer this driver.

---

## 7. Tests

Put these in the adapter package; they need no network and no LLM — construct a `ToolBridge`
directly and drive it from a fake worker thread.

| Test | Asserts |
|---|---|
| `test_tool_executed_exactly_once` | A Hermes turn with N tool calls produces N `AssistantMessage`s with `tool_calls`, and the τ² environment executes each call exactly once — no duplicates from a retried or re-delivered result. |
| `test_concurrent_sessions_are_isolated` | Two bridges with different `session_id`s, both blocked in `call_tool`; a result sent to one never reaches the other's handler. Drive `_make_handler(...)(args, session_id=...)` from two threads. |
| `test_late_result_is_dropped` | Let a call time out (`tool_timeout=0.1`), then `send_result(call_id, …)` → returns `False`, and a *subsequent* call on the same bridge receives only its own result. This is the regression test for the shared-queue bug. |
| `test_stop_releases_everything` | With a handler blocked, `stop()` returns within the join timeout, `bridge.pending_count() == 0`, the worker thread is not alive, and `threading.active_count()` is back to the pre-test baseline. |
| `test_history_roundtrip` | `_to_hermes_history` on a history containing an assistant tool call plus its `ToolMessage` yields `assistant` (with `tool_calls`) **and** a `role: "tool"` row carrying the same `tool_call_id`. |
| `test_second_domain_rejected` | `install_tau2_toolset(airline_tools)` then `install_tau2_toolset(retail_tools)` raises; the same set twice is a no-op. |
| `test_tool_surface_assertion_rejects_drift` | `check_tool_surface` on a stub exposing the `tool_search`/`tool_describe`/`tool_call` bridge raises `AgentError` and names `tool_search` in the message; on a stub exposing exactly the domain tools it returns. Needs no Hermes import — pass any object with a `.tools` list. This is the unit-level half of rung 1.5. |
| `test_timeouts_do_not_race` | `resolve_tool_timeout` returns strictly more than `turn_timeout` by default; an explicit value passes through, and survives the factory round-trip. |
| `test_unknown_session_returns_error_string_not_raise` | A call with no registered bridge returns JSON error text rather than raising — `registry.dispatch` would swallow a raise and loop (H3). |
| `test_abort_releases_blocked_handlers` | `abort()` unblocks every waiting handler, is idempotent, returns `threading.active_count()` to baseline, and makes subsequent calls return immediately. |

---

## 8. Bring-up ladder

| Rung | Command (from the Hermes checkout, via `uv run`) | Gate |
|---|---|---|
| **0** | `python -c "from run_agent import AIAgent; import tau2"` | §3 environment is real. |
| **0.5** | `tau2 domain airline` | Read the policy and real tool signatures before judging transcripts (`src/tau2/agent/README.md`). |
| **1** | `pytest tests/test_hermes_agent.py` | §7 passes — bridge semantics are right before any model is involved. |
| **1.5 / 1.6** | `uv run python tools/inspect_hermes_surface.py airline` — the script below | **The config gate, before any model spend.** 1.5: the printed tool list must be exactly the domain's tools. 1.6: read the prompt once, by eye. Both run offline; no provider call is made. |
| **2** | `python run_hermes_eval.py --domain mock --agent-llm '<model>' --user-llm '<model>'` with `--num-trials 1` | Constructs, runs, terminates. |
| **3** | `tau2 view` on the rung-2 run | **Not optional.** Are tool calls structured and routed to the environment, or text delivered to the user simulator? Is the turn-final text sensible prose? |
| **4** | `--task-set-name test` on `airline` (20 tasks) | Go/no-go. Near-zero here is a scaffold bug, not a model result. |
| **5** | `--num-trials 4 --max-concurrency 4` on `airline` | First reportable number. Watch memory and `threading.active_count()`. |
| **6** | `retail`, then `telecom` — **separate invocations** | Full sweep; `telecom` is dual-control. |

### The rung 1.5 / 1.6 inspector

Put this in the adapter package as `tools/inspect_hermes_surface.py`. It constructs the agent
exactly as τ² will and prints the two things that decide whether the run measures anything.

```python
"""Print the tool surface and system prompt Hermes will actually use for a tau2 domain.

Offline: constructs the agent and renders the prompt, never calls a provider.
Run it after any change to $HERMES_HOME/config.yaml. See sections 3 and 8.
"""

import sys

from tau2.runner.build import build_environment

from tau2_hermes.hermes_agent import create_hermes_agent, uninstall_tau2_toolset

domain = sys.argv[1] if len(sys.argv) > 1 else "airline"
model = sys.argv[2] if len(sys.argv) > 2 else "anthropic/claude-sonnet-4.6"

env = build_environment(domain)
agent = create_hermes_agent(
    tools=env.get_tools(), domain_policy=env.get_policy(), llm=model, llm_args={}
)
try:
    # Builds the AIAgent. check_tool_surface raises here if the surface is wrong.
    agent.get_init_state()
    hermes = agent._hermes

    # ---- Rung 1.5: the tool surface ----
    names = sorted(
        t["function"]["name"] for t in (hermes.tools or []) if isinstance(t, dict)
    )
    print(f"# {len(names)} tools exposed to the model\n")
    print("\n".join(names))

    # ---- Rung 1.6: the effective system prompt ----
    # AIAgent._build_system_prompt is a lazy forwarder to
    # agent.system_prompt.build_system_prompt(agent, system_message)
    # (run_agent.py:1104, agent/lazy_forward.py), so it renders at construction
    # time without running a turn -- _cached_system_prompt is still None here.
    # ephemeral_system_prompt is NOT part of it: Hermes appends that at API-call
    # time (chat_completion_helpers.py:2071), so append it the same way to see
    # what the model really receives.
    core = hermes._build_system_prompt(None)
    effective = (core + "\n\n" + (hermes.ephemeral_system_prompt or "")).strip()
    print(f"\n\n# system prompt ({len(effective)} chars)\n")
    print(effective)
finally:
    agent.stop()
    uninstall_tau2_toolset()
```

Read the output against this checklist:

| Look for | Verdict if present |
|---|---|
| `tool_search`, `tool_describe`, `tool_call` in the tool list | `tools.tool_search.enabled: off` is missing (H15). `check_tool_surface` should already have raised |
| Any name that is not a domain tool | Toolset filtering was bypassed — most likely compressor schemas (`agent_init.py:2003`) |
| A `git status` / branch / workspace block, or a coding brief | `agent.coding_context: off` is missing (H17) |
| "Keep working until the task is actually complete" / "Never end your turn with a promise of future action" | `agent.task_completion_guidance` / `agent.tool_use_enforcement` are still on (H16) |
| "# Execution discipline" or "# Parallel tool calls" | `agent.execution_guidance` / `agent.parallel_tool_call_guidance` are still on (H16, H18) |
| "You are Hermes Agent, built by Nous Research" | Expected unless you adopted `SOUL.md` (§3) — record it (§12) |
| The domain policy, at the end | Correct: `ephemeral_system_prompt` is appended last, so it holds the recency position |

`_build_system_prompt` has one harmless side effect — it caches the stable tier on the agent and
drains queued truncation warnings. That is irrelevant in a throwaway inspection process; do not
call it inside a scoring run.

---

## 9. Gotchas

Everything in the companion doc §8 still applies. Hermes-specific additions:

| # | Trap | Symptom | Fix |
|---|---|---|---|
| H1 | `AIAgent` built before `install_tau2_toolset()` | Hermes has no τ² tools; ACTION reward 0 | Register first — `agent_init.py:1076` snapshots `agent.tools` |
| H2 | Results correlated by queue order instead of id | Under concurrency or after a timeout, a result reaches the wrong call; silently wrong scores | Per-call id + private queue (§4); `ToolMessage.id == ToolCall.id` (`environment.py:485`) |
| H3 | Signalling abort by raising | `registry.dispatch` swallows it (`tools/registry.py:888`); Hermes loops on error strings to `max_iterations` | `hard_interrupt()` + return an error string (§5) |
| H4 | `deregister` called from an agent's `stop()` | Concurrent simulations lose their tools mid-run | Deregister once per process (`uninstall_tau2_toolset`) |
| H5 | `enabled_toolsets` not set | Hermes brings `terminal`, `browser`, `web_search`, `memory` into a benchmark task | `enabled_toolsets=[TAU2_TOOLSET]` |
| H6 | Two domains in one process | Wrong tools, or a silent `override=True` shadow | `install_tau2_toolset` raises (§4); one process per domain |
| H7 | Approval gate fires | Turn hangs on a human decision | Export `HERMES_YOLO_MODE=1` **in the shell** — frozen at import (`tools/approval.py:45`) |
| H8 | Hermes `max_iterations` left unlimited (`run_agent.py:256`) | One turn burns τ²'s whole `max_steps` budget | Cap at ~20-30 |
| H9 | Initial history flattened to text | Tool calls the task already performed become invisible to Hermes | `_to_hermes_history` preserves `tool_calls` and `role: "tool"` rows (§5) |
| H10 | `set_seed` not overridden | *"Setting seed … not implemented"* warning; seed never reaches Hermes | Override it (§5); fires on every run (`DEFAULT_SEED = 300`) |
| H11 | Empty `final_response` | `AgentError` → `AGENT_ERROR`, scored as failure | Fallback string (§5); investigate if frequent |
| H12 | `parameters` carrying `$defs`/`$ref` | Some providers 400 on `$ref` in tool schemas | τ² emits `params.model_json_schema()` (`tool.py:147`); inline `$defs` if your provider rejects them |
| H13 | Hermes' own system prompt still present | `ephemeral_system_prompt` **layers on top of** the core prompt (`chat_completion_helpers.py:2071`); `run_conversation(system_message=…)` is also additive (`system_prompt.py:696`), so there is no "replace" lever short of patching Hermes | Gate what can be gated (H16/H17); `SOUL.md` replaces the identity block (§3); budget for the remainder and report it (§12) |
| H14 | Memory / context files on | Cross-task contamination | `skip_memory`, `skip_context_files`, `skip_background_review`, `HERMES_HOME` (§3) |
| H15 | **Tool Search left at its default** | Every τ² tool is deferred behind `tool_search`/`tool_describe`/`tool_call` and the model never sees a domain schema. Calls still route correctly (the bridge is unwrapped in `model_tools.handle_function_call`, so `ToolCall.name` stays right) — so it is not a zero, it is a *quiet* depression: extra discovery round-trips, `max_iterations` burned, and a number that reads as model quality | `tools.tool_search.enabled: off` in `$HERMES_HOME/config.yaml` (§3). Default is `auto`, which is an alias of **on** (`tool_search.py:40,64`); `tau2_domain` is neither core nor in `_DIRECT_SURFACE_TOOLSETS`, so every tool is deferrable (`:142-154`) and `should_activate` fires on the first one (`:189`). `check_tool_surface` catches it at construction |
| H16 | Core guidance blocks contradict the task | `TASK_COMPLETION_GUIDANCE` ("keep working until the task is complete", `prompt_builder.py:380`) and `TOOL_USE_ENFORCEMENT_GUIDANCE` ("never end your turn with a promise of future action", `:345`) fight τ²'s requirement to stop and ask the customer, or to confirm before a write. Failure mode: the agent assumes a missing value and acts — `ACTION` fails on argument equality, and the confirmation turn `COMMUNICATE` wants never happens | `agent.task_completion_guidance: false`, `agent.tool_use_enforcement: false`, `agent.execution_guidance: false`, `agent.parallel_tool_call_guidance: false` (§3). Identity block (`"You are Hermes Agent…"` + a terseness prior that works against `NL_ASSERTION`) is replaceable only via `SOUL.md` |
| H17 | `coding_context` left at `auto` | `_coding_parts` is gated only on the agent having tools (`system_prompt.py:594`), **not** on `skip_context_files`. In a git repo with code — i.e. the checkout you launch from — `_detect_profile` turns on the coding posture (`coding_context.py:262-277`) and injects the coding brief plus a live `git status` snapshot of your cwd into a customer-service agent's prompt | `agent.coding_context: off` (§3). Verify at rung 1.6 |
| H18 | Comparing models *within* the Hermes arm | `TOOL_USE_ENFORCEMENT_MODELS` and `EXECUTION_GUIDANCE_MODELS` (`prompt_builder.py:359,373`) cover gpt/codex/grok/deepseek/kimi/qwen/glm/mistral but **not Claude**, so two models get materially different system prompts and the delta is not purely model quality | The §3 `false` settings remove the gate entirely, making the prompt model-invariant. Record the resolved set either way (§12) |
| H19 | One timeout for both waits | The bridge's per-call wait and the orchestrator's `next_event` wait expire together; the `AgentError` races the handler's timeout string back into Hermes' loop | `tool_timeout = turn_timeout + 60` by default (§5) |

---

## 10. Blast radius

**τ² codebase: unchanged.** No new files, no edits to `registry.py`, `src/tau2/agent/`,
`__init__.py`, the orchestrator, evaluator, user simulator, domains or task sets. `--agent
llm_agent` runs bit-for-bit as before and remains the comparable baseline. The only coupling is
that the driver imports τ² as a library and registers a factory in its own process.

**Isolation from the user's Hermes:** `skip_memory`, `skip_context_files`,
`skip_background_review`, `save_trajectories=False`, `HERMES_HOME=~/.hermes-tau2`, and
`enabled_toolsets` (no terminal/browser/filesystem/network). `session_db` is left `None`
(`agent/agent_init.py:1158`); the one lazy-`acquire()` path is `session_search`, which is not in
the τ² toolset.

**Measurement integrity:**

- *Tool-name collisions:* none today — no name across all 245 τ² domain tool functions collides
  with Hermes' core tool list. `install_tau2_toolset` now re-checks this at runtime and raises
  rather than shadowing (§4), so a future domain cannot break it silently.
- *Concurrency:* tool dispatch is safe — one process serves one domain, registrations are
  identical across its simulations, and results route by `session_id` + `call_id`, with
  `session_id` threaded from `agent.session_id` through to the handler
  (`agent/tool_executor.py:1585` → `model_tools.py:821`). Hermes' *init* does write
  process-global state (`os.environ["HERMES_SESSION_ID"]` and a gateway session context,
  `agent_init.py:1120-1142`; `model_tools._last_resolved_tool_names`), which concurrent
  simulations race. That race is not reachable here: every reader of those globals is a tool
  outside the τ² toolset — skills templating, `terminal_tool_background`, `code_kernel`,
  `kanban_tools`, `vision_tools`, and `execute_code`'s sandbox list. It becomes reachable the
  moment anything is added to `enabled_toolsets`. Rung 5 is the confirmation.
- *Scoring path untouched:* real `ToolCall` objects, executed by τ²'s environment; `ACTION` /
  `NL_ASSERTION` / `COMMUNICATE` read the same trajectory as always. Judge wiring
  (`misc/judge-rewire-plan.md`) is unaffected.
- *Prompt surface:* the §3 `config.yaml` removes the blocks that actively contradict the task
  (H16), the coding posture and cwd probe (H17), and the model-gated guidance that would
  otherwise confound within-arm model comparisons (H18). What remains of Hermes' core prompt —
  the identity block unless `SOUL.md` replaces it, the Hermes-docs pointer, the steering-channel
  note, the tool-guidance block, a timestamp and runtime-environment hints — is small, benign,
  and reported (§12) rather than removed.
- *Inside the measurement, deliberately:* Hermes' remaining core prompt under the domain policy,
  its context compression, retries and iteration cap. That is the scaffold — which is why §12
  insists on reporting the paired `llm_agent` arm.

**Resource hygiene:** one `AIAgent`, one worker thread and one bridge per concurrent task.
`stop()` interrupts, aborts pending calls, joins with a timeout and logs a leak if the thread
survives; the orchestrator calls it on every exit path (`orchestrator.py:250,779`). Workers are
daemons, so a hard kill never hangs the runner.

---

## 11. Fallback: out-of-process via MCP

If §3 cannot be made to resolve, invert the plumbing: expose the τ² tools as an **MCP server** in
the τ² process and let a Hermes subprocess connect. Hermes is a full MCP client with `sse` and
`streamable_http` transports (`tools/mcp_tool_transport.py:385,415`) and registers discovered
tools under a per-server toolset (`tools/mcp_tool_registration.py:347`). The bridge semantics are
unchanged; you add a per-task server and port, generated Hermes MCP config, subprocess lifecycle,
and a text channel for the conversation turn. More moving parts and worse latency — use only if
the single-environment install genuinely fails.

---

## 12. Reporting

- A `hermes_agent` number measures **Hermes-the-scaffold + the model**, not the model. It is
  **not** comparable to published τ-bench leaderboard numbers, which all use `llm_agent`.
- Run **both arms** where the model is also reachable as a plain tool-calling endpoint: default
  `llm_agent` vs. `hermes_agent`, same model, domains and trials. That difference is the
  scaffold's contribution and is the headline result.
- Hold the user simulator and judge fixed across arms (`misc/judge-rewire-plan.md`).
- Record Hermes version (0.21.3), τ² version (1.0.1), model string, `max_iterations`, and
  whether `seed` was honoured.
- **Record the prompt and tool configuration, not just the model.** Hermes' prompt is assembled
  from model-gated blocks (H18) and `config.yaml` keys, so "which model" does not identify the
  run. Capture, per run: the `$HERMES_HOME/config.yaml` `tools.tool_search` and `agent.*` keys
  from §3, whether `SOUL.md` was used, and the tool-name list `check_tool_surface` validated.
  Archiving the config file alongside the results directory is the cheapest way to do this.
- Note that what remains of Hermes' core system prompt sits under the domain policy (H13), and
  that the blocks which contradicted the task were gated off rather than left in (H16, H17) —
  a `hermes_agent` number measured with them left on is a different experiment, not a noisier
  version of the same one.

---

## 13. Reference index

| What | Where |
|---|---|
| Step-by-step run instructions | `misc/hermes-agent-runbook.md` |
| Plain-model (no-scaffold) baseline on the same endpoint | `misc/inference-hub-benchmark.md` |
| τ² custom-agent contract, registry, gotchas | `misc/custom-agent-integration.md` |
| τ² agent developer guide / directory rules | `src/tau2/agent/README.md`, `src/tau2/agent/AGENTS.md` |
| Runnable agent examples | `examples/agents/minimal_text_agent.py`, `react_agent.py` |
| τ² half-duplex base class | `src/tau2/agent/base_agent.py:52` |
| Valid-history filter | `src/tau2/agent/base_agent.py:37` |
| τ² tool schema | `src/tau2/environment/tool.py:140` |
| `ToolMessage.id` echoes `ToolCall.id` | `src/tau2/environment/environment.py:485` |
| τ² agent construction (7 kwargs) | `src/tau2/runner/build.py:117` |
| Batch entry point / thread pool | `src/tau2/runner/batch.py:1085`, `:1005` |
| Message validation, `stop()`, `set_seed()` call sites | `src/tau2/orchestrator/orchestrator.py:708,779,527` |
| Default seed | `src/tau2/config.py:15` |
| Hermes `AIAgent` constructor | `run_agent.py:251` |
| Hermes turn loop / result dict | `agent/conversation_loop.py:1579`, `agent/turn_finalizer.py:30,561` |
| Hermes `hard_interrupt` | `agent/interrupt_control.py:209` |
| Hermes registry `register` / `deregister` / `dispatch` | `tools/registry.py:649,727,874` |
| Dispatch swallows exceptions | `tools/registry.py:888` |
| `session_id`/`task_id` passed to handlers | `model_tools.py:820` |
| Tool-definition assembly / toolset filtering | `model_tools.py:213`, `agent/agent_init.py:1076` |
| Runtime toolset creation | `toolsets.py:444` |
| Parallel-execution gating | `agent/tool_dispatch_helpers.py:159` |
| Approval freeze | `tools/approval.py:45` |
| Hermes home resolution | `hermes_constants.py:102` |
| Hermes HTTP API ignores client `tools` | `gateway/platforms/api_server_openai_routes.py:561`; `website/docs/user-guide/features/api-server.md:147` |
| Tool Search: deferral rule, activation, config | `tools/tool_search.py:142-154`, `:189`, `:100-113`; `website/docs/user-guide/features/tool-search.md` |
| `config.yaml` resolves under `HERMES_HOME` | `hermes_cli/config.py:489` |
| System-prompt assembly (tiers, identity, guidance gates) | `agent/system_prompt.py:542,551,594,659` |
| Guidance block text and model gates | `agent/prompt_builder.py:158,345,380,408`; gates `:359,373` |
| Guidance config keys | `hermes_cli/config_defaults.py:146,150`; `agent/agent_init.py:1339-1340` |
| Coding posture detection | `agent/coding_context.py:192-195,262-277` |
| `SOUL.md` replaces the identity block | `agent/prompt_builder.py:1492`; `agent/system_prompt.py:542` |
| `AIAgent._build_system_prompt` is a lazy forwarder | `run_agent.py:1104`; `agent/lazy_forward.py:14` |
| `ephemeral_system_prompt` appended at API-call time | `agent/chat_completion_helpers.py:2071`; `agent/turn_context.py:1153` |
| `session_id` reaches the handler | `agent/tool_executor.py:1585` → `model_tools.py:821` |
| Silent no-op in `register()` | `tools/registry.py:687` |
| Compressor schemas appended past the toolset filter | `agent/agent_init.py:2003` |
| Hermes as a Python library | `website/docs/guides/python-library.md` |
