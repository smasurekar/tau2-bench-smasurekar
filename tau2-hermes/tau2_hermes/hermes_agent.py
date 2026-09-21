"""Hermes agent: runs the Hermes scaffold (github.com/NousResearch/hermes-agent) as a
tau2 half-duplex agent.

Each tau2 domain Tool is registered into Hermes' tool registry with a handler that
executes nothing: it hands the call out to the tau2 orchestrator and blocks until the
orchestrator returns the matching ToolMessage. Hermes therefore emits tau2's domain
tools as real tool calls, and tau2's environment executes them.

See ../misc/hermes-agent-integration.md (same repo).
"""

import json
import queue
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger
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
from tau2.environment.tool import Tool

TAU2_TOOLSET = "tau2_domain"

#: Name this agent is registered under in tau2's registry.
AGENT_NAME = "hermes_agent"

_BRIDGES: dict[str, "ToolBridge"] = {}
_BRIDGES_LOCK = threading.Lock()

_INSTALLED: dict[str, frozenset[str]] = {}  # toolset -> tool names served by this process
_INSTALL_LOCK = threading.Lock()


# =============================================================================
# THE TOOL BRIDGE
# =============================================================================


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

    def __init__(self, session_id: str, tool_timeout: float = 960.0):
        self.session_id = session_id
        self.tool_timeout = tool_timeout
        self.events: queue.Queue = queue.Queue()  # bridge -> tau2
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
            logger.error(
                f"tau2 did not answer {name} (id={call.call_id}) in {self.tool_timeout}s"
            )
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
    """Hermes handler: handler(args: dict, **kwargs) -> str.

    Routes by session_id, never by thread. Hermes threads every handler call with
    task_id/session_id (model_tools.py:821, sourced from agent.session_id at
    agent/tool_executor.py:1585), so a ContextVar or threading.local would be both
    unnecessary and wrong under Hermes' own worker threads.
    """

    def handler(args: dict, **kwargs) -> str:
        session_id = str(kwargs.get("session_id") or "")
        with _BRIDGES_LOCK:
            bridge = _BRIDGES.get(session_id)
        if bridge is None:
            return json.dumps({"error": f"no tau2 bridge for session {session_id!r}"})
        return bridge.call_tool(tool_name, args or {})

    return handler


# =============================================================================
# TOOL REGISTRATION
# =============================================================================


_HERMES_MISSING = (
    "hermes-agent is not importable in this interpreter. tau2 must be installed "
    "INTO the Hermes uv environment, not the reverse -- Hermes exact-pins its deps "
    "and ships no wheel. See misc/hermes-agent-integration.md section 3."
)


def _import_hermes():
    """Hermes' registry entry points, or a message that says what to do about it.

    Hermes is imported lazily and only here: the module must stay importable for the
    offline tests and for tau2 registry introspection.
    """
    try:
        from model_tools import get_tool_definitions  # Hermes
        from tools.registry import registry  # Hermes
        from toolsets import create_custom_toolset  # Hermes
    except ImportError as e:
        raise ImportError(_HERMES_MISSING) from e
    return get_tool_definitions, registry, create_custom_toolset


def install_tau2_toolset(tools: list[Tool], toolset: str = TAU2_TOOLSET) -> str:
    """Register the domain's tools into Hermes' process-global registry.

    Idempotent for the same tool set (many simulations share one process).
    Raises on a *different* tool set: Hermes' registry is process-global, so one
    process serves exactly one domain.
    """
    names = frozenset(t.name for t in tools)
    if not names:
        # Validated before the Hermes imports, so the error names the real problem
        # rather than an unrelated ModuleNotFoundError.
        raise ValueError("Refusing to install an empty tau2 toolset.")

    get_tool_definitions, registry, create_custom_toolset = _import_hermes()

    with _INSTALL_LOCK:
        installed = _INSTALLED.get(toolset)
        if installed is not None:
            if installed != names:
                raise RuntimeError(
                    "Hermes' tool registry is process-global: this process already serves "
                    f"{len(installed)} tau2 tools and cannot switch to a different set. "
                    "Run one domain per process (see misc/hermes-agent-integration.md "
                    "section 6)."
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
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                },
                handler=_make_handler(fn["name"]),
            )

        # registry.register() can fail silently: the shadow-rejection path logs and
        # returns without raising (tools/registry.py:687). Confirm rather than assume.
        registered = set(registry.get_all_tool_names())
        missing = names - registered
        if missing:
            raise RuntimeError(
                f"Hermes' registry did not accept {len(missing)} tau2 tools: "
                f"{sorted(missing)}. registry.register() does not always raise on "
                "rejection; check the Hermes log for 'registration REJECTED'."
            )
        _INSTALLED[toolset] = names

    logger.debug(f"Installed {len(names)} tau2 tools into Hermes toolset {toolset!r}")
    return toolset


