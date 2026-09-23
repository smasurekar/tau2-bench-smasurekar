"""Offline doubles: a scripted stand-in for tau2's ``generate()``.

The adapter's clients call ``generate(model=..., messages=..., tools=..., call_name=...)``
and read back a tau2 AssistantMessage, so the fake speaks exactly that interface.
Responders are keyed by ``call_name`` ("fba_frontend" / "fba_backend" /
"agent_response" / "user_simulator_response").
"""

import time
from collections.abc import Callable
from typing import Any, Optional

from tau2_fba.agent import create_fba_agent

from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.domains.airline.environment import get_environment as airline_env
from tau2.domains.mock.environment import get_environment as mock_env

GREETING = AssistantMessage(role="assistant", content="Hi! How can I help you today?")


def llm_reply(
    content: Optional[str] = None,
    calls: tuple[tuple[str, dict], ...] = (),
    *,
    ids: Optional[list[str]] = None,
    prompt: int = 100,
    completion: int = 10,
    reasoning: int = 0,
    cached: int = 0,
    latency: float = 0.5,
    cost: float = 0.0,
) -> AssistantMessage:
    """What tau2's generate() returns, including the provider usage in raw_data."""
    tool_calls = [
        ToolCall(id=(ids or [])[i] if ids else f"call_{i}", name=name, arguments=args)
        for i, (name, args) in enumerate(calls)
    ] or None
    return AssistantMessage(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        usage={"prompt_tokens": prompt, "completion_tokens": completion},
        cost=cost,
        generation_time_seconds=latency,
        raw_data={
            "model": "fake",
            "choices": [{"finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "completion_tokens_details": {"reasoning_tokens": reasoning},
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        },
    )


def delegate(query: str, filler: str = "One moment.", **kw: Any) -> AssistantMessage:
    args = {"query": query}
    if filler:
        args["filler_text"] = filler
    return llm_reply(calls=(("call_backend", args),), ids=["fe_call"], **kw)


class FakeGenerate:
    """Scripted generate(): per call_name, a queue of replies or a responder function."""

    def __init__(self, sleep: float = 0.0):
        self.queues: dict[str, list[Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.sleep = sleep

    def script(self, call_name: str, *replies: Any) -> "FakeGenerate":
        self.queues.setdefault(call_name, []).extend(replies)
        return self

    def respond(
        self, call_name: str, fn: Callable[[list], AssistantMessage]
    ) -> "FakeGenerate":
        self.queues[call_name] = fn  # type: ignore[assignment]
        return self

    def __call__(self, *, model, messages, tools=None, call_name=None, **kwargs):
        self.calls.append(
            {
                "call_name": call_name,
                "model": model,
                "messages": list(messages),
                "tools": sorted(
                    t.openai_schema["function"]["name"] for t in tools or []
                ),
                "kwargs": kwargs,
            }
        )
        if self.sleep:
            time.sleep(self.sleep)
        source = self.queues.get(call_name)
        if callable(source):
            return source(messages)
        if not source:
            raise AssertionError(f"FakeGenerate ran out of replies for {call_name!r}")
        reply = source.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def of(self, call_name: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["call_name"] == call_name]


def make_agent(
    fake: FakeGenerate,
    *,
    mode: str = "frontend_backend",
    domain: str = "mock",
    **llm_args,
):
    env = mock_env() if domain == "mock" else airline_env()
    agent = create_fba_agent(
        env.get_tools(),
        env.get_policy(),
        mode=mode,
        generate_fn=fake,
        llm="nvidia/nvidia/nemotron-3-ultra",
        llm_args={"fba_domain": domain, **llm_args},
    )
    return agent, env
