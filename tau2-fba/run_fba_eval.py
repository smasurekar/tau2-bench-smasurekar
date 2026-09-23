"""Run a tau2-bench domain with the Frontend/Backend Agent prototype. tau2 is not modified.

    uv run python tau2-fba/run_fba_eval.py --mode paired --domain airline \
        --user-llm 'openai/azure/openai/gpt-5.2' --num-trials 4

    uv run python tau2-fba/run_fba_eval.py --mode backend_only --domain airline \
        --user-llm 'openai/azure/openai/gpt-5.2' --num-trials 4

Models, endpoints, prompts and reasoning settings come from the prototype's own
config/agent.yaml; flags here override the model ids and endpoint only.
Step by step: ../misc/prototypes/text-frontend-backend-agent-tau2-runbook.md
Design:       ../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md
"""

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import tau2_fba
from tau2_fba import config as fba_config
from tau2_fba import metrics
from tau2_fba.agent import AGENT_BACKEND_ONLY, AGENT_PAIRED
from tau2_fba.prototype_import import ensure_importable, provenance

from tau2.data_model.simulation import TextRunConfig
from tau2.runner import run_domain
from tau2.utils import DATA_DIR

ARMS = {"paired": AGENT_PAIRED, "backend_only": AGENT_BACKEND_ONLY}


def _json_arg(raw: str | None, flag: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{flag} is not valid JSON: {e}")
    if not isinstance(value, dict):
        raise SystemExit(f"{flag} must be a JSON object, got {type(value).__name__}")
    return value


def _with_key(llm_config, api_key: str):
    """The role's LLMConfig as the agent will see it once --api-key-env is applied."""
    return replace(llm_config, api_key=api_key)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--mode", required=True, choices=sorted(ARMS), help="Which arm to run."
    )
    p.add_argument("--domain", default="airline")
    p.add_argument(
        "--user-llm",
        required=True,
        help="tau2 user simulator model (LiteLLM string, e.g. 'openai/azure/openai/gpt-5.2'). "
        "Hold it fixed across every arm you compare.",
    )
    p.add_argument(
        "--user-llm-args",
        default=None,
        help='JSON for the user simulator, e.g. \'{"temperature": 0.0, '
        '"api_base": "https://inference-api.nvidia.com/v1"}\'.',
    )

    agent = p.add_argument_group(
        "agent (defaults come from the prototype's agent.yaml)"
    )
    agent.add_argument(
        "--agent-llm",
        default=None,
        help="BACKEND model id as the endpoint knows it, no LiteLLM prefix "
        "(e.g. 'nvidia/nvidia/nemotron-3-ultra'). Recorded as tau2's agent llm.",
    )
    agent.add_argument(
        "--frontend-llm",
        default=None,
        help="FRONTEND model id (paired mode only), e.g. 'nvidia/nvidia/nemotron-3.5-lightning'.",
    )
    agent.add_argument("--fba-config", default=None, help="Alternative agent.yaml.")
    agent.add_argument(
        "--base-url", default=None, help="Override both roles' base_url."
    )
    agent.add_argument(
        "--api-key-env",
        default=None,
        help="Environment variable holding the agent's API key. Default: whatever "
        "agent.yaml interpolates (${NVIDIA_API_KEY}); if that is empty LiteLLM falls "
        "back to $OPENAI_API_KEY. The key itself is never taken on the command line.",
    )
    agent.add_argument(
        "--llm-kwargs",
        default=None,
        help="Extra LiteLLM kwargs for both roles, e.g. '{\"num_retries\": 3}'.",
    )
    agent.add_argument(
        "--lenient-transport-errors",
        action="store_true",
        help="Diagnostics only: let the prototype answer a failed backend LLM call "
        "with its canned error text instead of re-raising (which tau2 retries).",
    )
    agent.add_argument(
        "--fba-event-log",
        default=None,
        help="Also append every internal prototype event (filler, delegation, ...) to this JSONL file.",
    )
    agent.add_argument(
        "--allow-dirty-prototype",
        action="store_true",
        help="Run even if the prototype package has uncommitted changes (recorded).",
    )

    run = p.add_argument_group("run")
    run.add_argument("--num-trials", type=int, default=1)
    run.add_argument("--task-set-name", default=None)
    # A *split* of the task set: "base" (evaluation), "test"/"train" (RL experiments).
    # Defaults to "base" rather than tau2's None: telecom with no split silently runs
    # its full 2285-task set instead of the 114-task base split.
    run.add_argument("--task-split-name", default="base")
    run.add_argument("--task-ids", nargs="+", default=None)
    run.add_argument("--num-tasks", type=int, default=None)
    run.add_argument("--max-concurrency", type=int, default=1)
    run.add_argument("--max-steps", type=int, default=None)
    run.add_argument("--seed", type=int, default=None)
    run.add_argument(
        "--save-to",
        default=None,
        help="Run name under data/simulations/. Default: fba_<mode>_<domain>_<timestamp>. "
        "Pass the same name with --auto-resume to resume.",
    )
    run.add_argument("--auto-resume", action="store_true")
    run.add_argument(
        "--verbose-logs", action="store_true", help="tau2 per-task logs + llm_debug."
    )
    run.add_argument(
        "--no-report", action="store_true", help="Skip the FBA metrics report."
    )
    return p


