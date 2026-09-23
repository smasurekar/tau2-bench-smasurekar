"""The Frontend/Backend Agent prototype as a tau2 half-duplex agent.

The prototype runs with ``backend.tools.execution: external``: a backend tool-call
batch suspends the turn and comes back out of ``send()`` as ``AgentTurn(tool_calls)``,
which is exactly tau2's "return a tool call, be re-entered with the ToolMessage"
protocol. No threads, no bridge.

Every returned AssistantMessage carries the LLM work of *that tau2 step* in fields
tau2 already persists (``usage``, ``cost``, ``generation_time_seconds``,
``raw_data["fba"]``); the message that closes a user turn also carries the turn's
timing summary. ``tau2_fba.metrics`` aggregates them offline.

See ../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from tau2.agent.base_agent import AgentError, HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.message import (
    Message as Tau2Message,
)
from tau2.data_model.message import (
    ToolCall as Tau2ToolCall,
)
from tau2.environment.tool import Tool
from tau2_fba import config as fba_config
from tau2_fba.client import (
    BACKEND,
    FRONTEND,
    CallRecord,
    Tau2ChatClient,
    ToolSurfaceError,
    from_tau2_history,
    tool_names,
)
from tau2_fba.prototype_import import ensure_importable

ensure_importable()

from prototypes.text_frontend_backend_agent import events as fba_events  # noqa: E402
from prototypes.text_frontend_backend_agent.agent import assemble_agent  # noqa: E402
from prototypes.text_frontend_backend_agent.delegation import (  # noqa: E402
    CALL_BACKEND,
    FRONTEND_TOOLS,
)
from prototypes.text_frontend_backend_agent.errors import (  # noqa: E402
    FrontendBackendAgentError,
)
from prototypes.text_frontend_backend_agent.events import (  # noqa: E402
    InternalEvent,
    JsonlSink,
)
from prototypes.text_frontend_backend_agent.messages import ToolResult  # noqa: E402
from prototypes.text_frontend_backend_agent.prompts import (  # noqa: E402
    load_catalog,
    render,
)
from prototypes.text_frontend_backend_agent.session import SessionState  # noqa: E402
from prototypes.text_frontend_backend_agent.tools import ToolSpec  # noqa: E402

#: Registered agent names -> prototype mode. Two names rather than one name with a
#: mode argument, so results.json records the arm in info.agent_info.implementation.
AGENT_PAIRED = "fba_paired"
AGENT_BACKEND_ONLY = "fba_backend_only"
AGENT_MODES = {
    AGENT_PAIRED: fba_config.MODE_PAIRED,
    AGENT_BACKEND_ONLY: fba_config.MODE_BACKEND_ONLY,
}

#: Key under AssistantMessage.raw_data, and the version of what is stored there.
RAW_KEY = "fba"
SCHEMA_VERSION = 1

#: Turn decisions recorded in raw_data["fba"]["turn"]["decision"].
DECISION_DELEGATE = "delegate"
DECISION_DIRECT = "direct"
DECISION_FALLBACK = "contract_fallback"
DECISION_BACKEND_ONLY = "backend_only"

#: Sink events counted per turn.
_COUNTED_EVENTS = (
    fba_events.FRONTEND_REPAIR,
    fba_events.FRONTEND_CONTRACT_VIOLATION,
    fba_events.BACKEND_ERROR,
    fba_events.ITERATION_CAP,
)

_TOKEN_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cached_tokens",
)


class BackendTransportError(RuntimeError):
    """A backend LLM call failed and the prototype turned it into canned text.

    The prototype's BackendAgent catches every exception and answers the user with
    "I could not complete that request right now" (backend.py). Scoring that would
    count an endpoint hiccup against the agent, so it is re-raised as a plain
    RuntimeError: tau2 retries the simulation and, failing that, marks it
    infrastructure_error -- the same treatment llm_agent gets.
    """


# ---------------------------------------------------------------------------
# event sink
# ---------------------------------------------------------------------------

_JSONL_SINKS: dict[str, JsonlSink] = {}
_JSONL_LOCK = threading.Lock()


def _shared_jsonl_sink(path: str) -> JsonlSink:
    """One JsonlSink (and so one append lock) per path, shared by all simulations."""
    with _JSONL_LOCK:
        if path not in _JSONL_SINKS:
            _JSONL_SINKS[path] = JsonlSink(path)
        return _JSONL_SINKS[path]


class StepSink:
    """Collects the prototype's internal events for one tau2 step.

    One per agent instance, and tau2 builds one agent per simulation, so it is never
    shared across sessions. Optionally tees every event to a JSONL trace.
    """

    def __init__(self, tee: Optional[JsonlSink] = None):
        self._events: list[InternalEvent] = []
        self._tee = tee

    def emit(self, event: InternalEvent) -> None:
        self._events.append(event)
        if self._tee is not None:
            self._tee.emit(event)

    def drain(self) -> list[InternalEvent]:
        events, self._events = self._events, []
        return events


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


def _zero_totals() -> dict[str, float]:
    return {"calls": 0, "latency_s": 0.0, "cost": 0.0, **dict.fromkeys(_TOKEN_KEYS, 0)}


def _role_totals(records: list[CallRecord], role: str) -> dict[str, float]:
    totals = _zero_totals()
    for r in records:
        if r.role != role or r.error:
            continue
        totals["calls"] += 1
        totals["latency_s"] += r.latency_s
        totals["cost"] += r.cost
        for k in _TOKEN_KEYS:
            totals[k] += getattr(r, k)
    totals["latency_s"] = round(totals["latency_s"], 4)
    return totals


def _add_totals(a: dict, b: dict) -> dict:
    out = {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}
    out["latency_s"] = round(out.get("latency_s", 0.0), 4)
    return out


class TurnTracker(BaseModel):
    """Timing and accounting for the user turn currently open (or last closed)."""

    index: int = 0
    open: bool = False
    t_user: float = 0.0
    decision: Optional[str] = None
    t_decision: Optional[float] = None
    filler_text: str = ""
    delegation_query: str = ""
    frontend: dict = Field(default_factory=_zero_totals)
    backend: dict = Field(default_factory=_zero_totals)
    backend_tool_rounds: int = 0
    events: dict[str, int] = Field(default_factory=dict)


class FBAState(BaseModel):
    """The prototype's own SessionState plus the open turn's tracker."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Any  # prototypes...session.SessionState (frozen dataclass)
    turn: TurnTracker = Field(default_factory=TurnTracker)


