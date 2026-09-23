"""Makes `tau2_fba` importable when pytest is run from the tau2 repo root.

Same arrangement as tau2-hermes/conftest.py: this file's directory goes on sys.path,
so `import tau2_fba` resolves without installing the package.

The prototype is located by tau2_fba/prototype_import.py (FBA_PROTOTYPE_ROOT, else the
sibling nemotron-voice-agent checkout). When it cannot be found the whole suite is
skipped with the reason, instead of failing at collection.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_fba_prototype_import", Path(__file__).parent / "tau2_fba" / "prototype_import.py"
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

try:
    _module.ensure_importable()
    collect_ignore_glob: list[str] = []
except ImportError as _e:
    print(f"\n[tau2-fba] skipping tests: {_e}\n")
    collect_ignore_glob = ["tests/*"]