def uninstall_tau2_toolset(toolset: str = TAU2_TOOLSET) -> None:
    """Process-level teardown: call once after the whole run, never from an agent's
    stop() -- concurrent simulations share these registrations."""
    try:
        from tools.registry import registry  # Hermes
    except ImportError:
        return

    with _INSTALL_LOCK:
        for name in _INSTALLED.pop(toolset, frozenset()):
            with suppress(Exception):
                registry.deregister(name)


def check_tool_surface(expected: set[str], hermes: Any) -> None:
    """Raise unless what Hermes will show the model is exactly tau2's tools.

    This is the single check that catches, at construction time, every way the
    exposed surface can silently diverge:
      - tool_search deferral replacing the domain tools with the search bridge
        (H15) -- Hermes' default config activates it;
      - a registration that no-opped instead of raising (tools/registry.py:687);
      - toolset filtering being bypassed, e.g. context-compressor schemas appended
        after the filter (agent/agent_init.py:2003);
      - an AIAgent built before install_tau2_toolset() (H1).
    Without it, each of these reads downstream as a bad model, not a bad harness.

    Module-level and duck-typed on purpose: the tests drive it with a stub that has
    a .tools list, so the regression test needs no Hermes import.
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


# =============================================================================
# THE AGENT
# =============================================================================

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

#: Gap between the orchestrator's event wait and the bridge's per-call wait, so the
#: two never expire together (H19).
TOOL_TIMEOUT_MARGIN_SECONDS = 60.0


def resolve_tool_timeout(
    turn_timeout: float, tool_timeout: Optional[float] = None
) -> float:
    """Per-call handler timeout, strictly longer than the orchestrator's wait.

    On a stall exactly one side fires: tau2 raises AgentError and stop() unwinds,
    instead of racing the handler's own timeout string back into Hermes' loop.
    """
    if tool_timeout is not None:
        return tool_timeout
    return turn_timeout + TOOL_TIMEOUT_MARGIN_SECONDS


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
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
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
    """Runs the Hermes scaffold as one tau2 agent turn at a time."""

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
        self.tool_timeout = resolve_tool_timeout(turn_timeout, tool_timeout)
        # HERMES_YOLO_MODE is NOT set here: Hermes freezes it at import
        # (tools/approval.py:45), so a constructor write is too late -- and is the
        # escalation path that freeze defends against. It belongs in the shell
        # (misc/hermes-agent-integration.md section 3).
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
            raise ImportError(_HERMES_MISSING) from e

        hermes = AIAgent(
            model=self.model or "",
            session_id=self._session_id,
            enabled_toolsets=[self.toolset],
            ephemeral_system_prompt=AGENT_INSTRUCTION.format(
                domain_policy=self.domain_policy
            ),
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

    def get_init_state(
        self, message_history: Optional[list[Message]] = None
    ) -> HermesAgentState:
        self._bridge = ToolBridge(self._session_id, tool_timeout=self.tool_timeout)
        register_bridge(self._bridge)
        self._hermes = self._build_hermes()
        return HermesAgentState(hermes_history=_to_hermes_history(message_history or []))

    def set_seed(self, seed: int) -> None:
        """Called by the orchestrator whenever a seed is set (orchestrator.py:527);
        tau2's default seed is 300, so this fires on every ordinary run. The base
        implementation is a no-op that only logs. Runs before get_init_state(), so
        mutating hermes_args works.
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
                hard_interrupt("tau2 simulation ended")  # agent/interrupt_control.py:209
        except Exception:
            logger.debug("Hermes hard_interrupt failed", exc_info=True)

        if self._bridge is not None:
            self._bridge.abort()
            unregister_bridge(self._session_id)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                logger.error(
                    f"Hermes worker {self._session_id} did not exit; thread leaked"
                )

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
                            id=call_id,  # echoed back as ToolMessage.id
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
