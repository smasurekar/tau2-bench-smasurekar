"""Metrics for the Voice Frontend/Backend Agent on tau3 voice (full-duplex) runs.

Joins tau2 run directories with the agent's JSONL event log and reports, per run
(arm x domain x speech complexity):

- tau2's own metrics: Pass^1 and the interaction metrics (L_R, L_Y, R_R, R_Y, I_A,
  S_BC, S_VT, S_ND), computed by tau2's code (read-only use);
- mean per-turn backend LLM latency (exact from the agent's per-role usage when the
  log has it, derived otherwise);
- frontend filler voice latency (paired arm; projected, the filler is only logged);
- average tokens per task, frontend and backend.

Definitions: misc/prototypes/voice-frontend-backend-agent-tau3-integration-plan.md, section 3.
No network and no keys are needed.

Usage:
    uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py \\
      --run paired=data/simulations/fba_voice_paired_airline_regular \\
      --run backend_only=data/simulations/fba_voice_bo_airline_regular \\
      --event-log paired=<agent logs>/fba_voice_events.jsonl \\
      --event-log backend_only=<agent logs>/fba_voice_bo_events.jsonl \\
      --out <output dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

ROLES = ("frontend", "backend")
ROLE_FIELDS = (
    "calls",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "total_tokens",
)
BACKEND_ONLY_ARMS = ("bo", "backend_only", "backend-only")
JOIN_SLACK_S = 30.0  # time-window match slack around a simulation (plan section 3.2)
RUN_WINDOW_SLACK_S = 120.0  # sessions outside the run's span belong to other runs
C3_TOLERANCE = 0.05
FAILURE_KINDS = (
    "frontend_contract_violation",
    "backend_error",
    "tool_result_timeout",
)
OK_TERMINATIONS = ("user_stop", "agent_stop")


# -- inputs --------------------------------------------------------------------------------


@dataclass
class Sim:
    """What the metrics need from one tau2 simulation."""

    sim_id: str
    task_id: str
    trial: int
    start: float  # epoch seconds
    end: float
    reward: float | None
    termination: str
    tool_call_ids: frozenset[str] = frozenset()
    # tau2 agent_usage input + output (from response.done.usage)
    agent_tokens: int | None = None
    sim_seconds: float | None = None  # ticks x tick duration: tau2's simulated time


@dataclass
class Session:
    """One agent WebSocket session and its event-log records, in log order."""

    session_id: str
    model: str
    start: float
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def end(self) -> float:
        """Session end, or the last record's time for a session without ``session_end``."""
        for record in reversed(self.records):
            if record["kind"] == "session_end":
                return float(record["timestamp"])
        return max(float(r["timestamp"]) for r in self.records)

    def of(self, kind: str) -> list[dict[str, Any]]:
        """Records of one kind."""
        return [r for r in self.records if r["kind"] == kind]

    @property
    def call_ids(self) -> set[str]:
        """Tool call ids the client answered (``tool_output_in``)."""
        return {
            str(r["call_id"]) for r in self.of("tool_output_in") if r.get("call_id")
        }


def naive_local_to_epoch(value: str) -> float:
    """tau2 writes naive local-time ISO stamps; the agent writes epoch seconds on the same host."""
    return datetime.fromisoformat(value).timestamp()


def load_sessions(path: Path, model: str) -> dict[str, Session]:
    """Stream the event log and keep the sessions whose ``session_start.model`` is ``model``."""
    sessions: dict[str, Session] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = record.get("session_id")
            if record.get("kind") == "session_start" and record.get("model") == model:
                sessions[session_id] = Session(
                    session_id, model, float(record["timestamp"])
                )
            if session_id in sessions:
                sessions[session_id].records.append(record)
    return sessions


def load_tau2_run(run_dir: Path) -> tuple[Any, list[Sim]]:
    """Load a tau2 run with tau2's own loader; return (Results, sims)."""
    from tau2.data_model.simulation import Results

    results = Results.load(run_dir)
    config = results.info.audio_native_config
    tick_s = config.tick_duration_seconds if config else None
    sims = []
    for sim in results.simulations:
        ids = frozenset(
            call.id
            for tick in sim.ticks or []
            for call in tick.agent_tool_calls or []
            if call.id
        )
        tokens = None
        if sim.agent_usage is not None:
            tokens = sum(
                r.input_tokens + r.output_tokens for r in sim.agent_usage.records
            )
        reason = sim.termination_reason
        sims.append(
            Sim(
                sim_id=sim.id,
                task_id=sim.task_id,
                trial=sim.trial or 0,
                start=naive_local_to_epoch(sim.start_time),
                end=naive_local_to_epoch(sim.end_time),
                reward=sim.reward_info.reward if sim.reward_info else None,
                termination=getattr(reason, "value", str(reason)),
                tool_call_ids=ids,
                agent_tokens=tokens,
                sim_seconds=len(sim.ticks) * tick_s if sim.ticks and tick_s else None,
            )
        )
    return results, sims


# -- join (plan section 3.2) -----------------------------------------------------------------


@dataclass
class Join:
    """Sessions matched to simulations."""

    primary: dict[str, str]  # sim_id -> session_id (the last matching session)
    retried: dict[str, list[str]]  # sim_id -> earlier session ids
    method: dict[str, str]  # session_id -> "call_id" | "time"
    unmatched_sessions: list[str]
    unmatched_sims: list[str]
    other_run_sessions: int  # same model tag, outside this run's time span


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def join_sessions(sims: list[Sim], sessions: dict[str, Session]) -> Join:
    """Match every session to one simulation: tool call ids first, then time windows."""
    if not sims:
        return Join({}, {}, {}, [], [], len(sessions))
    span0 = min(s.start for s in sims) - RUN_WINDOW_SLACK_S
    span1 = max(s.end for s in sims) + RUN_WINDOW_SLACK_S
    in_run = {
        k: v for k, v in sessions.items() if _overlap(v.start, v.end, span0, span1) > 0
    }
    matched: dict[str, list[str]] = defaultdict(list)
    method: dict[str, str] = {}
    unmatched: list[str] = []
    for session_id, session in sorted(in_run.items(), key=lambda kv: kv[1].start):
        ids = session.call_ids
        by_id = [s for s in sims if ids & s.tool_call_ids]
        if len(by_id) == 1:
            matched[by_id[0].sim_id].append(session_id)
            method[session_id] = "call_id"
            continue
        best = max(
            sims,
            key=lambda s: _overlap(
                session.start, session.end, s.start - JOIN_SLACK_S, s.end + JOIN_SLACK_S
            ),
        )
        if (
            _overlap(
                session.start,
                session.end,
                best.start - JOIN_SLACK_S,
                best.end + JOIN_SLACK_S,
            )
            > 0
        ):
            matched[best.sim_id].append(session_id)
            method[session_id] = "time"
        else:
            unmatched.append(session_id)
    primary = {sim_id: ids[-1] for sim_id, ids in matched.items()}
    retried = {sim_id: ids[:-1] for sim_id, ids in matched.items() if len(ids) > 1}
    unmatched_sims = [s.sim_id for s in sims if s.sim_id not in primary]
    return Join(
        primary, retried, method, unmatched, unmatched_sims, len(sessions) - len(in_run)
    )


