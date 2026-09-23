# Runbook — Frontend/Backend Agent prototype on τ²-bench (text → text)

**Date:** 2026-09-23 · **Code:** `tau2-fba/` (this repo) · **Design:** [`text-frontend-backend-agent-tau2-integration-plan.md`](text-frontend-backend-agent-tau2-integration-plan.md)

Step by step, from a fresh shell to a comparison table with Pass^1–4, backend turn latency,
frontend filler latency, and per-role token usage for both variants of the agent.

Every command runs from the τ² repo root:

```bash
cd /localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar
```

---

## What you are measuring

| Arm | Registered agent | Who talks to the user | Tools |
|---|---|---|---|
| **A — paired** | `fba_paired` | frontend LLM (Nemotron 3.5 Lightning, reasoning off). It delegates task work with `call_backend(query, filler_text)`. | frontend: `call_backend` only · backend: all τ² domain tools |
| **B — backend only** | `fba_backend_only` | backend LLM (Nemotron 3 Ultra, reasoning on) directly, with its own history | backend: all τ² domain tools |
| C — baseline (optional) | `llm_agent` | τ²'s default agent on the backend model | all τ² domain tools |

A vs B isolates what the frontend costs or buys. B vs C isolates the prototype's backend prompt
against τ²'s default one (see the plan, §7).

| Metric | Where it comes from | Definition |
|---|---|---|
| **Pass^1 … Pass^4** | τ²'s own `compute_metrics()` | P(k random trials of a task all succeed). Needs `--num-trials 4`. |
| **Backend turn latency** | `raw_data.fba.turn.backend_latency_s` | Backend LLM time summed over one user turn (every tool round), averaged over turns with backend work. It's measured the same way in A and B. |
| **Filler latency** (A only) | `raw_data.fba.turn.filler_latency_s` | Time from the user message reaching the agent to the frontend's `call_backend` decision, i.e. when the filler phrase exists and a voice agent could start speaking |
| **Time to first response** | `raw_data.fba.turn.first_response_latency_s` | Filler latency where there is filler; otherwise the whole turn. This is the A-vs-B "how soon does the user hear something" number. |
| **Tokens per task, frontend / backend** | `raw_data.fba.step.{frontend,backend}` | Per role, summed over one simulation, then averaged. Reasoning and cached tokens are shown separately. |

---

## Models and endpoint

Everything runs on the NVIDIA Inference Hub, `https://inference-api.nvidia.com/v1`, with one
`sk-…` key.

| Role | Model | Where it's set |
|---|---|---|
| Frontend | `nvidia/nvidia/nemotron-3.5-lightning`, reasoning **off** | prototype `config/agent.yaml` (override: `--frontend-llm`) |
| Backend | `nvidia/nvidia/nemotron-3-ultra`, reasoning **on**, budget 1024 | prototype `config/agent.yaml` (override: `--agent-llm`) |
| User simulator | `openai/azure/openai/gpt-5.2` | `--user-llm` |
| Judge (retail only) | `openai/azure/openai/gpt-5.2` | `.env` (`TAU2_JUDGE_*`) |

Pass agent model ids **without** a LiteLLM prefix, as the prototype YAML writes them. The adapter
adds `openai/` itself. The user simulator and judge strings go to LiteLLM directly, so they
**do** carry `openai/`. `openai/azure/openai/gpt-5.2` is not a typo (see
`misc/hermes-agent-runbook.md`, "Why the strings differ").

---

## Step 1 — Commit the prototype's WIP (once)

The prototype repo has uncommitted edits that make `filler_text` a **required** `call_backend`
argument. The filler-latency metric depends on them: on the committed version the frontend may
omit the filler. Commit them so every run records a real SHA:

```bash
cd /localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar
git status --short -- src/prototypes/text_frontend_backend_agent tests/unit/prototypes
# expect exactly these three:
git add src/prototypes/text_frontend_backend_agent/delegation.py \
        src/prototypes/text_frontend_backend_agent/config/prompts.yaml \
        tests/unit/prototypes/test_delegation_args.py
git commit -m "feat(prototypes): require filler_text on call_backend"
cd /localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar
```

The driver refuses to run from a prototype with uncommitted changes. `--allow-dirty-prototype`
overrides that for experiments, and the report then marks the run **(DIRTY)**.

## Step 2 — Install

```bash
uv sync --extra dev
uv pip install websockets       # pre-existing τ² gap on core-only installs
```

