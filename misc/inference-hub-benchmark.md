# Benchmarking any NVIDIA Inference Hub model on τ³-bench

**Status:** runbook. Verified against this checkout (`tau2` v1.0.1); judge configuration reflects the
env-driven judge landed in `dfb4c50`.
**Endpoint:** `https://inference-api.nvidia.com/v1` (OpenAI-compatible)
**Model under test:** *any* chat model served by the hub — set once in §0 and reused everywhere below.

> This document is model-agnostic. Nothing below hardcodes a model; every command reads the shell
> variables from §0. The worked example that produced `tau2-runs/*_nemotron-3.5-lightning/` used
> `AGENT_MODEL='openai/nvidia/nvidia/nemotron-3.5-lightning'`.

---

## 0. Fill this in once

Everything in this runbook is driven by these five variables. Export them in the shell you will run
from (or put them in the repo's gitignored `.env`).

```bash
export IHUB='https://inference-api.nvidia.com/v1'   # /v1 — NOT /v1/chat/completions
export OPENAI_API_KEY='sk-...'                      # your Inference Hub key

# The model under test. Note the mandatory 'openai/' LiteLLM provider prefix.
export AGENT_MODEL='openai/<hub-model-id>'          # e.g. openai/nvidia/nvidia/nemotron-3.5-lightning

# Measurement apparatus — hold these FIXED across every model you compare (§5, §6).
export USER_MODEL='openai/azure/openai/gpt-5.2'
export TAU2_JUDGE_MODEL='openai/azure/openai/gpt-5.2'
export TAU2_JUDGE_BASE_URL="$IHUB"

# Short slug for run names. Keep it stable per model.
export RUN_TAG='<model-slug>'                       # e.g. nemotron35lightning
```

| Variable | What it selects | Must it be held fixed across models? |
|---|---|---|
| `AGENT_MODEL` | **The system under test.** Changes per experiment. | ❌ this is the variable |
| `USER_MODEL` | User simulator — part of the measurement apparatus | ✅ yes |
| `TAU2_JUDGE_MODEL` | NL-assertions judge (retail only) | ✅ yes |
| `IHUB` | Endpoint | ✅ yes |
| `RUN_TAG` | Output directory naming only | ❌ |

### Finding the model ID

The hub's model IDs are what you put after `openai/`. List them:

```bash
curl -s "$IHUB/models" -H "Authorization: Bearer $OPENAI_API_KEY" \
  | python -c 'import json,sys;[print(m["id"]) for m in json.load(sys.stdin)["data"]]' | sort
```

IDs are multi-segment (`nvidia/nvidia/...`, `azure/openai/...`, `us/azure/openai/...`). LiteLLM strips
only the **first** segment as the provider, so `openai/azure/openai/gpt-5.2` puts
`azure/openai/gpt-5.2` on the wire. Verify any model string without spending tokens:

```bash
uv run python -c "import litellm; print(litellm.get_llm_provider('$AGENT_MODEL'))"
# -> ('<wire model>', 'openai', None, None)
```

---

## 1. How this benchmark actually works

τ³-bench is a *simulation* benchmark, not a static Q&A set. For every task it spins up three
LLM-driven or rule-driven components and scores the resulting trajectory:

| Component | What it is | Which model drives it | CLI flag |
|---|---|---|---|
| **Agent** | The system under test. Gets a domain policy + tool schemas, must resolve the customer's request. | `$AGENT_MODEL` | `--agent-llm` |
| **User simulator** | Role-plays the customer from a hidden scenario/persona. | `$USER_MODEL`, held fixed (published reference: `gpt-4.1-2025-04-14`) | `--user-llm` |
| **Environment** | Deterministic Python domain (DB + tools). No LLM. | — | — |
| **Evaluator / judge** | Scores the finished trajectory. Mostly deterministic; one component is an LLM judge. | `$TAU2_JUDGE_MODEL` (env only) | *(no flag — see §5)* |

Scoring is a conjunction over the reward components listed in each task's `reward_basis`
(`src/tau2/data_model/tasks.py`): `DB` (final database state diff), `ENV_ASSERTION` (env predicate),
`ACTION` (required tool calls were made), `COMMUNICATE` (required info was conveyed), and
`NL_ASSERTION` (**LLM judge**). Reward is 1.0 only if every in-basis component passes.
Headline metrics are `pass^k` over `--num-trials` trials. Details: `docs/evaluation.md`.

### Domains and task counts (`base` split, text mode)

| Domain | `base` tasks | Uses the LLM judge? | Extra requirements |
|---|---|---|---|
| `mock` | 10 | no | none (smoke test only) |
| `airline` | 50 | no (`reward_basis` = `COMMUNICATE`+`DB`) | none |
| `retail` | 114 | **yes** — 112/114 tasks have `NL_ASSERTION` in `reward_basis` | none |
| `telecom` | 114 | no (`ENV_ASSERTION`, some `ACTION`) | none |
| `banking_knowledge` | 97 | no (`DB` / `ACTION`) | retrieval config; see §7 |

> Verified by inspecting `data/tau2/domains/*/split_tasks.json` and the `reward_basis` /
> `nl_assertions` fields in each `tasks.json`.
> Note `telecom/tasks.json` contains 2285 tasks total, but the `base` split is 114 — the `full`
> split (2285) is a training set, not an evaluation set. **Always pass `--task-split-name base`
> explicitly for telecom**, or you will queue 2285 tasks.

---

## 2. Environment setup

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
uv sync
```

### ⚠️ Known breakage — extra step required

`uv sync` (core only) installs a tree where the `tau2` CLI **cannot start**:

```
File "src/tau2/voice/audio_native/openai/provider.py", line 12, in <module>
    import websockets
ModuleNotFoundError: No module named 'websockets'
```

`src/tau2/data_model/simulation.py` unconditionally imports the OpenAI Live voice config, which
pulls in `websockets` — but `websockets` is only declared under the `voice` extra in `pyproject.toml`.
So core-only text-mode installs are broken.

Fix (either works):

```bash
uv pip install websockets          # minimal — what was used to validate this doc
# or
uv sync --extra voice              # heavier; also needs portaudio/ffmpeg system packages
```

Verify:

```bash
uv run tau2 check-data     # → "✅ Data directory exists"
```

---

## 3. Pointing τ-bench at the Inference Hub

τ-bench routes **all** LLM traffic through LiteLLM (`src/tau2/utils/llm_utils.py::generate`), and
passes `**llm_args` straight into `litellm.completion(...)`. So any OpenAI-compatible endpoint works
via LiteLLM's `openai/` provider prefix plus `api_base` / `api_key`. There is **no model-name
validation anywhere in `tau2/cli.py`** — arbitrary hub model strings are accepted.

### Option A — per-role args (recommended)

Each role points at the endpoint independently, so you can mix hub models and non-hub models in one
run. This is what every command below does.

```bash
uv run tau2 run \
  --domain airline \
  --agent-llm "$AGENT_MODEL" \
  --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --user-llm  "$USER_MODEL" \
  --user-llm-args  "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --num-trials 4
```

`--agent-llm-args` / `--user-llm-args` are JSON dicts forwarded verbatim to `litellm.completion`.
**Do not put the key in these dicts** — it lands in `results.json` and in your shell history. Leave
`api_key` out and let LiteLLM pick up `OPENAI_API_KEY` from the environment.

### Option B — global base URL

```bash
OPENAI_BASE_URL="$IHUB"     # LiteLLM falls back to OPENAI_API_BASE; confirmed in litellm/main.py
```

Shorter, but it redirects **all** `openai/*` traffic — including the judge — to the hub. If you use
it, pin the judge explicitly with `TAU2_JUDGE_MODEL` / `TAU2_JUDGE_BASE_URL` (§5) so a global
override can never silently change what scored your run.

### Per-model preflight — do this for every new model

Model IDs on the hub differ in capability, not just quality. Two minutes here saves a wasted sweep.

```bash
uv run python - <<'PY'
import os, litellm
r = litellm.completion(
    model=os.environ["AGENT_MODEL"], api_base=os.environ["IHUB"], temperature=0.0,
    messages=[{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
    tools=[{"type": "function", "function": {
        "name": "get_weather", "description": "Get weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}],
)
m = r.choices[0].message
print("finish_reason:", r.choices[0].finish_reason)
print("tool_calls:", m.tool_calls)
print("content:", (m.content or "")[:300])
PY
```

Checklist — a **no** on line 1 means this model cannot be benchmarked on τ-bench as a plain agent:

| Check | Why it matters | If it fails |
|---|---|---|
| `tool_calls` is a populated list, not `None` | τ-bench requires **native** function calling. A model that describes tool calls in prose scores ~0. | Stop. Either the model has no tool support, or the hub route for it doesn't expose it. |
| `finish_reason` is `tool_calls`/`stop`, not `length` | Truncated turns look like agent failures. | Add `"max_tokens": 4096` (or higher) to `--agent-llm-args`. |
| Response arrives in reasonable time | Reasoning models are slow; 200-step sims multiply it. | Lower `--max-concurrency` expectations, budget more wall clock. |
| `temperature: 0.0` accepted | Some reasoning models reject it. | Drop `temperature` from the args; note it when reporting. |

Reasoning models additionally: emit `reasoning_content` which τ² stores in `raw_data`, making
results files **~5× larger** than the §8 estimates (measured: airline `base` at 52 MB, not 8–11 MB).

### Smoke test before burning a full run

```bash
uv run tau2 run --domain mock \
  --agent-llm "$AGENT_MODEL" \
  --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --user-llm "$USER_MODEL" \
  --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --num-tasks 2 --num-trials 1 --max-concurrency 2 \
  --save-to "smoke_${RUN_TAG}" --verbose-logs
```

Inspect `data/simulations/smoke_${RUN_TAG}/`. Confirm: native `tool_calls` in the trajectories, no
`finish_reason == "length"`, sane termination reasons. Reported `cost` will be `0.0` — LiteLLM has no
price table for hub models. Expected and harmless; track spend on the Inference Hub side.

---

## 4. The benchmark runs

Work up the ladder in §4a before committing to the full sweep in §4b. The ladder is the same for
every model; only `$AGENT_MODEL` and `$RUN_TAG` change.

### 4a. Start small — `airline` is the smallest domain

**`airline` is the right first domain**, for three reasons:

1. **Smallest task set.** 50 `base` tasks vs. retail 114, telecom 114, banking_knowledge 97 — and it
   has a documented 20-task `test` split, so you can go smaller still.
2. **No extra infrastructure.** Unlike `banking_knowledge` it needs no embeddings API, no
   `sandbox-runtime`, no ripgrep/bubblewrap/socat.
3. **Judge-independent.** No airline task has `NL_ASSERTION` in its `reward_basis`, so airline scores
   are fully deterministic given the trajectories. That isolates variables: if airline works, your
   *endpoint and tool-calling* are sound, independent of any judge question. Move to `retail`
   afterwards to exercise the judge.

Airline splits (verified via the registry): `base` = 50, `train` = 30, `test` = 20.

#### Rung 1 — `mock`, 2 tasks (~2 minutes, plumbing only)

The §3 smoke test. It measures nothing; it answers "does this model respond and emit tool calls at
all against this endpoint?"

#### Rung 2 — `airline` `test` split, 20 tasks × 1 trial (the smallest *meaningful* run)

```bash
uv run tau2 run \
  --domain airline \
  --task-split-name test \
  --agent-llm "$AGENT_MODEL" \
  --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --user-llm  "$USER_MODEL" \
  --user-llm-args  "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --num-trials 1 \
  --max-concurrency 4 \
  --save-to "${RUN_TAG}_airline_test_smoke"
```

Then:

```bash
uv run tau2 view --dir "data/simulations/${RUN_TAG}_airline_test_smoke"
uv run tau2 view --dir "data/simulations/${RUN_TAG}_airline_test_smoke" --only-show-failed
```

> **Prefer `--task-split-name test` over `--num-tasks 20`.** `--num-tasks N` slices the **first N
> tasks in file order** (`src/tau2/runner/helpers.py`, `tasks[:num_tasks]`) — deterministic but an
> arbitrary, non-representative prefix. The `test` split is a curated held-out set. Use `--num-tasks`
> only for plumbing smoke tests like Rung 1.
>
> Caveat: 20 tasks × 1 trial is a **noisy** estimate — one task is worth 5 percentage points, and
> there is no `pass^k` signal at 1 trial. Treat it as a go/no-go gate, not as a result to report.

#### Rung 3 — full `airline`, 50 tasks × 4 trials (first reportable number)

```bash
uv run tau2 run \
  --domain airline \
  --agent-llm "$AGENT_MODEL" \
  --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --user-llm  "$USER_MODEL" \
  --user-llm-args  "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --num-trials 4 \
  --max-concurrency 8 \
  --auto-resume \
  --save-to "${RUN_TAG}_airline_base_4trials"
```

200 simulations, yielding `pass^1..pass^4` directly comparable to the `airline` column of the public
leaderboard (judge-independent, so no comparability asterisk beyond the user simulator — §6).

| Rung | Domain / split | Tasks × trials | Sims | Purpose |
|---|---|---|---|---|
| 1 | `mock`, `--num-tasks 2` | 2 × 1 | 2 | Endpoint + tool-calling plumbing |
| 2 | `airline` `test` | 20 × 1 | 20 | Smallest meaningful run; go/no-go gate |
| 3 | `airline` `base` | 50 × 4 | 200 | First reportable, leaderboard-comparable number |
| 4 | `retail` `base` | 114 × 4 | 456 | First domain that exercises the LLM judge |

Only after Rung 3 looks sane is the full sweep below worth the tokens.

### 4b. Full sweep

Reference protocol (matches `docs/leaderboard-submission.md`): all `base` tasks, 4 trials, identical
agent/user/judge config across domains.

```bash
for D in airline retail telecom; do
  uv run tau2 run \
    --domain "$D" \
    --task-split-name base \
    --agent-llm "$AGENT_MODEL" \
    --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
    --user-llm  "$USER_MODEL" \
    --user-llm-args  "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
    --num-trials 4 \
    --max-concurrency 8 \
    --max-retries 3 \
    --auto-resume \
    --save-to "${RUN_TAG}_${D}_base_4trials"
done
```

Notes:
- `--max-concurrency` defaults to **3**. Raise it to match the hub's rate limit for *this* model;
  this is the single biggest lever on wall-clock time. 278 tasks × 4 trials = **1112 simulations**
  for the three core domains, each 10–40 LLM calls.
- Rate limits are **per model**, so re-derive the right concurrency for each new model — Rungs 1–2
  surface 429s cheaply.
- `--seed` defaults to 300; keep it fixed for reproducibility.
- `--auto-resume` lets an interrupted sweep continue. Use a **fresh `--save-to` whenever the trial
  count changes** — resuming a 4-trial run into a 1-trial directory corrupts `pass^k`.
- Skip `--verbose-logs` on full runs (see §8).

Inspect and re-score:

```bash
uv run tau2 view                                                    # interactive browser
uv run tau2 evaluate-trajs "data/simulations/${RUN_TAG}_retail_base_4trials"
```

### Comparing models

To compare model A and B, change **only** `AGENT_MODEL` and `RUN_TAG` and re-run the same block.
Everything else — user simulator, judge, seed, trials, splits, temperature — must be byte-identical,
or the two numbers are not comparable. Record the full §0 block alongside each result.

---

## 5. The judge model

### The LLM judge (scoring-relevant)

`src/tau2/evaluator/evaluator_nl_assertions.py` (`NLAssertionsEvaluator`) shows the judge the full
conversation transcript plus the task's `nl_assertions` and asks for a per-assertion `true`/`false`
verdict in JSON. **All assertions must be met** for the `NL_ASSERTION` component to score 1.0.

**Where it matters:** effectively only `retail` — 112 of 114 `base` tasks carry `NL_ASSERTION` in
their `reward_basis`, of which 40 have non-empty `nl_assertions` (the rest short-circuit to 1.0).
`airline` tasks carry `nl_assertions` text but do *not* include `NL_ASSERTION` in `reward_basis`, so
the judge is not invoked for scoring. `telecom` and `banking_knowledge` never use it.

**There is no CLI flag — the judge is env-only** (`src/tau2/config.py:40`):

| Variable | Default | Purpose |
|---|---|---|
| `TAU2_JUDGE_MODEL` | `gpt-4.1-2025-04-14` | LiteLLM model string, `openai/`-prefixed for the hub |
| `TAU2_JUDGE_BASE_URL` | unset | OpenAI-compatible base URL, **without** `/chat/completions` |
| `TAU2_JUDGE_API_KEY` | unset → falls back to `OPENAI_API_KEY` | Only needed if the judge uses a different key |
| `TAU2_JUDGE_JSON_MODE` | `1` | Sends `response_format={"type":"json_object"}` |

Put them in the repo's gitignored `.env` so the judge cannot drift between runs:

```bash
TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1
# TAU2_JUDGE_API_KEY unset -> falls back to $OPENAI_API_KEY
```

With none of these set, behaviour is byte-for-byte the upstream default. Background and the
validation evidence for this mechanism: [`judge-rewire-plan.md`](judge-rewire-plan.md).

Judge gotchas:

- Some gateways reject `response_format=json_object` outright (`AzureException: messages must contain
  the word 'json'`). Set `TAU2_JUDGE_JSON_MODE=0`; the parser strips ``` fences and prose padding.
- Judge failures surface as `infrastructure_error` simulations, which `get_metrics_df` **silently
  filters out** — a healthy-looking score over fewer simulations. Watch the `infrastructure_error`
  count, not just the score.
- LiteLLM logs `This model isn't mapped yet` for hub models on every judge call. Cost lookup only;
  harmless.

⚠️ **Comparability.** Changing the judge changes retail scores, and results with a non-default judge
cannot be submitted to the public leaderboard as-is. Since `tau2 evaluate-trajs` re-scores saved
trajectories without re-running simulations, score retail twice when reporting:

```bash
TAU2_JUDGE_MODEL= TAU2_JUDGE_BASE_URL= \
  uv run tau2 evaluate-trajs "data/simulations/${RUN_TAG}_retail_base_4trials" \
  -o "data/simulations/${RUN_TAG}_retail_base_4trials_judge-default"
```

The judge is not recorded in `Info` (`src/tau2/data_model/simulation.py`), so a results file scored
with a swapped judge is indistinguishable from a default-scored one. **Encode the judge in
`--save-to`.**

### Other LLM roles (not part of the score)

| Role | Default | Configurable? | Purpose |
|---|---|---|---|
| Conversation reviewer | `claude-opus-4-5` | ✅ `--review-model` (with `--auto-review`, or `tau2 review`) | Post-hoc qualitative error analysis. Does **not** affect reward. |
| Env interface LLM | `gpt-4.1-2025-04-14` | ❌ `config.py` only | Only used by the beta `make env-cli` tool. |

`--review-model` takes a model name but no `api_base`, so a hub reviewer needs Option B (global base
URL) from §3.

---

## 6. User-simulator choice — an explicit decision

The user simulator is part of the measurement apparatus, not the system under test. Published τ-bench
numbers all use `gpt-4.1-2025-04-14`.

Since OpenAI models are available on the hub (`azure/openai/...`, `us/azure/openai/...`), the
practical answer is to run the user simulator on a hub-hosted OpenAI model too — a self-contained
all-NVIDIA setup with no external OpenAI account, still using the same model *family* as every
published result. The residual caveat is that these are **Azure** deployments rather than
OpenAI-hosted `gpt-4.1-2025-04-14`; behaviour is usually close but not guaranteed identical, so state
which one you used when reporting.

If you need strictly leaderboard-comparable numbers, set `USER_MODEL='gpt-4.1-2025-04-14'` against a
genuine OpenAI account (which rules out Option B's global base-URL override).

**Never** use the model under test as its own user simulator outside a plumbing smoke test — a model
scoring itself both sides is not a measurement.

---

## 7. `banking_knowledge` (optional, decide before running)

97 `base` tasks. The default retrieval config is `alltools`, which needs an **OpenAI embeddings key**
(`text-embedding-3-large`) *and* Anthropic's `sandbox-runtime` npm package plus `ripgrep`,
`bubblewrap`, `socat` on Linux. Neither is satisfied by the Inference Hub chat endpoint.

Fully offline alternative — no extra keys, no sandbox:

```bash
uv run tau2 run --domain banking_knowledge --retrieval-config bm25 \
  --agent-llm "$AGENT_MODEL" \
  --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --user-llm "$USER_MODEL" \
  --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
  --num-trials 4 \
  --save-to "${RUN_TAG}_banking_bm25"
```

`bm25` results are not comparable to `alltools` leaderboard entries. Recommendation: run the three
core domains first; treat `banking_knowledge` as a follow-up once the retrieval story is settled.

Voice/full-duplex mode is out of scope — it needs realtime WebSocket audio APIs, ElevenLabs +
Deepgram keys, and custom voice IDs. A text-only OpenAI-compatible endpoint cannot participate.

---

## 8. Storage requirements

### Fixed footprint (one-time)

| Item | Size | Notes |
|---|---|---|
| `data/` (checked into the repo) | **734 MB** | `data/tau2/domains` 140 MB, `data/tau2/results/final` 577 MB (paper reference results — **deletable**), `data/voice` 19 MB |
| Repo source + git history | ~30 MB | |
| `.venv` (core + `websockets`) | **251 MB** | `--extra voice` / `--all-extras` is substantially larger |

**Baseline: ~1.0 GB.**

### Per-run output (`data/simulations/<run_name>/results.json`)

Text runs write a single monolithic JSON per run. Baselines measured from the reference 4-trial
GPT-4.1 results in `data/tau2/results/final/`:

| Domain | tasks × trials | sims | file size | per-simulation |
|---|---|---|---|---|
| `airline` | 50 × 4 | 200 | 7.6 – 10.6 MB | ~40–53 KB |
| `retail` | 114 × 4 | 456 | ~25 MB | ~55 KB |
| `telecom` | 114 × 4 | 456 | 37 – 41 MB | ~82–90 KB |
| `banking_knowledge` | 97 × 4 | 388 | ~25 MB *(estimated)* | ~60 KB |

**These are a floor, not a forecast.** Size scales with trajectory length, and it is very
model-dependent:

- Verbose or looping models: 2–3× the table.
- **Reasoning models: ~5×** — `reasoning_content` is preserved in `raw_data`. Measured on
  nemotron-3.5-lightning: airline `base` 4-trial = **52 MB**, against an 8–11 MB baseline.

Budget **~250 MB per full sweep** for a normal model, **~1 GB** for a reasoning model. Check the size
of your Rung 3 airline run and multiply by ~6 to project the three-domain sweep.

### `--verbose-logs`

Writes one JSON per LLM call (full request incl. system prompt + tool schemas, plus the response) to
`artifacts/task_<id>/sim_<uuid>/llm_debug/`. Domain prompts and tool schemas are large and repeated in
**every** call record, so this dominates everything else.

- Default `--llm-log-mode latest` keeps only the most recent file per call-name per simulation — a
  few hundred KB per sim.
- `--llm-log-mode all` keeps every call and can reach **multiple GB** for a full sweep.

Recommendation: `--verbose-logs` for the smoke test and Rung 2 only; drop it for the full sweep.

### Bottom line

| Scenario | Disk |
|---|---|
| Baseline (repo + data + venv) | ~1.0 GB |
| \+ full 4-trial sweep, 3 core domains, non-reasoning model | ~1.1 GB |
| \+ reasoning model and/or `banking_knowledge` | ~2 GB |
| \+ `--verbose-logs --llm-log-mode all` across a full sweep | several GB |

Storage is rarely the binding constraint — rate limits and token spend are.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `LLM Provider NOT provided` | Model string missing the `openai/` prefix | `openai/azure/openai/gpt-5.2`, not `azure/openai/gpt-5.2` |
| 404 on `.../v1/chat/completions/chat/completions` | Base URL included the path | Use the bare `/v1` form |
| Score ~0, transcripts show prose not tool calls | Model doesn't emit native tool calls on this route | Re-run the §3 preflight; this model cannot be benchmarked as a plain agent |
| `finish_reason == "length"` warnings | Output cap too low | Add `max_tokens` to `--agent-llm-args` |
| `cost` reported as `0.0` | No LiteLLM price table for hub models | Expected; track spend hub-side |
| Run counts to 9140 instead of 456 on telecom | `--task-split-name base` omitted | Pass the split explicitly |
| `ValueError: Number of trials 1 is less than k 4` | pass^4 requested from a 1-trial run | Re-run with `--num-trials 4`; not recoverable after the fact |
| pass^k looks wrong after a resume | 4-trial run resumed into a 1-trial results dir | Fresh `--save-to` whenever trial count changes |
| `AzureException: messages must contain the word 'json'` | Judge gateway rejects `response_format` | `TAU2_JUDGE_JSON_MODE=0` |
| `N` < simulations run, score looks fine | Judge failures became `infrastructure_error` and were filtered | Check the `infrastructure_error` count |
| 429s / stalls | Per-model hub rate limit | Lower `--max-concurrency`, raise `--max-retries` |
| `ModuleNotFoundError: websockets` | Core-only install | See §2 |

---

## 10. What to record when you report

- The full §0 block: `AGENT_MODEL`, `USER_MODEL`, `TAU2_JUDGE_MODEL`, endpoint, `temperature`,
  `max_tokens` if set.
- Domains, splits (`base`), `--num-trials`, `--seed` (300), `--max-concurrency`.
- τ² version (1.0.1) and commit.
- `pass^1..pass^4`, the `infrastructure_error` count, and wall clock.
- For retail: **both** judge scorings (hub judge and default judge), clearly labelled.
- The caveat that the user simulator / judge are Azure-hosted deployments rather than OpenAI-hosted
  `gpt-4.1-2025-04-14`, if that is what you used.

### Suggested order of execution

1. §0 — set the five variables; confirm the model ID with `/v1/models` and `get_llm_provider`.
2. §2 — install (remember the `websockets` fix) → `tau2 check-data`.
3. §3 — preflight probe. Native tool calls or stop here.
4. §4a Rung 1 — `mock`, 2 tasks. Endpoint plumbing.
5. §4a Rung 2 — `airline` `test`, 20 tasks. Go/no-go gate; also your rate-limit and disk-size probe.
6. §4a Rung 3 — `airline` `base`, 50 × 4. **First reportable number**, judge-independent.
7. §4b — `retail` and `telecom`; score retail twice (§5). Then decide on `banking_knowledge` (§7).