# -- per turn (plan sections 3.3 and 3.4) ----------------------------------------------------


@dataclass
class Turn:
    """One user turn of one session."""

    session_id: str
    turn_id: int
    decision: str  # direct | delegate | backend_only
    outcome: str  # answer | tool_calls | cancelled | error | ...
    steps: int
    has_i1: bool
    backend_work: bool
    backend_exact_ms: float | None = None
    frontend_llm_ms: float | None = None  # frontend LLM time in the turn (paired arm)
    backend_derived_ms: float | None = None
    endpointing_ms: float | None = None
    filler_text: str = ""
    filler_text_ms: float | None = None
    frontend_ms: float | None = None
    tts_first_audio_ms: float | None = None  # filled per run (estimate)
    fvl_ms: float | None = None
    would_be_heard: bool | None = None
    answer_latency_ms: float | None = None  # wall clock, includes waits on tool results
    tool_wait_ms: float = (
        0.0  # tool calls sent -> next step started (the client's tool work)
    )
    tts_sample_ms: float | None = None  # this turn's own answer TTS first-audio time
    tokens: dict[str, dict[str, int]] = field(default_factory=dict)
    combined_tokens: int = 0

    @property
    def filler_text_latency_ms(self) -> float | None:
        """Endpointing plus filler text time: the exact part of the filler voice latency."""
        if self.endpointing_ms is None or self.filler_text_ms is None:
            return None
        return self.endpointing_ms + self.filler_text_ms

    @property
    def realtime_response_ms(self) -> float | None:
        """Answer latency minus the waits for the client's tool results (agent-side time)."""
        if self.answer_latency_ms is None:
            return None
        return self.answer_latency_ms - self.tool_wait_ms

    @property
    def ttfa_projected_ms(self) -> float | None:
        """When the user would first hear something: the filler if it would be heard, else the answer."""
        if (
            self.decision == "delegate"
            and self.would_be_heard
            and self.fvl_ms is not None
        ):
            return self.fvl_ms
        return self.answer_latency_ms


def _endpointing(
    session: Session, before_index: int, audio_end_ms: int | None = None
) -> float | None:
    """``speech_stopped.audio_ms - audio_end_ms`` for the turn's end of speech."""
    stops = [
        (i, r)
        for i, r in enumerate(session.records)
        if r["kind"] == "speech_stopped" and i < before_index
    ]
    if audio_end_ms is not None:
        exact = [r for _, r in stops if r.get("audio_end_ms") == audio_end_ms]
        if exact:
            record = exact[-1]
            return float(record["audio_ms"] - record["audio_end_ms"])
    if not stops:
        return None
    record = stops[-1][1]
    if record.get("audio_ms") is None or record.get("audio_end_ms") is None:
        return None
    return float(record["audio_ms"] - record["audio_end_ms"])


def session_turns(session: Session, *, backend_only: bool) -> list[Turn]:
    """Every user turn of a session that reached the agent (``agent_turn_start``)."""
    index_of_start: dict[int, int] = {}
    steps: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    fillers: dict[int, dict[str, Any]] = {}
    latencies: dict[int, tuple[int, dict[str, Any]]] = {}
    cancelled: set[int] = set()
    for i, record in enumerate(session.records):
        kind, turn_id = record["kind"], record.get("turn_id")
        if turn_id is None:
            continue
        if kind == "agent_turn_start":
            index_of_start.setdefault(turn_id, i)
        elif kind == "agent_turn_done":
            steps[turn_id].append((i, record))
        elif kind == "filler_timing":
            fillers[turn_id] = record
        elif kind == "turn_latency":
            latencies[turn_id] = (i, record)
        elif kind == "thinking_cancelled":
            cancelled.add(turn_id)

    turns = []
    for turn_id in sorted(set(index_of_start) | set(steps)):
        done = [r for _, r in steps.get(turn_id, [])]
        filler = fillers.get(turn_id)
        has_i1 = bool(done) and all("backend" in r and "frontend" in r for r in done)
        if backend_only:
            decision = "backend_only"
        else:
            decision = (
                "delegate" if filler is not None else "direct" if done else "undecided"
            )
        if turn_id in cancelled:
            outcome = "cancelled"
        elif filler is not None and filler.get("outcome") == "error":
            outcome = "error"
        elif done and done[-1].get("outcome") == "text":
            outcome = "answer"
        elif done:
            outcome = str(done[-1].get("outcome"))
        else:
            outcome = "no_step"
        if has_i1:
            backend_work = sum(r["backend"]["calls"] for r in done) > 0
        else:
            backend_work = bool(done) and decision in ("delegate", "backend_only")
        turn = Turn(
            session_id=session.session_id,
            turn_id=turn_id,
            decision=decision,
            outcome=outcome,
            steps=len(done),
            has_i1=has_i1,
            backend_work=backend_work,
        )
        for r in done:
            turn.combined_tokens += int(r.get("input_tokens") or 0) + int(
                r.get("output_tokens") or 0
            )
            if has_i1:
                for role in ROLES:
                    bucket = turn.tokens.setdefault(role, dict.fromkeys(ROLE_FIELDS, 0))
                    for key in ROLE_FIELDS:
                        bucket[key] += int(r[role].get(key) or 0)

        # backend latency (3.3)
        if backend_work and done:
            if has_i1:
                turn.backend_exact_ms = float(
                    sum(r["backend"]["latency_ms"] for r in done)
                )
            if backend_only:
                turn.backend_derived_ms = float(sum(r["latency_ms"] for r in done))
            elif filler is not None:
                frontend_ms = (filler.get("filler_ready") or {}).get(
                    "frontend_latency_ms"
                )
                if frontend_ms is not None:
                    turn.backend_derived_ms = float(
                        done[0]["latency_ms"]
                        - frontend_ms
                        + sum(r["latency_ms"] for r in done[1:])
                    )

        # frontend LLM latency (paired): exact from I1, else from the filler record
        # (delegated turns) or the single step (direct turns)
        if has_i1 and sum(r["frontend"]["calls"] for r in done):
            turn.frontend_llm_ms = float(sum(r["frontend"]["latency_ms"] for r in done))
        elif not has_i1 and done and not backend_only:
            if filler is not None:
                ready = filler.get("filler_ready") or {}
                turn.frontend_llm_ms = ready.get("frontend_latency_ms")
            else:
                turn.frontend_llm_ms = float(done[0]["latency_ms"])

        # endpointing and answer latency
        start_index = index_of_start.get(
            turn_id, steps[turn_id][0][0] if steps.get(turn_id) else 0
        )
        turn.endpointing_ms = _endpointing(session, start_index)
        latency = latencies.get(turn_id)
        if latency is not None:
            li, lr = latency
            if (
                lr.get("user_stop_to_first_audio_ms") is not None
                and turn.endpointing_ms is not None
            ):
                turn.answer_latency_ms = turn.endpointing_ms + float(
                    lr["user_stop_to_first_audio_ms"]
                )
            prior = [r for i, r in steps.get(turn_id, []) if i < li]
            for before, after in zip(prior, prior[1:]):
                started = float(after["timestamp"]) - float(after["latency_ms"]) / 1000
                turn.tool_wait_ms += (
                    max(0.0, started - float(before["timestamp"])) * 1000
                )
            text_steps = [
                (i, r)
                for i, r in steps.get(turn_id, [])
                if r.get("outcome") == "text" and i < li
            ]
            if text_steps:
                turn.tts_sample_ms = (
                    float(lr["timestamp"]) - float(text_steps[-1][1]["timestamp"])
                ) * 1000.0

        # filler (3.4)
        if filler is not None:
            turn.filler_text = str(filler.get("text") or "")
            ready = filler.get("filler_ready") or {}
            turn.filler_text_ms = ready.get("since_turn_end_ms")
            turn.frontend_ms = ready.get("frontend_latency_ms")
            turn.would_be_heard = filler.get("would_have_spoken")
            turn_end = filler.get("turn_end") or {}
            exact = _endpointing(session, start_index + 1, turn_end.get("audio_ms"))
            if exact is not None:
                turn.endpointing_ms = exact
        turns.append(turn)
    return turns