The prototype is imported from its source tree, so nothing extra is installed. It's found at
`$FBA_PROTOTYPE_ROOT`, which defaults to the sibling checkout
`../nemotron-voice-agent-smasurekar`. Set it only if the repo lives elsewhere:

```bash
export FBA_PROTOTYPE_ROOT=/path/to/nemotron-voice-agent-smasurekar
```

## Step 3 — Credentials and judge

```bash
export OPENAI_API_KEY='sk-...'   # Inference Hub key: both agent roles, the user sim, and the judge
unset NVIDIA_API_KEY             # or set it to the SAME value, never a different one
```

LiteLLM reads `$OPENAI_API_KEY` itself, so the key is never passed per call and never lands in a
log. The prototype YAML's `${NVIDIA_API_KEY}` is only used if it's set **and** different. In that
case it is passed per call, and the driver refuses `--verbose-logs`, because τ²'s `llm_debug`
logs write every call's kwargs to disk.

Pin the judge. This checkout has no `.env` (τ² prints "No .env file found"), so create one. It's
gitignored:

```bash
cat >> .env <<'EOF'
TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1
TAU2_JUDGE_JSON_MODE=0
EOF
```

`TAU2_JUDGE_JSON_MODE=0` is required on this gateway, which rejects `response_format=json_object`.
Without `TAU2_JUDGE_MODEL`, τ² silently defaults the judge to `gpt-4.1-2025-04-14`. Only retail
scores with the judge. Airline and telecom never call it.

Convenience variable for every command below:

```bash
USER_ARGS='{"api_base": "https://inference-api.nvidia.com/v1", "temperature": 0.0}'
```

---

## Step 4 — Verify before spending anything (offline, free)

**4a. Tests.** No network and no keys: 28 tests, including all three arms run through τ²'s real
`run_domain` with scripted LLMs.

```bash
(cd tau2-fba && ../.venv/bin/python -m pytest -q)
```

**4b. Tool routing.** Print exactly what each role will be given, per domain, without any LLM
call:

```bash
uv run python tau2-fba/tools/inspect_fba_surface.py airline
uv run python tau2-fba/tools/inspect_fba_surface.py airline --mode backend_only
```

Expected verdict (exit code 0):

```
frontend LLM ... tools ( 1)   : ['call_backend']
backend LLM  ... tools (14)   : ['book_reservation', ..., 'update_reservation_passengers']
backend tools == domain tools (14): True
frontend tools == ['call_backend']: True
domain tool names in frontend prompt: none
policy in backend prompt: True
```

It also shows each role's `extra_body`. Check `enable_thinking: false` for the frontend and
`true` for the backend.

---

## Step 5 — Smoke run (mock domain, cheap)

```bash
for MODE in paired backend_only; do
  RUN=fba_smoke_${MODE}_mock
  uv run python tau2-fba/run_fba_eval.py --mode $MODE --domain mock \
    --user-llm 'openai/azure/openai/gpt-5.2' --user-llm-args "$USER_ARGS" \
    --num-tasks 2 --num-trials 1 --save-to $RUN 2>&1 | tee /tmp/$RUN.console.log
  keep_console_log $RUN
done
```

Each run prints τ²'s usual summary, then the FBA report, and writes the following next to
`results.json`:

| File | Content |
|---|---|
| `fba_report.md` | The tables below plus a **Checks** section |
| `fba_metrics.json` | Every number in the report, machine-readable |
| `fba_per_task.csv` | Per task: reward, turns, per-role tokens |
| `console.log` | τ²'s console output: progress, per-simulation summaries, warnings. Copied in by `keep_console_log` (below). |

**Keep the console log with the run.** τ² doesn't save its console output, and `/tmp` can be
cleared on reboot. Every run command in this runbook therefore `tee`s its output to
`/tmp/<run>.console.log` and then calls `keep_console_log <run>`, which copies it into
`data/simulations/<run>/console.log`. (The copy happens after the run because τ² creates the run
folder itself when it starts.) Define the helper once per shell, together with `USER_ARGS` from
step 3:

```bash
keep_console_log() {  # usage: keep_console_log <run-name>
  cp "/tmp/$1.console.log" "data/simulations/$1/console.log" \
    && echo "saved data/simulations/$1/console.log"
}
```

If you launch a run in the background (`nohup … > /tmp/<run>.console.log 2>&1 &`), run
`keep_console_log <run>` yourself once it has exited. When resuming with `--auto-resume`, use `tee -a`
so the earlier output is kept.

## Step 6 — Gate: read the checks and the transcripts (do not skip)

