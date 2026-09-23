"""Adapter semantics, offline: real tau2 environments, scripted LLMs.

Numbers refer to the test table in
misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md section 9.
"""

import asyncio

import pytest
import tau2_fba
from _fakes import GREETING, FakeGenerate, delegate, llm_reply, make_agent
from tau2_fba.agent import (
    BackendTransportError,
    FBAHalfDuplexAgent,
    create_fba_agent,
)
from tau2_fba.client import (
    EMPTY_ASSISTANT_PLACEHOLDER,
    Tau2ChatClient,
    ToolSurfaceError,
)

from tau2.agent.base_agent import AgentError
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import as_tool

MOCK_TOOLS = [
    "create_task",
    "get_users",
    "transfer_to_human_agents",
    "update_task_status",
]


def _user(text: str) -> UserMessage:
    return UserMessage(role="user", content=text)


def _run_turn(agent, env, state, text):
    """Drive one user turn like the orchestrator: execute tool calls in the real env."""
    out = []
    msg, state = agent.generate_next_message(_user(text), state)
    out.append(msg)
    while msg.is_tool_call():
        results = [env.get_response(tc) for tc in msg.tool_calls]
        reply = (
            results[0]
            if len(results) == 1
            else MultiToolMessage(role="tool", tool_messages=results)
        )
        msg, state = agent.generate_next_message(reply, state)
        out.append(msg)
    return out, state


def _paired_script(fake: FakeGenerate) -> FakeGenerate:
    return fake.script(
        "fba_frontend",
        delegate("List the users.", "Let me check.", completion=30, latency=0.2),
    ).script(
        "fba_backend",
        llm_reply(
            calls=(("get_users", {}),),
            ids=["provider_id_1"],
            prompt=300,
            completion=20,
            reasoning=15,
            latency=1.0,
        ),
        llm_reply(
            "There are two users.", prompt=400, completion=12, reasoning=5, latency=0.7
        ),
    )


# ---------------------------------------------------------------- 1-5: routing