def filler_tts_samples(session: Session) -> list[float]:
    """Fallback TTS samples: ``first_answer_audio - backend_done`` of filler records answered directly."""
    samples = []
    for record in session.of("filler_timing"):
        done, audio = record.get("backend_done"), record.get("first_answer_audio")
        if record.get("outcome") == "answer" and done and audio:
            t0 = datetime.fromisoformat(done["wall"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(audio["wall"].replace("Z", "+00:00"))
            samples.append((t1 - t0).total_seconds() * 1000.0)
    return samples


def apply_tts_estimate(turns: list[Turn], estimate_ms: float | None) -> None:
    """Fill the projected filler voice latency of every delegated turn with filler text."""
    for turn in turns:
        if turn.decision != "delegate" or not turn.filler_text.strip():
            continue
        turn.tts_first_audio_ms = estimate_ms
        if estimate_ms is not None and turn.filler_text_latency_ms is not None:
            turn.fvl_ms = turn.filler_text_latency_ms + estimate_ms


# -- aggregation -----------------------------------------------------------------------------


def stats(values: Iterable[float | None]) -> dict[str, float | int | None]:
    """n, mean, p50, p90 (nearest-rank) of the non-None values."""
    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return {"n": 0, "mean": None, "p50": None, "p90": None}

    def rank(q: float) -> float:  # nearest rank
        return data[max(0, math.ceil(q * len(data)) - 1)]

    return {
        "n": len(data),
        "mean": statistics.fmean(data),
        "p50": statistics.median(data),
        "p90": rank(0.9),
    }


@dataclass
class Check:
    """One consistency check (plan section 4.2)."""

    code: str
    status: str  # pass | warn | fail
    detail: str


@dataclass
class RunReport:
    """Everything computed for one run."""

    arm: str
    run_dir: str
    run_name: str
    domain: str
    complexity: str
    model: str
    backend_only: bool
    tau2: dict[str, Any]
    interaction: dict[str, Any]
    latency: dict[str, Any]
    filler: dict[str, Any]
    tokens: dict[str, Any]
    diagnostics: dict[str, Any]
    checks: list[Check]
    provenance: dict[str, Any]
    per_task: list[dict[str, Any]]
    per_turn: list[dict[str, Any]]
    join_rows: list[dict[str, Any]]


def analyse(
    *,
    arm: str,
    backend_only: bool,
    sims: list[Sim],
    sessions: dict[str, Session],
) -> tuple[
    dict[str, Any],
    list[Check],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Join, per-turn metrics, per-task tokens and checks (everything that doesn't need tau2)."""
    join = join_sessions(sims, sessions)
    by_sim = {s.sim_id: s for s in sims}
    primary_sessions = [sessions[sid] for sid in join.primary.values()]

    turns_by_session = {
        s.session_id: session_turns(s, backend_only=backend_only)
        for s in primary_sessions
    }
    turns = [t for ts in turns_by_session.values() for t in ts]
    tts_samples = [t.tts_sample_ms for t in turns if t.tts_sample_ms is not None]
    tts_source = "answer_step_to_first_audio"
    if not tts_samples:
        tts_samples = [x for s in primary_sessions for x in filler_tts_samples(s)]
        tts_source = "filler_record_answers"
    tts_estimate = statistics.median(tts_samples) if tts_samples else None
    if tts_estimate is None:
        tts_source = "none"
    apply_tts_estimate(turns, tts_estimate)

    has_i1 = bool(turns) and all(t.has_i1 for t in turns if t.steps)
    completed = [t for t in turns if t.outcome not in ("cancelled", "no_step")]
    backend_turns = [t for t in completed if t.backend_work]
    delegated = [t for t in completed if t.decision == "delegate"]
    with_filler = [t for t in delegated if t.filler_text.strip()]
    heard = [t for t in with_filler if t.would_be_heard]

    exact = stats(t.backend_exact_ms for t in backend_turns)
    derived = stats(t.backend_derived_ms for t in backend_turns)
    headline = exact if has_i1 and exact["n"] else derived
    latency = {
        "backend_turn_latency_ms": headline,
        "backend_turn_latency_source": "exact" if headline is exact else "derived",
        "backend_turn_latency_exact_ms": exact,
        "backend_turn_latency_derived_ms": derived,
        "turns_with_backend_work": len(backend_turns),
        "direct_turns": sum(1 for t in completed if t.decision == "direct"),
        "cancelled_turns": sum(1 for t in turns if t.outcome == "cancelled"),
        "answer_latency_ms": stats(t.answer_latency_ms for t in completed),
        "realtime_response_latency_ms": stats(
            t.realtime_response_ms for t in completed
        ),
        "tool_wait_ms": stats(t.tool_wait_ms for t in completed if t.steps > 1),
        "frontend_turn_latency_ms": stats(t.frontend_llm_ms for t in completed),
        "frontend_turn_latency_source": "exact" if has_i1 else "derived",
        "endpointing_ms": stats(t.endpointing_ms for t in completed),
        "ttfa_projected_ms": stats(t.ttfa_projected_ms for t in completed),
    }
    filler = {
        "applies": not backend_only,
        "filler_voice_latency_ms": stats(t.fvl_ms for t in with_filler),
        "filler_text_latency_ms": stats(t.filler_text_latency_ms for t in with_filler),
        "frontend_latency_ms": stats(t.frontend_ms for t in delegated),
        "tts_first_audio_estimate_ms": tts_estimate,
        "tts_first_audio_source": tts_source,
        "tts_first_audio_samples": len(tts_samples),
        "delegated_turns": len(delegated),
        "filler_present": (len(with_filler) / len(delegated)) if delegated else None,
        "filler_would_be_heard": (
            sum(1 for t in delegated if t.would_be_heard) / len(delegated)
        )
        if delegated
        else None,
        "answer_latency_saved_ms": stats(
            t.answer_latency_ms - t.fvl_ms
            for t in heard
            if t.answer_latency_ms is not None and t.fvl_ms is not None
        ),
    }

    per_task, tokens_fe, tokens_be, tokens_combined = [], [], [], []
    for sim in sims:
        session_id = join.primary.get(sim.sim_id)
        sim_turns = turns_by_session.get(session_id, []) if session_id else []
        fe = dict.fromkeys(ROLE_FIELDS, 0)
        be = dict.fromkeys(ROLE_FIELDS, 0)
        for t in sim_turns:
            for key in ROLE_FIELDS:
                fe[key] += t.tokens.get("frontend", {}).get(key, 0)
                be[key] += t.tokens.get("backend", {}).get(key, 0)
        combined = sum(t.combined_tokens for t in sim_turns)
        if session_id:
            tokens_combined.append(combined)
            if has_i1:
                tokens_fe.append(fe)
                tokens_be.append(be)
        done_turns = [t for t in sim_turns if t.outcome not in ("cancelled", "no_step")]
        per_task.append(
            {
                "task_id": sim.task_id,
                "trial": sim.trial,
                "sim_id": sim.sim_id,
                "reward": sim.reward,
                "termination": sim.termination,
                "session_id": session_id or "",
                "retried_sessions": len(join.retried.get(sim.sim_id, [])),
                "turns": len(done_turns),
                "delegated_turns": sum(
                    1 for t in done_turns if t.decision == "delegate"
                ),
                "fe_calls": fe["calls"] if has_i1 else "",
                "fe_prompt_tokens": fe["prompt_tokens"] if has_i1 else "",
                "fe_completion_tokens": fe["completion_tokens"] if has_i1 else "",
                "fe_total_tokens": fe["total_tokens"] if has_i1 else "",
                "be_calls": be["calls"] if has_i1 else "",
                "be_prompt_tokens": be["prompt_tokens"] if has_i1 else "",
                "be_completion_tokens": be["completion_tokens"] if has_i1 else "",
                "be_total_tokens": be["total_tokens"] if has_i1 else "",
                "combined_tokens": combined if session_id else "",
                "tau2_agent_tokens": sim.agent_tokens
                if sim.agent_tokens is not None
                else "",
                "mean_backend_latency_ms": stats(
                    (t.backend_exact_ms if has_i1 else t.backend_derived_ms)
                    for t in done_turns
                    if t.backend_work
                )["mean"],
                "mean_fvl_ms": stats(t.fvl_ms for t in done_turns)["mean"],
            }
        )

    def role_means(rows: list[dict[str, int]]) -> dict[str, float | None]:
        return {
            k: (statistics.fmean(r[k] for r in rows) if rows else None)
            for k in ROLE_FIELDS
        }

    tokens = {
        "per_role_available": has_i1,
        "sims": len(tokens_combined),
        "frontend_per_task": role_means(tokens_fe),
        "backend_per_task": role_means(tokens_be),
        "combined_per_task": statistics.fmean(tokens_combined)
        if tokens_combined
        else None,
        "cancelled_steps": latency["cancelled_turns"],
    }
    if backend_only and not has_i1:
        tokens["note"] = (
            "no per-role fields; in the backend-only arm the combined total is backend usage"
        )

    # diagnostics and checks
    failure_counts = Counter()
    for session in primary_sessions:
        for record in session.records:
            kind = record["kind"]
            if kind in FAILURE_KINDS:
                failure_counts[kind] += 1
            elif kind == "response_done" and record.get("status") == "failed":
                failure_counts["response_done_failed"] += 1
            elif kind == "filler_timing" and record.get("outcome") == "error":
                failure_counts["filler_outcome_error"] += 1
    filler_records = [r for s in primary_sessions for r in s.of("filler_timing")]
    diagnostics = {
        "sessions_in_log_for_model": len(sessions),
        "sessions_matched": len(join.method),
        "retried_sessions": sum(len(v) for v in join.retried.values()),
        "sessions_from_other_runs": join.other_run_sessions,
        "join_methods": dict(Counter(join.method.values())),
        "agent_failures": dict(failure_counts),
        "turns": len(turns),
        "turn_outcomes": dict(Counter(t.outcome for t in turns)),
        "sim_time_to_wall_time": (
            sum(s.sim_seconds for s in sims if s.sim_seconds)
            / sum(s.end - s.start for s in sims if s.sim_seconds)
        )
        if any(s.sim_seconds for s in sims)
        else None,
    }

    checks: list[Check] = []
    ok_join = not join.unmatched_sessions and not join.unmatched_sims
    checks.append(
        Check(
            "C1",
            "pass" if ok_join else "fail",
            "join is 1:1"
            if ok_join
            else f"unmatched sessions {join.unmatched_sessions}, unmatched simulations {join.unmatched_sims}",
        )
    )
    checks.append(
        Check(
            "C2",
            "pass" if has_i1 else "warn",
            "per-role usage (I1) present"
            if has_i1
            else "I1 fields missing: combined tokens only, derived backend latency",
        )
    )
    if exact["p50"] is not None and derived["p50"] is not None and exact["p50"] > 0:
        diff = abs(derived["p50"] - exact["p50"]) / exact["p50"]
        checks.append(
            Check(
                "C3",
                "pass" if diff <= C3_TOLERANCE else "fail",
                f"median exact {exact['p50']:.0f} ms vs derived {derived['p50']:.0f} ms ({diff:.1%})",
            )
        )
    else:
        checks.append(
            Check("C3", "pass", "not applicable (no exact or no derived values)")
        )
    mismatches, undercounts = [], []
    interrupted = {
        s.session_id
        for s in primary_sessions
        if s.of("barge_in") or s.of("thinking_cancelled")
    }
    for row in per_task:
        if row["session_id"] == "" or row["tau2_agent_tokens"] == "":
            continue
        ours = (
            (row["fe_total_tokens"] + row["be_total_tokens"])
            if has_i1
            else row["combined_tokens"]
        )
        if ours == row["tau2_agent_tokens"]:
            continue
        text = f"{row['task_id']}: agent {ours} vs tau2 {row['tau2_agent_tokens']}"
        if ours > row["tau2_agent_tokens"] and row["session_id"] in interrupted:
            undercounts.append(text)  # tau2 drops usage of responses cut by barge-in
        else:
            mismatches.append(text)
    checks.append(
        Check(
            "C4",
            "fail" if mismatches else "warn" if undercounts else "pass",
            "; ".join(mismatches)
            if mismatches
            else (
                "agent tokens exceed tau2 agent_usage in sessions with barge-ins "
                "(tau2 drops the usage of responses the user cut off; the agent "
                "count is used): " + "; ".join(undercounts)
            )
            if undercounts
            else "agent tokens equal tau2 agent_usage",
        )
    )
    checks.append(
        Check(
            "C5",
            "pass" if not failure_counts else "warn",
            "no agent failures in the event log"
            if not failure_counts
            else f"agent failures {dict(failure_counts)} (also check the archived docker logs)",
        )
    )
    modes = Counter(r.get("mode") for r in filler_records)
    bad_modes = {m: n for m, n in modes.items() if m != "log_only"}
    checks.append(
        Check(
            "C6",
            "pass" if not bad_modes else "fail",
            f"filler modes {dict(modes) or 'none'}",
        )
    )
    if backend_only:
        c7_ok, c7_detail = (
            not filler_records,
            f"{len(filler_records)} filler records (expected 0)",
        )
    else:
        c7_ok, c7_detail = (
            bool(filler_records) or not delegated,
            f"{len(filler_records)} filler records",
        )
    checks.append(Check("C7", "pass" if c7_ok else "fail", c7_detail))
    infra = [f"{s.task_id}" for s in sims if s.termination == "infrastructure_error"]
    other = [
        f"{s.task_id}:{s.termination}"
        for s in sims
        if s.termination not in OK_TERMINATIONS + ("infrastructure_error",)
    ]
    checks.append(
        Check(
            "C8",
            "fail" if infra else "warn" if other else "pass",
            (f"infrastructure errors: {', '.join(infra)}. " if infra else "")
            + (
                f"agent-caused endings (scored, reward 0): {', '.join(other)}"
                if other
                else ""
            )
            or "all simulations ended with user_stop/agent_stop",
        )
    )

    per_turn = []
    sim_of_session = {sid: sim_id for sim_id, sid in join.primary.items()}
    for t in turns:
        sim = by_sim.get(sim_of_session.get(t.session_id, ""))
        per_turn.append(
            {
                "task_id": sim.task_id if sim else "",
                "session_id": t.session_id,
                "turn_id": t.turn_id,
                "decision": t.decision,
                "outcome": t.outcome,
                "steps": t.steps,
                "endpointing_ms": t.endpointing_ms,
                "filler_text_ms": t.filler_text_ms,
                "frontend_ms": t.frontend_ms,
                "tts_first_audio_ms": t.tts_first_audio_ms,
                "fvl_ms": t.fvl_ms,
                "backend_exact_ms": t.backend_exact_ms,
                "backend_derived_ms": t.backend_derived_ms,
                "answer_latency_ms": t.answer_latency_ms,
                "tool_wait_ms": t.tool_wait_ms,
                "realtime_response_ms": t.realtime_response_ms,
                "would_be_heard": t.would_be_heard,
                "fe_total_tokens": t.tokens.get("frontend", {}).get("total_tokens", ""),
                "be_total_tokens": t.tokens.get("backend", {}).get("total_tokens", ""),
                "combined_tokens": t.combined_tokens,
            }
        )
    join_rows = []
    for sim in sims:
        for session_id in join.retried.get(sim.sim_id, []) + (
            [join.primary[sim.sim_id]] if sim.sim_id in join.primary else []
        ):
            join_rows.append(
                {
                    "session_id": session_id,
                    "task_id": sim.task_id,
                    "trial": sim.trial,
                    "sim_id": sim.sim_id,
                    "method": join.method.get(session_id, ""),
                    "role": "primary"
                    if join.primary.get(sim.sim_id) == session_id
                    else "retried",
                }
            )
    for session_id in join.unmatched_sessions:
        join_rows.append({"session_id": session_id, "role": "unmatched"})

    computed = {
        "latency": latency,
        "filler": filler,
        "tokens": tokens,
        "diagnostics": diagnostics,
    }
    return computed, checks, per_task, per_turn, join_rows


SETUP_FIELDS = (
    "backend_llm",
    "backend_reasoning",
    "frontend_llm",
    "frontend_reasoning",
    "asr",
    "tts",
    "user_llm",
    "judge_llm",
    "tau_tts",
)


def _console_tts(run_dir: Path) -> str | None:
    """The user TTS model from the run's console log (``USER TTS OVERRIDE`` line of I0)."""
    console = run_dir.parent / "_consoles" / f"{run_dir.name}.log"
    if not console.exists():
        return None
    for line in console.open(encoding="utf-8", errors="replace"):
        if "USER TTS OVERRIDE:" in line:
            return line.split("USER TTS OVERRIDE:", 1)[1].split(" at ", 1)[0].strip()
    return None


def build_setup(
    run_dir: Path, results: Any, backend_only: bool, given: dict[str, Any]
) -> dict[str, Any]:
    """The configuration columns of the results table.

    Agent-side values (LLMs, reasoning, ASR, TTS) come from ``--setup`` because tau2
    doesn't record them. The user LLM comes from the run, the judge from
    ``TAU2_JUDGE_MODEL``, and the user TTS from the run's console log; ``--setup``
    overrides any of them.
    """
    info = results.info
    setup = {
        "user_llm": info.user_info.llm if info.user_info else None,
        "judge_llm": os.getenv("TAU2_JUDGE_MODEL"),
        "tau_tts": _console_tts(run_dir),
    }
    if backend_only:
        setup |= {"frontend_llm": "none (backend-only)", "frontend_reasoning": "n/a"}
    setup |= {k: v for k, v in given.items() if v not in (None, "")}
    if backend_only:
        setup["frontend_llm"], setup["frontend_reasoning"] = (
            "none (backend-only)",
            "n/a",
        )
    return {k: setup.get(k) for k in SETUP_FIELDS}


def build_run_report(
    arm: str,
    run_dir: Path,
    event_log: Path,
    model_override: str | None,
    setup: dict[str, Any] | None = None,
) -> RunReport:
    """Load one tau2 run and its arm's event log, and compute everything."""
    from tau2.metrics.agent_metrics import compute_metrics
    from tau2.scripts.leaderboard.compute_interaction_metrics import (
        compute_interaction_metrics_block,
    )

    results, sims = load_tau2_run(run_dir)
    info = results.info
    model = model_override or (
        info.audio_native_config.model if info.audio_native_config else None
    )
    if not model:
        raise SystemExit(f"{run_dir}: no audio_native_config.model; pass --model")
    backend_only = arm.lower() in BACKEND_ONLY_ARMS
    sessions = load_sessions(event_log, model)
    computed, checks, per_task, per_turn, join_rows = analyse(
        arm=arm, backend_only=backend_only, sims=sims, sessions=sessions
    )
    metrics = compute_metrics(results)
    tau2 = {
        "sims": len(sims),
        "pass_1": metrics.pass_hat_ks.get(1),
        "avg_reward": metrics.avg_reward,
        "infra_errors": metrics.infra_error_count,
        "terminations": dict(Counter(s.termination for s in sims)),
    }
    try:
        interaction = compute_interaction_metrics_block([run_dir])
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        interaction = {"error": f"{type(exc).__name__}: {exc}"}
    complexity = getattr(info, "speech_complexity", None)
    provenance = {
        "setup": build_setup(run_dir, results, backend_only, setup or {}),
        "tau2_git_commit": info.git_commit,
        "agent_model_tag": model,
        "agent_llm": info.agent_info.llm if info.agent_info else None,
        "user_llm": info.user_info.llm if info.user_info else None,
        "speech_complexity": complexity,
        "seed": info.seed,
        "event_log": str(event_log),
        "first_sim_start": datetime.fromtimestamp(
            min(s.start for s in sims)
        ).isoformat()
        if sims
        else None,
        "last_sim_end": datetime.fromtimestamp(max(s.end for s in sims)).isoformat()
        if sims
        else None,
    }
    return RunReport(
        arm=arm,
        run_dir=str(run_dir),
        run_name=run_dir.name,
        domain=info.environment_info.domain_name,
        complexity=str(complexity),
        model=model,
        backend_only=backend_only,
        tau2=tau2,
        interaction=interaction,
        checks=checks,
        provenance=provenance,
        per_task=per_task,
        per_turn=per_turn,
        join_rows=join_rows,
        **computed,
    )


# -- output ------------------------------------------------------------------------------------


def _s(value: float | None, scale: float = 1000.0, digits: int = 2) -> str:
    return "–" if value is None else f"{value / scale:.{digits}f}"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.0f}%"


def _im(report: RunReport) -> dict[str, Any]:
    return (report.interaction.get("domains") or {}).get(report.domain, {})


def render_markdown(reports: list[RunReport]) -> str:
    """The human-readable report."""
    lines = [
        "# Voice Frontend/Backend Agent on tau3 voice: metrics",
        "",
        f"Generated {datetime.now().isoformat(timespec='seconds')} by `fba_voice_metrics.py`. "
        "Latencies in seconds. Filler voice latency and TTFA are **projected** (the filler is only "
        "logged; the TTS term is estimated). User TTS: `openai/openai/gpt-4o-mini-tts` via the Inference "
        "Hub (I0): not leaderboard-comparable; S_VT is approximate.",
        "",
        "## Results table",
        "",
        "Column definitions: runbook section 7.3. *Mean realtime response latency*: end of the "
        "user's speech to the agent's first answer audio, minus the time spent waiting for "
        "tau2's tool results, per answered turn (agent log). "
        "*Frontend Mean Per-turn Latency*: frontend LLM time per turn in which the frontend ran.",
        "",
        render_results_table(reports),
        "## Headline",
        "",
        "| Arm | Domain | Complexity | Sims | Pass^1 | L_R | L_Y | R_R | R_Y | I_A | S_BC | S_VT | S_ND "
        "| Backend turn latency mean (p90) | Filler voice latency mean (p90) | Filler would be heard "
        "| TTFA projected mean | FE tokens/task | BE tokens/task |",
        "|" + "---|" * 19,
    ]
    for r in reports:
        im = _im(r)
        be = r.latency["backend_turn_latency_ms"]
        fvl = r.filler["filler_voice_latency_ms"]
        fe_tok = r.tokens["frontend_per_task"]["total_tokens"]
        be_tok = r.tokens["backend_per_task"]["total_tokens"]
        fvl_s = "n/a" if r.backend_only else f"{_s(fvl['mean'])} ({_s(fvl['p90'])})"
        if not r.tokens["per_role_available"]:
            fe_tok_s = "n/a (no I1)"
            be_tok_s = (
                f"{r.tokens['combined_per_task']:.0f} (combined)"
                if r.tokens["combined_per_task"] is not None
                else "–"
            )
        else:
            fe_tok_s = "–" if fe_tok is None else f"{fe_tok:.0f}"
            be_tok_s = "–" if be_tok is None else f"{be_tok:.0f}"
        lines.append(
            f"| {r.arm} | {r.domain} | {r.complexity} | {r.tau2['sims']} | {_pct(r.tau2['pass_1'])} "
            f"| {_s(im.get('response_latency_mean'), 1)} | {_s(im.get('yield_latency_mean'), 1)} "
            f"| {_pct(im.get('response_rate'))} | {_pct(im.get('yield_rate'))} "
            f"| {_pct(im.get('agent_interruption_rate'))} | {_pct(im.get('selectivity_backchannel'))} "
            f"| {_pct(im.get('selectivity_vocal_tic'))} | {_pct(im.get('selectivity_non_directed'))} "
            f"| {_s(be['mean'])} ({_s(be['p90'])}) {r.latency['backend_turn_latency_source']} "
            f"| {fvl_s} "
            f"| {'n/a' if r.backend_only else _pct(r.filler['filler_would_be_heard'])} "
            f"| {_s(r.latency['ttfa_projected_ms']['mean'])} | {fe_tok_s} | {be_tok_s} |"
        )
    for r in reports:
        lines += ["", f"## {r.arm} · {r.domain} · {r.complexity} (`{r.run_name}`)", ""]
        lines += [
            "### Latency detail (s)",
            "",
            "| Measure | n | mean | p50 | p90 |",
            "|---|---|---|---|---|",
        ]
        rows = [
            (
                "Backend turn latency, exact (I1)",
                r.latency["backend_turn_latency_exact_ms"],
            ),
            (
                "Backend turn latency, derived",
                r.latency["backend_turn_latency_derived_ms"],
            ),
            (
                "Realtime response latency (answer latency minus tool waits)",
                r.latency["realtime_response_latency_ms"],
            ),
            (
                "Answer latency, wall clock (end of speech to first answer audio)",
                r.latency["answer_latency_ms"],
            ),
            (
                "Wait for tau2 tool results per multi-step turn",
                r.latency["tool_wait_ms"],
            ),
            ("Endpointing (end of speech to VAD commit)", r.latency["endpointing_ms"]),
            ("Time to first audio, projected", r.latency["ttfa_projected_ms"]),
        ]
        if not r.backend_only:
            rows += [
                (
                    "Filler voice latency, projected",
                    r.filler["filler_voice_latency_ms"],
                ),
                (
                    "Filler text latency (exact part)",
                    r.filler["filler_text_latency_ms"],
                ),
                (
                    f"Frontend LLM per turn ({r.latency['frontend_turn_latency_source']})",
                    r.latency["frontend_turn_latency_ms"],
                ),
                ("Frontend LLM (to call_backend)", r.filler["frontend_latency_ms"]),
                (
                    "Answer latency saved by the filler",
                    r.filler["answer_latency_saved_ms"],
                ),
            ]
        for name, st in rows:
            lines.append(
                f"| {name} | {st['n']} | {_s(st['mean'])} | {_s(st['p50'])} | {_s(st['p90'])} |"
            )
        lines += [
            "",
            f"Turns with backend work: {r.latency['turns_with_backend_work']} · direct (frontend-only) turns: "
            f"{r.latency['direct_turns']} · cancelled by barge-in: {r.latency['cancelled_turns']}.",
        ]
        if not r.backend_only:
            est = r.filler["tts_first_audio_estimate_ms"]
            lines.append(
                f"TTS first-audio estimate: {_s(est, 1, 0)} ms (median of {r.filler['tts_first_audio_samples']} "
                f"samples, source `{r.filler['tts_first_audio_source']}`) · delegated turns: "
                f"{r.filler['delegated_turns']} · filler present: {_pct(r.filler['filler_present'])} · "
                f"would be heard: {_pct(r.filler['filler_would_be_heard'])}."
            )
        tk = r.tokens
        lines += [
            "",
            "### Tokens per task (mean)",
            "",
            "| Role | calls | prompt | completion | cached | total |",
        ]
        lines.append("|---|---|---|---|---|---|")
        for role in ROLES:
            row = tk[f"{role}_per_task"]
            lines.append(
                f"| {role} | "
                + " | ".join(
                    "–" if row[k] is None else f"{row[k]:.1f}" for k in ROLE_FIELDS
                )
                + " |"
            )
        combined = tk["combined_per_task"]
        lines.append(
            f"| combined | | | | | {'–' if combined is None else f'{combined:.1f}'} |"
        )
        if tk.get("note"):
            lines.append(f"\n{tk['note']}.")
        lines += [
            "",
            f"Reasoning tokens are part of completion. Steps cancelled by barge-in are not metered "
            f"({tk['cancelled_steps']} cancelled turns).",
            "",
            "### Diagnostics",
            "",
            f"```json\n{json.dumps({'tau2': r.tau2, **r.diagnostics}, indent=2)}\n```",
            "",
            "### Checks",
            "",
            "| Check | Status | Detail |",
            "|---|---|---|",
        ]
        for c in r.checks:
            lines.append(f"| {c.code} | {c.status.upper()} | {c.detail} |")
        lines += [
            "",
            "### Provenance",
            "",
            f"```json\n{json.dumps(r.provenance, indent=2)}\n```",
        ]
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], extra: dict[str, str]) -> None:
    rows = [{**extra, **row} for row in rows]
    keys: list[str] = []
    for row in rows:
        keys += [k for k in row if k not in keys]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys or list(extra))
        writer.writeheader()
        writer.writerows(rows)


