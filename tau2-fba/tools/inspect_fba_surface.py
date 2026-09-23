"""Print exactly what each role of the agent will be given, without calling any LLM.

    uv run python tau2-fba/tools/inspect_fba_surface.py airline
    uv run python tau2-fba/tools/inspect_fba_surface.py airline --mode backend_only --full

Checks the tool routing guarantee before a run costs anything: the frontend must be
offered only `call_backend`, the backend exactly the domain's tools. Also shows the
model, endpoint and reasoning settings per role, and the rendered system prompts.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tau2_fba.agent import AGENT_MODES, FBAHalfDuplexAgent  # noqa: E402
from tau2_fba.client import BACKEND, FRONTEND  # noqa: E402

from tau2.registry import registry  # noqa: E402


def _no_llm(**kwargs):
    raise RuntimeError("inspect_fba_surface makes no LLM calls")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("domain")
    p.add_argument("--mode", choices=["paired", "backend_only"], default="paired")
    p.add_argument("--full", action="store_true", help="Print the full system prompts.")
    args = p.parse_args(argv)

    env = registry.get_env_constructor(args.domain)()
    mode = AGENT_MODES["fba_paired" if args.mode == "paired" else "fba_backend_only"]
    agent = FBAHalfDuplexAgent(
        env.get_tools(),
        env.get_policy(),
        mode=mode,
        domain=args.domain,
        generate_fn=_no_llm,
    )

    for client in agent.clients:
        kwargs = {k: v for k, v in client.kwargs.items() if k != "api_key"}
        print(f"=== {client.role} LLM")
        print(f"model        : {client.model}")
        print(
            f"api_key      : {'passed per call' if 'api_key' in client.kwargs else 'from $OPENAI_API_KEY (not passed)'}"
        )
        print(f"kwargs       : {json.dumps(kwargs, sort_keys=True)}")
        print(
            f"tools ({len(client.expected_tools):>2})   : {sorted(client.expected_tools)}"
        )
        prompt = agent.system_prompt(client.role)
        print(f"system prompt: {len(prompt)} chars")
        print(
            prompt
            if args.full
            else prompt[:600]
            + ("\n[... --full for the rest]" if len(prompt) > 600 else "")
        )
        print()

    domain_tools = {t.name for t in env.get_tools()}
    ok = agent._backend_client.expected_tools == domain_tools and (
        agent._frontend_client is None
        or agent._frontend_client.expected_tools == {"call_backend"}
    )
    leaked = [
        t
        for t in domain_tools
        if agent._frontend_client and t in agent.system_prompt(FRONTEND)
    ]
    print("=== verdict")
    print(
        f"backend tools == domain tools ({len(domain_tools)}): {agent._backend_client.expected_tools == domain_tools}"
    )
    if agent._frontend_client is not None:
        print(
            f"frontend tools == ['call_backend']: {agent._frontend_client.expected_tools == {'call_backend'}}"
        )
        print(f"domain tool names in frontend prompt: {leaked or 'none'}")
    print(
        f"policy in backend prompt: {env.get_policy().strip()[:60] in agent.system_prompt(BACKEND)}"
    )
    return 0 if ok and not leaked else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