def test_1_tool_surface_per_role():
    fake = _paired_script(FakeGenerate())
    agent, env = make_agent(fake)
    _run_turn(agent, env, agent.get_init_state([GREETING]), "who are the users?")

    assert [c["tools"] for c in fake.of("fba_frontend")] == [["call_backend"]]
    assert all(c["tools"] == MOCK_TOOLS for c in fake.of("fba_backend"))
    # Models and per-role reasoning settings come from the prototype's agent.yaml.
    fe, be = fake.of("fba_frontend")[0], fake.of("fba_backend")[0]
    assert fe["model"] == "openai/nvidia/nvidia/nemotron-3.5-lightning"
    assert be["model"] == "openai/nvidia/nvidia/nemotron-3-ultra"
    assert (
        fe["kwargs"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    )
    assert be["kwargs"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


def test_1_airline_backend_gets_every_domain_tool_and_frontend_none():
    fake = FakeGenerate()
    agent, env = make_agent(fake, domain="airline")
    assert agent._backend_client.expected_tools == {t.name for t in env.get_tools()}
    assert agent._frontend_client.expected_tools == {"call_backend"}


def test_1_frontend_prompt_carries_no_tools_and_no_policy():
    agent, env = make_agent(FakeGenerate(), domain="airline")
    frontend, backend = agent.system_prompt("frontend"), agent.system_prompt("backend")
    for tool in env.get_tools():
        assert tool.name not in frontend
    policy_lines = [
        ln.strip() for ln in env.get_policy().splitlines() if len(ln.strip()) > 40
    ]
    assert policy_lines
    assert not [ln for ln in policy_lines if ln in frontend]
    assert all(ln in backend for ln in policy_lines)
    assert "booking new flight reservations" in frontend  # domains.yaml capabilities


def test_2_surface_guard_rejects_wrong_tool_list():
    client = Tau2ChatClient(
        "backend", "m", {}, expected_tools={"a"}, generate_fn=FakeGenerate()
    )
    bad = [{"type": "function", "function": {"name": "b", "parameters": {}}}]
    with pytest.raises(ToolSurfaceError):
        asyncio.run(client.complete(messages=[], tools=bad))


@pytest.mark.parametrize("strict", [True, False])
def test_2_backend_surface_violation_is_raised_even_though_prototype_swallows_it(
    strict,
):
    fake = FakeGenerate().script("fba_frontend", delegate("List the users."))
    agent, _ = make_agent(fake, strict_transport_errors=strict)
    agent._backend_client.expected_tools = frozenset({"something_else"})
    with pytest.raises(ToolSurfaceError):
        agent.generate_next_message(_user("users?"), agent.get_init_state([GREETING]))
    assert fake.of("fba_backend") == []  # guard fired before any backend call


def test_2_call_backend_name_collision_rejected():
    def call_backend(query: str) -> str:
        """Collides with the delegation tool."""
        return query

    with pytest.raises(ToolSurfaceError):
        FBAHalfDuplexAgent(
            [as_tool(call_backend)],
            "policy",
            mode="frontend_backend",
            domain="mock",
            generate_fn=FakeGenerate(),
        )


def test_3_5_trajectory_shape_ids_and_no_leaks():
    fake = _paired_script(FakeGenerate())
    agent, env = make_agent(fake)
    msgs, _ = _run_turn(
        agent, env, agent.get_init_state([GREETING]), "who are the users?"
    )

    assert [m.is_tool_call() for m in msgs] == [True, False]
    assert msgs[0].tool_calls[0].id == "provider_id_1"  # provider id preserved
    assert msgs[0].tool_calls[0].requestor == "assistant"
    for m in msgs:  # content XOR tool_calls
        assert (m.content is None) == m.is_tool_call()
        assert not any(tc.name == "call_backend" for tc in m.tool_calls or [])
        assert m.content != "Let me check."  # filler never delivered
    assert msgs[-1].content == "There are two users."


# ---------------------------------------------------------------- 4: batches


def test_4_multitool_message_is_one_batch_in_emission_order():
    fake = (
        FakeGenerate()
        .script("fba_frontend", delegate("Create two tasks for user_1."))
        .script(
            "fba_backend",
            llm_reply(
                calls=(
                    ("create_task", {"user_id": "user_1", "title": "A"}),
                    ("create_task", {"user_id": "user_1", "title": "B"}),
                ),
                ids=["id_a", "id_b"],
            ),
            llm_reply("Created both."),
        )
    )
    agent, env = make_agent(fake)
    state = agent.get_init_state([GREETING])
    msg, state = agent.generate_next_message(_user("two tasks please"), state)
    results = [env.get_response(tc) for tc in msg.tool_calls]
    reversed_batch = MultiToolMessage(
        role="tool", tool_messages=list(reversed(results))
    )
    msg, state = agent.generate_next_message(reversed_batch, state)

    assert msg.content == "Created both."
    wire = fake.of("fba_backend")[1]["messages"]
    assert [m.id for m in wire if isinstance(m, ToolMessage)] == ["id_a", "id_b"]


# ---------------------------------------------------------------- 6: backend_only


def test_6_backend_only_has_no_frontend_work():
    fake = FakeGenerate().script(
        "fba_backend",
        llm_reply(calls=(("get_users", {}),)),
        llm_reply("There are two users."),
    )
    agent, env = make_agent(fake, mode="backend_only")
    msgs, _ = _run_turn(
        agent, env, agent.get_init_state([GREETING]), "who are the users?"
    )

    assert fake.of("fba_frontend") == []
    turn = msgs[-1].raw_data["fba"]["turn"]
    assert turn["decision"] == "backend_only"
    assert turn["frontend_calls"] == 0 and turn["filler_latency_s"] is None
    assert (
        turn["first_response_latency_s"] == turn["wall_s"]
    )  # nothing heard until the end
    # The backend keeps its own history across turns in this mode.
    assert "who are the users?" in [
        m.content for m in fake.of("fba_backend")[0]["messages"]
    ]


# ---------------------------------------------------------------- 7: accounting


def test_7_step_and_turn_accounting():
    fake = _paired_script(FakeGenerate(sleep=0.01))
    agent, env = make_agent(fake)
    msgs, _ = _run_turn(
        agent, env, agent.get_init_state([GREETING]), "who are the users?"
    )

    first, last = msgs[0].raw_data["fba"], msgs[1].raw_data["fba"]
    assert "turn" not in first and first["step_kind"] == "tool_calls"
    assert (
        first["step"]["frontend"]["calls"] == 1
        and first["step"]["backend"]["calls"] == 1
    )
    assert (
        last["step"]["frontend"]["calls"] == 0 and last["step"]["backend"]["calls"] == 1
    )

    # tau2-native fields: exactly this step's LLM calls (FE 100/30 + BE 300/20).
    assert msgs[0].usage == {"prompt_tokens": 400, "completion_tokens": 50}
    assert msgs[0].generation_time_seconds == pytest.approx(1.2)
    assert msgs[1].usage == {"prompt_tokens": 400, "completion_tokens": 12}

    turn = last["turn"]
    assert turn["decision"] == "delegate"
    assert turn["filler_text"] == "Let me check."
    assert turn["delegation_query"] == "List the users."
    assert turn["frontend_latency_s"] == pytest.approx(0.2)
    assert turn["backend_latency_s"] == pytest.approx(1.7)
    assert turn["backend_calls"] == 2 and turn["backend_tool_rounds"] == 1
    assert 0 < turn["filler_latency_s"] < turn["wall_s"]
    assert turn["first_response_latency_s"] == turn["filler_latency_s"]
    # reasoning tokens are carried per role: the gate for "reasoning off on the frontend"
    assert first["step"]["frontend"]["reasoning_tokens"] == 0
    assert (
        first["step"]["backend"]["reasoning_tokens"]
        + last["step"]["backend"]["reasoning_tokens"]
        == 20
    )


def test_7_turn_index_advances_and_turns_close():
    fake = FakeGenerate().script(
        "fba_frontend", llm_reply("Hello!"), llm_reply("You're welcome.")
    )
    agent, env = make_agent(fake)
    state = agent.get_init_state([GREETING])
    m1, state = agent.generate_next_message(_user("hi"), state)
    m2, state = agent.generate_next_message(_user("thanks"), state)
    assert [m.raw_data["fba"]["turn_index"] for m in (m1, m2)] == [1, 2]
    for m in (m1, m2):
        turn = m.raw_data["fba"]["turn"]
        assert turn["decision"] == "direct"
        assert turn["backend_calls"] == 0 and turn["filler_latency_s"] is None
    assert fake.of("fba_backend") == []


# ---------------------------------------------------------------- 8: failures


def test_8_backend_transport_error_is_reraised_when_strict():
    boom = RuntimeError("503 from the hub")
    fake = (
        FakeGenerate()
        .script("fba_frontend", delegate("List the users."))
        .script("fba_backend", boom)
    )
    agent, _ = make_agent(fake)
    with pytest.raises(BackendTransportError) as info:
        agent.generate_next_message(_user("users?"), agent.get_init_state([GREETING]))
    assert info.value.__cause__ is boom
    assert not isinstance(info.value, AgentError)  # tau2 retries it, never scores it


def test_8_backend_transport_error_becomes_text_when_lenient():
    fake = (
        FakeGenerate()
        .script("fba_frontend", delegate("List the users."))
        .script("fba_backend", RuntimeError("503"))
    )
    agent, _ = make_agent(fake, strict_transport_errors=False)
    msg, _ = agent.generate_next_message(
        _user("users?"), agent.get_init_state([GREETING])
    )
    assert msg.content.startswith("I could not complete that request")
    assert msg.raw_data["fba"]["turn"]["events"]["backend_error"] == 1


def test_8_frontend_transport_error_propagates_unchanged():
    boom = ConnectionError("hub down")
    fake = FakeGenerate().script("fba_frontend", boom)
    agent, _ = make_agent(fake)
    with pytest.raises(ConnectionError):
        agent.generate_next_message(_user("hi"), agent.get_init_state([GREETING]))


def test_8_user_message_mid_turn_is_an_agent_error():
    fake = (
        FakeGenerate()
        .script("fba_frontend", delegate("List the users."))
        .script("fba_backend", llm_reply(calls=(("get_users", {}),)))
    )
    agent, _ = make_agent(fake)
    msg, state = agent.generate_next_message(
        _user("users?"), agent.get_init_state([GREETING])
    )
    assert msg.is_tool_call()
    with pytest.raises(AgentError):
        agent.generate_next_message(_user("hello?"), state)


# ---------------------------------------------------------------- 9: repair path


def test_9_repair_placeholder_and_contract_fallback_are_counted():
    # Frontend calls a tool it does not have, then returns nothing: the prototype
    # reprompts once (max_repair_attempts: 1) and then fails closed.
    fake = FakeGenerate().script(
        "fba_frontend",
        llm_reply(calls=(("get_users", {}),)),
        llm_reply(None),
    )
    agent, _ = make_agent(fake)
    msg, _ = agent.generate_next_message(
        _user("users?"), agent.get_init_state([GREETING])
    )

    assert msg.content == agent.config.frontend.delegation.fallback_text
    turn = msg.raw_data["fba"]["turn"]
    assert turn["decision"] == "contract_fallback"
    assert turn["events"]["frontend_repair"] == 2
    assert turn["events"]["empty_assistant_placeholder"] == 1
    repair_wire = fake.of("fba_frontend")[1]["messages"]
    assert EMPTY_ASSISTANT_PLACEHOLDER in [m.content for m in repair_wire]


# ---------------------------------------------------------------- 10: replay


@pytest.mark.parametrize("mode", ["frontend_backend", "backend_only"])
def test_10_greeting_replay(mode):
    agent, _ = make_agent(FakeGenerate(), mode=mode)
    session = agent.get_init_state([GREETING]).session
    target = (
        session.frontend_history
        if mode == "frontend_backend"
        else session.backend_history
    )
    assert [m.content for m in target.messages] == [GREETING.content]


def test_10_backend_only_replays_tool_calls_and_rejects_unresolved_tail():
    agent, _ = make_agent(FakeGenerate(), mode="backend_only")
    call = AssistantMessage(
        role="assistant", tool_calls=[ToolCall(id="c1", name="get_users", arguments={})]
    )
    result = ToolMessage(id="c1", role="tool", content="[]", requestor="assistant")
    history = [GREETING, _user("users?"), call, result]
    session = agent.get_init_state(history).session
    assert [m.role for m in session.backend_history.messages] == [
        "assistant",
        "user",
        "assistant",
        "tool",
    ]
    with pytest.raises(AgentError):
        agent.get_init_state([GREETING, _user("users?"), call])


# ---------------------------------------------------------------- 12: wiring


def test_12_seed_reaches_every_llm_call():
    fake = _paired_script(FakeGenerate())
    agent, env = make_agent(fake)
    agent.set_seed(300)
    _run_turn(agent, env, agent.get_init_state([GREETING]), "users?")
    assert {c["kwargs"].get("seed") for c in fake.calls} == {300}


def test_12_register_is_idempotent_and_exposes_both_arms():
    from tau2.registry import registry

    assert tau2_fba.register() == ["fba_paired", "fba_backend_only"]
    tau2_fba.register()
    assert {"fba_paired", "fba_backend_only"} <= set(registry.get_agents())


def test_12_factory_validation():
    from tau2.domains.mock.environment import get_environment

    env = get_environment()
    kw = dict(mode="frontend_backend", generate_fn=FakeGenerate(), llm="m")
    with pytest.raises(ValueError, match="fba_domain"):
        create_fba_agent(env.get_tools(), env.get_policy(), llm_args={}, **kw)
    with pytest.raises(ValueError, match="unknown FBA llm_args"):
        create_fba_agent(
            env.get_tools(),
            env.get_policy(),
            llm_args={"fba_domain": "mock", "tempreature": 0},
            **kw,
        )
    with pytest.raises(ValueError, match="No FBA profile"):
        create_fba_agent(
            env.get_tools(), env.get_policy(), llm_args={"fba_domain": "nowhere"}, **kw
        )