RESULTS_COLUMNS = (
    ("Arm", "arm"),
    ("Domain", "domain"),
    ("Speech complexity", "complexity"),
    ("Tasks", "sims"),
    ("Backend LLM", "backend_llm"),
    ("Backend Reasoning", "backend_reasoning"),
    ("Frontend LLM", "frontend_llm"),
    ("Frontend Reasoning", "frontend_reasoning"),
    ("Voice Agent ASR", "asr"),
    ("Voice Agent TTS", "tts"),
    ("User simulator LLM", "user_llm"),
    ("Judge LLM", "judge_llm"),
    ("Tau TTS", "tau_tts"),
    ("Pass^1", "pass_1"),
    ("Responsiveness", "responsiveness"),
    ("Latency", "latency"),
    ("Interrupts", "interrupts"),
    ("Selectivity", "selectivity"),
    ("Mean realtime response latency (s)", "realtime_response_latency_s"),
    ("Frontend Mean Per-turn Latency (s)", "frontend_turn_latency_s"),
    ("Backend Mean Per-turn LLM Latency (s)", "backend_turn_latency_s"),
    ("Frontend Filler Voice Latency (s, projected)", "filler_voice_latency_s"),
    ("FE tokens/task", "fe_tokens_per_task"),
    ("BE tokens/task", "be_tokens_per_task"),
)


