"""Offline tests for I2 (fba_voice_metrics) over a synthetic event log.

fixtures/events.jsonl holds:
- paired (model ``pine-t-paired``): a retried session without tool calls, then the
  real session with a direct turn, a delegated turn with two tool rounds, and a turn
  cancelled by barge-in; plus a session of another run with the same model tag;
- backend-only (model ``pine-t-bo``): one turn with one tool round.
"""

import copy
from pathlib import Path

import fba_voice_metrics as m
import pytest

T0 = 1_800_000_000.0
EVENTS = Path(__file__).resolve().parent / "fixtures" / "events.jsonl"


def paired_sim(**overrides):
    values = dict(
        sim_id="sim_p",
        task_id="task_p",
        trial=0,
        start=T0,
        end=T0 + 60,
        reward=1.0,
        termination="user_stop",
        tool_call_ids=frozenset({"c1", "c2"}),
        agent_tokens=2770,
    )
    return m.Sim(**{**values, **overrides})


def bo_sim(**overrides):
    values = dict(
        sim_id="sim_b",
        task_id="task_b",
        trial=0,
        start=T0 + 100,
        end=T0 + 140,
        reward=0.0,
        termination="agent_stop",
        tool_call_ids=frozenset({"b1"}),
        agent_tokens=1075,
    )
    return m.Sim(**{**values, **overrides})


def run(backend_only=False, sims=None, sessions=None):
    model = "pine-t-bo" if backend_only else "pine-t-paired"
    sessions = sessions if sessions is not None else m.load_sessions(EVENTS, model)
    sims = sims if sims is not None else [bo_sim() if backend_only else paired_sim()]
    return m.analyse(
        arm="bo" if backend_only else "paired",
        backend_only=backend_only,
        sims=sims,
        sessions=sessions,
    )


def checks(result):
    return {c.code: c.status for c in result[1]}


def test_load_sessions_filters_by_model_tag():
    sessions = m.load_sessions(EVENTS, "pine-t-paired")
    assert set(sessions) == {"sess_paired", "sess_paired_retry", "sess_other_run"}
    assert sessions["sess_paired"].call_ids == {"c1", "c2"}


def test_join_prefers_call_ids_and_keeps_the_last_session():
    join = m.join_sessions([paired_sim()], m.load_sessions(EVENTS, "pine-t-paired"))
    assert join.primary == {"sim_p": "sess_paired"}
    assert join.retried == {"sim_p": ["sess_paired_retry"]}
    assert join.method == {"sess_paired": "call_id", "sess_paired_retry": "time"}
    assert join.other_run_sessions == 1
    assert not join.unmatched_sessions and not join.unmatched_sims


def test_paired_turns():
    session = m.load_sessions(EVENTS, "pine-t-paired")["sess_paired"]
    direct, delegated, cancelled = m.session_turns(session, backend_only=False)

    assert (direct.decision, direct.outcome, direct.backend_work) == (
        "direct",
        "answer",
        False,
    )
    assert direct.endpointing_ms == 500
    assert direct.answer_latency_ms == 1200
    assert direct.tts_sample_ms == pytest.approx(100, abs=1)

    assert (delegated.decision, delegated.outcome, delegated.steps) == (
        "delegate",
        "answer",
        3,
    )
    assert delegated.backend_exact_ms == 1000 + 880 + 1090
    assert delegated.backend_derived_ms == 1500 - 490 + 900 + 1100
    assert delegated.endpointing_ms == 600
    assert delegated.filler_text_latency_ms == 600 + 520
    assert delegated.answer_latency_ms == 5600
    assert delegated.tool_wait_ms == pytest.approx(600 + 900, abs=1)
    assert delegated.realtime_response_ms == pytest.approx(4100, abs=1)
    assert direct.tool_wait_ms == 0
    assert delegated.tokens["frontend"]["total_tokens"] == 420
    assert delegated.tokens["backend"]["total_tokens"] == 630 + 740 + 860

    assert cancelled.outcome == "cancelled" and cancelled.steps == 0


def test_paired_run_metrics_and_checks():
    computed, run_checks, per_task, per_turn, join_rows = run()
    latency, filler, tokens = (
        computed["latency"],
        computed["filler"],
        computed["tokens"],
    )

    assert latency["backend_turn_latency_source"] == "exact"
    assert latency["backend_turn_latency_ms"]["mean"] == 2970
    assert latency["turns_with_backend_work"] == 1
    assert (latency["direct_turns"], latency["cancelled_turns"]) == (1, 1)

    assert filler["tts_first_audio_estimate_ms"] == pytest.approx(110, abs=1)
    assert filler["tts_first_audio_source"] == "answer_step_to_first_audio"
    assert filler["filler_voice_latency_ms"]["mean"] == pytest.approx(1230, abs=1)
    assert filler["filler_would_be_heard"] == 1.0
    assert filler["answer_latency_saved_ms"]["mean"] == pytest.approx(4370, abs=1)
    # TTFA: the direct turn's answer (1200) and the delegated turn's filler (1230)
    assert latency["ttfa_projected_ms"]["mean"] == pytest.approx(1215, abs=1)

    assert tokens["per_role_available"]
    assert tokens["frontend_per_task"]["total_tokens"] == 540
    assert tokens["backend_per_task"]["total_tokens"] == 2230
    assert tokens["combined_per_task"] == 2770

    assert set(checks((computed, run_checks)).values()) == {"pass"}
    assert per_task[0]["retried_sessions"] == 1
    assert len(per_turn) == 3
    assert {row["role"] for row in join_rows} == {"primary", "retried"}


