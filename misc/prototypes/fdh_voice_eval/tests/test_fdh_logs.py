"""Offline tests for fdh_logs.py (synthetic logs in tmp_path)."""

import csv
import json
from pathlib import Path

import fdh_logs
import pytest

RUN = "fdh_voice_dlg_airline_regular"
MODEL = "pine-fdh-voice-dlg-airline-regular"


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records) + "not json\n\n",
        encoding="utf-8",
    )
    return path


def session(sid: str, model: str, t0: float) -> list[dict]:
    return [
        {"timestamp": t0, "kind": "session_start", "session_id": sid, "model": model},
        {"timestamp": t0 + 1, "kind": "delegation_decision", "session_id": sid, "turn_id": 1,
         "delegate": True, "repair": "timeout" if sid.endswith("2") else ""},
        {"timestamp": t0 + 2, "kind": "filler_timing", "session_id": sid, "turn_id": 1,
         "user_stop_to_first_audio_ms": 1500},
        {"timestamp": t0 + 3, "kind": "backend_action", "session_id": sid, "turn_id": 1, "action": "start"},
        {"timestamp": t0 + 4, "kind": "backend_run_done", "session_id": sid, "status": "ok"},
        {"timestamp": t0 + 5, "kind": "turn_latency", "session_id": sid, "turn_id": 1,
         "user_stop_to_first_audio_ms": 4000},
        {"timestamp": t0 + 6, "kind": "session_end", "session_id": sid},
    ]  # fmt: skip


@pytest.fixture
def event_log(tmp_path: Path) -> Path:
    records = (
        session("sess_a1", MODEL, 0)
        + session("sess_a2", MODEL, 10)
        + session("sess_b1", "pine-other", 20)
    )
    return write_jsonl(tmp_path / "events.jsonl", records)