def results_row(r: RunReport) -> dict[str, Any]:
    """One row of the results table (runbook section 7.3)."""
    im = _im(r)
    setup = r.provenance.get("setup") or {}

    def sec(st: dict[str, Any]) -> str:
        return _s(st.get("mean"))

    fe_tok = r.tokens["frontend_per_task"]["total_tokens"]
    be_tok = r.tokens["backend_per_task"]["total_tokens"]
    if not r.tokens["per_role_available"]:
        fe_tok, be_tok = None, r.tokens["combined_per_task"]
    return {
        "arm": r.arm,
        "domain": r.domain,
        "complexity": r.complexity,
        "sims": r.tau2["sims"],
        **{k: setup.get(k) or "unrecorded" for k in SETUP_FIELDS},
        "pass_1": "–" if r.tau2["pass_1"] is None else f"{r.tau2['pass_1']:.3f}",
        "responsiveness": f"R_R {_pct(im.get('response_rate'))} · "
        f"R_Y {_pct(im.get('yield_rate'))}",
        "latency": f"L_R {_s(im.get('response_latency_mean'), 1)} s · "
        f"L_Y {_s(im.get('yield_latency_mean'), 1)} s",
        "interrupts": f"I_A {_pct(im.get('agent_interruption_rate'))}",
        "selectivity": f"S_BC {_pct(im.get('selectivity_backchannel'))} · "
        f"S_VT {_pct(im.get('selectivity_vocal_tic'))} · "
        f"S_ND {_pct(im.get('selectivity_non_directed'))}",
        "realtime_response_latency_s": sec(r.latency["realtime_response_latency_ms"]),
        "frontend_turn_latency_s": "n/a"
        if r.backend_only
        else sec(r.latency["frontend_turn_latency_ms"]),
        "backend_turn_latency_s": sec(r.latency["backend_turn_latency_ms"]),
        "filler_voice_latency_s": "n/a"
        if r.backend_only
        else sec(r.filler["filler_voice_latency_ms"]),
        "fe_tokens_per_task": "–" if fe_tok is None else f"{fe_tok:.0f}",
        "be_tokens_per_task": "–"
        if be_tok is None
        else f"{be_tok:.0f}"
        + ("" if r.tokens["per_role_available"] else " (combined)"),
    }


