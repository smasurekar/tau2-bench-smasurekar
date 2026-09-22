# Runbook — Hermes Agent on τ²-bench (text → text)

How to benchmark the **Hermes agent scaffold** on τ²-bench, with **Nemotron 3 Ultra**
served from the NVIDIA Inference Hub.

Follow the steps in order. Each one ends with a check; don't move on until it passes.

- **Design and rationale:** `misc/hermes-agent-integration.md`
- **Adapter code:** `tau2-hermes/`
- **Plain-model baseline (no Hermes):** `misc/inference-hub-benchmark.md`

---

## What you are measuring

| | |
|---|---|
| **Agent** (system under test) | Hermes scaffold + `nvidia/nvidia/nemotron-3-ultra` |
| **User simulator** | `azure/openai/gpt-5.2` — the repo's recommendation, held fixed |
| **Judge** (NL assertions) | `azure/openai/gpt-5.2` — pinned in `.env`, held fixed |

**Only the agent varies.** The user simulator and judge stay constant across every run
and every arm; that is what makes two numbers comparable.

`docs/leaderboard-submission.md` recommends `gpt-5.2` as the user simulator and notes
that the choice is reported alongside leaderboard results. Do **not** use the model
under test as the user simulator: if it follows instructions imperfectly, the simulated
customer misbehaves and the score moves for reasons unrelated to agent quality.

The judge has no documented recommendation — τ²'s built-in default is
`gpt-4.1-2025-04-14` and NL assertions are marked experimental. What matters is that it
never changes between runs you intend to compare. This repo's `.env` already pins it.

A Hermes score measures **the scaffold plus the model**, not the model alone. It is not
comparable to published τ-bench leaderboard numbers, which all use the plain `llm_agent`.
To make it meaningful, run the baseline arm too (step 8).

## Models and endpoint

Everything runs on one OpenAI-compatible endpoint with one key:

| | |
|---|---|
| Base URL | `https://inference-api.nvidia.com/v1` |
| Key | your `sk-...`, read from `$OPENAI_API_KEY` — never typed on a command line |

| Role | Model at the Hub | String you pass |
|---|---|---|
| Agent | `nvidia/nvidia/nemotron-3-ultra` | `nvidia/nvidia/nemotron-3-ultra` |
| User simulator | `azure/openai/gpt-5.2` | `openai/azure/openai/gpt-5.2` |
| Judge | `azure/openai/gpt-5.2` | `openai/azure/openai/gpt-5.2` |

### Why the strings differ from the model names

**The agent goes to Hermes, which resolves the provider itself** — it takes the model
name exactly as the Hub knows it, no prefix.

**The user simulator and judge go through LiteLLM**, which reads the first path segment
as the provider name. It must be `openai/`, and LiteLLM strips it before sending:

| String | LiteLLM provider | Sent on the wire |
|---|---|---|
| `azure/openai/gpt-5.2` | `azure` ✗ | `openai/gpt-5.2` — wrong name, Azure-style URL, wants `AZURE_API_KEY` |
| `openai/azure/openai/gpt-5.2` | `openai` ✓ | `azure/openai/gpt-5.2` — correct |

So `openai/azure/openai/gpt-5.2` is not a typo and not a doubled prefix. Do not
"simplify" it to `azure/openai/gpt-5.2`; that routes to LiteLLM's Azure handler and
fails against this endpoint.

---

## Step 1 — Install both projects in one environment

τ² goes **into Hermes' environment**, not the other way around: Hermes exact-pins its
dependencies and ships no wheel.

```bash
cd ~/Desktop/Swapnil/github_repos/hermes-agent-smasurekar
uv sync
uv pip install -e ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
uv pip install websockets
uv pip install -e ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-hermes
```

**Check** — both import in one interpreter:

```bash
uv run python -c "
from run_agent import AIAgent
from tau2.registry import registry
import tau2_hermes
print('OK —', len(registry.get_domains()), 'domains')"
```

If this fails, stop and fix it. Nothing downstream can work.

> Run every later command from the **Hermes checkout** with `uv run`, so you get this
> environment. Where a command needs the τ² repo, the path is written out in full.

---

## Step 2 — Give the benchmark its own Hermes home

This keeps the sweep away from your real Hermes memory, sessions and trajectories.

