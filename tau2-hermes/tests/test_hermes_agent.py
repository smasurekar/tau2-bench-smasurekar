"""Bridge semantics, offline.

No network and no LLM: a fake worker thread stands in for Hermes and the test body
stands in for the tau2 orchestrator. Tests that genuinely need Hermes importable are
marked `needs_hermes` and skip when it is not.

See misc/hermes-agent-integration.md section 7.
"""

import json
import threading

import pytest
from tau2.agent.base_agent import AgentError
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.environment.tool import as_tool

from tau2_hermes.hermes_agent import (
    TOOL_TIMEOUT_MARGIN_SECONDS,
    ToolBridge,
    _make_handler,
    _to_hermes_history,
    check_tool_surface,
    create_hermes_agent,
    install_tau2_toolset,
    register_bridge,
    resolve_tool_timeout,
    unregister_bridge,
)

def _hermes_importable() -> bool:
    try:
        import run_agent  # noqa: F401
    except Exception:
        return False
    return True


needs_hermes = pytest.mark.skipif(
    not _hermes_importable(),
    reason="hermes-agent not importable; see integration doc section 3",
)


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def bridge():
    b = ToolBridge("sess-a", tool_timeout=5.0)
    register_bridge(b)
    yield b
    b.abort()
    unregister_bridge(b.session_id)


def _fake_tools():
    """Two real tau2 Tools, built from plain functions."""

    def get_reservation(reservation_id: str) -> str:
        """Look up a reservation.

        Args:
            reservation_id: The reservation id.

        Returns:
            The reservation.
        """
        return "{}"

    def cancel_reservation(reservation_id: str) -> str:
        """Cancel a reservation.

        Args:
            reservation_id: The reservation id.

        Returns:
            Confirmation.
        """
        return "{}"

    return [as_tool(get_reservation), as_tool(cancel_reservation)]


class _StubHermes:
    """Anything with a .tools list satisfies check_tool_surface."""

    def __init__(self, names):
        self.tools = [
            {"type": "function", "function": {"name": n, "parameters": {}}} for n in names
        ]


# ---------------------------------------------------------------- bridge


def test_tool_executed_exactly_once(bridge):
    """N handler calls produce N distinct tau2 tool calls, each answered once."""
    handler = _make_handler("get_reservation")
    results: list[str] = []

    def worker():
        for i in range(3):
            results.append(handler({"reservation_id": f"R{i}"}, session_id="sess-a"))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    executed: list[tuple[str, dict]] = []
    seen_ids: set[str] = set()
    for i in range(3):
        kind, (call_id, name, args) = bridge.next_event(timeout=5.0)
        assert kind == "tool_call"
        assert name == "get_reservation"
        assert call_id not in seen_ids, "call ids must be unique"
        seen_ids.add(call_id)
        executed.append((call_id, args))
        # tau2's environment echoes ToolCall.id into ToolMessage.id.
        assert bridge.send_result(call_id, f"result-{i}") is True
        # A re-delivered result must never reach the handler. Whether send_result
        # reports False depends on a race with the handler popping its entry; what
        # is guaranteed is that the private queue is maxsize=1 and already full, so
        # the second value is dropped either way. Assert the invariant, not the race.
        bridge.send_result(call_id, "duplicate")

    t.join(timeout=5.0)
    assert not t.is_alive()
    assert results == ["result-0", "result-1", "result-2"]
    assert [a["reservation_id"] for _, a in executed] == ["R0", "R1", "R2"]
    assert bridge.pending_count() == 0
    # Once every call has completed, a late result is unroutable by construction.
    for call_id, _ in executed:
        assert bridge.send_result(call_id, "late") is False


def test_concurrent_sessions_are_isolated():
    """A result sent to one session never reaches another session's handler."""
    a, b = ToolBridge("sess-a", tool_timeout=5.0), ToolBridge("sess-b", tool_timeout=5.0)
    register_bridge(a)
    register_bridge(b)
    handler = _make_handler("get_reservation")
    out: dict[str, str] = {}

    def call(session_id):
        out[session_id] = handler({"reservation_id": session_id}, session_id=session_id)

    threads = [
        threading.Thread(target=call, args=(s,), daemon=True) for s in ("sess-a", "sess-b")
    ]
    try:
        for t in threads:
            t.start()

        kind, (a_id, _, a_args) = a.next_event(timeout=5.0)
        assert kind == "tool_call" and a_args["reservation_id"] == "sess-a"
        kind, (b_id, _, b_args) = b.next_event(timeout=5.0)
        assert kind == "tool_call" and b_args["reservation_id"] == "sess-b"

        # Cross-delivery is impossible: the id belongs to the other bridge.
        assert a.send_result(b_id, "wrong") is False
        assert b.send_result(a_id, "wrong") is False

        assert a.send_result(a_id, "for-a") is True
        assert b.send_result(b_id, "for-b") is True
        for t in threads:
            t.join(timeout=5.0)
        assert out == {"sess-a": "for-a", "sess-b": "for-b"}
    finally:
        a.abort()
        b.abort()
        unregister_bridge("sess-a")
        unregister_bridge("sess-b")


def test_unknown_session_returns_error_string_not_raise():
    """dispatch swallows exceptions, so an unroutable call must return text."""
    out = _make_handler("get_reservation")({}, session_id="nobody")
    assert json.loads(out)["error"].startswith("no tau2 bridge")