def test_backend_only_run():
    computed, run_checks, *_ = run(backend_only=True)
    latency, tokens = computed["latency"], computed["tokens"]
    assert latency["backend_turn_latency_ms"]["mean"] == 970 + 690
    assert latency["backend_turn_latency_derived_ms"]["mean"] == 980 + 700
    assert tokens["frontend_per_task"]["total_tokens"] == 0
    assert tokens["backend_per_task"]["total_tokens"] == 1075
    assert computed["filler"]["delegated_turns"] == 0
    assert set(checks((computed, run_checks)).values()) == {"pass"}


def test_without_i1_falls_back_to_derived_latency_and_combined_tokens():
    sessions = copy.deepcopy(m.load_sessions(EVENTS, "pine-t-paired"))
    for session in sessions.values():
        for record in session.records:
            for key in ("frontend", "backend", "step"):
                record.pop(key, None)
    computed, run_checks, *_ = run(sessions=sessions)
    assert computed["latency"]["backend_turn_latency_source"] == "derived"
    assert computed["latency"]["backend_turn_latency_ms"]["mean"] == 3010
    assert not computed["tokens"]["per_role_available"]
    assert computed["tokens"]["combined_per_task"] == 2770
    result = checks((computed, run_checks))
    assert result["C2"] == "warn" and result["C4"] == "pass"


def test_failing_checks():
    # agent > tau2 in a session with a barge-in: tau2 undercounts -> warn
    assert checks(run(sims=[paired_sim(agent_tokens=1000)]))["C4"] == "warn"
    # agent < tau2: the agent log is missing usage -> fail
    assert checks(run(sims=[paired_sim(agent_tokens=9999)]))["C4"] == "fail"
    # agent > tau2 without any barge-in -> fail
    assert (
        checks(run(backend_only=True, sims=[bo_sim(agent_tokens=1000)]))["C4"] == "fail"
    )
    assert (
        checks(run(sims=[paired_sim(termination="infrastructure_error")]))["C8"]
        == "fail"
    )
    assert checks(run(sims=[paired_sim(termination="too_many_errors")]))["C8"] == "warn"
    lonely = m.load_sessions(EVENTS, "pine-t-paired")
    far = paired_sim(sim_id="sim_far", start=T0 + 5000, end=T0 + 5060)
    result = checks(run(sims=[paired_sim(), far], sessions=lonely))
    assert result["C1"] == "fail"  # sim_far has no session
    paired_sessions = m.load_sessions(EVENTS, "pine-t-paired")
    result = checks(
        m.analyse(
            arm="bo",
            backend_only=True,
            sims=[paired_sim()],
            sessions=paired_sessions,
        )
    )
    assert result["C7"] == "fail"  # filler records in a backend-only run


def test_log_only_mode_is_enforced():
    sessions = copy.deepcopy(m.load_sessions(EVENTS, "pine-t-paired"))
    for record in sessions["sess_paired"].records:
        if record["kind"] == "filler_timing":
            record["mode"] = "speak"
    assert checks(run(sessions=sessions))["C6"] == "fail"


def test_stats():
    result = m.stats([None, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert result == {"n": 10, "mean": 5.5, "p50": 5.5, "p90": 9.0}
    assert m.stats([])["mean"] is None


def test_frontend_turn_latency():
    computed, *_ = run()
    frontend = computed["latency"]["frontend_turn_latency_ms"]
    assert computed["latency"]["frontend_turn_latency_source"] == "exact"
    assert (frontend["n"], frontend["mean"]) == (2, (580 + 480) / 2)
    computed, *_ = run(backend_only=True)
    assert computed["latency"]["frontend_turn_latency_ms"]["n"] == 0


def _report(backend_only=False):
    computed, run_checks, per_task, per_turn, join_rows = run(backend_only=backend_only)
    interaction = {
        "domains": {
            "mock": {
                "response_rate": 1.0,
                "yield_rate": 0.5,
                "response_latency_mean": 2.345,
                "yield_latency_mean": None,
                "agent_interruption_rate": 0.25,
                "selectivity_backchannel": 1.0,
                "selectivity_vocal_tic": None,
                "selectivity_non_directed": 0.5,
            }
        }
    }
    setup = dict.fromkeys(m.SETUP_FIELDS, "x") | {"frontend_llm": "fe-model"}
    if backend_only:
        setup |= {"frontend_llm": "none (backend-only)", "frontend_reasoning": "n/a"}
    return m.RunReport(
        arm="bo" if backend_only else "paired",
        run_dir="d",
        run_name="r",
        domain="mock",
        complexity="regular",
        model="pine-t",
        backend_only=backend_only,
        tau2={"sims": 1, "pass_1": 1.0},
        interaction=interaction,
        checks=run_checks,
        provenance={"setup": setup},
        per_task=per_task,
        per_turn=per_turn,
        join_rows=join_rows,
        **computed,
    )


def test_results_row_has_every_requested_column():
    row = m.results_row(_report())
    assert list(row) == [key for _, key in m.RESULTS_COLUMNS]
    assert row["frontend_llm"] == "fe-model"
    assert row["pass_1"] == "1.000"
    assert row["responsiveness"] == "R_R 100% · R_Y 50%"
    assert row["latency"] == "L_R 2.35 s · L_Y – s"
    assert row["interrupts"] == "I_A 25%"
    assert row["selectivity"] == "S_BC 100% · S_VT – · S_ND 50%"
    # (1200 + (5600 - 1500 of tool waits)) / 2 ms
    assert row["realtime_response_latency_s"] == "2.65"
    assert row["frontend_turn_latency_s"] == "0.53"
    assert row["backend_turn_latency_s"] == "2.97"
    table = m.render_results_table([_report(), _report(backend_only=True)])
    assert table.count("\n") == 4
    bo = m.results_row(_report(backend_only=True))
    assert bo["frontend_turn_latency_s"] == "n/a"
    assert bo["frontend_llm"] == "none (backend-only)"