# ---------------------------------------------------------------------------
# tool surface
# ---------------------------------------------------------------------------


def tool_spec(tool: Tool) -> ToolSpec:
    """A tau2 Tool as a prototype ToolSpec with no callable: tau2 executes it."""
    fn = tool.openai_schema["function"]
    return ToolSpec(
        name=fn["name"],
        description=fn.get("description") or fn["name"],
        parameters=fn.get("parameters") or {"type": "object", "properties": {}},
    )


def check_tool_surface(domain_tool_names: set[str]) -> None:
    """Construction-time half of the tool-routing guarantee (plan section 4).

    The per-call half is Tau2ChatClient's guard.
    """
    frontend = tool_names(FRONTEND_TOOLS)
    if frontend != {CALL_BACKEND}:
        raise ToolSurfaceError(
            f"prototype frontend tools are {sorted(frontend)}; expected only {CALL_BACKEND}"
        )
    if not domain_tool_names:
        raise ToolSurfaceError(
            "tau2 passed no domain tools; nothing for the backend to do"
        )
    if CALL_BACKEND in domain_tool_names:
        raise ToolSurfaceError(
            f"a tau2 domain tool is named {CALL_BACKEND!r}, which collides with the "
            "frontend's delegation tool"
        )


# ---------------------------------------------------------------------------
# the agent
# ---------------------------------------------------------------------------