def test_late_result_is_dropped():
    """The regression test for the shared-queue bug.

    A timed-out call's late result is unroutable, and the next call on the same
    bridge receives only its own result.
    """
    b = ToolBridge("sess-late", tool_timeout=0.1)
    register_bridge(b)
    try:
        handler = _make_handler("get_reservation")
        first = handler({"n": 1}, session_id="sess-late")
        assert json.loads(first)["error"].startswith("tool call timed out")

        kind, (stale_id, _, _) = b.next_event(timeout=5.0)
        assert kind == "tool_call"
        # tau2 answers after the handler gave up: nowhere to route it.
        assert b.send_result(stale_id, "too late") is False

        b.tool_timeout = 5.0
        result: list[str] = []
        t = threading.Thread(
            target=lambda: result.append(handler({"n": 2}, session_id="sess-late")),
            daemon=True,
        )
        t.start()
        kind, (second_id, _, args) = b.next_event(timeout=5.0)
        assert args == {"n": 2}
        assert second_id != stale_id
        assert b.send_result(second_id, "for-second") is True
        t.join(timeout=5.0)
        assert result == ["for-second"], "the second call must not see the stale result"
    finally:
        b.abort()
        unregister_bridge("sess-late")


def test_abort_releases_blocked_handlers():
    """stop() -> abort() must unblock every waiting handler, and stay idempotent."""
    b = ToolBridge("sess-abort", tool_timeout=30.0)
    register_bridge(b)
    try:
        handler = _make_handler("get_reservation")
        baseline = threading.active_count()
        out: list[str] = []
        threads = [
            threading.Thread(
                target=lambda: out.append(handler({}, session_id="sess-abort")),
                daemon=True,
            )
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for _ in range(3):
            b.next_event(timeout=5.0)

        b.abort()
        b.abort()  # idempotent

        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()
        assert len(out) == 3
        assert all(json.loads(o)["error"] == "tau2 run aborted" for o in out)
        assert b.pending_count() == 0
        assert threading.active_count() == baseline

        # A call arriving after the abort returns immediately rather than blocking.
        assert json.loads(handler({}, session_id="sess-abort"))["error"] == (
            "tau2 run aborted"
        )
    finally:
        unregister_bridge("sess-abort")


# ---------------------------------------------------------------- history


def test_history_roundtrip():
    """Actions already taken must stay visible to Hermes."""
    history = [
        UserMessage(role="user", content="cancel my flight"),
        AssistantMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="get_reservation",
                    arguments={"reservation_id": "R1"},
                    requestor="assistant",
                )
            ],
        ),
        ToolMessage(id="call_1", role="tool", content='{"ok": true}', requestor="assistant"),
        AssistantMessage(role="assistant", content="Found it."),
    ]
    rows = _to_hermes_history(history)

    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"]
    tc = rows[1]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "get_reservation"
    assert json.loads(tc["function"]["arguments"]) == {"reservation_id": "R1"}
    assert rows[2]["tool_call_id"] == "call_1"
    assert rows[2]["content"] == '{"ok": true}'


def test_history_drops_user_tool_results():
    """Only assistant-requested tool results belong in the agent's history."""
    rows = _to_hermes_history(
        [ToolMessage(id="x", role="tool", content="{}", requestor="user")]
    )
    assert rows == []


# ---------------------------------------------------------------- surface


def test_tool_surface_assertion_accepts_exact_match():
    check_tool_surface({"a", "b"}, _StubHermes(["a", "b"]))


def test_tool_surface_assertion_rejects_drift():
    """The rung 1.5 gate: bridge tools mean tool_search is still on (H15)."""
    with pytest.raises(AgentError) as exc:
        check_tool_surface(
            {"get_reservation", "cancel_reservation"},
            _StubHermes(["tool_search", "tool_describe", "tool_call"]),
        )
    msg = str(exc.value)
    assert "tool_search" in msg
    assert "tools.tool_search.enabled: off" in msg
    assert "get_reservation" in msg  # named as missing

    with pytest.raises(AgentError) as exc:
        check_tool_surface({"a"}, _StubHermes(["a", "read_file"]))
    assert "tool_search" not in str(exc.value)  # no misleading hint


def test_tool_surface_assertion_rejects_empty():
    with pytest.raises(AgentError):
        check_tool_surface({"a"}, _StubHermes([]))


# ---------------------------------------------------------------- timeouts


def test_timeouts_do_not_race():
    """The handler must outlive the orchestrator's wait (H19)."""
    turn = 900.0
    assert resolve_tool_timeout(turn) == turn + TOOL_TIMEOUT_MARGIN_SECONDS
    assert resolve_tool_timeout(turn) > turn
    assert resolve_tool_timeout(turn, 10.0) == 10.0


@needs_hermes
def test_tool_timeout_survives_factory_roundtrip():
    from tau2_hermes.hermes_agent import uninstall_tau2_toolset

    try:
        agent = create_hermes_agent(
            tools=_fake_tools(),
            domain_policy="policy",
            llm="test/model",
            llm_args={"turn_timeout": 100.0, "tool_timeout": 123.0},
        )
        assert agent.turn_timeout == 100.0
        assert agent.tool_timeout == 123.0
    finally:
        # Registration is process-global: leaving it behind makes the *next*
        # test's collision check fire on our own tools.
        uninstall_tau2_toolset()


# ---------------------------------------------------------------- toolset


@needs_hermes
def test_second_domain_rejected():
    from tau2_hermes.hermes_agent import uninstall_tau2_toolset

    tools = _fake_tools()
    toolset = "tau2_domain_test"
    try:
        install_tau2_toolset(tools, toolset=toolset)
        install_tau2_toolset(tools, toolset=toolset)  # same set: no-op
        with pytest.raises(RuntimeError, match="process-global"):
            install_tau2_toolset(tools[:1], toolset=toolset)
    finally:
        uninstall_tau2_toolset(toolset)


def test_install_rejects_empty_toolset():
    with pytest.raises(ValueError):
        install_tau2_toolset([], toolset="tau2_domain_empty")