```bash
mkdir -p ~/.hermes-tau2
cp ~/.hermes/.env ~/.hermes/config.yaml ~/.hermes-tau2/   # provider config only
```

Add these to your shell profile, or export them in every terminal you use:

```bash
export HERMES_HOME=~/.hermes-tau2
export HERMES_YOLO_MODE=1     # must be exported BEFORE the process starts
```

`HERMES_YOLO_MODE` is read once at import by design, so setting it from Python is too
late. No τ² tool is approval-gated; this is only a safety net against a hang.

---

## Step 3 — Configure Hermes (required, not cosmetic)

Edit `~/.hermes-tau2/config.yaml`. **Two of these change what you measure.** Skipping
them produces a low score that looks like a model result but is a harness artifact.

```yaml
tools:
  tool_search:
    enabled: off              # else every tau2 tool hides behind a search bridge

agent:
  coding_context: off         # no coding brief, no live `git status` of your cwd
  task_completion_guidance: false
  parallel_tool_call_guidance: false
  tool_use_enforcement: false
  execution_guidance: false
```

Why each one matters:

| Setting | Left at default, you get |
|---|---|
| `tool_search.enabled: off` | The model never sees a domain tool schema — it has to *search* for tools first. Burns turns, depresses the score. |
| `coding_context: off` | A coding brief and a snapshot of your git working tree injected into a customer-service agent's prompt. |
| `task_completion_guidance`, `tool_use_enforcement` | Instructions that say "never end your turn without acting" — the opposite of what τ² tasks need, where the agent must stop and ask the customer or confirm before a write. |
| `parallel_tool_call_guidance`, `execution_guidance` | Extra steering that differs per model family, so two models get different prompts and the comparison is confounded. |

---

## Step 4 — Set credentials

```bash
export OPENAI_API_KEY='sk-...'        # your Inference Hub key
```

One variable serves both sides: Hermes receives it explicitly via `--base-url`
(step 6), and LiteLLM reads it for the user simulator.

**Pin the judge separately.** τ²'s NL-assertion judge defaults to an OpenAI model. If
you ever set `OPENAI_BASE_URL` globally, the judge silently gets redirected to the
Inference Hub too. Pin it explicitly in the τ² repo's `.env`:

This repo's `.env` already does that:

```bash
# tau2-bench-smasurekar/.env  -- already set; do not change mid-experiment
TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1
# TAU2_JUDGE_API_KEY unset -> falls back to $OPENAI_API_KEY
```

If you would rather give the judge its own credential instead of sharing
`$OPENAI_API_KEY`, add `TAU2_JUDGE_API_KEY=sk-...` to that file.

Without `TAU2_JUDGE_MODEL`, τ² would default to `gpt-4.1-2025-04-14`
(`src/tau2/config.py:40`). There is no CLI flag for the judge — it is env-only.

The judge must stay **identical across every arm you compare**. A changed judge makes
two runs incomparable even if everything else matches.

---

## Step 5 — Verify before spending anything

This is the most valuable 10 seconds in the runbook. It builds the agent offline and
prints exactly what the model will receive — no API call, no cost.

```bash
cd ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-hermes
uv run python tools/inspect_hermes_surface.py airline
```

**Check the tool list** (printed first): it must be *exactly* the airline domain's
tools. Then read the system prompt below it:

| If you see | Fix |
|---|---|
| `tool_search`, `tool_describe`, `tool_call` | `tool_search.enabled: off` missing (step 3) |
| A tool that isn't a domain tool | Toolset filtering bypassed — re-check step 3 |
| A `git status` block or coding brief | `coding_context: off` missing |
| "Keep working until the task is actually complete" | `task_completion_guidance: false` missing |
| "Never end your turn with a promise of future action" | `tool_use_enforcement: false` missing |
| "# Execution discipline" / "# Parallel tool calls" | `execution_guidance` / `parallel_tool_call_guidance` still on |
| "You are Hermes Agent, built by Nous Research" | Expected — record it when you report |
| The airline policy, at the very end | Correct |

The agent also checks the tool list itself at construction and refuses to run if it's
wrong, so a misconfiguration fails loudly instead of quietly scoring low.

Run the offline tests too:

