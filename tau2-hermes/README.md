# tau2-hermes

Runs the [Hermes agent](https://github.com/NousResearch/hermes-agent) scaffold as a
[τ²-bench](https://github.com/sierra-research/tau2-bench) half-duplex agent.

**τ²-bench is not modified.** This package registers an agent factory at runtime and
calls τ²'s own batch runner. τ² stays the measuring instrument, byte-for-byte.

- **Run it:** [`../misc/hermes-agent-runbook.md`](../misc/hermes-agent-runbook.md) — step by step, including the NVIDIA Inference Hub / Nemotron 3 Ultra setup.
- **Why it works this way:** [`../misc/hermes-agent-integration.md`](../misc/hermes-agent-integration.md).

This package lives inside the τ²-bench checkout so it is versioned alongside the runs
it produces, but it is **additive only**: nothing under `src/tau2/` is imported by τ²
from here, and no τ² file is edited. The agent factory is registered at runtime, so
`git diff upstream/main -- src/ tests/` stays empty.

## How it works

Both systems want to own the tool-execution loop. τ² is a state machine — the agent
*returns* a tool call and is re-entered with the result. Hermes is a blocking loop that
calls its own handlers.

So Hermes runs on a worker thread, and each τ² domain tool is registered into Hermes'
tool registry with a handler that executes nothing: it hands the call out to the τ²
orchestrator and blocks until the matching `ToolMessage` comes back. Hermes emits τ²'s
tools as real tool calls; τ²'s environment executes them; Hermes never learns its tools
are remote. Results correlate by `call_id`, never by queue order.

## Install

Install τ² **into Hermes' environment**, not the reverse — Hermes exact-pins its deps and
ships no wheel:

```bash
cd /path/to/hermes-agent
uv sync
uv pip install -e /path/to/tau2-bench
uv pip install websockets
uv pip install -e /path/to/tau2-bench/tau2-hermes
```

## Configure

Give the benchmark a dedicated Hermes home so a sweep never touches your real Hermes
memory, sessions or trajectories:

```bash
mkdir -p ~/.hermes-tau2
cp ~/.hermes/.env ~/.hermes/config.yaml ~/.hermes-tau2/
export HERMES_HOME=~/.hermes-tau2
export HERMES_YOLO_MODE=1     # must be exported before the process starts
```

Then edit `~/.hermes-tau2/config.yaml`. **These are required, not cosmetic** — two of
them change what is measured:

```yaml
tools:
  tool_search:
    enabled: off              # else every tau2 tool hides behind the search bridge
agent:
  coding_context: off         # no coding brief, no live `git status` of your cwd
  task_completion_guidance: false
  parallel_tool_call_guidance: false
  tool_use_enforcement: false
  execution_guidance: false
```

## Verify before spending

```bash
uv run python tools/inspect_hermes_surface.py airline
```

Prints the exact tool list and system prompt the model will receive. The tool list must
be exactly the domain's tools; `tool_search`/`tool_describe`/`tool_call` in it means the
config above is missing. The agent also asserts this at construction, so a
misconfiguration fails loudly instead of producing a quietly depressed score.

## Run

```bash
uv run python run_hermes_eval.py --domain airline \
    --agent-llm 'anthropic/claude-sonnet-4.6' \
    --user-llm 'openai/gpt-4.1' \
    --task-set-name test
```

**One domain per process** — Hermes' tool registry is process-global. Sweep several
domains with several invocations, not a loop inside one.

## Test

```bash
uv run pytest        # or: PYTHONPATH=. python -m pytest
```

Bridge semantics are covered offline — no network, no LLM. Tests that genuinely need
Hermes importable skip when it is not.

## Reporting

A `hermes_agent` number measures **Hermes-the-scaffold plus the model**, not the model,
and is not comparable to published τ-bench leaderboard numbers (which all use
`llm_agent`). Run both arms with the same model, domains and trials; the difference is
the scaffold's contribution. Record the `config.yaml` used — Hermes assembles its prompt
from model-gated blocks, so "which model" does not identify a run.
