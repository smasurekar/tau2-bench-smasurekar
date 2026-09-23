"""End to end through tau2's own run_domain, offline.

Real orchestrator, real mock-domain environment, real evaluator and results.json;
only the LLMs are scripted (agent roles, user simulator, and llm_agent for the
baseline arm). Then the report is computed from what landed on disk -- which is also
the check that raw_data/usage survive tau2's serialization (plan section 8.1).
"""

import pytest
import tau2_fba
from _fakes import FakeGenerate, delegate, llm_reply
from tau2_fba import metrics

from tau2.data_model.message import AssistantMessage, ToolMessage, UserMessage
from tau2.data_model.simulation import TextRunConfig
from tau2.runner import run_domain
from tau2.user.user_simulator_base import STOP

TASK = (
    "create_task_1"  # expects create_task(user_id="user_1", title="Important Meeting")
)
REQUEST = "Please create a task called 'Important Meeting' for user_1."
DONE = "Created the task 'Important Meeting' for user_1."
CREATE = ("create_task", {"user_id": "user_1", "title": "Important Meeting"})


def _user_sim(messages):
    # The simulator sees the conversation role-flipped: the agent's words are "user".
    agent_said = [m.content or "" for m in messages if isinstance(m, UserMessage)]
    return llm_reply(
        STOP if any(DONE in s for s in agent_said) else REQUEST, latency=0.0
    )


def _worker(messages):
    """Backend / llm_agent: call the tool once, then report."""
    if isinstance(messages[-1], ToolMessage):
        return llm_reply(DONE, prompt=500, completion=15, reasoning=7, latency=0.8)
    return llm_reply(
        calls=(CREATE,), prompt=450, completion=25, reasoning=11, latency=1.2
    )


def _frontend(messages):
    return delegate(
        "Create a task titled 'Important Meeting' for user_1.",
        "One moment.",
        prompt=200,
        completion=40,
        latency=0.3,
    )


@pytest.fixture
def fake(monkeypatch):
    fake = FakeGenerate()
    fake.respond("user_simulator_response", _user_sim)
    fake.respond("fba_frontend", _frontend)
    fake.respond("fba_backend", _worker)
    fake.respond("agent_response", _worker)
    monkeypatch.setattr("tau2_fba.client.tau2_generate", fake)
    monkeypatch.setattr("tau2.user.user_simulator.generate", fake)
    monkeypatch.setattr("tau2.agent.llm_agent.generate", fake)
    return fake


def _run(tmp_path, agent: str, **llm_args_agent):
    tau2_fba.register()
    return run_domain(
        TextRunConfig(
            domain="mock",
            agent=agent,
            llm_agent="nvidia/nvidia/nemotron-3-ultra",
            llm_args_agent=llm_args_agent,
            llm_user="fake-user",
            task_ids=[TASK],
            num_trials=2,
            max_concurrency=1,
            save_to=str(tmp_path / agent),
        )
    )


def test_three_arms_run_score_and_report(tmp_path, fake):
    paired = _run(tmp_path, "fba_paired", fba_domain="mock")
    backend_only = _run(tmp_path, "fba_backend_only", fba_domain="mock")
    baseline = _run(tmp_path, "llm_agent", temperature=0.0)

    # Every run solved the task on both trials: the routed tool call really executed.
    for results in (paired, backend_only, baseline):
        assert [s.reward_info.reward for s in results.simulations] == [1.0, 1.0]

    # Reload from disk, as fba_report.py does -- by run directory, which is how the
    # runbook passes them (tau2's own Results.load would find 0 simulations there).
    loaded = {
        name: metrics.load(tmp_path / name)
        for name in ("fba_paired", "fba_backend_only", "llm_agent")
    }
    assert metrics.load(tmp_path / "fba_paired" / "results.json").simulations
    sims = loaded["fba_paired"].simulations
    agent_msgs = [m for m in sims[0].messages if isinstance(m, AssistantMessage)][
        1:
    ]  # skip greeting
    assert all("fba" in m.raw_data for m in agent_msgs)  # persisted
    assert not any(
        tc.name == "call_backend" for m in agent_msgs for tc in m.tool_calls or []
    )

    s_p = metrics.summarize(loaded["fba_paired"], "p")
    s_b = metrics.summarize(loaded["fba_backend_only"], "b")
    s_c = metrics.summarize(loaded["llm_agent"], "c")

    for s in (s_p, s_b, s_c):
        assert s["pass_hat_k"] == {1: 1.0, 2: 1.0}
        assert s["simulations"] == 2 and s["infra_errors"] == 0
        # One user turn per sim, two backend calls in it: 1.2 + 0.8 s.
        assert s["turns"] == 2
        assert s["backend_turn_latency_s"]["mean"] == pytest.approx(2.0)
        assert s["backend_calls_per_turn"] == pytest.approx(2.0)
        assert s["tokens_per_task"]["backend"]["total_tokens"] == pytest.approx(
            450 + 25 + 500 + 15
        )

    # Paired: frontend tokens, filler latency and presence are measured.
    assert s_p["tokens_per_task"]["frontend"]["total_tokens"] == pytest.approx(240)
    for s in (s_p, s_b, s_c):  # the baseline reads reasoning from tau2's raw_data
        assert s["tokens_per_task"]["backend"]["reasoning_tokens"] == pytest.approx(18)
    assert s_p["filler_presence_rate"] == 1.0
    assert s_p["filler_latency_s"]["n"] == 2
    assert s_p["decisions"] == {"delegate": 2}
    assert s_p["frontend_completion_tokens_per_call"] == pytest.approx(40)

    # Backend-only and baseline: no frontend, no filler.
    for s in (s_b, s_c):
        assert s["tokens_per_task"]["frontend"]["total_tokens"] == 0
        assert s["filler_latency_s"] is None and s["filler_presence_rate"] is None
    assert s_b["decisions"] == {"backend_only": 2}
    assert s_c["decisions"] == {"native": 2}

    # Automatic gates: 2 trials is flagged; nothing else is wrong with these runs.
    for s in (s_p, s_b, s_c):
        assert metrics.checks(s) == ["only 2 trial(s): Pass^3..4 need --num-trials 4."]
    s_bad = {
        **s_p,
        "tokens_per_task": {
            **s_p["tokens_per_task"],
            "frontend": {**s_p["tokens_per_task"]["frontend"], "reasoning_tokens": 5},
        },
        "filler_presence_rate": 0.5,
    }
    found = " ".join(metrics.checks(s_bad))
    assert "reasoning-OFF setting did not take effect" in found and "50%" in found

    report = metrics.render_markdown([s_p, s_b, s_c])
    for table in report.split("\n\n"):  # every Markdown table is well-formed
        rows = [r for r in table.splitlines() if r.startswith("|")]
        assert len({r.count("|") for r in rows}) <= 1, table
    for needle in (
        "Pass^1",
        "Pass^2",
        "fba_paired",
        "fba_backend_only",
        "llm_agent",
        "Filler latency",
    ):
        assert needle in report
    metrics.write_per_task_csv(loaded["fba_paired"], tmp_path / "per_task.csv")
    assert (tmp_path / "per_task.csv").read_text().startswith("task_id,")


