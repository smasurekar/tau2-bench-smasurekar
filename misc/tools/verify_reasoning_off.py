#!/usr/bin/env python
"""Verify that a tau2 run configured with reasoning disabled actually ran that way.

Why this exists
---------------
`litellm.drop_params = True` (llm_utils.py) silently discards provider params it does
not recognise. So a run can be *configured* to disable reasoning and still execute with
reasoning fully on, with nothing in the logs to say so. Worse, tau2's results.json
persists neither `raw_data` nor per-message `usage`, so the saved trajectories cannot
answer the question either -- a naive "no reasoning_content found" check over them
returns 0 for a reasoning-ON run too, and is therefore vacuous.

What this checks
----------------
A. PROVENANCE  -- the recorded info.agent_info.llm_args in results.json carry the flag.
B. WIRE (live) -- replay the exact recorded llm_args through tau2's OWN generate()
                  path and read reasoning_tokens off the live response.
C. CONTROL     -- the same call WITHOUT the flag must show reasoning_tokens > 0.
                  Without this, B proving "0 tokens" means nothing: a detector that
                  cannot detect the positive case is not evidence.
D. CONTENT     -- no <think> blocks leaked into assistant content in the trajectories.

Exit code 0 only if every check passes. Usage:
    .venv/bin/python misc/tools/verify_reasoning_off.py <run_dir> [--control-run <dir>]
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

PROBE = [{"role": "user", "content": "What is 17*23? Answer with just the number."}]


def reasoning_signal(resp) -> tuple[int, str]:
    """How much reasoning did this response contain?

    Models differ in what they report. nemotron-3-ultra returns
    completion_tokens_details.reasoning_tokens; nemotron-3.5-lightning returns
    None there but still populates message.reasoning_content. Prefer whichever
    is present, and say which was used so a null signal is never mistaken for
    a zero one.
    """
    usage = getattr(resp, "usage", None)
    det = getattr(usage, "completion_tokens_details", None) if usage else None
    rt = getattr(det, "reasoning_tokens", None) if det else None
    if rt is not None:
        return int(rt), "reasoning_tokens"
    msg = resp.choices[0].message
    rc = getattr(msg, "reasoning_content", None) or ""
    return len(rc), "len(reasoning_content)"


def live_probe(model: str, llm_args: dict) -> tuple[int, str, int | None]:
    import litellm

    litellm.drop_params = True  # mirror llm_utils.py exactly
    resp = litellm.completion(model=model, messages=PROBE, **llm_args)
    usage = getattr(resp, "usage", None)
    sig, src = reasoning_signal(resp)
    return sig, src, getattr(usage, "completion_tokens", None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--control-run", default=None,
                    help="A reasoning-ON run dir, used as the positive control.")
    ap.add_argument("--skip-live", action="store_true")
    args = ap.parse_args()

    results = Path(args.run_dir) / "results.json"
    if not results.exists():
        results = Path(args.run_dir)
    data = json.loads(results.read_text())
    info = data["info"]["agent_info"]
    model, llm_args = info["llm"], info["llm_args"]
    sims = data["simulations"]

    failures: list[str] = []
    print(f"Run:   {args.run_dir}")
    print(f"Model: {model}")
    print(f"Sims:  {len(sims)}")
    print()

    # --- A. provenance -----------------------------------------------------
    # The disable key is model-specific: nemotron-3-ultra honours "thinking",
    # nemotron-3.5-lightning honours "enable_thinking". Each no-ops on the other.
    ctk = (llm_args.get("extra_body") or {}).get("chat_template_kwargs") or {}
    thinking = ctk.get("thinking", ctk.get("enable_thinking"))
    ok_a = thinking is False
    print("[A] PROVENANCE  recorded llm_args")
    print(f"    {json.dumps(llm_args)}")
    print(f"    chat_template_kwargs disable-key = {thinking!r}  ->  {'PASS' if ok_a else 'FAIL'}")
    if not ok_a:
        failures.append("A: results.json does not record thinking=false")
    print()

    # --- B/C. live wire check + positive control ---------------------------
    if not args.skip_live:
        rt_off, src, ct_off = live_probe(model, llm_args)
        ok_b = rt_off == 0
        print("[B] WIRE        replay recorded llm_args through a live call")
        print(f"    {src}={rt_off}  completion_tokens={ct_off}  ->  {'PASS' if ok_b else 'FAIL'}")
        if not ok_b:
            failures.append(f"B: {src}={rt_off}, expected 0")
        print()

        ctrl_args = dict(llm_args)
        ctrl_args.pop("extra_body", None)
        if args.control_run:
            cinfo = json.loads((Path(args.control_run) / "results.json").read_text())
            ctrl_args = cinfo["info"]["agent_info"]["llm_args"]
        rt_on, src_on, ct_on = live_probe(model, ctrl_args)
        ok_c = rt_on > 0
        print("[C] CONTROL     same call WITHOUT the flag (must show reasoning)")
        print(f"    {src_on}={rt_on}  completion_tokens={ct_on}  ->  {'PASS' if ok_c else 'FAIL'}")
        if not ok_c:
            failures.append("C: control showed no reasoning; detector is not sensitive, [B] is meaningless")
        print()

    # --- D. content leakage ------------------------------------------------
    n_msgs = leaked = 0
    for s in sims:
        for m in s["messages"]:
            if m.get("role") != "assistant":
                continue
            n_msgs += 1
            c = (m.get("content") or "").lower()
            if "<think>" in c or "</think>" in c:
                leaked += 1
    ok_d = leaked == 0
    print("[D] CONTENT     <think> blocks leaked into assistant turns")
    print(f"    {leaked}/{n_msgs} assistant messages  ->  {'PASS' if ok_d else 'FAIL'}")
    if not ok_d:
        failures.append(f"D: {leaked} messages contain <think>")
    print()
    print("    note: results.json persists no raw_data/usage, so trajectories cannot")
    print("    themselves prove reasoning was off. [B]+[C] are the load-bearing checks.")
    print()

    if failures:
        print("VERDICT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VERDICT: PASS - reasoning was disabled on the wire for this configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