```bash
uv run pytest tests/ -q     # expect: 14 passed
```

---

## Step 6 — Smoke run (2 minutes, cheap)

The `mock` domain is tiny. This proves the whole loop turns.

```bash
cd ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-hermes

uv run python run_hermes_eval.py \
  --domain mock \
  --agent-llm 'nvidia/nvidia/nemotron-3-ultra' \
  --base-url 'https://inference-api.nvidia.com/v1' \
  --user-llm 'openai/azure/openai/gpt-5.2' \
  --user-llm-args '{"api_base": "https://inference-api.nvidia.com/v1", "temperature": 0.0}' \
  --hermes-args '{"request_overrides": {"temperature": 0.0}}' \
  --num-trials 1 \
  --save-to hermes_mock_smoke
```

**Check:** it constructs, runs and exits cleanly.

---

## Step 7 — Read the transcripts (do not skip)

A run that completes can still be completely wrong. Look at it:

```bash
cd ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
uv run tau2 view --dir data/simulations/hermes_mock_smoke
```

Three questions:

1. Are tool calls **structured calls** routed to the environment — not prose describing
   a tool call? Prose means ~0 on every action check.
2. Is the agent's final text a sensible reply to the customer?
3. Does the agent stop to ask when information is missing, rather than inventing it?

If all three look right, scale up.

---

## Step 8 — Real runs

### Task sets vs. splits — get this right or the command errors

They are two different axes, and mixing them up is a hard failure, not a silent
fallback:

| Flag | Values | Meaning |
|---|---|---|
| `--task-set-name` | `mock`, `airline`, `retail`, `telecom`, ... | Which domain's task file. |
| `--task-split-name` | `base`, `test`, `train` | A *subset* of that file. |

Passing `--task-set-name test` raises `KeyError: Task Set test not found in
registry`. `test` is a split.

For airline the splits carve up the same 50 tasks — they are not extra tasks:

| Split | Tasks | Use |
|---|---|---|
| `base` (default) | 50 | **Evaluation.** What leaderboard numbers are computed over. |
| `train` | 30 | RL experiments only |
| `test` | 20 | RL experiments only |

`AGENTS.md` is explicit: `base` is the evaluation split; `train`/`test` exist for RL.
Use `base` for anything you intend to report or compare.

### 8a. Airline, cheap go/no-go

20 tasks, one trial — a harness check, not a reportable number.

```bash
cd ~/Desktop/Swapnil/github_repos/hermes-agent-smasurekar

HERMES_HOME=$HOME/.hermes-tau2 \
HERMES_YOLO_MODE=1 \
TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2 \
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1 \
.venv/bin/python ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-hermes/run_hermes_eval.py \
  --domain airline \
  --agent-llm 'nvidia/nvidia/nemotron-3-ultra' \
  --base-url 'https://inference-api.nvidia.com/v1' \
  --user-llm 'openai/azure/openai/gpt-5.2' \
  --user-llm-args '{"api_base": "https://inference-api.nvidia.com/v1", "temperature": 0.0}' \
  --hermes-args '{"request_overrides": {"temperature": 0.0}}' \
  --task-set-name airline \
  --task-split-name test \
  --num-trials 1 \
  --save-to hermes_nemotron3ultra_airline_gonogo
```

A near-zero here is a harness bug, not a model result. Go back to step 5.

### 8b. The reportable run — pass^1 through pass^4

**`--num-trials 4` is what produces pass^4.** Change nothing else: same 50 `base`
tasks, run 4 times each = 200 simulations.

```bash
cd ~/Desktop/Swapnil/github_repos/hermes-agent-smasurekar

HERMES_HOME=$HOME/.hermes-tau2 \
HERMES_YOLO_MODE=1 \
TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2 \
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1 \
.venv/bin/python ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-hermes/run_hermes_eval.py \
  --domain airline \
  --agent-llm 'nvidia/nvidia/nemotron-3-ultra' \
  --base-url 'https://inference-api.nvidia.com/v1' \
  --user-llm 'openai/azure/openai/gpt-5.2' \
  --user-llm-args '{"api_base": "https://inference-api.nvidia.com/v1", "temperature": 0.0}' \
  --hermes-args '{"request_overrides": {"temperature": 0.0}}' \
  --task-set-name airline \
  --num-trials 4 \
  --max-concurrency 4 \
  --save-to hermes_nemotron3ultra_airline_base_4trials
```