class FBAHalfDuplexAgent(HalfDuplexAgent[FBAState]):
    """Runs one prototype session per tau2 simulation, one tau2 step at a time."""

    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        *,
        mode: str,
        domain: str,
        backend_llm: Optional[str] = None,
        frontend_llm: Optional[str] = None,
        fba_config_path: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key_env: Optional[str] = None,
        llm_kwargs: Optional[dict] = None,
        strict_transport_errors: bool = True,
        event_log: Optional[str] = None,
        generate_fn: Optional[Callable[..., AssistantMessage]] = None,
    ):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.mode = mode
        self.domain = domain
        self.strict_transport_errors = strict_transport_errors

        specs = [tool_spec(t) for t in tools]
        self.domain_tool_names = {s.name for s in specs}
        check_tool_surface(self.domain_tool_names)

        raw, source_dir = fba_config.load_raw_config(fba_config_path)
        self.config = fba_config.build_fba_config(
            raw,
            source_dir,
            mode=mode,
            domain_policy=domain_policy,
            profile=fba_config.load_domain_profile(domain),
            frontend_model=frontend_llm,
            backend_model=backend_llm,
            base_url=base_url,
            api_key=fba_config.resolve_api_key(api_key_env),
        )

        extra = dict(llm_kwargs or {})
        self._backend_client = Tau2ChatClient(
            BACKEND,
            fba_config.litellm_model(self.config.backend.llm.model),
            fba_config.litellm_kwargs(self.config.backend.llm, extra),
            expected_tools=self.domain_tool_names,
            generate_fn=generate_fn,
        )
        self._frontend_client: Optional[Tau2ChatClient] = None
        if self.paired:
            self._frontend_client = Tau2ChatClient(
                FRONTEND,
                fba_config.litellm_model(self.config.frontend.llm.model),
                fba_config.litellm_kwargs(self.config.frontend.llm, extra),
                expected_tools={CALL_BACKEND},
                generate_fn=generate_fn,
            )

        self._sink = StepSink(_shared_jsonl_sink(event_log) if event_log else None)
        self._agent = assemble_agent(
            self.config,
            tools=specs,
            event_sink=self._sink,
            frontend_client=self._frontend_client,
            backend_client=self._backend_client,
        )

    @property
    def paired(self) -> bool:
        return self.mode == fba_config.MODE_PAIRED

    @property
    def clients(self) -> list[Tau2ChatClient]:
        return [c for c in (self._frontend_client, self._backend_client) if c]

    def system_prompt(self, role: str) -> str:
        """The rendered system prompt a role receives (for inspection and tests)."""
        key = (
            self.config.frontend.prompt_key
            if role == FRONTEND
            else self.config.backend.prompt_key
        )
        catalog = load_catalog(self.config.prompts_path, self.config.prompts.inline)
        return render(catalog.get(key), self.config)

    # ---- tau2 lifecycle --------------------------------------------------

    def get_init_state(
        self, message_history: Optional[list[Tau2Message]] = None
    ) -> FBAState:
        """Replay tau2's history under the prototype's documented, lossy rule.

        On an ordinary run this is just tau2's greeting (orchestrator.py:627).
        """
        try:
            session = SessionState.from_message_history(
                from_tau2_history(message_history or []),
                backend_only=not self.paired,
            )
        except FrontendBackendAgentError as e:
            raise AgentError(
                f"cannot replay tau2 history into the prototype: {e}"
            ) from e
        return FBAState(session=session)

    def set_seed(self, seed: int) -> None:
        """Forward tau2's seed to every LLM call, as LLMConfigMixin does for llm_agent."""
        for client in self.clients:
            client.kwargs["seed"] = seed

    # ---- one tau2 step ---------------------------------------------------

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: FBAState
    ) -> tuple[AssistantMessage, FBAState]:
        if isinstance(message, UserMessage):
            if state.turn.open or state.session.pending is not None:
                raise AgentError("user message arrived while a turn was still open")
            state.turn = TurnTracker(
                index=state.turn.index + 1,
                open=True,
                t_user=time.time(),
                decision=None if self.paired else DECISION_BACKEND_ONLY,
            )
            call = self._agent.send(message.content or "", state.session)
        elif isinstance(message, (ToolMessage, MultiToolMessage)):
            if not state.turn.open:
                raise AgentError("tool result arrived with no open turn")
            batch = (
                message.tool_messages
                if isinstance(message, MultiToolMessage)
                else [message]
            )
            call = self._agent.send_tool_results(
                [
                    ToolResult(
                        tool_call_id=m.id, content=m.content or "", is_error=m.error
                    )
                    for m in batch
                ],
                state.session,
            )
        else:
            raise AgentError(f"unexpected message type {type(message).__name__}")

        try:
            agent_turn, session = asyncio.run(call)
        except FrontendBackendAgentError as e:
            self._reset()
            raise AgentError(f"prototype protocol error: {e}") from e
        except BaseException:
            self._reset()
            raise

        records, events = self._drain()
        self._raise_on_hidden_errors(records)
        state.session = session
        return self._to_assistant_message(agent_turn, records, events, state), state

    def _drain(self) -> tuple[list[CallRecord], list[InternalEvent]]:
        records = [r for client in self.clients for r in client.drain()]
        return records, self._sink.drain()

    def _reset(self) -> None:
        """Discard a failed step's records, events and errors."""
        self._drain()
        for client in self.clients:
            client.last_error = None

    def _raise_on_hidden_errors(self, records: list[CallRecord]) -> None:
        """Re-raise what the prototype's backend swallowed (see BackendTransportError)."""
        errors = [c.last_error for c in self.clients if c.last_error is not None]
        for client in self.clients:
            client.last_error = None
        for err in errors:
            if isinstance(err, ToolSurfaceError):
                raise err  # a harness bug: never scored, whatever the strictness
        if errors and self.strict_transport_errors:
            failed = [r.error for r in records if r.error]
            raise BackendTransportError(
                f"backend LLM call failed ({'; '.join(failed)}); re-raised so tau2 "
                "retries instead of scoring the prototype's canned error reply"
            ) from errors[0]

    def _fold_events(self, turn: TurnTracker, events: list[InternalEvent]) -> None:
        for e in events:
            if e.kind == fba_events.DELEGATION:
                turn.decision = DECISION_DELEGATE
                turn.t_decision = e.timestamp
                turn.delegation_query = str(e.data.get("query") or "")
            elif e.kind == fba_events.FILLER:
                turn.filler_text = str(e.data.get("text") or "")
            elif e.kind == fba_events.DIRECT_ANSWER:
                turn.decision = DECISION_DIRECT
                turn.t_decision = e.timestamp
            elif (
                e.kind == fba_events.FRONTEND_CONTRACT_VIOLATION and "problem" in e.data
            ):
                # The final, fail-closed violation; the other flavour ("discarded_text")
                # is a delegation that also carried text, and still delegates.
                turn.decision = DECISION_FALLBACK
                turn.t_decision = e.timestamp
            if e.kind in _COUNTED_EVENTS:
                turn.events[e.kind] = turn.events.get(e.kind, 0) + 1

    def _to_assistant_message(
        self,
        agent_turn: Any,
        records: list[CallRecord],
        events: list[InternalEvent],
        state: FBAState,
    ) -> AssistantMessage:
        turn = state.turn
        self._fold_events(turn, events)
        placeholders = sum(r.placeholders for r in records)
        if placeholders:
            turn.events["empty_assistant_placeholder"] = (
                turn.events.get("empty_assistant_placeholder", 0) + placeholders
            )

        step = {
            FRONTEND: _role_totals(records, FRONTEND),
            BACKEND: _role_totals(records, BACKEND),
            "per_call": [r.as_dict() for r in records],
        }
        turn.frontend = _add_totals(turn.frontend, step[FRONTEND])
        turn.backend = _add_totals(turn.backend, step[BACKEND])

        if agent_turn.tool_calls:
            turn.backend_tool_rounds += 1
            msg = AssistantMessage(
                role="assistant",
                content=None,  # never both (orchestrator.py _check_communication_error)
                tool_calls=[
                    Tau2ToolCall(
                        id=c.id,  # echoed back by tau2 as ToolMessage.id
                        name=c.name,
                        arguments=c.arguments,
                        requestor="assistant",
                    )
                    for c in agent_turn.tool_calls
                ],
            )
            step_kind = "tool_calls"
        else:
            msg = AssistantMessage(role="assistant", content=agent_turn.final_text)
            step_kind = "final"

        fba: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "mode": self.mode,
            "turn_index": turn.index,
            "step_kind": step_kind,
            "step": step,
        }
        if step_kind == "final":
            fba["turn"] = self._close_turn(turn)

        fe, be = step[FRONTEND], step[BACKEND]
        msg.usage = {
            "prompt_tokens": fe["prompt_tokens"] + be["prompt_tokens"],
            "completion_tokens": fe["completion_tokens"] + be["completion_tokens"],
        }
        msg.cost = fe["cost"] + be["cost"]
        msg.generation_time_seconds = round(fe["latency_s"] + be["latency_s"], 4)
        msg.raw_data = {RAW_KEY: fba}
        return msg

    def _close_turn(self, turn: TurnTracker) -> dict[str, Any]:
        """Summarize the turn on the message that ends it (plan section 8.1)."""
        wall = time.time() - turn.t_user
        filler_latency = None
        if turn.decision == DECISION_DELEGATE and turn.t_decision is not None:
            filler_latency = turn.t_decision - turn.t_user
        if (
            turn.decision == DECISION_DELEGATE
            and turn.filler_text
            and filler_latency is not None
        ):
            first_response = filler_latency
        else:
            # direct / fallback: the answer itself is the first thing heard;
            # backend_only, or a delegation without filler: nothing until the end.
            first_response = wall
        turn.open = False
        return {
            "decision": turn.decision,
            "filler_text": turn.filler_text,
            "filler_latency_s": None
            if filler_latency is None
            else round(filler_latency, 4),
            "frontend_latency_s": turn.frontend["latency_s"],
            "backend_latency_s": turn.backend["latency_s"],
            "frontend_calls": turn.frontend["calls"],
            "backend_calls": turn.backend["calls"],
            "backend_tool_rounds": turn.backend_tool_rounds,
            "first_response_latency_s": round(first_response, 4),
            "wall_s": round(wall, 4),
            "delegation_query": turn.delegation_query,
            "events": dict(turn.events),
        }


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------

