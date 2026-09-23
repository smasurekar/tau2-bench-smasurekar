"""Offline metrics over tau2 results.json files.

Pure post-processing: reads what the adapter wrote into AssistantMessage fields tau2
already persists, and calls tau2's own ``compute_metrics`` for pass^k so those numbers
are identical to what the tau2 CLI prints. Also accepts plain ``llm_agent`` runs (the
optional baseline arm), treating every agent LLM call as "backend".

Definitions: ../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md
section 8.
"""

import csv
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from tau2.data_model.message import AssistantMessage, UserMessage
from tau2.data_model.simulation import Results, SimulationRun, TerminationReason
from tau2.metrics.agent_metrics import compute_metrics
from tau2_fba.agent import AGENT_MODES, DECISION_DELEGATE, RAW_KEY

ROLES = ("frontend", "backend")
TOKEN_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cached_tokens",
)
NATIVE = "native"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def stats(values: Iterable[Optional[float]]) -> Optional[dict]:
    """n / mean / p50 / p90 / p95 / max, or None when there is nothing to summarize."""
    xs = sorted(float(v) for v in values if v is not None)
    if not xs:
        return None

    def pct(p: float) -> float:
        # Linear interpolation between closest ranks (numpy's default).
        pos = (len(xs) - 1) * p
        lo, hi = math.floor(pos), math.ceil(pos)
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    return {
        "n": len(xs),
        "mean": sum(xs) / len(xs),
        "p50": pct(0.50),
        "p90": pct(0.90),
        "p95": pct(0.95),
        "max": xs[-1],
    }


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _fba(msg: Any) -> Optional[dict]:
    if isinstance(msg, AssistantMessage) and isinstance(msg.raw_data, dict):
        data = msg.raw_data.get(RAW_KEY)
        return data if isinstance(data, dict) else None
    return None


