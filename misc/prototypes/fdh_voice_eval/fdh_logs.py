"""Log helpers for the τ³ voice evaluation of the Frontend Delegation Agent (``fdh-voice``).

Standard library only, so it runs with any ``python3``. Runbook:
``misc/prototypes/voice-frontend-delegation-hermes-tau3-runbook.md``.

Subcommands:

* ``sessions``      session ids of one run (``session_start.model`` == the run's ``pine-`` tag)
* ``filter``        copy the records of those sessions from the voice, legacy and gateway logs
* ``exact-join``    check ``join.csv`` against the session id each simulation's ``task.log`` records
* ``filler``        measured filler and answer first-audio latency per run
* ``status``        agent-side progress counts of one run
* ``checks``        summarise ``fba_voice_metrics.json`` checks; exit 1 on an unexpected FAIL
* ``ratelimit``     the 429 gate (P0.1 of the agent's ``tau3-geval-failure-fixes-plan.md``): backend
                    rate-limit hits per run from the Hermes worker logs, and the backend-run p90;
                    exit 1 when any session of a run was throttled (the run is invalid)
* ``campaign-runs`` run names started since the campaign began (from ``_consoles/*.start``)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION_CREATED = re.compile(r"session created \(session_id=(sess_[0-9a-f]+)\)")
RUN_PREFIX = "fdh_voice_"


def model_tag(run_name: str) -> str:
    """The ``pine-`` model tag of a run: ``fdh_voice_dlg_airline_regular`` -> ``pine-fdh-voice-dlg-airline-regular``."""
    return "pine-" + run_name.replace("_", "-")


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Records of a JSONL file; blank and malformed lines are skipped."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def run_sessions(event_log: Path, model: str) -> set[str]:
    """Session ids whose ``session_start.model`` is ``model``."""
    return {
        str(r["session_id"])
        for r in iter_jsonl(event_log)
        if r.get("kind") == "session_start" and r.get("model") == model
    }


def filter_records(src: Path, dst: Path, session_ids: set[str]) -> int | None:
    """Copy the lines of ``src`` that belong to ``session_ids``; None when ``src`` is missing."""
    if not src.exists():
        return None
    count = 0
    with src.open(encoding="utf-8") as handle, dst.open("w", encoding="utf-8") as out:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(record.get("session_id")) in session_ids:
                out.write(line if line.endswith("\n") else line + "\n")
                count += 1
    return count


def task_log_sessions(run_dir: Path) -> dict[str, list[str]]:
    """sim_id -> Realtime session ids in that simulation's ``task.log``, in order."""
    found = {}
    for log in run_dir.glob("artifacts/task_*/sim_*/task.log"):
        text = log.read_text(encoding="utf-8", errors="ignore")
        found[log.parent.name[len("sim_") :]] = SESSION_CREATED.findall(text)
    return found