#: Keys run_fba_eval.py places in llm_args_agent. Anything else is a typo.
LLM_ARG_KEYS = frozenset(
    {
        "fba_domain",
        "fba_config",
        "frontend_llm",
        "base_url",
        "api_key_env",
        "llm_kwargs",
        "strict_transport_errors",
        "event_log",
        "provenance",  # informational only; recorded in results.json
    }
)


def create_fba_agent(tools, domain_policy, *, mode: str, generate_fn=None, **kwargs):
    """Factory for tau2's registry (bound to a mode by ``tau2_fba.register``).

    Must accept **kwargs: build_agent() always passes llm, llm_args, task,
    audio_native_config and audio_taps_dir (src/tau2/runner/build.py:117).
    tau2 does not pass the domain name, so run_fba_eval.py puts it in llm_args.
    """
    llm_args = dict(kwargs.get("llm_args") or {})
    unknown = set(llm_args) - LLM_ARG_KEYS
    if unknown:
        raise ValueError(
            f"unknown FBA llm_args keys {sorted(unknown)}; allowed: {sorted(LLM_ARG_KEYS)}"
        )
    if not llm_args.get("fba_domain"):
        raise ValueError(
            "llm_args_agent must carry 'fba_domain' (tau2 does not pass the domain to "
            "agent factories). Launch through tau2-fba/run_fba_eval.py."
        )
    return FBAHalfDuplexAgent(
        tools=tools,
        domain_policy=domain_policy,
        mode=mode,
        domain=llm_args["fba_domain"],
        backend_llm=kwargs.get("llm"),
        frontend_llm=llm_args.get("frontend_llm"),
        fba_config_path=llm_args.get("fba_config"),
        base_url=llm_args.get("base_url"),
        api_key_env=llm_args.get("api_key_env"),
        llm_kwargs=llm_args.get("llm_kwargs"),
        strict_transport_errors=llm_args.get("strict_transport_errors", True),
        event_log=llm_args.get("event_log"),
        generate_fn=generate_fn,
    )