def test_backend_transport_error_becomes_infra_error_not_a_score(tmp_path, fake):
    fake.respond(
        "fba_backend", lambda messages: (_ for _ in ()).throw(RuntimeError("503"))
    )
    results = _run(tmp_path, "fba_paired", fba_domain="mock")
    assert {s.termination_reason for s in results.simulations} == {
        "infrastructure_error"
    }
    summary = metrics.summarize(results)
    assert summary["infra_errors"] == 2 and summary["pass_hat_k"] == {}


def test_driver_runs_records_provenance_and_never_the_key(tmp_path, fake, monkeypatch):
    import run_fba_eval

    monkeypatch.setenv("NVIDIA_API_KEY", "sk-SENTINEL-must-not-be-persisted")
    save = tmp_path / "driver_run"
    rc = run_fba_eval.main(
        [
            "--mode",
            "paired",
            "--domain",
            "mock",
            "--user-llm",
            "fake-user",
            "--task-ids",
            TASK,
            "--num-trials",
            "1",
            "--save-to",
            str(save),
            "--llm-kwargs",
            '{"num_retries": 0}',
            "--allow-dirty-prototype",
        ]
    )
    assert rc == 0
    for name in (
        "results.json",
        "fba_report.md",
        "fba_metrics.json",
        "fba_per_task.csv",
    ):
        assert (save / name).is_file(), name

    results = metrics.load(save)
    info = results.info.agent_info
    assert info.implementation == "fba_paired"
    assert (
        info.llm == "nvidia/nvidia/nemotron-3-ultra"
    )  # backend model, from agent.yaml
    prov = info.llm_args["provenance"]
    assert prov["frontend"]["model"] == "nvidia/nvidia/nemotron-3.5-lightning"
    assert prov["prototype"]["git_sha"] and "dirty" in prov["prototype"]
    assert len(prov["prompts_sha256"]) == 64 and prov["max_concurrency"] == 1
    assert results.simulations[0].reward_info.reward == 1.0

    # The key reached the LLM call ...
    assert fake.of("fba_frontend")[0]["kwargs"]["api_key"].startswith("sk-SENTINEL")
    assert fake.of("fba_frontend")[0]["kwargs"]["num_retries"] == 0
    # ... and nothing written to disk contains it.
    for f in save.rglob("*"):
        if f.is_file():
            assert "SENTINEL" not in f.read_text(errors="ignore"), f


def test_key_equal_to_openai_api_key_is_never_passed_per_call(
    tmp_path, fake, monkeypatch
):
    """LiteLLM reads $OPENAI_API_KEY itself, so the key never enters kwargs or logs."""
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-same")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-same")
    from _fakes import make_agent

    agent, _ = make_agent(FakeGenerate())
    assert all("api_key" not in c.kwargs for c in agent.clients)


def test_driver_refuses_verbose_logs_that_would_persist_the_key(tmp_path, monkeypatch):
    import run_fba_eval

    monkeypatch.setenv("NVIDIA_API_KEY", "sk-one")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        run_fba_eval.main(
            [
                "--mode",
                "paired",
                "--domain",
                "mock",
                "--user-llm",
                "u",
                "--verbose-logs",
                "--allow-dirty-prototype",
                "--save-to",
                str(tmp_path / "x"),
            ]
        )