def make_run(tmp_path: Path, logs: dict[str, list[str]]) -> Path:
    run = tmp_path / RUN
    for n, (sim, ids) in enumerate(logs.items()):
        d = run / "artifacts" / f"task_{n}" / f"sim_{sim}"
        d.mkdir(parents=True)
        lines = [
            f"2026-09-29 21:43:45.123 | INFO | x - OpenAI Realtime API: session created (session_id={i})"
            for i in ids
        ]
        (d / "task.log").write_text(
            "noise\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
    return run


def make_join(
    tmp_path: Path,
    rows: list[tuple[str, str, str]],
    run: str = RUN,
    name: str = "join.csv",
) -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(
            ["arm", "run", "session_id", "task_id", "trial", "sim_id", "method", "role"]
        )
        for n, (sid, sim, role) in enumerate(rows):
            w.writerow(["dlg", run, sid, str(n), "0", sim, "call_id", role])
    return path


def test_model_tag():
    assert fdh_logs.model_tag(RUN) == MODEL
    assert fdh_logs.model_tag("fdh_voice_dlg_banking_knowledge_regular_smoke") == (
        "pine-fdh-voice-dlg-banking-knowledge-regular-smoke"
    )


def test_run_sessions_selects_by_model(event_log: Path):
    assert fdh_logs.run_sessions(event_log, MODEL) == {"sess_a1", "sess_a2"}


def test_filter_writes_only_run_sessions(tmp_path: Path, event_log: Path):
    gateway = write_jsonl(tmp_path / "gw.jsonl", [
        {"ts": 1, "event": "session_open", "session_id": "sess_a1"},
        {"ts": 2, "event": "session_open", "session_id": "sess_b1"},
    ])  # fmt: skip
    out = tmp_path / "out"
    assert fdh_logs.main(["filter", str(event_log), RUN, "--gateway", str(gateway),
                          "--legacy", str(tmp_path / "missing.jsonl"), "--out", str(out)]) == 0  # fmt: skip
    assert (out / "sessions.txt").read_text().split() == ["sess_a1", "sess_a2"]
    kept = [
        json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()
    ]
    assert {r["session_id"] for r in kept} == {"sess_a1", "sess_a2"} and len(kept) == 14
    assert [
        json.loads(line)["session_id"]
        for line in (out / "gateway_events.jsonl").read_text().splitlines()
    ] == ["sess_a1"]
    assert not (out / "events.legacy.jsonl").exists()


def test_filter_fails_without_sessions(tmp_path: Path, event_log: Path):
    assert (
        fdh_logs.main(
            [
                "filter",
                str(event_log),
                "fdh_voice_dlg_retail_regular",
                "--out",
                str(tmp_path / "o"),
            ]
        )
        == 1
    )


def test_exact_join_pass_and_mismatch(tmp_path: Path):
    run = make_run(
        tmp_path, {"s1": ["sess_a1"], "s2": ["sess_0f", "sess_a2"], "s3": []}
    )
    ok = make_join(
        tmp_path,
        [
            ("sess_a1", "s1", "primary"),
            ("sess_a2", "s2", "primary"),
            ("sess_0f", "s2", "retry"),
        ],
        name="ok.csv",
    )
    rows, bad = fdh_logs.exact_join(run, ok)
    assert len(rows) == 2 and bad == []
    wrong = make_join(
        tmp_path,
        [("sess_0f", "s2", "primary"), ("sess_a1", "s3", "primary")],
        name="wrong.csv",
    )
    rows, bad = fdh_logs.exact_join(run, wrong)
    assert [b["sim_id"] for b in bad] == ["s2", "s3"]
    assert bad[0]["task_log_sessions"] == ["sess_0f", "sess_a2"]
    assert fdh_logs.main(["exact-join", str(run), "--join", str(wrong)]) == 1
    assert fdh_logs.main(["exact-join", str(run), "--join", str(ok)]) == 0


def test_exact_join_ignores_other_runs(tmp_path: Path):
    run = make_run(tmp_path, {"s1": ["sess_a1"]})
    join = make_join(
        tmp_path, [("sess_zz", "s9", "primary")], run="fdh_voice_dlg_retail_regular"
    )
    assert fdh_logs.exact_join(run, join) == ([], [])
    assert (
        fdh_logs.main(["exact-join", str(run), "--join", str(join)]) == 1
    )  # no joins at all is a failure


def test_first_audio_latencies_primary_only(tmp_path: Path, event_log: Path):
    join = make_join(
        tmp_path, [("sess_a1", "s1", "primary"), ("sess_a2", "s2", "retry")]
    )
    result = fdh_logs.first_audio_latencies([event_log], join)
    assert result == {RUN: {"filler": [1.5], "answer": [4.0]}}
    out = tmp_path / "f.json"
    assert (
        fdh_logs.main(
            ["filler", str(event_log), "--join", str(join), "--json", str(out)]
        )
        == 0
    )
    assert json.loads(out.read_text())[RUN]["filler"] == {
        "n": 1,
        "mean": 1.5,
        "p90": 1.5,
    }


def test_p90_nearest_rank():
    assert fdh_logs.p90([1.0, 2.0]) == 2.0
    assert fdh_logs.p90(list(map(float, range(1, 11)))) == 9.0
    assert fdh_logs.summarize([]) == {"n": 0, "mean": None, "p90": None}


def test_run_status(tmp_path: Path, event_log: Path):
    gateway = write_jsonl(tmp_path / "gw.jsonl", [
        {"ts": 1, "event": "session_open", "session_id": "sess_a1"},
        {"ts": 2, "event": "capacity_refused", "session_id": None},
    ])  # fmt: skip
    s = fdh_logs.run_status(event_log, MODEL, gateway)
    assert s["sessions"] == 2 and s["open_sessions"] == 0 and s["delegated"] == 2
    assert s["repairs"] == {"timeout": 1} and s["backend_runs"] == {"ok": 2}
    assert (s["session_rules"], s["tools_sha256"], s["answered_locally"]) == (0, [], {})
    assert (
        s["gateway"] == {"session_open": 1}
        and s["gateway_capacity_refused_all_time"] == 1
    )


def test_run_status_counts_schema_rules_and_normalization(tmp_path: Path):
    records = session("sess_b1", MODEL, 0) + [
        {"timestamp": 1, "kind": "backend_configured", "session_id": "sess_b1", "session_tools_sha256": "abc"},
        {"timestamp": 1, "kind": "session_rules", "session_id": "sess_b1", "rules": []},
        {"timestamp": 2, "kind": "argument_normalized", "session_id": "sess_b1"},
        {"timestamp": 3, "kind": "call_answered_locally", "session_id": "sess_b1", "reason": "invalid"},
        {"timestamp": 4, "kind": "result_hint", "session_id": "sess_b1"},
    ]  # fmt: skip
    s = fdh_logs.run_status(write_jsonl(tmp_path / "ev.jsonl", records), MODEL)
    assert (s["session_rules"], s["tools_sha256"], s["argument_normalized"]) == (
        1,
        ["abc"],
        1,
    )
    assert (s["answered_locally"], s["result_hints"]) == ({"invalid": 1}, 1)


def test_checks_allow_c6(tmp_path: Path):
    metrics = tmp_path / "m.json"
    checks = [{"code": "C1", "status": "pass", "detail": ""}, {"code": "C4", "status": "warn", "detail": ""},
              {"code": "C6", "status": "fail", "detail": "filler modes {'speak': 2}"}]  # fmt: skip
    metrics.write_text(json.dumps([{"run_name": RUN, "checks": checks}]))
    assert fdh_logs.main(["checks", str(metrics)]) == 0
    checks.append({"code": "C1", "status": "fail", "detail": "unmatched"})
    metrics.write_text(json.dumps([{"run_name": RUN, "checks": checks}]))
    lines, bad = fdh_logs.unexpected_failures(metrics, {"C6"})
    assert len(lines) == 4 and bad == [f"{RUN}: C1 FAIL unmatched"]
    assert fdh_logs.main(["checks", str(metrics)]) == 1


def test_campaign_runs(tmp_path: Path):
    (tmp_path / "fdh_voice_dlg_mock_control.start").write_text(
        "2026-09-30T08:05:00+00:00\n2026-09-30T09:00:00+00:00\n"
    )
    (tmp_path / "fdh_voice_dlg_mock_control_smoke.start").write_text(
        "2026-09-29T21:43:00+00:00\n"
    )
    (tmp_path / "fdh_voice_dlg_airline_regular.start").write_text(
        "2026-09-30T10:00:00+02:00\n"
    )  # 08:00 UTC
    (tmp_path / "fba_voice_paired_airline_regular.start").write_text(
        "2026-09-30T09:00:00+00:00\n"
    )
    runs = fdh_logs.campaign_runs(tmp_path, "2026-09-30_08-00-00Z_fdh-voice")
    assert runs == ["fdh_voice_dlg_airline_regular", "fdh_voice_dlg_mock_control"]


def test_c4_barge_in_undercount_is_reclassified(tmp_path: Path, event_log: Path):
    # sess_a1 has a barge-in in the raw log; sess_a2 has none.
    raw = write_jsonl(
        tmp_path / "raw.jsonl",
        [{"timestamp": 1, "kind": "barge_in", "session_id": "sess_a1"}],
    )
    join = make_join(
        tmp_path, [("sess_a1", "s1", "primary"), ("sess_a2", "s2", "primary")]
    )
    metrics = tmp_path / "m.json"

    def check(detail: str) -> dict:
        return {"code": "C4", "status": "fail", "detail": detail}

    metrics.write_text(
        json.dumps([{"run_name": RUN, "checks": [check("0: agent 900 vs tau2 800")]}])
    )
    lines, bad = fdh_logs.unexpected_failures(metrics, {"C6"}, join, [raw])
    assert bad == [] and "C4 WARN barge-in undercount" in lines[0]
    assert (
        fdh_logs.main(
            ["checks", str(metrics), "--join", str(join), "--event-logs", str(raw)]
        )
        == 0
    )
    # without the raw log it stays a FAIL
    assert fdh_logs.unexpected_failures(metrics, {"C6"})[1]

    # a gap in a session without barge-ins, or tau2 > agent, is still a FAIL
    for detail in (
        "0: agent 900 vs tau2 800; 1: agent 900 vs tau2 800",
        "0: agent 700 vs tau2 800",
    ):
        metrics.write_text(json.dumps([{"run_name": RUN, "checks": [check(detail)]}]))
        assert fdh_logs.unexpected_failures(metrics, {"C6"}, join, [raw])[1]