The summary table then prints `Pass^1` … `Pass^4` instead of `Pass^1` alone.

#### How to read pass^k

pass^k = `C(successes, k) / C(trials, k)` (`src/tau2/metrics/agent_metrics.py:113`)
— the probability that *k* randomly drawn trials of a task **all** succeeded. It
measures **reliability**, not average quality:

- **pass^1** — equals average reward. Most stable number.
- **pass^4** — all-or-nothing per task: a task counts only if all 4 trials passed.
  Coarse and high-variance on 4 trials; read it as "how often is this agent
  *consistently* right", not as a headline score.

Two rules that bite:

- **`k` may never exceed `--num-trials`.** `pass_hat_k` raises
  `ValueError: Number of trials 1 is less than k 4` — pass^4 is *undefined* on a
  1-trial run, not zero. You cannot recover it afterwards; you must re-run.
- **Use a fresh `--save-to` when changing trial count.** τ² *resumes* into an
  existing results directory, and `get_metrics_df` requires every simulation to
  have the same trial count. Reusing a 1-trial directory for a 4-trial run mixes
  them and corrupts the pass^k math.

At `temperature 0.0` the 4 trials differ only through endpoint nondeterminism and
the user simulator, not sampling diversity — so pass^k here measures residual
flakiness rather than sampling spread. Still meaningful; just know what it captures.

Watch memory and that the process exits cleanly.

### 8c. Other domains — **separate commands, not a loop**

Hermes' tool registry is process-global, so one process serves exactly one domain.

**Always pass `--task-split-name base` explicitly.** The default split is *not*
uniform across domains — verified by calling `load_tasks()` directly:

| Command | Tasks you actually get |
|---|---|
| `--task-set-name airline` | 50 = `base` ✓ |
| `--task-set-name retail` | 114 = `base` ✓ |
| `--task-set-name telecom` | **2285 = the FULL set**, not `base` |
| `--task-set-name telecom --task-split-name base` | 114 ✓ |

Telecom's loader returns the full set when no split is given. At 4 trials that is
**9,140 simulations (~30 h)** instead of 456. Nothing warns you: the banner just
reads `Tasks: All`, and the first clue is the status line counting to 9140.

Check the status line in the first minute of every run — `Status: N/<total>` must
match the task count you expect.

```bash
  --domain retail  --task-set-name retail  --task-split-name base \
    --num-trials 4 --save-to hermes_nemotron3ultra_retail_base_4trials
  --domain telecom --task-set-name telecom --task-split-name base \
    --num-trials 4 --save-to hermes_nemotron3ultra_telecom_base_4trials
```

### Which domains actually use the judge

Only retail invokes the NL-assertion judge. Confirmed from each run's
`reward_breakdown`:

| Domain | Tasks with `nl_assertions` | `NL_ASSERTION` scored? |
|---|---|---|
| airline | 50/50 | **No** — DB + COMMUNICATE only |
| retail | 40/114 | **Yes** |
| telecom | 0/114 | **No** — ENV_ASSERTION |

So `TAU2_JUDGE_MODEL` is load-bearing for retail and inert for airline/telecom.
Report the judge only where it was actually used; carrying it across all three rows
implies a scoring component that airline and telecom never had.

### 8d. The baseline arm — same model, no Hermes

This is what makes the Hermes number mean something. Run it from the **τ² repo**:

```bash
cd ~/Desktop/Swapnil/github_repos/tau2-bench-smasurekar

uv run tau2 run \
  --domain airline \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3-ultra' \
  --agent-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1"}' \
  --user-llm 'openai/azure/openai/gpt-5.2' \
  --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1"}' \
  --task-set-name airline \
  --num-trials 4 \
  --save-to baseline_nemotron3ultra_airline_base_4trials
```

Same model, same domain, same tasks, same judge. **The difference between the two arms
is the scaffold's contribution — that is your headline result.**

---

## All flags

`run_hermes_eval.py`, in full:

