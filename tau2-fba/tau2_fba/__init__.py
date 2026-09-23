"""tau2-fba: run the text Frontend/Backend Agent prototype as a tau2-bench agent.

tau2-bench itself is unmodified. The two agent factories are registered at runtime:

    import tau2_fba
    tau2_fba.register()        # -> "fba_paired", "fba_backend_only"

See ../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md.
"""

from functools import partial

from tau2_fba.agent import (
    AGENT_BACKEND_ONLY,
    AGENT_MODES,
    AGENT_PAIRED,
    RAW_KEY,
    BackendTransportError,
    FBAHalfDuplexAgent,
    FBAState,
    create_fba_agent,
)
from tau2_fba.client import Tau2ChatClient, ToolSurfaceError

__all__ = [
    "AGENT_BACKEND_ONLY",
    "AGENT_MODES",
    "AGENT_PAIRED",
    "RAW_KEY",
    "BackendTransportError",
    "FBAHalfDuplexAgent",
    "FBAState",
    "Tau2ChatClient",
    "ToolSurfaceError",
    "create_fba_agent",
    "register",
]


def register() -> list[str]:
    """Register both arms with tau2's registry. Idempotent.

    Runtime registration, not an edit to src/tau2/registry.py: the batch runner uses a
    ThreadPoolExecutor in this same process (src/tau2/runner/batch.py), so one
    registration is visible to every worker.
    """
    from tau2.registry import registry

    registered = registry.get_agents()
    for name, mode in AGENT_MODES.items():
        if name not in registered:
            registry.register_agent_factory(partial(create_fba_agent, mode=mode), name)
    return list(AGENT_MODES)