def exact_join(
    run_dir: Path, join_csv: Path
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Primary joins of ``run_dir`` in ``join_csv``, and those that disagree with ``task.log``.

    A join is exact when its session is the last one the simulation's ``task.log`` records
    (earlier ones are reconnects or retries of the same simulation).
    """
    exact = task_log_sessions(run_dir)
    with join_csv.open(encoding="utf-8") as handle:
        rows = [
            r
            for r in csv.DictReader(handle)
            if r["run"] == run_dir.name and r["role"] == "primary"
        ]
    bad = []
    for row in rows:
        ids = exact.get(row["sim_id"]) or []
        if not ids or ids[-1] != row["session_id"]:
            bad.append({**row, "task_log_sessions": ids})
    return rows, bad


def primary_sessions(join_csv: Path) -> dict[str, set[str]]:
    """run name -> primary session ids, from ``join.csv``."""
    runs: dict[str, set[str]] = defaultdict(set)
    with join_csv.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["role"] == "primary":
                runs[row["run"]].add(row["session_id"])
    return runs


#: A Hermes worker log line of a backend rate limit: the 429 itself, or the retry wait it causes.
RATE_LIMIT_MARKERS = ("Error code: 429", "Retrying API call")
WORKER_LOG = re.compile(r"^(sess_[0-9a-f]+)-\d+\.log$")


def run_session_ids(join_csv: Path) -> dict[str, set[str]]:
    """run name -> every session id of the run in ``join.csv`` (primary and retried)."""
    runs: dict[str, set[str]] = defaultdict(set)
    with join_csv.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            runs[row["run"]].add(row["session_id"])
    return runs


def rate_limits(worker_log_dir: Path, sessions: set[str]) -> dict[str, dict[str, int]]:
    """session -> {"429": n, "retry_waits": n} for the sessions with at least one hit."""
    out: dict[str, dict[str, int]] = {}
    for path in sorted(worker_log_dir.glob("sess_*.log")):
        match = WORKER_LOG.match(path.name)
        if match is None or match.group(1) not in sessions:
            continue
        counts = {"429": 0, "retry_waits": 0}
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                counts["429"] += RATE_LIMIT_MARKERS[0] in line
                counts["retry_waits"] += RATE_LIMIT_MARKERS[1] in line
        if counts["429"] or counts["retry_waits"]:
            entry = out.setdefault(match.group(1), {"429": 0, "retry_waits": 0})
            for key, value in counts.items():
                entry[key] += value
    return out


def backend_run_seconds(gateway_logs: list[Path], sessions: set[str]) -> list[float]:
    """Gateway ``backend_run_dispatched`` -> ``backend_run_done`` durations of the sessions' runs."""
    dispatched: dict[str, float] = {}
    out: list[float] = []
    for log in gateway_logs:
        for record in iter_jsonl(log):
            if record.get("session_id") not in sessions:
                continue
            if record.get("event") == "backend_run_dispatched":
                dispatched[str(record.get("run_id"))] = float(record["ts"])
            elif (
                record.get("event") == "backend_run_done"
                and str(record.get("run_id")) in dispatched
            ):
                out.append(
                    float(record["ts"]) - dispatched.pop(str(record.get("run_id")))
                )
    return out


def p90(values: list[float]) -> float:
    """Nearest-rank 90th percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def first_audio_latencies(
    event_logs: list[Path], join_csv: Path
) -> dict[str, dict[str, list[float]]]:
    """run -> {"filler": [...], "answer": [...]} seconds from the end of user speech to first audio.

    ``filler`` is ``filler_timing.user_stop_to_first_audio_ms`` (the spoken filler). ``answer`` is
    ``turn_latency.user_stop_to_first_audio_ms`` (the answer; wall clock, includes tool waits).
    """
    runs = primary_sessions(join_csv)
    owner = {sid: run for run, sids in runs.items() for sid in sids}
    out: dict[str, dict[str, list[float]]] = {
        run: {"filler": [], "answer": []} for run in runs
    }
    for log in event_logs:
        for record in iter_jsonl(log):
            run = owner.get(str(record.get("session_id")))
            value = record.get("user_stop_to_first_audio_ms")
            if run is None or value is None:
                continue
            if record.get("kind") == "filler_timing":
                out[run]["filler"].append(float(value) / 1000.0)
            elif record.get("kind") == "turn_latency":
                out[run]["answer"].append(float(value) / 1000.0)
    return out


def summarize(values: list[float]) -> dict[str, float | int | None]:
    """n, mean and p90 of a list of seconds."""
    if not values:
        return {"n": 0, "mean": None, "p90": None}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "p90": round(p90(values), 3),
    }


def run_status(
    event_log: Path, model: str, gateway_log: Path | None = None
) -> dict[str, Any]:
    """Agent-side progress of one run: sessions, decisions, backend runs, repairs, gateway refusals."""
    records = list(iter_jsonl(event_log))
    sids = {
        str(r["session_id"])
        for r in records
        if r.get("kind") == "session_start" and r.get("model") == model
    }
    mine = [r for r in records if str(r.get("session_id")) in sids]
    kinds = Counter(r.get("kind") for r in mine)
    status = {
        "sessions": len(sids),
        "open_sessions": len(
            sids
            - {str(r["session_id"]) for r in mine if r.get("kind") == "session_end"}
        ),
        "decisions": kinds["delegation_decision"],
        "delegated": sum(
            1
            for r in mine
            if r.get("kind") == "delegation_decision" and r.get("delegate")
        ),
        "repairs": dict(
            Counter(
                r.get("repair")
                for r in mine
                if r.get("kind") == "delegation_decision" and r.get("repair")
            )
        ),
        "backend_runs": dict(
            Counter(
                r.get("status") for r in mine if r.get("kind") == "backend_run_done"
            )
        ),
        "backend_actions": dict(
            Counter(r.get("action") for r in mine if r.get("kind") == "backend_action")
        ),
        "tool_outputs": kinds["tool_output_in"],
        "status_spoken": kinds["status_spoken"],
        "barge_in": kinds["barge_in"],
        "backend_error": kinds["backend_error"],
        # Tool-argument normalization and recovery notes (every profile with client tools).
        "argument_normalized": kinds["argument_normalized"],
        "answered_locally": dict(
            Counter(
                r.get("reason")
                for r in mine
                if r.get("kind") == "call_answered_locally"
            )
        ),
        "result_hints": kinds["result_hint"],
        # Schema-derived rules (realtime_eval.yaml): one session_rules event per session, and one
        # tool-schema hash per domain.
        "session_rules": kinds["session_rules"],
        "tools_sha256": sorted(
            {
                str(r["session_tools_sha256"])
                for r in mine
                if r.get("kind") == "backend_configured"
                and r.get("session_tools_sha256")
            }
        ),
    }
    if gateway_log is not None and gateway_log.exists():
        gateway = Counter(
            r.get("event")
            for r in iter_jsonl(gateway_log)
            if str(r.get("session_id")) in sids
        )
        status["gateway"] = {
            k: gateway[k]
            for k in ("session_open", "worker_ready", "backend_run_done")
            if gateway[k]
        }
        status["gateway_capacity_refused_all_time"] = sum(
            1 for r in iter_jsonl(gateway_log) if r.get("event") == "capacity_refused"
        )
    return status


C4_ENTRY = re.compile(r"(?P<task>[^;:]+): agent (?P<agent>\d+) vs tau2 (?P<tau2>\d+)")
INTERRUPT_KINDS = ("barge_in", "thinking_cancelled")


def interrupted_sessions(event_logs: list[Path]) -> set[str]:
    """Sessions with a ``barge_in`` or ``thinking_cancelled`` record in the raw voice event logs."""
    return {
        str(r.get("session_id"))
        for log in event_logs
        for r in iter_jsonl(log)
        if r.get("kind") in INTERRUPT_KINDS
    }


def c4_is_barge_in_undercount(
    run_name: str, detail: str, join_csv: Path, interrupted: set[str]
) -> bool:
    """Whether a C4 FAIL is only the known barge-in undercount, which ``fba_voice_metrics.py`` reports as WARN.

    The script calls a task's token gap a WARN when the agent count is higher and the session
    has a barge-in. It looks for barge-ins in the report adapter's output, which drops them, so
    every gap becomes a FAIL. This applies the same rule with the raw event log.
    """
    with join_csv.open(encoding="utf-8") as handle:
        session = {
            r["task_id"]: r["session_id"]
            for r in csv.DictReader(handle)
            if r["run"] == run_name and r["role"] == "primary"
        }
    entries = [m.groupdict() for m in C4_ENTRY.finditer(detail)]
    return bool(entries) and all(
        int(e["agent"]) > int(e["tau2"])
        and session.get(e["task"].strip()) in interrupted
        for e in entries
    )


def unexpected_failures(
    metrics_json: Path,
    allowed: set[str],
    join_csv: Path | None = None,
    event_logs: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """(report lines, unexpected FAIL lines) from ``fba_voice_metrics.json``.

    With ``join_csv`` and the raw ``event_logs``, a C4 FAIL that is only the barge-in
    undercount is reported as WARN (see ``c4_is_barge_in_undercount``).
    """
    interrupted = interrupted_sessions(event_logs) if join_csv and event_logs else None
    lines, bad = [], []
    for run in json.loads(metrics_json.read_text(encoding="utf-8")):
        for check in run.get("checks") or []:
            status = str(check.get("status"))
            if (
                status == "fail"
                and check.get("code") == "C4"
                and interrupted is not None
                and c4_is_barge_in_undercount(
                    str(run.get("run_name")),
                    str(check.get("detail")),
                    join_csv,
                    interrupted,
                )
            ):
                status = "warn"
                check = {
                    **check,
                    "detail": "barge-in undercount (agent > tau2 in sessions with barge-ins; "
                    f"reclassified from FAIL, the agent count is used): {check.get('detail')}",
                }
            line = f"{run.get('run_name')}: {check.get('code')} {status.upper()} {check.get('detail')}"
            lines.append(line)
            if status == "fail" and check.get("code") not in allowed:
                bad.append(line)
    return lines, bad


def campaign_start(campaign: str) -> datetime:
    """UTC start of a campaign named ``YYYY-MM-DD_HH-MM-SSZ_<label>``."""
    return datetime.strptime(campaign[:20], "%Y-%m-%d_%H-%M-%SZ").replace(
        tzinfo=timezone.utc
    )


def campaign_runs(consoles: Path, campaign: str) -> list[str]:
    """``fdh_voice_*`` runs whose first start stamp is at or after the campaign start."""
    since = campaign_start(campaign)
    runs = []
    for stamp in sorted(consoles.glob(f"{RUN_PREFIX}*.start")):
        lines = stamp.read_text(encoding="utf-8").split()
        if lines and datetime.fromisoformat(lines[0]) >= since:
            runs.append(stamp.stem)
    return runs


# -- CLI ----------------------------------------------------------------------------------------


def _cmd_sessions(args: argparse.Namespace) -> int:
    for sid in sorted(run_sessions(args.event_log, args.model or model_tag(args.run))):
        print(sid)
    return 0


def _cmd_filter(args: argparse.Namespace) -> int:
    model = model_tag(args.run)
    sids = run_sessions(args.event_log, model)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sessions.txt").write_text(
        "".join(f"{s}\n" for s in sorted(sids)), encoding="utf-8"
    )
    sources = [(args.event_log, "events.jsonl")]
    if args.legacy:
        sources.append((args.legacy, "events.legacy.jsonl"))
    if args.gateway:
        sources.append((args.gateway, "gateway_events.jsonl"))
    print(f"{len(sids)} sessions for {model}")
    for src, name in sources:
        count = filter_records(src, args.out / name, sids)
        print(
            f"  {name}: " + (f"missing {src}" if count is None else f"{count} records")
        )
    return 0 if sids else 1


def _cmd_exact_join(args: argparse.Namespace) -> int:
    failed = False
    for run_dir in args.run_dirs:
        rows, bad = exact_join(run_dir, args.join)
        print(
            f"{run_dir.name}: {len(rows)} primary joins, {len(rows) - len(bad)} exact, {len(bad)} mismatch"
        )
        for row in bad:
            print(f"  MISMATCH task {row['task_id']} sim {row['sim_id']} joined {row['session_id']} "
                  f"task.log {row['task_log_sessions'] or None}")  # fmt: skip
        failed = failed or bool(bad) or not rows
    return 1 if failed else 0


def _cmd_filler(args: argparse.Namespace) -> int:
    result = first_audio_latencies(args.event_logs, args.join)
    for run in sorted(result):
        f, a = summarize(result[run]["filler"]), summarize(result[run]["answer"])
        print(f"{run}: filler first audio n={f['n']} mean={f['mean']}s p90={f['p90']}s | "
              f"answer first audio (wall, incl. tool waits) n={a['n']} mean={a['mean']}s p90={a['p90']}s")  # fmt: skip
    if args.json:
        summary = {
            run: {k: summarize(v) for k, v in values.items()}
            for run, values in result.items()
        }
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(run_status(args.event_log, model_tag(args.run), args.gateway)))
    return 0


def _cmd_checks(args: argparse.Namespace) -> int:
    lines, bad = unexpected_failures(
        args.metrics_json, set(args.allow_fail), args.join, args.event_logs
    )
    for line in lines:
        print(line)
    for line in bad:
        print(f"UNEXPECTED FAIL: {line}", file=sys.stderr)
    return 1 if bad else 0


def _cmd_ratelimit(args: argparse.Namespace) -> int:
    invalid = 0
    for run, sessions in sorted(run_session_ids(args.join).items()):
        hits = rate_limits(args.worker_logs, sessions)
        durations = backend_run_seconds(args.gateway_logs, sessions)
        p90_s = f"{p90(durations):.1f}s" if durations else "n/a"
        total_429 = sum(h["429"] for h in hits.values())
        waits = sum(h["retry_waits"] for h in hits.values())
        verdict = "INVALID (re-run it)" if hits else "OK"
        invalid += bool(hits)
        print(
            f"P0.1 {run}: 429s {total_429}, retry waits {waits}, throttled sessions {len(hits)}/{len(sessions)}; "
            f"backend run p90 {p90_s} (n={len(durations)}): {verdict}"
        )
    return 1 if invalid else 0


def _cmd_campaign_runs(args: argparse.Namespace) -> int:
    for run in campaign_runs(args.consoles, args.campaign):
        print(run)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sessions", help="session ids of one run")
    p.add_argument("event_log", type=Path)
    p.add_argument("run", help="run name, e.g. fdh_voice_dlg_airline_regular")
    p.add_argument(
        "--model", default="", help="model tag (default: derived from the run name)"
    )
    p.set_defaults(func=_cmd_sessions)

    p = sub.add_parser("filter", help="copy one run's records from the agent logs")
    p.add_argument("event_log", type=Path, help="voice server event log")
    p.add_argument("run")
    p.add_argument("--legacy", type=Path, help="report-adapter output of the same log")
    p.add_argument("--gateway", type=Path, help="gateway event log")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=_cmd_filter)

    p = sub.add_parser("exact-join", help="check join.csv against task.log session ids")
    p.add_argument("run_dirs", type=Path, nargs="+")
    p.add_argument("--join", type=Path, required=True)
    p.set_defaults(func=_cmd_exact_join)

    p = sub.add_parser("filler", help="measured filler / answer first-audio latency")
    p.add_argument("event_logs", type=Path, nargs="+")
    p.add_argument("--join", type=Path, required=True)
    p.add_argument("--json", type=Path, help="also write the summary as JSON")
    p.set_defaults(func=_cmd_filler)

    p = sub.add_parser("status", help="agent-side progress of one run")
    p.add_argument("event_log", type=Path)
    p.add_argument("run")
    p.add_argument("--gateway", type=Path)
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("checks", help="summarise the metrics script's checks")
    p.add_argument("metrics_json", type=Path)
    p.add_argument(
        "--allow-fail",
        nargs="*",
        default=["C6"],
        help="check codes allowed to fail (default: C6)",
    )
    p.add_argument("--join", type=Path, help="join.csv (to reclassify a C4 FAIL)")
    p.add_argument(
        "--event-logs",
        type=Path,
        nargs="*",
        default=[],
        help="raw voice event logs (to reclassify a C4 FAIL)",
    )
    p.set_defaults(func=_cmd_checks)

    p = sub.add_parser(
        "ratelimit", help="the 429 gate: backend rate limits per run (P0.1)"
    )
    p.add_argument("--join", type=Path, required=True, help="join.csv of the report")
    p.add_argument(
        "--worker-logs", type=Path, required=True, help="Hermes worker log directory"
    )
    p.add_argument(
        "--gateway-logs",
        type=Path,
        nargs="*",
        default=[],
        help="gateway event logs (p90)",
    )
    p.set_defaults(func=_cmd_ratelimit)

    p = sub.add_parser("campaign-runs", help="runs started since the campaign began")
    p.add_argument("consoles", type=Path)
    p.add_argument("campaign")
    p.set_defaults(func=_cmd_campaign_runs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