**6a. Checks.** Open `data/simulations/fba_smoke_paired_mock/fba_report.md` → *Checks*. On a
healthy 1-trial smoke run, the only warning is `only 1 trial(s): Pass^2..4 need --num-trials 4`.
Stop and fix anything else, in particular:

| Warning | Meaning |
|---|---|
| `frontend produced reasoning tokens` | The reasoning-off setting was dropped on the way to the endpoint. Filler latency would be wrong. |
| `backend reasoning is configured ON but no reasoning tokens` | The setting was dropped, or the endpoint doesn't report reasoning tokens. Confirm before trusting B-vs-C comparisons. |
| `only N% of delegations carried filler_text` | Step 1 not done, or the frontend is ignoring the schema |
| `turns fell back to the frontend's error text` | The frontend broke its tool contract after repair attempts |

The same frontend-reasoning check, by hand:

```bash
jq -r '[.simulations[].messages[] | select(.raw_data.fba) | .raw_data.fba.step.per_call[]
        | select(.role=="frontend") | .reasoning_tokens]
       | "frontend calls: \(length), with reasoning>0: \(map(select(. > 0)) | length)"' \
  data/simulations/fba_smoke_paired_mock/results.json
```

**6b. Per-turn view:** decision, filler latency, backend latency, filler, and delegation query:

```bash
jq -r '.simulations[] | .task_id as $t | .messages[] | select(.raw_data.fba.turn)
       | .raw_data.fba.turn | [$t, .decision, .filler_latency_s, .backend_latency_s,
         .filler_text, .delegation_query] | @tsv' \
  data/simulations/fba_smoke_paired_mock/results.json
```

**6c. Transcripts:**

```bash
uv run tau2 view --file data/simulations/fba_smoke_paired_mock/results.json
```

Confirm all of the following:
- Agent tool calls are structured calls to **domain** tools. `call_backend` never appears.
- No filler phrase ("Let me take a look.") is ever delivered to the user as an agent message.
- No agent message reads "Sorry, I could not process that…" (frontend fallback) or "I could not
  complete that request right now…" (backend error text).
- Delegation queries (6b) are self-contained: they restate ids and details, not "change it to 8".
- The frontend doesn't refuse in-domain requests as unsupported. If it does, the capability list
  in `tau2-fba/tau2_fba/domains.yaml` is too narrow.

---

## Step 7 — Airline go/no-go (50 tasks, 1 trial per arm)

```bash
for MODE in paired backend_only; do
  RUN=fba_${MODE}_airline_base_1trial
  uv run python tau2-fba/run_fba_eval.py --mode $MODE --domain airline \
    --user-llm 'openai/azure/openai/gpt-5.2' --user-llm-args "$USER_ARGS" \
    --num-trials 1 --max-concurrency 4 --save-to $RUN 2>&1 | tee /tmp/$RUN.console.log
  keep_console_log $RUN
done
```

`--task-split-name` defaults to `base` in this driver (50 airline tasks). A near-zero Pass^1 means
a harness problem, not a result. Go back to step 6.

## Step 8 — The reportable run: Pass^1 through Pass^4

`--num-trials 4` produces Pass^4: 50 tasks × 4 = 200 simulations per arm. Use a fresh
`--save-to` whenever the trial count changes. τ² resumes into an existing directory, and mixing
trial counts corrupts pass^k.

```bash
for MODE in paired backend_only; do
  RUN=fba_${MODE}_airline_base_4trials
  uv run python tau2-fba/run_fba_eval.py --mode $MODE --domain airline \
    --user-llm 'openai/azure/openai/gpt-5.2' --user-llm-args "$USER_ARGS" \
    --num-trials 4 --max-concurrency 4 --save-to $RUN 2>&1 | tee /tmp/$RUN.console.log
  keep_console_log $RUN
done
```

**Keep `--max-concurrency` identical across arms.** Every latency number includes endpoint
queueing, so arms run at different concurrency aren't comparable. For cleaner absolute latencies,
add a small latency-only pass at concurrency 1, e.g. `--num-tasks 10 --num-trials 1
--max-concurrency 1 --save-to fba_${MODE}_airline_latency`.

**Optional arm C — the `llm_agent` baseline** on the backend model with the backend's exact
settings:

```bash
uv run tau2 run --domain airline --agent llm_agent \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3-ultra' \
  --agent-llm-args '{"api_base": "https://inference-api.nvidia.com/v1", "temperature": 0.0, "max_tokens": 4096, "timeout": 120.0, "extra_body": {"chat_template_kwargs": {"enable_thinking": true}, "reasoning_budget": 1024}}' \
  --user-llm 'openai/azure/openai/gpt-5.2' --user-llm-args "$USER_ARGS" \
  --task-set-name airline --task-split-name base \
  --num-trials 4 --max-concurrency 4 --save-to llm_agent_ultra_airline_base_4trials \
  2>&1 | tee /tmp/llm_agent_ultra_airline_base_4trials.console.log
keep_console_log llm_agent_ultra_airline_base_4trials
```