def render_results_table(reports: list[RunReport]) -> str:
    """The results table in the requested column order."""
    lines = [
        "| " + " | ".join(title for title, _ in RESULTS_COLUMNS) + " |",
        "|" + "---|" * len(RESULTS_COLUMNS),
    ]
    for r in reports:
        row = results_row(r)
        lines.append(
            "| " + " | ".join(str(row[key]) for _, key in RESULTS_COLUMNS) + " |"
        )
    return "\n".join(lines) + "\n"


def write_outputs(reports: list[RunReport], out: Path) -> None:
    """Write the report, JSON and CSVs to ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "fba_voice_results_table.md").write_text(
        render_results_table(reports), encoding="utf-8"
    )
    _write_csv(
        out / "fba_voice_results_table.csv",
        [
            {title: results_row(r)[key] for title, key in RESULTS_COLUMNS}
            for r in reports
        ],
        {},
    )
    (out / "fba_voice_report.md").write_text(render_markdown(reports), encoding="utf-8")
    payload = []
    for r in reports:
        item = asdict(r)
        for key in ("per_task", "per_turn", "join_rows"):
            item.pop(key)
        payload.append(item)
    (out / "fba_voice_metrics.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (out / "interaction_metrics.json").write_text(
        json.dumps({r.run_name: r.interaction for r in reports}, indent=2, default=str),
        encoding="utf-8",
    )
    for name, key in (
        ("fba_voice_per_task.csv", "per_task"),
        ("fba_voice_per_turn.csv", "per_turn"),
        ("join.csv", "join_rows"),
    ):
        rows = []
        for r in reports:
            rows += [
                {"arm": r.arm, "run": r.run_name, **row} for row in getattr(r, key)
            ]
        _write_csv(out / name, rows, {})


def _pairs(values: list[str], flag: str) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"{flag} expects KEY=VALUE, got {value!r}")
        key, _, rest = value.partition("=")
        pairs.append((key.strip(), rest.strip()))
    return pairs


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; exits 1 when any check fails."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--run", action="append", required=True, help="ARM=RUN_DIR (repeatable)"
    )
    parser.add_argument(
        "--event-log",
        action="append",
        required=True,
        help="ARM=EVENT_LOG.jsonl (one per arm)",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="ARM_OR_RUN_NAME=MODEL_TAG (override)",
    )
    parser.add_argument(
        "--setup",
        action="append",
        default=[],
        help="ARM=SETUP.json: the agent-side configuration columns of the results "
        f"table ({', '.join(SETUP_FIELDS)})",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    load_dotenv()  # TAU2_JUDGE_MODEL for the results table, as tau2 reads it
    setups = {
        arm: json.loads(Path(path).read_text(encoding="utf-8"))
        for arm, path in _pairs(args.setup, "--setup")
    }

    logs = dict(_pairs(args.event_log, "--event-log"))
    models = dict(_pairs(args.model, "--model"))
    reports = []
    for arm, run in _pairs(args.run, "--run"):
        run_dir = Path(run)
        if arm not in logs:
            raise SystemExit(f"no --event-log for arm {arm!r}")
        model = models.get(run_dir.name) or models.get(arm)
        reports.append(
            build_run_report(arm, run_dir, Path(logs[arm]), model, setups.get(arm))
        )
    write_outputs(reports, args.out)
    failed = [(r.run_name, c) for r in reports for c in r.checks if c.status != "pass"]
    for run_name, c in failed:
        print(f"{c.status.upper()} {c.code} [{run_name}]: {c.detail}", file=sys.stderr)
    print(f"wrote {args.out / 'fba_voice_report.md'}")
    return 1 if any(c.status == "fail" for _, c in failed) else 0


if __name__ == "__main__":
    sys.exit(main())
