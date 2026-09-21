"""Run a tau2-bench domain with the Hermes agent. tau2-bench is not modified.

    uv run python run_hermes_eval.py --domain airline \
        --agent-llm 'nvidia/nvidia/nemotron-3-ultra' \
        --base-url 'https://inference-api.nvidia.com/v1' \
        --user-llm 'openai/azure/openai/gpt-5.2'

One domain per process: Hermes' tool registry is process-global, so sweep several
domains with several invocations, not a loop inside one.
See ../misc/hermes-agent-runbook.md for the step-by-step, and
../misc/hermes-agent-integration.md for the design.
"""

import argparse
import json
import os
import sys

from tau2.data_model.simulation import TextRunConfig
from tau2.runner import run_domain

import tau2_hermes
from tau2_hermes.hermes_agent import uninstall_tau2_toolset


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--domain", default="airline")
    p.add_argument(
        "--agent-llm",
        required=True,
        help="Model string for Hermes. NOT LiteLLM-prefixed: Hermes resolves the "
        "provider itself, e.g. 'nvidia/nvidia/nemotron-3-ultra'.",
    )
    p.add_argument(
        "--user-llm",
        required=True,
        help="Model string for the tau2 user simulator, which goes through LiteLLM "
        "and so IS prefixed, e.g. 'openai/azure/openai/gpt-5.2'. tau2 recommends "
        "gpt-5.2 here (docs/leaderboard-submission.md); hold it fixed across arms "
        "and do not use the model under test.",
    )

    endpoint = p.add_argument_group("agent endpoint (OpenAI-compatible)")
    endpoint.add_argument(
        "--base-url",
        default=None,
        help="Base URL for the agent model, e.g. https://inference-api.nvidia.com/v1. "
        "Omit to use whatever the Hermes config.yaml/.env already selects.",
    )
    endpoint.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Name of the environment variable holding the API key (default: "
        "OPENAI_API_KEY). The key itself is never taken on the command line.",
    )

    p.add_argument("--num-trials", type=int, default=1)
    p.add_argument("--task-set-name", default=None)
    # A *split* of the task set, not a set: "base" (the evaluation default),
    # "test"/"train" (RL experiments). Passing "test" to --task-set-name is a
    # registry KeyError, not a silent fallback.
    p.add_argument("--task-split-name", default=None)
    p.add_argument("--num-tasks", type=int, default=None)
    p.add_argument("--max-concurrency", type=int, default=1)
    p.add_argument("--save-to", default=None)
    p.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="Cap on Hermes' internal tool-calling loop per tau2 turn (H8).",
    )
    p.add_argument(
        "--hermes-args",
        default=None,
        help='JSON passed straight to AIAgent(**hermes_args), e.g. '
        '\'{"request_overrides": {"temperature": 0.0}}\'.',
    )
    p.add_argument(
        "--user-llm-args",
        default=None,
        help='JSON for the user simulator, e.g. \'{"temperature": 0.0, '
        '"api_base": "https://inference-api.nvidia.com/v1"}\'.',
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    hermes_args = _json_arg(args.hermes_args, "--hermes-args")
    if args.base_url:
        api_key = os.environ.get(args.api_key_env, "")
        if not api_key:
            raise SystemExit(
                f"--base-url was given but ${args.api_key_env} is empty. "
                f"Export the key first:  export {args.api_key_env}='sk-...'"
            )
        # Hermes uses the explicit-client path only when BOTH are set
        # (agent/agent_init.py:923). api_mode is pinned rather than inferred: the
        # host-based ladder would land on chat_completions anyway
        # (agent_init.py:373-418), but pinning it means a future host rule cannot
        # silently switch the wire protocol mid-benchmark.
        hermes_args.setdefault("base_url", args.base_url)
        hermes_args.setdefault("api_key", api_key)
        hermes_args.setdefault("api_mode", "chat_completions")

    agent_name = tau2_hermes.register()

    config = TextRunConfig(
        domain=args.domain,
        agent=agent_name,
        llm_agent=args.agent_llm,
        llm_user=args.user_llm,
        llm_args_agent={
            "max_iterations": args.max_iterations,
            "hermes_args": hermes_args,
        },
        llm_args_user=_json_arg(args.user_llm_args, "--user-llm-args"),
        num_trials=args.num_trials,
        task_set_name=args.task_set_name,
        task_split_name=args.task_split_name,
        num_tasks=args.num_tasks,
        max_concurrency=args.max_concurrency,
        save_to=args.save_to or f"hermes_{args.domain}",
    )

    try:
        run_domain(config)
    finally:
        # Process-level teardown: never from an agent's stop(), where concurrent
        # simulations would lose their tools mid-run (H4).
        uninstall_tau2_toolset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
