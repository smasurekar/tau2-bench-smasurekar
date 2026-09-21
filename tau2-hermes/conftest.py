"""Makes `tau2_hermes` importable when pytest is run from the tau2 repo root.

pytest's prepend import mode puts the directory of each test file's package root on
sys.path -- for `tests/test_hermes_agent.py` with no `__init__.py`, that is
`tau2-hermes/tests`, not `tau2-hermes`. The presence of this conftest adds its own
directory instead, so `import tau2_hermes` resolves without the package being
installed and without a PYTHONPATH.

Consequence, deliberate: tau2's own `pytest` at the repo root collects and runs these
tests too (they are offline and take ~0.1s; the two that need hermes-agent skip when
it is not importable). Deleting this file does not hide them -- it turns them into a
collection error. See ../misc/hermes-agent-integration.md section 6.
"""