**Resuming after interruptions or infra errors.** A failed backend LLM call is re-raised, so τ²
retries the simulation. If retries run out it's recorded as `infrastructure_error`: excluded from
the metrics, never scored as an agent failure. Re-run the **same command with the same
`--save-to`**, plus `--auto-resume`, to fill those in. The report's Checks section lists how many
remain.

## Step 9 — Compare the arms

```bash
uv run python tau2-fba/fba_report.py \
  data/simulations/fba_paired_airline_base_4trials \
  data/simulations/fba_backend_only_airline_base_4trials \
  data/simulations/llm_agent_ultra_airline_base_4trials \
  --out misc/prototypes/results/airline_base_4trials.md \
  --json misc/prototypes/results/airline_base_4trials.json \
  --csv-dir misc/prototypes/results/airline_base_4trials
```

Pass run **directories** or `results.json` files. (τ²'s own `Results.load` reads a text-run
directory as zero simulations; `fba_report.py` handles that.) The report has these sections:

| Section | What's in it |
|---|---|
| Headline | Pass^1–4 · backend turn latency · filler latency · filler presence · time to first response · FE/BE tokens per task |
| Latency detail | p50/p95 of each latency, backend calls per turn, per-call latency, FE+BE LLM time per turn, frontend completion tokens per call |
| Tokens per task | Per role: prompt, completion, of which reasoning, cached, total |
| Diagnostics | Decisions (delegate/direct/fallback), turns without backend work, backend errors, repairs, contract violations |
| Checks | The step-6 gates, for every run |
| Provenance | Prototype SHA and dirty flag, models, user sim, trials, concurrency |

## Step 10 — Retail and telecom

One domain per command. Use a separate `--save-to` per domain.

```bash
for DOMAIN in retail telecom; do
  for MODE in paired backend_only; do
    RUN=fba_${MODE}_${DOMAIN}_base_4trials
    uv run python tau2-fba/run_fba_eval.py --mode $MODE --domain $DOMAIN \
      --user-llm 'openai/azure/openai/gpt-5.2' --user-llm-args "$USER_ARGS" \
      --num-trials 4 --max-concurrency 4 --save-to $RUN 2>&1 | tee /tmp/$RUN.console.log
    keep_console_log $RUN
  done
done
```

| Domain | `base` tasks | Simulations per arm at 4 trials | Judge used |
|---|---|---|---|
| airline | 50 | 200 | no |
| retail | 114 | 456 | **yes** (40 tasks) |
| telecom | 114 | 456 | no |

Check the status line in the first minute: `Status: N/<total>` must match the table. For the
baseline arm with `tau2 run`, **always** pass `--task-split-name base`: telecom without it runs
the full 2285-task set.

Telecom conversations can run past 20 user turns. The paired frontend keeps 20 turns of history,
the prototype's default, so the earliest turns drop out of its context. `fba_per_task.csv` →
`turns_max` shows which tasks that affected.

---

## How to read the numbers

- **Pass^k** measures reliability. Pass^1 equals average reward, and Pass^4 counts a task only if
  all 4 trials passed. At temperature 0 the trials differ mainly through the user simulator and
  endpoint nondeterminism.
- **Backend turn latency** excludes paired turns the frontend answered by itself (greetings,
  thanks, unsupported). Their count is in *Diagnostics → Turns with no backend work*. Use
  *FE+BE LLM per turn* for total LLM time.
- **Filler latency is non-streaming.** The filler "exists" once the whole `call_backend` call has
  been generated, and the schema generates the long self-contained `query` **before**
  `filler_text`. So it grows with query length and is an upper bound on a streaming frontend.
  *FE completion tokens per call* shows how much the frontend writes before the filler is
  available.
- **Paired backends are stateless per delegation.** Every delegated turn starts a fresh backend
  context from the frontend's query, so expect more backend calls and tokens per turn in A than
  in B. `delegation_query` in `raw_data` shows exactly what the backend was told.
- **Cost** is not reported. LiteLLM can't price Hub models and records 0.0.

## All flags (`run_fba_eval.py`)

