# Runbook: Evaluating a Custom Agent

**Status:** operational runbook · **Date:** 2026-09-18 · **Repo:** `tau2-bench-smasurekar` @ `caca045` (v1.0.1)

**Scope.** How to actually *run* an evaluation once a custom agent is integrated: pre-flight
checks, phased bring-up with explicit go/no-go gates, reading the results, archiving them,
resuming a broken run, scaling throughput, and reporting.

> **Where runs live.** `tau2 run` writes to `data/simulations/<save-to>/` — that is scratch.
> Every run worth keeping is archived to
> **`/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-runs/`**,
> one self-contained folder per run. See **§8** for the layout, naming, and capture script, and
> `tau2-runs/README.md` for the runs already recorded there.

**Prerequisite.** The agent is implemented and registered. See
[`misc/custom-agent-integration.md`](custom-agent-integration.md) for that. This runbook assumes
`--agent my_agent` resolves.

Throughout: replace `my_agent` with your registered name and `openai/<your-model>` with your
model string.

---

## 0. Pre-flight

Run all five. Each takes seconds and each prevents a class of wasted sweep.

```bash
# 0.1 -- CLI starts at all
tau2 --version
```

> Known repo breakage: `uv sync` core-only yields `ModuleNotFoundError: No module named
> 'websockets'` because `src/tau2/data_model/simulation.py:62` unconditionally imports the
> OpenAI Live voice config. Fix: `uv pip install websockets` (or `uv sync --extra voice`).

```bash
# 0.2 -- your agent is visible to the CLI
tau2 run --help | grep -A3 -- '--agent '
```

`my_agent` must appear in the `choices`. If it does not, you registered outside the package —
see §6 of the integration doc. **This is the #1 cause of "the CLI says invalid choice".**

```bash
# 0.3 -- task data is present
tau2 check-data

# 0.4 -- secrets are in the environment, not on disk
env | grep -c OPENAI_API_KEY        # expect >= 1
grep -rn "sk-" src/tau2/config.py   # expect no output
```

```bash
# 0.5 -- endpoint reachable with your exact model string
curl -s "$OPENAI_BASE_URL/models" -H "Authorization: Bearer $OPENAI_API_KEY" | head -c 400
```

**Record before starting:** `git rev-parse --short HEAD`, `git status --short`, the model
string, and the endpoint. These go in the run log (§12). The commit is also written into
`results.json` under `info.git_commit`, so results stay traceable.

---

## 1. Configuration reference

The flags that determine what your number means. Defaults are from `src/tau2/config.py`.

### Identity — what is under test

| Flag | Default | Notes |
|------|---------|-------|
| `--agent` | `llm_agent` | Your registered name. **The custom-agent switch.** |
| `--agent-llm` | `gpt-4.1-2025-04-14` | Model inside the agent. |
| `--agent-llm-args` | `{"temperature": 0.0}` | JSON. Carries `api_base` / `api_key` for a custom endpoint. |

### Apparatus — hold these FIXED across every arm you compare

| Flag | Default | Notes |
|------|---------|-------|
| `--user-llm` | `gpt-4.1-2025-04-14` | User simulator. **Not** under test. |
| `--user-llm-args` | `{"temperature": 0.0}` | |
| *(judge)* | `TAU2_JUDGE_MODEL` env, else `gpt-4.1-2025-04-14` | No CLI flag. Only used when `NL_ASSERTION` is in a task's `reward_basis`. |

Changing any apparatus value between arms invalidates the comparison.

### Scope

| Flag | Default | Notes |
|------|---------|-------|
| `--domain` | — | `mock` · `airline` · `retail` · `telecom` · `banking_knowledge` |
| `--task-split-name` | `base` | `base` is the eval split. Also `train`, `test`, `full`. |
| `--num-tasks` | all | Truncate — smoke tests only. |
| `--task-ids` | all | Re-run specific failures. |
| `--num-trials` | `1` | **Use 4 for anything reportable** — `pass^k` needs k ≤ trials. |

### Execution