def _ts(msg: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(msg.timestamp).timestamp()
    except (TypeError, ValueError, AttributeError):
        return None


def is_infra(sim: SimulationRun) -> bool:
    return sim.termination_reason == TerminationReason.INFRASTRUCTURE_ERROR


def arm_of(results: Results) -> str:
    return results.info.agent_info.implementation


def is_fba(results: Results) -> bool:
    return arm_of(results) in AGENT_MODES


# ---------------------------------------------------------------------------
# per-simulation extraction
# ---------------------------------------------------------------------------


def turn_rows(sim: SimulationRun) -> list[dict]:
    """One row per completed user turn.

    FBA runs: the ``raw_data["fba"]["turn"]`` summaries the adapter wrote.
    Native runs: rebuilt from message boundaries -- every agent LLM call counts as
    backend, and first-response latency is the wall time from the user message to
    the agent's text reply (there is no filler).
    """
    rows = []
    fba_seen = False
    for msg in sim.messages or []:
        data = _fba(msg)
        if data is None:
            continue
        fba_seen = True
        if "turn" in data:
            rows.append({"task_id": sim.task_id, "sim_id": sim.id, **data["turn"]})
    if fba_seen:
        return rows

    open_turn: Optional[dict] = None
    for msg in sim.messages or []:
        if isinstance(msg, UserMessage) and not msg.is_tool_call():
            open_turn = {"t_user": _ts(msg), "latency": 0.0, "calls": 0}
        elif isinstance(msg, AssistantMessage) and open_turn is not None:
            open_turn["latency"] += float(msg.generation_time_seconds or 0.0)
            open_turn["calls"] += 1
            if not msg.is_tool_call():
                t_end, t_user = _ts(msg), open_turn["t_user"]
                wall = (
                    t_end - t_user if t_end is not None and t_user is not None else None
                )
                rows.append(
                    {
                        "task_id": sim.task_id,
                        "sim_id": sim.id,
                        "decision": NATIVE,
                        "filler_text": "",
                        "filler_latency_s": None,
                        "frontend_latency_s": 0.0,
                        "backend_latency_s": open_turn["latency"],
                        "frontend_calls": 0,
                        "backend_calls": open_turn["calls"],
                        "first_response_latency_s": wall
                        if wall is not None
                        else open_turn["latency"],
                        "wall_s": wall,
                        "events": {},
                    }
                )
                open_turn = None
    return rows


def backend_call_latencies(sim: SimulationRun) -> list[float]:
    """Latency of every successful backend LLM call in the simulation."""
    out = []
    for msg in sim.messages or []:
        data = _fba(msg)
        if data is not None:
            out.extend(
                c["latency_s"]
                for c in data["step"].get("per_call", [])
                if c.get("role") == "backend" and not c.get("error")
            )
        elif isinstance(msg, AssistantMessage) and msg.generation_time_seconds:
            out.append(float(msg.generation_time_seconds))
    return out


def frontend_completion_tokens(sim: SimulationRun) -> list[int]:
    """Completion tokens of every frontend call (filler waits on all of them)."""
    out = []
    for msg in sim.messages or []:
        data = _fba(msg)
        if data is not None:
            out.extend(
                c["completion_tokens"]
                for c in data["step"].get("per_call", [])
                if c.get("role") == "frontend" and not c.get("error")
            )
    return out


def sim_tokens(sim: SimulationRun) -> dict[str, dict[str, int]]:
    """Per-role token totals for one simulation (agent side only)."""
    totals = {role: dict.fromkeys(TOKEN_KEYS, 0) for role in ROLES}
    for msg in sim.messages or []:
        data = _fba(msg)
        if data is not None:
            for role in ROLES:
                step = data["step"].get(role) or {}
                for k in TOKEN_KEYS:
                    totals[role][k] += int(step.get(k) or 0)
        elif isinstance(msg, AssistantMessage) and msg.usage:
            # Native (llm_agent) message: tau2 keeps the provider response in raw_data.
            be = totals["backend"]
            p = int(msg.usage.get("prompt_tokens") or 0)
            c = int(msg.usage.get("completion_tokens") or 0)
            raw_usage = (
                (msg.raw_data or {}).get("usage")
                if isinstance(msg.raw_data, dict)
                else None
            )
            raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
            be["prompt_tokens"] += p
            be["completion_tokens"] += c
            be["total_tokens"] += p + c
            be["reasoning_tokens"] += int(
                (raw_usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens"
                )
                or 0
            )
            be["cached_tokens"] += int(
                (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            )
    return totals


def _reward(sim: SimulationRun) -> Optional[float]:
    return sim.reward_info.reward if sim.reward_info is not None else None


# ---------------------------------------------------------------------------
# per-run summary
# ---------------------------------------------------------------------------


def summarize(results: Results, source: str = "") -> dict:
    """Every number in the report for one results.json."""
    info = results.info
    sims = results.simulations or []
    ok = [s for s in sims if not is_infra(s)]
    fba = is_fba(results)

    pass_hat_k: dict[int, float] = {}
    avg_reward = None
    if ok:
        metrics = compute_metrics(results)
        pass_hat_k = {int(k): float(v) for k, v in metrics.pass_hat_ks.items()}
        avg_reward = float(metrics.avg_reward)

    turns = [row for s in ok for row in turn_rows(s)]
    backend_turns = [t for t in turns if (t.get("backend_calls") or 0) > 0]
    delegated = [t for t in turns if t.get("decision") == DECISION_DELEGATE]
    with_filler = [t for t in delegated if t.get("filler_text")]

    token_sums = [sim_tokens(s) for s in ok]
    tokens_per_task = {
        role: {k: _mean([t[role][k] for t in token_sums]) for k in TOKEN_KEYS}
        for role in ROLES
    }

    events: Counter = Counter()
    for t in turns:
        events.update(t.get("events") or {})

    llm_args = info.agent_info.llm_args or {}
    return {
        "source": source,
        "arm": arm_of(results),
        "fba": fba,
        "domain": info.environment_info.domain_name,
        "agent_llm": info.agent_info.llm,
        "user_llm": info.user_info.llm,
        "num_trials": info.num_trials,
        "simulations": len(ok),
        "tasks": len({s.task_id for s in ok}),
        "infra_errors": len(sims) - len(ok),
        "avg_reward": avg_reward,
        "pass_hat_k": pass_hat_k,
        # 8.3 -- mean per-turn LLM latency = backend latency per user turn
        "backend_turn_latency_s": stats(t["backend_latency_s"] for t in backend_turns),
        "backend_call_latency_s": stats(
            x for s in ok for x in backend_call_latencies(s)
        ),
        "backend_calls_per_turn": _mean([t["backend_calls"] for t in backend_turns]),
        "e2e_llm_latency_per_turn_s": stats(
            (t.get("frontend_latency_s") or 0.0) + (t.get("backend_latency_s") or 0.0)
            for t in turns
        ),
        # 8.4 -- filler and time-to-first-response
        "filler_latency_s": stats(t["filler_latency_s"] for t in with_filler)
        if fba
        else None,
        "filler_presence_rate": (len(with_filler) / len(delegated))
        if delegated
        else None,
        "first_response_latency_s": stats(
            t.get("first_response_latency_s") for t in turns
        ),
        "frontend_completion_tokens_per_call": _mean(
            [x for s in ok for x in frontend_completion_tokens(s)]
        ),
        # 8.5 -- tokens per task (per simulation, averaged)
        "tokens_per_task": tokens_per_task,
        # diagnostics
        "turns": len(turns),
        "turns_without_backend": len(turns) - len(backend_turns),
        "decisions": dict(Counter(t.get("decision") for t in turns)),
        "events": dict(events),
        "provenance": llm_args.get("provenance"),
    }


def per_task_rows(results: Results) -> list[dict]:
    """Per task_id: mean reward, turns and per-role tokens over its trials."""
    by_task: dict[str, list[SimulationRun]] = {}
    for s in results.simulations or []:
        if not is_infra(s):
            by_task.setdefault(s.task_id, []).append(s)
    rows = []
    for task_id, sims in sorted(by_task.items()):
        toks = [sim_tokens(s) for s in sims]
        n_turns = [len(turn_rows(s)) for s in sims]
        rewards = [r for r in (_reward(s) for s in sims) if r is not None]
        row = {
            "task_id": task_id,
            "trials": len(sims),
            "reward_mean": _mean(rewards),
            "turns_mean": _mean(n_turns),
            "turns_max": max(n_turns) if n_turns else 0,
        }
        for role in ROLES:
            for k in (
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "total_tokens",
            ):
                row[f"{role}_{k}"] = _mean([t[role][k] for t in toks])
        rows.append(row)
    return rows


def write_per_task_csv(results: Results, path: Path) -> None:
    rows = per_task_rows(results)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# automatic gates
# ---------------------------------------------------------------------------


def checks(s: dict) -> list[str]:
    """Things that make a run's numbers untrustworthy or incomparable.

    Each is a runbook gate; an empty list means the run passed all of them.
    """
    out = []
    tok, ev, prov = s["tokens_per_task"], s["events"], s["provenance"] or {}
    if (
        s["fba"]
        and s["arm"] == "fba_paired"
        and (tok["frontend"]["reasoning_tokens"] or 0) > 0
    ):
        out.append(
            "frontend produced reasoning tokens: the reasoning-OFF setting did not take "
            "effect (LiteLLM drop_params?). Filler latency is not representative."
        )
    backend_reasoning_on = (
        ((prov.get("backend") or {}).get("extra_body") or {})
        .get("chat_template_kwargs", {})
        .get("enable_thinking")
    )
    if (
        s["fba"]
        and backend_reasoning_on
        and s["simulations"]
        and not tok["backend"]["reasoning_tokens"]
    ):
        out.append(
            "backend reasoning is configured ON but no reasoning tokens were reported "
            "(the endpoint may not report them, or the setting was dropped)."
        )
    if s["filler_presence_rate"] is not None and s["filler_presence_rate"] < 1.0:
        out.append(
            f"only {s['filler_presence_rate']:.0%} of delegations carried filler_text."
        )
    if ev.get("backend_error"):
        out.append(
            f"{ev['backend_error']} backend LLM errors were answered with canned text (lenient mode)."
        )
    if s["infra_errors"]:
        out.append(
            f"{s['infra_errors']} infrastructure-error simulations excluded: resume with --auto-resume."
        )
    if s["num_trials"] and max(s["pass_hat_k"] or {0: 0}) < min(4, s["num_trials"]):
        out.append("pass^k is incomplete: some tasks have fewer trials than requested.")
    if s["num_trials"] < 4:
        out.append(
            f"only {s['num_trials']} trial(s): Pass^{s['num_trials'] + 1}..4 need --num-trials 4."
        )
    if (prov.get("prototype") or {}).get("dirty"):
        out.append(
            "prototype had uncommitted changes (--allow-dirty-prototype): not reproducible."
        )
    if s["decisions"].get("contract_fallback"):
        out.append(
            f"{s['decisions']['contract_fallback']} turns fell back to the frontend's error text."
        )
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _f(x: Optional[float], digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def _s(st: Optional[dict], key: str = "mean", digits: int = 2) -> str:
    return "—" if not st else f"{st[key]:.{digits}f}"


def _mp(st: Optional[dict]) -> str:
    """'mean (p90)' in seconds."""
    return "—" if not st else f"{st['mean']:.2f} ({st['p90']:.2f})"


def _tok(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:,.0f}"


def render_markdown(summaries: list[dict]) -> str:
    """The comparison report: one row per run (arm x domain)."""
    ks = sorted({k for s in summaries for k in s["pass_hat_k"]}) or [1, 2, 3, 4]
    lines = ["# Frontend/Backend Agent on tau2-bench — metrics", ""]

    lines += [
        "## Headline",
        "",
        "Latencies in seconds, `mean (p90)`. Tokens are per task (one simulation), averaged.",
        "",
        "| Arm | Domain | Sims | "
        + " | ".join(f"Pass^{k}" for k in ks)
        + " | Backend turn latency | Filler latency | Filler present | Time to first response | FE tokens/task | BE tokens/task |",
        "|" + "---|" * (9 + len(ks)),
    ]
    for s in summaries:
        tok = s["tokens_per_task"]
        lines.append(
            f"| {s['arm']} | {s['domain']} | {s['simulations']} | "
            + " | ".join(_f(s["pass_hat_k"].get(k), 3) for k in ks)
            + f" | {_mp(s['backend_turn_latency_s'])}"
            + f" | {_mp(s['filler_latency_s'])}"
            + f" | {'—' if s['filler_presence_rate'] is None else f'{s["filler_presence_rate"]:.0%}'}"
            + f" | {_mp(s['first_response_latency_s'])}"
            + f" | {_tok(tok['frontend']['total_tokens']) if s['fba'] else '—'}"
            + f" | {_tok(tok['backend']['total_tokens'])} |"
        )

    lines += [
        "",
        "## Latency detail",
        "",
        "| Arm | Domain | Backend turn p50 / p95 | Backend calls per turn | Backend LLM call mean (p90) | FE+BE LLM per turn | Filler p50 / p95 | TTFR p50 / p95 | FE completion tokens per call |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        bt, fl, tt = (
            s["backend_turn_latency_s"],
            s["filler_latency_s"],
            s["first_response_latency_s"],
        )
        lines.append(
            f"| {s['arm']} | {s['domain']}"
            f" | {_s(bt, 'p50')} / {_s(bt, 'p95')}"
            f" | {_f(s['backend_calls_per_turn'])}"
            f" | {_mp(s['backend_call_latency_s'])}"
            f" | {_mp(s['e2e_llm_latency_per_turn_s'])}"
            f" | {_s(fl, 'p50')} / {_s(fl, 'p95')}"
            f" | {_s(tt, 'p50')} / {_s(tt, 'p95')}"
            f" | {_f(s['frontend_completion_tokens_per_call'], 0)} |"
        )

    lines += [
        "",
        "## Tokens per task",
        "",
        "| Arm | Domain | Role | Prompt | Completion | of which reasoning | Cached prompt | Total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        for role in ROLES:
            if role == "frontend" and not s["fba"]:
                continue
            t = s["tokens_per_task"][role]
            lines.append(
                f"| {s['arm']} | {s['domain']} | {role}"
                f" | {_tok(t['prompt_tokens'])} | {_tok(t['completion_tokens'])}"
                f" | {_tok(t['reasoning_tokens'])} | {_tok(t['cached_tokens'])} | {_tok(t['total_tokens'])} |"
            )

    lines += [
        "",
        "## Diagnostics",
        "",
        "| Arm | Domain | Turns | Decisions | Turns with no backend work | Backend errors | Repairs | Contract violations | Placeholders | Infra errors excluded |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        ev = s["events"]
        decisions = ", ".join(
            f"{k}={v}" for k, v in sorted(s["decisions"].items(), key=str)
        )
        lines.append(
            f"| {s['arm']} | {s['domain']} | {s['turns']} | {decisions or '—'}"
            f" | {s['turns_without_backend']} | {ev.get('backend_error', 0)}"
            f" | {ev.get('frontend_repair', 0)} | {ev.get('frontend_contract_violation', 0)}"
            f" | {ev.get('empty_assistant_placeholder', 0)} | {s['infra_errors']} |"
        )

    lines += ["", "## Checks", ""]
    for s in summaries:
        found = checks(s)
        lines.append(
            f"- **{s['arm']} / {s['domain']}**: " + ("all passed" if not found else "")
        )
        lines += [f"  - ⚠ {c}" for c in found]

    lines += ["", "## Provenance", ""]
    for s in summaries:
        prov = s["provenance"] or {}
        proto = prov.get("prototype") or {}
        frontend = (prov.get("frontend") or {}).get("model")  # absent in backend_only
        lines.append(
            f"- **{s['arm']} / {s['domain']}** — `{s['source']}` · agent llm `{s['agent_llm']}` · "
            f"user llm `{s['user_llm']}` · trials {s['num_trials']}"
            + (
                (f" · frontend `{frontend}`" if frontend else "")
                + f" · prototype `{(proto.get('git_sha') or '?')[:10]}`"
                f"{' (DIRTY)' if proto.get('dirty') else ''} · "
                f"max_concurrency {prov.get('max_concurrency')}"
                if prov
                else ""
            )
        )

    lines += [
        "",
        "## How to read this",
        "",
        "- **Backend turn latency**: backend LLM time summed over one user turn (all tool rounds), "
        "averaged over turns that did backend work. Paired turns the frontend answered itself are "
        "excluded; their count is *Turns with no backend work*.",
        "- **Filler latency**: user message reaching the agent -> frontend's `call_backend` decision, "
        "over delegated turns that carried filler. Non-streaming: an upper bound on a streaming "
        "frontend, and the schema generates `query` before `filler_text`.",
        "- **Time to first response**: filler latency where filler exists; otherwise the full turn "
        "(backend-only and llm_agent have no filler).",
        "- Latencies depend on endpoint load: compare arms only at equal `max_concurrency`.",
        "- Cost is omitted: LiteLLM cannot price Inference Hub models and reports 0.0.",
        "- Scaffold results (fba_*) are not comparable to published leaderboard numbers.",
    ]
    return "\n".join(lines) + "\n"


def load(path: str | Path) -> Results:
    """Load a run from its directory or its results.json.

    tau2's Results.load treats *any* directory as the voice "dir" format and reads
    simulations from <dir>/simulations/, which text runs do not have -- so a text
    run directory silently loads with zero simulations. Resolve it to the file.
    """
    path = Path(path)
    if path.is_dir() and not (path / "simulations").is_dir():
        path = path / "results.json"
    return Results.load(path)
