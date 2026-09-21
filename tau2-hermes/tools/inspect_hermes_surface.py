"""Print the tool surface and system prompt Hermes will actually use for a tau2 domain.

Offline: constructs the agent and renders the prompt, never calls a provider.
Run it after any change to $HERMES_HOME/config.yaml.

    uv run python tools/inspect_hermes_surface.py airline

Rungs 1.5 and 1.6 of misc/hermes-agent-integration.md section 8.
"""

import sys

from tau2.runner.build import build_environment

from tau2_hermes.hermes_agent import create_hermes_agent, uninstall_tau2_toolset


def main() -> int:
    domain = sys.argv[1] if len(sys.argv) > 1 else "airline"
    model = sys.argv[2] if len(sys.argv) > 2 else "anthropic/claude-sonnet-4.6"

    env = build_environment(domain)
    agent = create_hermes_agent(
        tools=env.get_tools(), domain_policy=env.get_policy(), llm=model, llm_args={}
    )
    try:
        # Builds the AIAgent. check_tool_surface raises here if the surface is wrong.
        agent.get_init_state()
        hermes = agent._hermes

        # ---- Rung 1.5: the tool surface ----
        names = sorted(
            t["function"]["name"] for t in (hermes.tools or []) if isinstance(t, dict)
        )
        print(f"# {len(names)} tools exposed to the model\n")
        print("\n".join(names))

        # ---- Rung 1.6: the effective system prompt ----
        # AIAgent._build_system_prompt is a lazy forwarder to
        # agent.system_prompt.build_system_prompt(agent, system_message)
        # (run_agent.py:1104, agent/lazy_forward.py:14), so it renders at construction
        # time without running a turn -- _cached_system_prompt is still None here.
        # ephemeral_system_prompt is NOT part of it: Hermes appends that at API-call
        # time (chat_completion_helpers.py:2071), so append it the same way to see
        # what the model really receives.
        core = hermes._build_system_prompt(None)
        effective = (core + "\n\n" + (hermes.ephemeral_system_prompt or "")).strip()
        print(f"\n\n# system prompt ({len(effective)} chars)\n")
        print(effective)
    finally:
        agent.stop()
        uninstall_tau2_toolset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