| Flag | Default | Notes |
|---|---|---|
| `--mode {paired,backend_only}` | required | The arm |
| `--domain` | `airline` | Needs an entry in `tau2_fba/domains.yaml` (airline, retail, telecom, mock) |
| `--user-llm` / `--user-llm-args` | required / `{}` | Hold fixed across arms |
| `--agent-llm` | YAML backend model | Backend model id (no `openai/` prefix). Recorded as τ²'s agent llm. |
| `--frontend-llm` | YAML frontend model | Paired only |
| `--base-url` | YAML | Overrides both roles |
| `--api-key-env` | YAML `${NVIDIA_API_KEY}` → `$OPENAI_API_KEY` | Name of the variable, never the key |
| `--llm-kwargs` | `{}` | Extra LiteLLM kwargs for both roles, e.g. `{"num_retries": 3}` |
| `--fba-config` | prototype `config/agent.yaml` | Alternative agent YAML |
| `--num-trials` / `--num-tasks` / `--task-ids` | 1 / all / all | 4 trials for Pass^4 |
| `--task-set-name` / `--task-split-name` | domain / **`base`** | |
| `--max-concurrency` / `--max-steps` / `--seed` | 1 / 200 / τ² default | |
| `--save-to` | `fba_<mode>_<domain>_<timestamp>` | Reuse with `--auto-resume` to resume |
| `--auto-resume`, `--verbose-logs` | off | `--verbose-logs` adds per-call `llm_debug/*.json` |
| `--fba-event-log PATH` | off | Every internal prototype event (delegation, filler, repairs) as JSONL |
| `--lenient-transport-errors` | off | Diagnostics only: score backend LLM failures as the prototype's canned reply |
| `--allow-dirty-prototype` | off | Run despite uncommitted prototype changes (flagged in the report) |
| `--no-report` | off | Skip writing `fba_report.md` / `fba_metrics.json` / `fba_per_task.csv` |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `The prototype package has uncommitted changes` | Step 1 not done | Commit, or `--allow-dirty-prototype` for experiments |
| `The Frontend/Backend Agent prototype is not importable` | Prototype not at the default path | `export FBA_PROTOTYPE_ROOT=…` |
| `No FBA profile for domain 'x'` | Domain has no capability list | Add it to `tau2-fba/tau2_fba/domains.yaml` |
| `--verbose-logs writes every LLM call's kwargs …` | `NVIDIA_API_KEY` differs from `OPENAI_API_KEY` | Make them equal (or unset `NVIDIA_API_KEY`), or drop `--verbose-logs` |
| `ToolSurfaceError` | A role was offered the wrong tools: a harness bug | Run step 4b. Never ignore it. |
| Many `infrastructure_error` sims | Endpoint failures (429/5xx/timeouts) exhausted τ²'s retries | Lower `--max-concurrency`, add `--llm-kwargs '{"num_retries": 5}'`, resume with `--auto-resume` |
| Checks: `frontend produced reasoning tokens` | `extra_body` didn't reach the endpoint | Check `inspect_fba_surface.py` output, then `misc/tools/verify_reasoning_off.py` |
| Frontend answers in-domain requests as "unsupported" | Capability list too narrow | Widen `domains.yaml`, then re-run **both** arms (it's part of what's measured) |
| `LLM Provider NOT provided` on the user sim | Missing `openai/` prefix | `openai/azure/openai/gpt-5.2` |
| `AzureException: messages must contain the word 'json'` | Judge JSON mode | `TAU2_JUDGE_JSON_MODE=0` in `.env` |
| `ValueError: Number of trials 1 is less than k 4` | Pass^4 on a 1-trial run | Re-run with `--num-trials 4` into a fresh `--save-to` |
| `fba_report.py`: `no simulations found` | Wrong path, or a run that never completed a simulation | Point it at `data/simulations/<run>` |

## What to record when you report

- All arms: same user sim, judge, domains, split, trials, and `--max-concurrency`.
- Keep each run folder whole: `results.json`, the `fba_*` report files, and `console.log`
  (step 5). The console log is the only record of warnings, retries, and progress.
- Both models and their `extra_body` (reasoning on/off, budget). The report's Provenance section
  lists them, and `results.json → info.agent_info.llm_args.provenance` has the full record:
  prototype SHA, sha256 of `agent.yaml`, `prompts.yaml`, and the domain profile.
- Whether any run needed `--allow-dirty-prototype` or `--lenient-transport-errors`. Neither
  belongs in a reported number.
- State plainly that `fba_*` numbers measure **scaffold + two models**. They aren't comparable to
  published τ-bench leaderboard figures (all `llm_agent`), and filler latency is non-streaming.
