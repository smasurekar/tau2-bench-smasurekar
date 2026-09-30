#!/usr/bin/env python
"""Time-to-first-speakable-text per user turn, from tau2 run artifacts.

For a voice agent this is the silence the caller actually experiences: they stop
speaking, and nothing can be sent to TTS until the agent emits non-empty content.
Any tool-call turns with empty content in between are dead air.

Measured as: timestamp(first assistant message with non-empty content)
           - timestamp(user message)

Caveats, which matter for interpreting these numbers:
  * tau2 messages are timestamped at CREATION, i.e. after the LLM call returns.
    These runs are non-streaming, so this is full-completion latency, not
    time-to-first-token. A streaming voice stack would start TTS earlier; treat
    these as an upper bound for the same model/prompt.
  * Runs used --max-concurrency 8, so latencies include contention.
  * Tool execution time IS included (correctly - the caller waits through it).

Usage: .venv/bin/python misc/tools/voice_silence.py <run_dir> [...]
"""
import json, statistics as st, sys
from datetime import datetime
from pathlib import Path

def pct(xs, p):
    if not xs: return float("nan")
    xs = sorted(xs); k = (len(xs)-1)*p; lo = int(k); hi = min(lo+1, len(xs)-1)
    return xs[lo] + (xs[hi]-xs[lo])*(k-lo)

def ts(m):
    t = m.get("timestamp")
    return datetime.fromisoformat(t) if t else None

def analyse(run_dir):
    d = json.loads((Path(run_dir)/"results.json").read_text())
    gaps, hops, never = [], [], 0
    for s in d["simulations"]:
        msgs = s["messages"]
        for i, m in enumerate(msgs):
            if m.get("role") != "user": continue
            t0 = ts(m)
            if t0 is None: continue
            n_calls = 0
            for j in range(i+1, len(msgs)):
                nxt = msgs[j]
                if nxt.get("role") == "user": break
                if nxt.get("role") != "assistant": continue
                n_calls += 1
                if (nxt.get("content") or "").strip():
                    t1 = ts(nxt)
                    if t1: gaps.append((t1-t0).total_seconds()); hops.append(n_calls)
                    break
            else:
                never += 1
    return {"run": Path(run_dir).name, "n": len(gaps), "mean": st.mean(gaps) if gaps else float("nan"),
            "p50": pct(gaps,.5), "p90": pct(gaps,.9), "p95": pct(gaps,.95), "p99": pct(gaps,.99),
            "max": max(gaps) if gaps else float("nan"),
            "over3": sum(1 for g in gaps if g>3)/len(gaps) if gaps else float("nan"),
            "over10": sum(1 for g in gaps if g>10)/len(gaps) if gaps else float("nan"),
            "hops": st.mean(hops) if hops else float("nan")}

def main():
    rows = [analyse(r) for r in sys.argv[1:]]
    h = f"{'run':<44} {'turns':>6} {'mean':>7} {'p50':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'max':>7} {'>3s':>6} {'>10s':>6} {'calls':>6}"
    print(h); print("-"*len(h))
    for r in rows:
        print(f"{r['run']:<44} {r['n']:>6} {r['mean']:>7.2f} {r['p50']:>7.2f} {r['p90']:>7.2f} "
              f"{r['p95']:>7.2f} {r['p99']:>7.2f} {r['max']:>7.1f} {r['over3']:>5.0%} {r['over10']:>5.0%} {r['hops']:>6.1f}")
    print("\nseconds of caller-perceived silence; non-streaming, --max-concurrency 8")
    print("'calls' = mean LLM calls (incl. silent tool-call turns) before speakable text")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
