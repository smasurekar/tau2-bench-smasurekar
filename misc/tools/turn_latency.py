#!/usr/bin/env python
"""Per-turn agent latency from tau2 run artifacts.

Source of truth: `generation_time_seconds` on assistant messages in results.json.
It is the measured wall time of one agent LLM call. Notes on what it is NOT:

- User-simulator turns are not instrumented (the field is None on them), so this
  is agent latency only, not end-to-end turn time.
- The first assistant message of each simulation is a canned greeting with no LLM
  call; it carries None and is excluded rather than counted as zero.
- Runs executed with --max-concurrency 8, so these are latencies UNDER LOAD and
  are inflated relative to an idle single-stream call. They are comparable across
  the four runs here (identical concurrency) but not to a solo probe.

Usage: .venv/bin/python misc/tools/turn_latency.py <run_dir> [<run_dir> ...]
"""

import json
import statistics as st
import sys
from pathlib import Path


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def analyse(run_dir: str) -> dict:
    d = json.loads((Path(run_dir) / "results.json").read_text())
    sims = d["simulations"]
    lat: list[float] = []
    per_sim_turns: list[int] = []
    missing = 0
    for s in sims:
        n = 0
        for m in s["messages"]:
            if m.get("role") != "assistant":
                continue
            g = m.get("generation_time_seconds")
            if g is None:
                missing += 1
                continue
            lat.append(float(g))
            n += 1
        per_sim_turns.append(n)
    lat.sort()
    dur = [s["duration"] for s in sims if s.get("duration") is not None]
    return {
        "run": Path(run_dir).name,
        "sims": len(sims),
        "turns": len(lat),
        "skipped_greeting": missing,
        "turns_per_sim": st.mean(per_sim_turns) if per_sim_turns else float("nan"),
        "mean": st.mean(lat) if lat else float("nan"),
        "median": pct(lat, 0.50),
        "p90": pct(lat, 0.90),
        "p95": pct(lat, 0.95),
        "p99": pct(lat, 0.99),
        "max": lat[-1] if lat else float("nan"),
        "total_agent_s": sum(lat),
        "sim_duration_mean": st.mean(dur) if dur else float("nan"),
    }


def main() -> int:
    rows = [analyse(r) for r in sys.argv[1:]]
    hdr = f"{'run':<44} {'sims':>5} {'turns':>6} {'t/sim':>6} {'mean':>7} {'med':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'max':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['run']:<44} {r['sims']:>5} {r['turns']:>6} {r['turns_per_sim']:>6.1f} "
            f"{r['mean']:>7.2f} {r['median']:>7.2f} {r['p90']:>7.2f} {r['p95']:>7.2f} "
            f"{r['p99']:>7.2f} {r['max']:>8.1f}"
        )
    print("\nall times in seconds; measured at --max-concurrency 8 (under load)")
    print(f"{'run':<44} {'agent_s_total':>14} {'mean_sim_dur':>13} {'agent_share':>12}")
    for r in rows:
        share = r["total_agent_s"] / (r["sim_duration_mean"] * r["sims"]) if r["sims"] else 0
        print(f"{r['run']:<44} {r['total_agent_s']:>14.0f} {r['sim_duration_mean']:>13.1f} {share:>11.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