def resolve_provenance(
    args: argparse.Namespace, raw: dict, source_dir: Path
) -> tuple[dict, object]:
    """What identifies the measured agent (never the API key), plus a preview Config."""
    package_dir = ensure_importable()
    proto = provenance(package_dir)
    if proto.get("dirty") and not args.allow_dirty_prototype:
        raise SystemExit(
            "The prototype package has uncommitted changes:\n  "
            + "\n  ".join(proto.get("dirty_files") or [])
            + "\nCommit them (so the run is reproducible) or pass --allow-dirty-prototype."
        )
    profile = fba_config.load_domain_profile(args.domain)
    preview = fba_config.build_fba_config(
        raw,
        source_dir,
        mode=fba_config.MODE_PAIRED
        if args.mode == "paired"
        else fba_config.MODE_BACKEND_ONLY,
        domain_policy="(from tau2 environment)",
        profile=profile,
        frontend_model=args.frontend_llm,
        backend_model=args.agent_llm,
        base_url=args.base_url,
    )
    out = {
        "arm": ARMS[args.mode],
        "prototype": proto,
        "agent_yaml_sha256": fba_config.sha256_of(source_dir / "agent.yaml")
        if not args.fba_config
        else fba_config.sha256_of(Path(args.fba_config).expanduser().resolve()),
        "prompts_sha256": fba_config.sha256_of(preview.prompts_path),
        "domain_profile_sha256": fba_config.sha256_of(profile),
        "backend": fba_config.RoleSummary.of(preview.backend.llm).__dict__,
        "max_concurrency": args.max_concurrency,
        "strict_transport_errors": not args.lenient_transport_errors,
    }
    if args.mode == "paired":
        out["frontend"] = fba_config.RoleSummary.of(preview.frontend.llm).__dict__
    return out, preview


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "backend_only" and args.frontend_llm:
        raise SystemExit("--frontend-llm has no effect with --mode backend_only")

    raw, source_dir = fba_config.load_raw_config(args.fba_config)
    try:
        api_key = fba_config.resolve_api_key(
            args.api_key_env
        )  # fail fast on an empty key
    except ValueError as e:
        raise SystemExit(str(e))
    prov, preview = resolve_provenance(args, raw, source_dir)

    roles = [preview.backend.llm] + (
        [preview.frontend.llm] if args.mode == "paired" else []
    )
    if args.verbose_logs and any(
        fba_config.passes_explicit_key(r if api_key is None else _with_key(r, api_key))
        for r in roles
    ):
        raise SystemExit(
            "--verbose-logs writes every LLM call's kwargs to llm_debug/*.json, which "
            "would include the agent's API key. Export the same key as OPENAI_API_KEY "
            "(LiteLLM then reads it from the environment and it is never passed or "
            "logged), or drop --verbose-logs."
        )

    agent_name = ARMS[args.mode]
    tau2_fba.register()

    llm_args_agent = {
        "fba_domain": args.domain,
        "frontend_llm": args.frontend_llm,
        "fba_config": args.fba_config,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
        "llm_kwargs": _json_arg(args.llm_kwargs, "--llm-kwargs"),
        "strict_transport_errors": not args.lenient_transport_errors,
        "event_log": args.fba_event_log,
        "provenance": prov,
    }
    save_to = args.save_to or (
        f"fba_{args.mode}_{args.domain}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    run_kwargs = dict(
        domain=args.domain,
        agent=agent_name,
        llm_agent=preview.backend.llm.model,
        llm_args_agent=llm_args_agent,
        llm_user=args.user_llm,
        llm_args_user=_json_arg(args.user_llm_args, "--user-llm-args"),
        num_trials=args.num_trials,
        task_set_name=args.task_set_name,
        task_split_name=args.task_split_name,
        task_ids=args.task_ids,
        num_tasks=args.num_tasks,
        max_concurrency=args.max_concurrency,
        save_to=save_to,
        auto_resume=args.auto_resume,
        verbose_logs=args.verbose_logs,
    )
    if args.max_steps is not None:
        run_kwargs["max_steps"] = args.max_steps
    if args.seed is not None:
        run_kwargs["seed"] = args.seed

    results = run_domain(TextRunConfig(**run_kwargs))

    if not args.no_report:
        run_dir = DATA_DIR / "simulations" / save_to
        summary = metrics.summarize(results, source=str(run_dir))
        report = metrics.render_markdown([summary])
        (run_dir / "fba_report.md").write_text(report, encoding="utf-8")
        (run_dir / "fba_metrics.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        metrics.write_per_task_csv(results, run_dir / "fba_per_task.csv")
        print(report)
        print(f"FBA report written to {run_dir}/fba_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
