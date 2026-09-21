"""tau2-hermes: run the Hermes agent scaffold as a tau2-bench agent.

tau2-bench itself is unmodified. The agent factory is registered at runtime:

    import tau2_hermes
    tau2_hermes.register()

See ../misc/hermes-agent-integration.md (same repo).
"""

from tau2_hermes.hermes_agent import (
    AGENT_INSTRUCTION,
    AGENT_NAME,
    TAU2_TOOLSET,
    HermesAgentState,
    HermesHalfDuplexAgent,
    ToolBridge,
    check_tool_surface,
    create_hermes_agent,
    install_tau2_toolset,
    uninstall_tau2_toolset,
)

__all__ = [
    "AGENT_INSTRUCTION",
    "AGENT_NAME",
    "TAU2_TOOLSET",
    "HermesAgentState",
    "HermesHalfDuplexAgent",
    "ToolBridge",
    "check_tool_surface",
    "create_hermes_agent",
    "install_tau2_toolset",
    "register",
    "uninstall_tau2_toolset",
]


def register(name: str = AGENT_NAME) -> str:
    """Register the Hermes agent factory with tau2's registry. Idempotent.

    Runtime registration, not an edit to src/tau2/registry.py: the batch runner uses
    a ThreadPoolExecutor in this same process (src/tau2/runner/batch.py:1005), so one
    registration is visible to every worker.
    """
    from tau2.registry import registry

    if name not in registry.get_agents():
        registry.register_agent_factory(create_hermes_agent, name)
    return name
