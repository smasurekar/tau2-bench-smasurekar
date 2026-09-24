"""`tau2` CLI with the voice user simulator's TTS and hardcoded LLM calls routed to the Inference Hub (see tau2_ihub_overrides.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tau2_ihub_overrides as ihub  # noqa: E402  (installs the overrides on import)
from loguru import logger  # noqa: E402

from tau2.cli import main  # noqa: E402

if __name__ == "__main__":
    logger.warning(
        f"USER TTS OVERRIDE: {ihub.MODEL} at {ihub.BASE_URL} "
        "(non-official user voices; not leaderboard-comparable)"
    )
    logger.warning(
        f"USER DECISION LLM OVERRIDE: {ihub.DECISION_MODEL} at {ihub.DECISION_BASE_URL} "
        f"for {', '.join(ihub.DECISION_CALLS)}"
    )
    logger.warning(
        f"REVIEW LLM OVERRIDE: {ihub.REVIEW_MODEL} at {ihub.REVIEW_BASE_URL} "
        "for the hallucination check and --auto-review"
    )
    sys.exit(main())
