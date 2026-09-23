"""The prototype's ChatClient, implemented on tau2's own ``generate()``.

The prototype names this seam itself (``llm.py``: "the future tau2-bench adapter
replaces [OpenAIChatClient] with a ``tau2.utils.llm_utils.generate()``-backed
client"). Going through ``generate()`` gives LiteLLM routing, ``num_retries``,
per-call ``llm_debug`` logs, usage, cost and timing for free.

This module also owns the per-call tool-surface guard: the frontend must only ever
be offered ``call_backend``, the backend exactly the tau2 domain tools.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from tau2.data_model.message import (
    APICompatibleMessage,
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.message import (
    Message as Tau2Message,
)
from tau2.data_model.message import (
    ToolCall as Tau2ToolCall,
)
from tau2.utils.llm_utils import generate as tau2_generate
from tau2_fba.prototype_import import ensure_importable

ensure_importable()

from prototypes.text_frontend_backend_agent.llm import ChatResponse  # noqa: E402
from prototypes.text_frontend_backend_agent.messages import (  # noqa: E402
    Message,
    ToolCall,
    Usage,
    canonical_json,
)

FRONTEND = "frontend"
BACKEND = "backend"

#: Sent in place of an assistant turn with neither text nor tool calls. Only the
#: frontend's repair reprompt produces one (frontend.py appends
#: ``Message.assistant(response.content or "")``), and tau2's ``validate_message``
#: rejects an empty assistant message. Counted per call so it is never silent.
EMPTY_ASSISTANT_PLACEHOLDER = "(no reply)"


class ToolSurfaceError(RuntimeError):
    """An LLM was offered a tool set other than the one its role must see.

    A harness bug, never a model outcome: it is a RuntimeError (not AgentError) so
    tau2 retries the simulation and, failing that, marks it infrastructure_error
    instead of scoring it.
    """


@dataclass
class CallRecord:
    """Accounting for one LLM call, drained by the adapter after every tau2 step."""

    role: str
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    finish_reason: str = ""
    placeholders: int = 0
    error: Optional[str] = None

    def as_dict(self) -> dict:
        out = {
            "role": self.role,
            "latency_s": round(self.latency_s, 4),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "cost": self.cost,
            "finish_reason": self.finish_reason,
        }
        if self.placeholders:
            out["placeholders"] = self.placeholders
        if self.error:
            out["error"] = self.error
        return out


class _SchemaTool:
    """Duck-types tau2's Tool for ``generate()``, which only reads ``.openai_schema``."""

    def __init__(self, schema: dict[str, Any]):
        self.openai_schema = schema


# ---------------------------------------------------------------------------
# message conversion
# ---------------------------------------------------------------------------


def to_tau2_messages(
    messages: Sequence[Message],
) -> tuple[list[APICompatibleMessage], int]:
    """Prototype messages -> tau2 messages for ``generate()``.

    Returns the converted list and how many empty assistant turns were replaced by
    :data:`EMPTY_ASSISTANT_PLACEHOLDER`.
    """
    out: list[APICompatibleMessage] = []
    placeholders = 0
    for m in messages:
        if m.role == "system":
            out.append(SystemMessage(role="system", content=m.content or ""))
        elif m.role == "user":
            out.append(UserMessage(role="user", content=m.content or ""))
        elif m.role == "tool":
            out.append(
                ToolMessage(
                    id=m.tool_call_id or "",
                    role="tool",
                    content=m.content or "",
                    requestor="assistant",
                )
            )
        elif m.tool_calls:
            out.append(
                AssistantMessage(
                    role="assistant",
                    content=m.content or None,
                    tool_calls=[
                        Tau2ToolCall(id=c.id, name=c.name, arguments=c.arguments)
                        for c in m.tool_calls
                    ],
                )
            )
        else:
            text = m.content
            if not (text and text.strip()):
                text = EMPTY_ASSISTANT_PLACEHOLDER
                placeholders += 1
            out.append(AssistantMessage(role="assistant", content=text))
    return out, placeholders