| Flag | Default | What it does |
|---|---|---|
| `--domain` | `airline` | τ² domain. One per command. |
| `--agent-llm` | *required* | Model for Hermes. **No LiteLLM prefix.** |
| `--user-llm` | *required* | Model for the user simulator. **Needs the `openai/` prefix.** |
| `--base-url` | none | OpenAI-compatible endpoint for the agent model. |
| `--api-key-env` | `OPENAI_API_KEY` | Name of the env var holding the key. The key is never taken on the command line. |
| `--hermes-args` | `{}` | JSON passed to Hermes, e.g. `{"request_overrides": {"temperature": 0.0}}`. |
| `--user-llm-args` | `{}` | JSON for the user simulator, e.g. `{"api_base": "...", "temperature": 0.0}`. |
| `--max-iterations` | `30` | Cap on Hermes' internal tool loop per τ² turn. Raise only with a reason — an uncapped loop can burn a whole task's budget in one turn. |
| `--num-trials` | `1` | Repeats per task. **Set to 4 to get pass^1–pass^4**; `k` may never exceed this. |
| `--task-set-name` | none | The domain's task file: `airline`, `retail`, ... Not a split — `test` here is a `KeyError`. |
| `--task-split-name` | none | `base` (evaluation default), or `test`/`train` for RL experiments. |
| `--num-tasks` | none | Cap the number of tasks, for a quick partial run. |
| `--max-concurrency` | `1` | Parallel simulations. |
| `--save-to` | `hermes_<domain>` | Results directory under `data/simulations/`. |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `hermes-agent is not importable` | τ² installed into its own venv instead of Hermes' | Redo step 1 |
| `Hermes' tool surface does not match...` naming `tool_search` | Step 3 not applied, or `HERMES_HOME` not exported | Fix config, re-run step 5 |
| `--base-url was given but $OPENAI_API_KEY is empty` | Key not exported in this shell | `export OPENAI_API_KEY='sk-...'` |
| `LLM Provider NOT provided` / Azure auth error on the user sim or judge | Model string missing the `openai/` prefix | Use `openai/azure/openai/gpt-5.2`, not `azure/openai/gpt-5.2` |
| Score is ~0 and transcripts show prose, not tool calls | The model isn't emitting structured tool calls against this endpoint | Check the endpoint supports OpenAI tool calling; compare with the baseline arm |
| Agent acts without confirming with the customer | Guidance blocks still on | Re-check step 3, verify with step 5 |
| `cost` reported as 0.0 | No price table for this model | Expected, harmless |
| Run hangs on one task | A tool call never returned | Agent times out and unwinds on its own; check logs for `did not answer` |
| Second domain in the same process fails | Hermes' registry is process-global | One command per domain (8c) |
| Run is counting to 9140, not 456 | `--task-split-name base` omitted on telecom | Telecom defaults to the full 2285-task set; pass the split explicitly (8c) |
| `AzureException: messages must contain the word 'json'` | Judge sends `response_format=json_object`, which this gateway rejects **regardless of prompt wording** | Set `TAU2_JUDGE_JSON_MODE=0`; the fence-stripping parser handles plain JSON |
| pass^k looks fine but `N` < simulations run | Judge failures became `infrastructure_error`, which `get_metrics_df` silently filters out | Watch the `infrastructure_error` count, not just the score |
| `KeyError: Task Set test not found in registry` | `test` is a split, not a task set | `--task-set-name airline --task-split-name test` |
| `ValueError: Number of trials 1 is less than k 4` | pass^4 requested from a 1-trial run | Re-run with `--num-trials 4`; it cannot be recovered after the fact |
| pass^k numbers look wrong / trial-count mismatch | A 4-trial run resumed into a 1-trial results dir | Use a fresh `--save-to` whenever the trial count changes |

---

## What to record when you report

- Both arms: Hermes and baseline, same model / domains / trials / judge.
- Model string, endpoint, `temperature`, `max_iterations` (default 30).
- Hermes version, τ² version (1.0.1), and whether `seed` was honoured by the endpoint.
- **The `~/.hermes-tau2/config.yaml` you used.** Hermes builds its prompt from
  config-gated blocks, so "which model" does not identify a run. Archive the file next
  to the results.
- That Hermes' own identity prompt still sits above the domain policy.

State plainly that the Hermes number is **scaffold + model**, and is not comparable to
published τ-bench leaderboard figures.