| Flag | Default | Notes |
|------|---------|-------|
| `--max-concurrency` | `3` | Raise it; 3 is very conservative. |
| `--max-steps` | `200` | Turn cap per simulation. |
| `--max-errors` | `10` | Consecutive tool errors before abort. |
| `--seed` | `300` | Keep fixed for reproducibility. |
| `--max-retries` | `3` | Retries for failed tasks. |
| `--timeout` | none | Wallclock cap per simulation, seconds. |
| `--auto-resume` | off | **Always set for long runs.** Resumes without prompting. |
| `--save-to` | timestamped | Names the output dir. Set it explicitly. |
| `--log-level` | `ERROR` | `INFO` while debugging. |
| `--verbose-logs` | off | Writes per-task LLM call logs to `artifacts/`. Costs disk. |
| `--enforce-communication-protocol` | off | Turns protocol violations into hard errors. Useful in bring-up. |

---

## 2. Phase 1 — Smoke test

**Goal:** the agent constructs, the endpoint answers, one task completes. **Cost:** cents.

```bash
tau2 run \
  --domain mock \
  --agent my_agent \
  --agent-llm 'openai/<your-model>' \
  --agent-llm-args '{"temperature": 0.0}' \
  --user-llm 'openai/<user-sim-model>' \
  --num-tasks 2 --num-trials 1 \
  --max-concurrency 2 \
  --log-level INFO \
  --verbose-logs \
  --save-to smoke_my_agent
```

**Pass:** two simulations complete, no traceback, `results.json` written.

**Abort and fix if you see:**

| Symptom | Cause |
|---------|-------|
| `TypeError: unexpected keyword argument 'audio_native_config'` | Factory missing `**kwargs` |
| `argparse: invalid choice: 'my_agent'` | Not registered in-package (§0.2) |
| 401 / 404 from the endpoint | Wrong `api_base`, key, or model string |
| All sims end `MAX_STEPS` | Agent never terminates — go to Phase 2, read the transcript |

> **Baseline comparison.** Run the same command with `--agent llm_agent` and nothing else
> changed. If the default agent works and yours does not, the fault is in your agent, not the
> model or the endpoint. This one-line diff localizes almost every bring-up failure.

---

## 3. Phase 2 — Transcript verification ⚠ MANDATORY GATE

**Goal:** confirm tool calls became *structured actions*, not text. **This is the only step
that catches the failure where everything "runs" and the score is quietly zero.** Do not skip
it, and do not automate it — read the transcript with your eyes, once.

```bash
tau2 view --file data/simulations/smoke_my_agent/results.json
```

Check all four:

1. **Tool calls render as structured calls**, not as raw text in an assistant message. If you
   see your tool-call syntax quoted verbatim in a message that went to the user simulator, the
   agent is emitting text where it should emit `ToolCall` objects — the orchestrator routes on
   `msg.is_tool_call()`, so this is a routing failure, not a cosmetic one.
2. **Tool results come back** and the next agent turn reflects them. Repeated identical calls
   means `MultiToolMessage` is not being unpacked.
3. **No message carries both text and tool calls.**
4. **Termination is `USER_STOP` or `AGENT_STOP`**, not `MAX_STEPS` on every task.

Machine check of the same thing:

```bash
python3 -c "
import json
d=json.load(open('data/simulations/smoke_my_agent/results.json'))
n=sum(1 for s in d['simulations'] for m in s['messages'] if m.get('tool_calls'))
print('structured tool calls:', n)
print('terminations:', {s['termination_reason'] for s in d['simulations']})
"
```

`structured tool calls: 0` means **stop here and fix the agent.** Every downstream number
would be meaningless.

---

## 4. Phase 3 — Go/no-go on a real domain

**Goal:** first signal on a real policy, on the held-out split. ~20 tasks, single trial.

```bash
tau2 run \
  --domain airline --task-split-name test \
  --agent my_agent \
  --agent-llm 'openai/<your-model>' \
  --agent-llm-args '{"temperature": 0.0}' \
  --user-llm 'openai/<user-sim-model>' \
  --num-trials 1 \
  --max-concurrency 8 --max-retries 3 --auto-resume \
  --save-to airline_test_my_agent
```

**Gate:** a non-trivial pass rate. Near zero is a scaffold bug, not a model result — return to
Phase 2 and inspect failures:

```bash
tau2 view --file data/simulations/airline_test_my_agent/results.json --only-show-failed
```

Use the `test` split here deliberately: it keeps `base` clean for the reportable run.

---

## 5. Phase 4 — First reportable number