def from_tau2_history(messages: Sequence[Tau2Message]) -> list[Message]:
    """tau2 transcript -> prototype messages, for ``SessionState.from_message_history``."""
    out: list[Message] = []
    for m in messages:
        if isinstance(m, MultiToolMessage):
            out.extend(from_tau2_history(m.tool_messages))
        elif isinstance(m, ToolMessage):
            if m.requestor == "assistant":
                out.append(Message.tool(m.id, m.content or ""))
        elif isinstance(m, UserMessage):
            if not m.is_tool_call() and m.content:
                out.append(Message.user(m.content))
        elif isinstance(m, AssistantMessage):
            if m.is_tool_call():
                out.append(
                    Message.assistant_tool_calls(
                        tuple(
                            ToolCall(
                                id=c.id,
                                name=c.name,
                                arguments_json=canonical_json(c.arguments),
                            )
                            for c in m.tool_calls
                        ),
                        content=m.content or None,
                    )
                )
            elif m.content:
                out.append(Message.assistant(m.content))
    return out


def _dig(d: Any, *keys: str) -> int:
    for k in keys:
        if not isinstance(d, dict):
            return 0
        d = d.get(k)
    return int(d or 0)


def tool_names(tools: Optional[Sequence[dict[str, Any]]]) -> set[str]:
    """Function names in a list of OpenAI tool schemas."""
    return {t["function"]["name"] for t in tools or []}


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------


class Tau2ChatClient:
    """ChatClient for one role, calling ``tau2.utils.llm_utils.generate``.

    ``complete`` is ``async`` to satisfy the prototype's protocol but calls the
    synchronous ``generate`` directly: one tau2 simulation drives one session, so
    there is nothing to overlap, and no event-loop-bound resource outlives the
    ``asyncio.run`` of a single tau2 step.
    """

    def __init__(
        self,
        role: str,
        model: str,
        kwargs: dict[str, Any],
        expected_tools: set[str],
        generate_fn: Optional[Callable[..., AssistantMessage]] = None,
    ):
        self.role = role
        self.model = model
        self.kwargs = dict(kwargs)
        self.expected_tools = frozenset(expected_tools)
        self._generate = generate_fn or tau2_generate
        self._records: list[CallRecord] = []
        self.last_error: Optional[BaseException] = None

    def drain(self) -> list[CallRecord]:
        """Return and clear the calls made since the last drain."""
        records, self._records = self._records, []
        return records

    def _guard(self, tools: Optional[Sequence[dict[str, Any]]]) -> None:
        offered = tool_names(tools)
        if offered != self.expected_tools:
            # Recorded as well as raised: the prototype's backend swallows every
            # exception from complete(), so the adapter must be able to find it.
            self.last_error = ToolSurfaceError(
                f"{self.role} LLM offered tools {sorted(offered)}, expected exactly "
                f"{sorted(self.expected_tools)}"
            )
            raise self.last_error

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Optional[Sequence[dict[str, Any]]] = None,
    ) -> ChatResponse:
        self._guard(tools)
        wire, placeholders = to_tau2_messages(messages)
        record = CallRecord(role=self.role, placeholders=placeholders)
        try:
            msg = self._generate(
                model=self.model,
                messages=wire,
                tools=[_SchemaTool(t) for t in tools] if tools else None,
                call_name=f"fba_{self.role}",
                **self.kwargs,
            )
        except Exception as e:
            record.error = f"{type(e).__name__}: {e}"
            self._records.append(record)
            self.last_error = e
            raise

        raw = msg.raw_data if isinstance(msg.raw_data, dict) else {}
        raw_usage = raw.get("usage") or {}
        usage = msg.usage or {}
        record.latency_s = float(msg.generation_time_seconds or 0.0)
        record.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        record.completion_tokens = int(usage.get("completion_tokens") or 0)
        record.total_tokens = int(
            raw_usage.get("total_tokens")
            or record.prompt_tokens + record.completion_tokens
        )
        record.reasoning_tokens = _dig(
            raw_usage, "completion_tokens_details", "reasoning_tokens"
        )
        record.cached_tokens = _dig(raw_usage, "prompt_tokens_details", "cached_tokens")
        record.cost = float(msg.cost or 0.0)
        choices = raw.get("choices") or [{}]
        record.finish_reason = str((choices[0] or {}).get("finish_reason") or "")
        self._records.append(record)

        return ChatResponse(
            content=msg.content,
            tool_calls=tuple(
                ToolCall(
                    id=c.id or "",
                    name=c.name,
                    arguments_json=canonical_json(c.arguments or {}),
                )
                for c in msg.tool_calls or ()
            ),
            usage=Usage(
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                total_tokens=record.total_tokens,
                cached_tokens=record.cached_tokens,
            ),
            cost=record.cost,
            latency_ms=record.latency_s * 1000.0,
            model=str(raw.get("model") or self.model),
            finish_reason=record.finish_reason,
        )
