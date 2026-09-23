"""Locate and import the Frontend/Backend Agent prototype, and record which one ran.

The prototype lives in another repository (nemotron-voice-agent) and is not packaged,
so it is imported from its source tree. Installing that repository instead would drag
the whole Pipecat voice stack into tau2's environment for no benefit: the prototype's
runtime dependencies (pyyaml, loguru, openai) are already tau2 dependencies.

See ../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md section 6.4.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

#: Environment variable naming the prototype repository root.
ENV_ROOT = "FBA_PROTOTYPE_ROOT"

#: Package path of the agent inside that repository, relative to the root.
PACKAGE_DIR = Path("src/prototypes/text_frontend_backend_agent")

#: Default: the sibling checkout next to this tau2 repository.
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "nemotron-voice-agent-smasurekar"

_MISSING = (
    "The Frontend/Backend Agent prototype is not importable. Point {env} at the "
    "nemotron-voice-agent checkout (the directory that contains {pkg}), e.g.\n"
    "    export {env}=/path/to/nemotron-voice-agent-smasurekar\n"
    "Looked in: {root}"
)


def prototype_root() -> Path:
    """The prototype repository root: ``$FBA_PROTOTYPE_ROOT``, else the sibling checkout."""
    raw = os.environ.get(ENV_ROOT)
    return Path(raw).expanduser().resolve() if raw else DEFAULT_ROOT


def ensure_importable() -> Path:
    """Make ``prototypes.text_frontend_backend_agent`` importable, or explain why not.

    An existing ``PYTHONPATH`` entry wins; otherwise ``<root>/src`` is appended to
    ``sys.path``. Returns the directory the package was actually imported from.
    """
    try:
        import prototypes.text_frontend_backend_agent as pkg
    except ImportError:
        root = prototype_root()
        src = root / "src"
        if not (root / PACKAGE_DIR).is_dir():
            raise ImportError(
                _MISSING.format(env=ENV_ROOT, pkg=PACKAGE_DIR, root=root)
            ) from None
        if str(src) not in sys.path:
            sys.path.append(str(src))
        try:
            import prototypes.text_frontend_backend_agent as pkg
        except ImportError as e:
            raise ImportError(
                _MISSING.format(env=ENV_ROOT, pkg=PACKAGE_DIR, root=root)
            ) from e
    return Path(pkg.__file__).resolve().parent


def _git(root: Path, *args: str, strip: bool = True) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if strip else out.stdout.rstrip("\n")


def provenance(package_dir: Optional[Path] = None) -> dict:
    """Git SHA and dirty flag of the prototype that is about to be measured.

    "Dirty" is scoped to the agent package itself: an unrelated edit elsewhere in the
    voice-agent repo does not change what is measured, an edit to a prompt does.
    """
    package_dir = package_dir or ensure_importable()
    repo = _git(package_dir, "rev-parse", "--show-toplevel")
    if repo is None:
        return {"package_dir": str(package_dir), "git_sha": None, "dirty": None}
    status = _git(
        Path(repo), "status", "--porcelain", "--", str(package_dir), strip=False
    )
    return {
        "package_dir": str(package_dir),
        "git_sha": _git(Path(repo), "rev-parse", "HEAD"),
        "dirty": bool(status),
        "dirty_files": status.splitlines() if status else [],
    }