**Goal:** a defensible result. `airline`, `base` split, 50 tasks × 4 trials = 200 simulations.
Judge-independent (airline's `reward_basis` is `[DB, COMMUNICATE]` — no LLM judge involved), so
this isolates agent quality from judge noise.

```bash
tau2 run \
  --domain airline \
  --agent my_agent \
  --agent-llm 'openai/<your-model>' \
  --agent-llm-args '{"temperature": 0.0}' \
  --user-llm 'openai/<user-sim-model>' \
  --num-trials 4 \
  --max-concurrency 8 --max-retries 3 --auto-resume \
  --seed 300 \
  --save-to airline_base_my_agent
```

`--num-trials 4` is what makes `pass^1..4` available. Trials are the headline metric's
denominator — a single-trial run cannot produce `pass^4`.

---

## 6. Phase 5 — Full sweep

```bash
MODEL='openai/<your-model>'
USER_LLM='openai/<user-sim-model>'
STAMP=$(date +%Y%m%d-%H%M%S)

for DOMAIN in airline retail telecom; do
  echo "=== $DOMAIN ==="
  tau2 run \
    --domain "$DOMAIN" \
    --agent my_agent \
    --agent-llm "$MODEL" \
    --agent-llm-args '{"temperature": 0.0}' \
    --user-llm "$USER_LLM" \
    --num-trials 4 \
    --max-concurrency 8 --max-retries 3 --auto-resume \
    --seed 300 \
    --save-to "${DOMAIN}_base_my_agent_${STAMP}" \
    || echo "FAILED: $DOMAIN"
done
```

| Domain | Tasks | × 4 trials | Notes |
|--------|-------|-----------|-------|
| `airline` | 50 | 200 | Judge-independent |
| `retail` | 114 | 456 | Largest single-control domain |
| `telecom` | 114 | 456 | **Dual control** — user also has tools; agent must talk them through steps |
| `banking_knowledge` | 97 | 388 | Optional; needs `--extra knowledge`, uses the LLM judge |

`telecom` stresses a different capability than the other two — the agent cannot act alone and
must instruct the user. Expect lower scores there; that is the domain working as designed.

Run the loop under `nohup`/`tmux`. With `--auto-resume` and a stable `--save-to`, re-running the
identical command after any interruption picks up where it stopped.

---

## 7. Reading the results

### Layout

`tau2 run` writes here — **scratch, transient, overwritten**:

```
data/simulations/<save-to>/
├── results.json          # everything: config, tasks, all simulations, rewards
└── artifacts/            # only with --verbose-logs
    ├── task_0/ ...
```

Anything worth keeping is then archived to `tau2-runs/<stamp>_<domain-split>_<agent>/` (§8).
Read results from either location; cite only the archived copy.

`results.json` top-level keys: `timestamp`, `info`, `tasks`, `simulations`, `simulation_index`.

- `info` — `git_commit`, `num_trials`, `max_steps`, `seed`, `agent_info`, `user_info`,
  `environment_info`. **This is your provenance record.** Check `agent_info` to confirm the run
  used the agent and model you think it did.
- `simulations[]` — per run: `task_id`, `trial`, `termination_reason`, `agent_cost`,
  `duration`, `messages`, `reward_info`.
- `reward_info` — `reward`, plus per-component detail: `db_check`, `env_assertions`,
  `action_checks`, `nl_assertions`, `communicate_checks`, `reward_basis`, `reward_breakdown`.

### Metrics

```bash
tau2 evaluate-trajs data/simulations/airline_base_my_agent/results.json
```

Reports `avg_reward`, `pass_hat_k` for k = 1..num_trials, `avg_agent_cost`, termination
breakdown, DB match/mismatch counts, and read/write action correctness.

**How reward works** (`docs/evaluation.md`): final reward is the **product** of the components
in each task's `reward_basis`. Default for airline/retail/telecom is `[DB, COMMUNICATE]` — so
reward is 1.0 only if the DB end state matches *and* every required string was communicated.
Partial credit does not exist at the task level.

**`pass^k`** is the headline metric: the probability that all of k independent trials succeed.
It punishes inconsistency, which is the point — `pass^1` = 0.6 with `pass^4` = 0.1 describes an
agent that is unreliable, not one that is 60% good.

**`actions` is not a required trajectory.** It is one reference solution, replayed on a fresh
environment to derive the *target DB state*. Any path reaching an equivalent end state passes.
Only when `ACTION` is in `reward_basis` (a few `banking_knowledge` tasks; never in
airline/retail/telecom) must the agent match the listed calls. Do not debug against `actions`
as if it were a rubric.

### Triage

```bash
# failures in every trial -- systematic, most informative
tau2 view --file <results.json> --only-show-all-failed

# re-run specific tasks after a fix
tau2 run ... --task-ids task_17 task_42 --save-to debug_my_agent

# optional: LLM review of what went wrong (extra cost)
tau2 review data/simulations/airline_base_my_agent/results.json --mode full --show-details
```

`--only-show-all-failed` first. A task failing 4/4 is a reproducible defect; a task failing 1/4
is usually variance.

### Re-grading without re-running

```bash
tau2 evaluate-trajs 'data/simulations/*_my_agent*/results.json' --fresh-tasks -o regraded/
```

`--fresh-tasks` re-grades against current task definitions instead of those embedded in the
results file — this is how v1.0.1's `banking_knowledge` fixes get applied to older runs.

---

## 8. Archiving the run → `tau2-runs/`

`tau2 run` writes to `data/simulations/<save-to>/`, which is scratch space: it is transient,
untracked, and gets overwritten. **Every run you intend to keep, cite, or report must be
archived to `tau2-runs/`.**

```
/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar/tau2-runs/
```

This is the established convention in this repo — see `tau2-runs/README.md` and the three
existing nemotron runs. Follow it for custom-agent runs too, so results stay comparable.

### Folder naming

```
tau2-runs/<YYYYMMDD-HHMMSS>_<domain-split>_<agent-or-model>/
```

Local time, run **start**. Examples:

```
20260910-141133_airline-base_nemotron-3.5-lightning     # existing
20260918-093000_airline-base_my-agent                   # custom agent
```

### Folder contents — self-contained

Each folder must stand alone: readable and archivable without the repo around it.

```
<run-folder>/
├── README.md              # what was run, headline numbers, interpretation
├── command.sh             # exact command, with a comment explaining why this run exists
├── console.log            # full stdout/stderr
├── timing.txt             # start_utc, end_utc, exit_code
├── env/
│   ├── environment.txt    # git commit, branch, python/uv versions, endpoint
│   ├── local-changes.diff # uncommitted diff at run time
│   └── pip-freeze.txt     # resolved dependency versions
├── results/
│   └── results.json       # verbatim copy of data/simulations/<save-to>/results.json
├── traces/
│   └── artifacts/         # verbatim copy of artifacts/ (--verbose-logs)
└── metrics/
    ├── summary.json       # headline: avg_reward, pass^k, cost, wall clock
    ├── metrics.json       # full AgentMetrics dump
    ├── per_simulation.json
    └── per_task_pass_hat_k.csv
```

### Rules

- **Verbatim copies only.** `results.json` and `artifacts/` are copied unmodified. Derived
  metrics are read-only additions. No run is filtered, re-scored, or edited in place — if you
  need a re-grade, write it to a new folder and say so in its README.
- **`--verbose-logs` on every archived run**, so `traces/` is populated.
- **`--seed 300`** (repo default) unless you are deliberately varying it.
- **Never pass API keys on the command line** — `command.sh` is committed, and a key on the
  command line ends up in `console.log` too. Let LiteLLM fall back to `OPENAI_API_KEY` from the
  environment.
- **Scan for secrets before finalising** (below).
- **Update `tau2-runs/README.md`** — add a row to the run table with domain/split, tasks ×
  trials, pass^1, wall clock, and whether the run is reportable.

### Capture the run and archive it

Wrap the run so the log, timing, and environment are captured as it happens — reconstructing
them afterwards is unreliable.

```bash
REPO=/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
STAMP=$(date +%Y%m%d-%H%M%S)
RUN="${STAMP}_airline-base_my-agent"
SAVE="airline_base_my_agent_${STAMP}"
DEST="$REPO/tau2-runs/$RUN"

mkdir -p "$DEST"/{env,results,traces,metrics}

# 1. environment snapshot, before the run
{
  echo "run:        $RUN"
  echo "commit:     $(git -C $REPO rev-parse HEAD)"
  echo "branch:     $(git -C $REPO rev-parse --abbrev-ref HEAD)"
  echo "python:     $(python3 --version)"
  echo "uv:         $(uv --version)"
  echo "endpoint:   $OPENAI_BASE_URL"
  echo "agent:      my_agent"
  echo "model:      openai/<your-model>"
} > "$DEST/env/environment.txt"
git -C $REPO diff > "$DEST/env/local-changes.diff"
uv pip freeze > "$DEST/env/pip-freeze.txt"

# 2. the command itself, recorded before execution
cat > "$DEST/command.sh" <<'CMD'
#!/usr/bin/env bash
# Phase 4 of misc/custom-agent-eval-runbook.md
# airline 'base' split - 50 tasks x 4 trials = 200 simulations.
# First reportable number; judge-independent (DB + COMMUNICATE only).
# API key is NOT passed on the command line: LiteLLM falls back to OPENAI_API_KEY.
uv run tau2 run \
  --domain airline \
  --agent my_agent \
  --agent-llm 'openai/<your-model>' \
  --agent-llm-args '{"temperature": 0.0, "api_base": "<endpoint>"}' \
  --user-llm 'openai/<user-sim-model>' \
  --num-trials 4 \
  --max-concurrency 8 \
  --max-retries 3 \
  --seed 300 \
  --auto-resume \
  --verbose-logs \
  --save-to SAVE_PLACEHOLDER
CMD
sed -i "s/SAVE_PLACEHOLDER/$SAVE/" "$DEST/command.sh"
chmod +x "$DEST/command.sh"

# 3. run it, capturing console + timing
echo "start_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DEST/timing.txt"
bash "$DEST/command.sh" 2>&1 | tee "$DEST/console.log"
echo "exit_code: ${PIPESTATUS[0]}" >> "$DEST/timing.txt"
echo "end_utc:   $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$DEST/timing.txt"

# 4. archive results and traces verbatim
cp "$REPO/data/simulations/$SAVE/results.json" "$DEST/results/"
cp -r "$REPO/data/simulations/$SAVE/artifacts"  "$DEST/traces/" 2>/dev/null || true

# 5. derived metrics
uv run tau2 evaluate-trajs "$DEST/results/results.json" | tee "$DEST/metrics/summary.txt"
```

### Secret scan before finalising

Non-negotiable, because these folders get committed:

```bash
grep -rIn "sk-[A-Za-z0-9_-]\{12,\}\|Bearer [A-Za-z0-9._-]\{20,\}" "$DEST" && \
  echo "!! SECRET FOUND -- do not commit" || echo "clean"
```

### Run README

Each folder's `README.md` should answer, in order: what was run and why, the headline numbers
(pass^1 and pass^4), whether it is reportable, and what the failures were concentrated in. The
existing `20260910-141133_airline-base_nemotron-3.5-lightning/README.md` is a good model — note
how it moves past the score to characterise the failure mode ("DB fails in 58 of 60 failed
simulations; write actions 48.0% vs read actions 79.7%"), which is what makes a run useful six
months later.

For a custom agent, also state explicitly in the README: **the agent implementation, and
whether a `--agent llm_agent` baseline arm exists for the same model.** Without that line, a
future reader cannot tell whether the number measures the model or the scaffold.

### Storage

Budget ~50 MB per `airline base` run with `--verbose-logs`, and more for reasoning models whose
`reasoning_content` is preserved in `raw_data` (the nemotron airline run came to 52 MB against
an 8–11 MB estimate). `retail` and `telecom` are roughly 2× the task count. Check free space
before a full sweep.

---
## 9. Interruptions and recovery

**Resume:** re-run the *identical* command. `--auto-resume` detects the existing `--save-to`
directory and completes only missing simulations.

**Never** change `--agent-llm`, `--user-llm`, `--seed`, or `--num-trials` when resuming into an
existing directory — you would silently mix configurations inside one results file, and nothing
will warn you.

| Situation | Action |
|-----------|--------|
| Rate limits / 429s | Lower `--max-concurrency`, raise `--max-retries`, resume |
| Endpoint down mid-run | Fix, re-run identical command |
| Config was wrong | New `--save-to`. Do not resume into it. |
| Agent code changed | New `--save-to`. Mixed-code results are not interpretable. |
| A few tasks stuck | `--timeout 600` and resume |

Check what actually completed:

```bash
python3 -c "
import json,collections
d=json.load(open('data/simulations/airline_base_my_agent/results.json'))
print('sims:', len(d['simulations']), '| expected:', len(d['tasks'])*d['info']['num_trials'])
print(collections.Counter(s['termination_reason'] for s in d['simulations']))
"
```

---

## 10. Scaling

Default `--max-concurrency` is 3 — conservative. Tuning order:

1. **Raise `--max-concurrency`** (8–16) until you hit provider rate limits. Single process,
   `ThreadPoolExecutor`; simplest and usually sufficient.
2. **`--workers N`** when past the single-process ceiling. This process becomes a controller
   scheduling N worker processes, with `N × --max-concurrency` in flight.
3. **`--provider-limit "openai=40"`** caps per-provider concurrency in controller mode. Requires
   `--workers`.

Disk: `--verbose-logs` writes per-task LLM logs and grows fast. `--llm-log-mode latest` (the
default) keeps only the most recent call of each type. Prefer `--verbose-logs` in Phases 1–3
and drop it for full sweeps unless you need the traces.

---

## 11. Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `invalid choice: 'my_agent'` | Registered outside the package | Register in `src/tau2/registry.py` |
| `TypeError: ... 'audio_native_config'` | Factory lacks `**kwargs` | Fix the factory signature |
| `ModuleNotFoundError: websockets` | Core-only `uv sync` | `uv pip install websockets` |
| Reward 0 everywhere, runs look fine | Tool calls emitted as text | Phase 2 gate; fix parsing |
| All sims `MAX_STEPS` | Agent never terminates | Read a transcript; check stop handling |
| `AGENT_ERROR` terminations | Message had both text and tool calls, or was empty | `content=None` when `tool_calls` is set |
| Agent repeats the same call | `MultiToolMessage` not unpacked | Recurse over `.tool_messages` |
| `avg_agent_cost` is 0 | Bypassed `generate()` | Use `tau2.utils.llm_utils.generate` |
| `pass_hat_4` missing | Ran with `--num-trials 1` | Re-run with 4 |
| Scores shifted after a rebase | Task definitions changed | Compare `info.git_commit`; re-grade with `--fresh-tasks` |

---

## 12. Reporting

### Checklist

- [ ] `--num-trials 4`, report `pass^1` and `pass^4`
- [ ] Same `--seed` across all arms
- [ ] `--user-llm` and judge identical across all arms
- [ ] `info.git_commit` recorded; note tau2-bench version (< 1.0.1 results are **not**
      comparable with >= 1.0.1 on `banking_knowledge`)
- [ ] Agent implementation named alongside the model
- [ ] Baseline arm included where possible: `--agent llm_agent`, same model, same everything
- [ ] Run archived to `tau2-runs/` with `command.sh`, `console.log`, `env/`, `results/`,
      `traces/`, `metrics/` (§8), scanned for secrets, and `tau2-runs/README.md` updated

### State the comparability caveat explicitly

Published leaderboard numbers use the default `llm_agent`. A custom-agent score measures
**model + scaffold**, not the model. Running both arms isolates the scaffold's contribution and
is what makes the result interpretable to anyone outside your team.

```bash
tau2 leaderboard --domain airline --metric pass_1   # published reference points
```

### Run log template

```
Date:            2026-09-__
Commit:          <git rev-parse --short HEAD>    Dirty: <yes/no>
Agent:           my_agent            Model: openai/<model>
Endpoint:        <base url>
User sim:        openai/<model>      Judge: <TAU2_JUDGE_MODEL or default>
Seed / trials:   300 / 4
Domains:         airline, retail, telecom (base split)
Save dirs:       data/simulations/<...>
Archived to:     tau2-runs/<YYYYMMDD-HHMMSS>_<domain-split>_my-agent/
Results:         airline pass^1 __ / pass^4 __ | retail __ / __ | telecom __ / __
Baseline arm:    llm_agent, same model -> pass^1 __ / pass^4 __
Anomalies:       <rate limits, retries, excluded tasks>
```

### Leaderboard submission

```bash
tau2 submit prepare ...
tau2 submit validate ...
tau2 submit verify ...
```

See `docs/leaderboard-submission.md`. Note that a custom-agent result is a different category
from a default-agent model result — do not submit one as the other.

---

## 13. References

| What | Where |
|------|-------|
| Integrating the agent | `misc/custom-agent-integration.md` |
| Run archive + conventions | `tau2-runs/README.md` |
| Worked archived run (layout model) | `tau2-runs/20260910-141133_airline-base_nemotron-3.5-lightning/` |
| Full CLI reference | `docs/cli-reference.md` |
| Reward basis and task schema | `docs/evaluation.md` |
| Running simulations | `docs/running_simulations.md` |
| Getting started / env setup | `docs/getting-started.md` |
| Leaderboard submission | `docs/leaderboard-submission.md` |
| Metrics implementation | `src/tau2/metrics/agent_metrics.py` |
| Defaults | `src/tau2/config.py` |
| Endpoint routing / model strings | `misc/nemotron-inference-hub-benchmark.md` §3 |
| Judge wiring and secrets | `misc/judge-rewire-plan.md` |
