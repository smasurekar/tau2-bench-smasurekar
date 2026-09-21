# Benchmarking `nvidia/nvidia/nemotron-3.5-lightning` (NVIDIA Inference Hub) on τ³-bench

**Status:** plan / runbook. Verified against this checkout (`tau2` v1.0.1, commit `caca045`).
**Target endpoint:** `https://inference-api.nvidia.com/v1` (OpenAI-compatible)
**Target model:** `nvidia/nvidia/nemotron-3.5-lightning`

---

## 1. How this benchmark actually works

τ³-bench is a *simulation* benchmark, not a static Q&A set. For every task it spins up three LLM-driven
or rule-driven components and scores the resulting trajectory:

| Component | What it is | Which model drives it | CLI flag |
|---|---|---|---|
| **Agent** | The system under test. Gets a domain policy + tool schemas, must resolve the customer's request. | *The model you are benchmarking* | `--agent-llm` |
| **User simulator** | Role-plays the customer from a hidden scenario/persona. | Held fixed across submissions (reference: `gpt-4.1-2025-04-14`) | `--user-llm` |
| **Environment** | Deterministic Python domain (DB + tools). No LLM. | — | — |
| **Evaluator / judge** | Scores the finished trajectory. Mostly deterministic; one component is an LLM judge. | See §5 | *(no flag — see §5)* |

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
> split (2285) is a training set, not an evaluation set.

---

## 2. Environment setup

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
uv sync
```

### ⚠️ Known breakage at this commit — extra step required

`uv sync` (core only) installs a tree where the `tau2` CLI **cannot start**:

```
File "src/tau2/voice/audio_native/openai/provider.py", line 12, in <module>
    import websockets
ModuleNotFoundError: No module named 'websockets'
```

`src/tau2/data_model/simulation.py:62` unconditionally imports the OpenAI Live voice config, which
pulls in `websockets` — but `websockets` is only declared under the `voice` extra in `pyproject.toml`.
So core-only text-mode installs are broken on `main`.

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

## 3. Pointing τ-bench at the NVIDIA Inference Hub

τ-bench routes **all** LLM traffic through LiteLLM (`src/tau2/utils/llm_utils.py::generate`), and
passes `**llm_args` straight into `litellm.completion(...)`. So any OpenAI-compatible endpoint works
via LiteLLM's `openai/` provider prefix plus `api_base` / `api_key`.

**Model string to use:** `openai/nvidia/nvidia/nemotron-3.5-lightning`

LiteLLM strips the first segment as the provider, so the model sent on the wire is
`nvidia/nvidia/nemotron-3.5-lightning`. Verified locally: the call routes to the OpenAI handler and
attempts an HTTP request against the supplied `api_base` (no model-name validation anywhere in
`tau2/cli.py`, so arbitrary model strings are accepted).

### Option A — environment variables (recommended)

Cleanest, because it also covers the LLM judge and the reviewer, neither of which accepts an
`api_base` argument (§5).

`.env` in the repo root (`cp .env.example .env` first):

```bash
OPENAI_API_KEY=sk-______                              # your Inference Hub key
OPENAI_BASE_URL=https://inference-api.nvidia.com/v1
```

LiteLLM reads `OPENAI_BASE_URL` (falling back to `OPENAI_API_BASE`) for every `openai/*` model —
confirmed in `litellm/main.py`. Every model name you pass must then be `openai/`-prefixed.

> Caveat: with this option *all* `openai/*` traffic goes to the hub. If you want the user simulator
> to run on real OpenAI for comparability with published numbers, use Option B for the agent instead
> and leave `OPENAI_BASE_URL` unset.

### Option B — per-role args, no global override

```bash
uv run tau2 run \
  --domain airline \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --agent-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-______"}' \
  --user-llm gpt-4.1-2025-04-14 \
  --user-llm-args '{"temperature": 0.0}' \
  --num-trials 4
```

`--agent-llm-args` / `--user-llm-args` are JSON dicts forwarded verbatim to `litellm.completion`.

### Smoke test before burning a full run

```bash
uv run tau2 run --domain mock \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --user-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --num-tasks 2 --num-trials 1 --max-concurrency 2 \
  --save-to smoke_nemotron --verbose-logs
```

Then inspect `data/simulations/smoke_nemotron/`. What to confirm:
- tool calls are emitted in OpenAI `tool_calls` format (τ-bench requires native function calling —
  a model that only emits tool calls as prose text will score ~0);
- no `finish_reason == "length"` warnings (raise `max_tokens` in `--agent-llm-args` if so);
- reported `cost` will be `0.0` — LiteLLM has no price table for this model. Expected, harmless;
  track spend on the Inference Hub side instead.

---

## 4. The benchmark runs

Work up the ladder in §4a before committing to the full sweep in §4b.

### 4a. Start small — `airline` is the smallest domain

**`airline` is the right first domain**, for three reasons:

1. **Smallest task set.** 50 `base` tasks vs. retail 114, telecom 114, banking_knowledge 97 — and it
   has a documented 20-task `test` split, so you can go smaller still.
2. **No extra infrastructure.** Unlike `banking_knowledge` it needs no embeddings API, no
   `sandbox-runtime`, no ripgrep/bubblewrap/socat.
3. **Judge-independent.** No airline task has `NL_ASSERTION` in its `reward_basis` (verified in
   `data/tau2/domains/airline/tasks.json`), so airline scores are fully deterministic given the
   trajectories. That isolates variables: if airline works, your *endpoint and tool-calling* are
   sound, independent of any judge question. Move to `retail` afterwards to exercise the judge.

Airline splits (verified via the registry): `base` = 50, `train` = 30, `test` = 20.

#### Rung 1 — `mock`, 2 tasks (~2 minutes, plumbing only)

Does not measure anything. It answers "does the endpoint respond and emit tool calls at all?"

```bash
uv run tau2 run --domain mock \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --agent-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-______"}' \
  --user-llm 'openai/us/azure/openai/gpt-4.1' \
  --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-_____"}' \
  --num-tasks 2 --num-trials 1 --max-concurrency 2 \
  --save-to smoke_mock --verbose-logs
```

Check `data/simulations/smoke_mock/` for the items listed at the end of §3 — native `tool_calls`, no
`finish_reason == "length"`, sane termination reasons.

#### Rung 2 — `airline` `test` split, 20 tasks × 1 trial (the smallest *meaningful* run)

This is the recommended real first measurement: 20 simulations, ~1 MB of output, and it produces a
number you can actually reason about.

```bash
uv run tau2 run \
  --domain airline \
  --task-split-name test \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --agent-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-______"}' \
  --user-llm 'openai/us/azure/openai/gpt-4.1' \
  --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-_____"}' \
  --num-trials 1 \
  --max-concurrency 4 \
  --save-to nemotron_airline_test_smoke
```

Then:

```bash
uv run tau2 view --dir data/simulations/nemotron_airline_test_smoke
uv run tau2 view --dir data/simulations/nemotron_airline_test_smoke --only-show-failed
```

> **Prefer `--task-split-name test` over `--num-tasks 20`.** `--num-tasks N` slices the **first N
> tasks in file order** (`src/tau2/runner/helpers.py:91`, `tasks[:num_tasks]`) — deterministic but an
> arbitrary, non-representative prefix. The `test` split is a curated held-out set. Use `--num-tasks`
> only for plumbing smoke tests like Rung 1.
>
> Caveat: 20 tasks × 1 trial is a **noisy** estimate — a single task is worth 5 percentage points, and
> there is no `pass^k` signal at 1 trial. Treat it as a go/no-go gate, not as a result to report.

#### Rung 3 — full `airline`, 50 tasks × 4 trials (first reportable number)

```bash
uv run tau2 run \
  --domain airline \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --agent-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-______"}' \
  --user-llm 'openai/us/azure/openai/gpt-4.1' \
  --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-_____"}' \
  --num-trials 4 \
  --max-concurrency 8 \
  --auto-resume \
  --save-to nemotron35lightning_airline
```

200 simulations, ~8–11 MB of output, and it yields `pass^1..pass^4` directly comparable to the
`airline` column of the public leaderboard (judge-independent, so no comparability asterisk).

| Rung | Domain / split | Tasks × trials | Sims | Output size | Purpose |
|---|---|---|---|---|---|
| 1 | `mock`, `--num-tasks 2` | 2 × 1 | 2 | <1 MB | Endpoint + tool-calling plumbing |
| 2 | `airline` `test` | 20 × 1 | 20 | ~1 MB | Smallest meaningful run; go/no-go gate |
| 3 | `airline` `base` | 50 × 4 | 200 | ~8–11 MB | First reportable, leaderboard-comparable number |
| 4 | `retail` `base` | 114 × 4 | 456 | ~25 MB | First domain that exercises the LLM judge |

Only after Rung 3 looks sane is the full sweep below worth the tokens.

### 4b. Full sweep

Reference protocol (matches `docs/leaderboard-submission.md`): all `base` tasks, 4 trials,
identical agent/user config across domains.

```bash
export IHUB='https://inference-api.nvidia.com/v1'
export IHUB_KEY='sk-______'                                  # agent key
export IHUB_KEY_GPT41='sk-_____'                             # gpt-4.1 key (same hub)

export TAU2_AGENT='openai/nvidia/nvidia/nemotron-3.5-lightning'
export TAU2_USER='openai/us/azure/openai/gpt-4.1'            # see §6

for D in airline retail telecom; do
  uv run tau2 run \
    --domain "$D" \
    --agent-llm "$TAU2_AGENT" \
    --agent-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\", \"api_key\": \"$IHUB_KEY\"}" \
    --user-llm  "$TAU2_USER" \
    --user-llm-args  "{\"temperature\": 0.0, \"api_base\": \"$IHUB\", \"api_key\": \"$IHUB_KEY_GPT41\"}" \
    --num-trials 4 \
    --max-concurrency 8 \
    --max-retries 3 \
    --auto-resume \
    --save-to "nemotron35lightning_${D}"
done
```

(If both keys are the same, or you set `OPENAI_BASE_URL`/`OPENAI_API_KEY` per Option A in §3, the
`--*-llm-args` can collapse back to just `'{"temperature": 0.0}'`.)

Notes:
- `--max-concurrency` defaults to **3**. Raise it to match your Inference Hub rate limit; this is the
  single biggest lever on wall-clock time. 278 tasks × 4 trials = **1112 simulations** for the three
  core domains, each 10–40 LLM calls.
- `--seed` defaults to 300; keep it fixed for reproducibility.
- `--auto-resume` lets an interrupted sweep continue from the existing save file.
- Skip `--verbose-logs` on full runs (see §8).

Inspect and re-score:

```bash
uv run tau2 view                                          # interactive browser
uv run tau2 evaluate-trajs data/simulations/nemotron35lightning_retail
```

---

## 5. The judge model — what it is and how to change it

### The LLM judge (scoring-relevant)

Defined in `src/tau2/config.py`:

```python
DEFAULT_LLM_NL_ASSERTIONS = "gpt-4.1-2025-04-14"
DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"temperature": 0.0}
```

Used by `src/tau2/evaluator/evaluator_nl_assertions.py` (`NLAssertionsEvaluator`), which shows the
judge the full conversation transcript plus the task's `nl_assertions` and asks for a per-assertion
`true`/`false` verdict in JSON. **All assertions must be met** for the `NL_ASSERTION` component to
score 1.0.

**Where it matters:** effectively only `retail` — 112 of 114 `base` tasks carry `NL_ASSERTION` in
their `reward_basis`, of which 40 have non-empty `nl_assertions` (the rest short-circuit to 1.0).
`airline` tasks carry `nl_assertions` text but do *not* include `NL_ASSERTION` in `reward_basis`, so
the judge is not invoked for scoring. `telecom` and `banking_knowledge` never use it.

**There is no CLI flag or environment variable for the judge model.** It is read at import time from
`config.py`. To use an Inference Hub judge you must edit `src/tau2/config.py`:

```python
DEFAULT_LLM_NL_ASSERTIONS = "openai/us/azure/openai/gpt-4.1"
DEFAULT_LLM_NL_ASSERTIONS_ARGS = {
    "temperature": 0.0,
    "api_base": "https://inference-api.nvidia.com/v1",
    "api_key": "sk-_____",
    "response_format": {"type": "json_object"},   # see warning below
}
```

> **Judge decided:** Inference Hub GPT-4.1, `openai/us/azure/openai/gpt-4.1` at
> `https://inference-api.nvidia.com/v1`. Rather than hardcoding it as above (which puts a key in
> git), the full step-by-step re-wire — env-var driven, JSON-parse hardening, validation without
> re-running simulations — is in **[`judge-rewire-plan.md`](judge-rewire-plan.md)**. Routing is
> verified: `litellm.get_llm_provider("openai/us/azure/openai/gpt-4.1")` resolves to provider
> `openai` with wire model `us/azure/openai/gpt-4.1`.
>
> Use the **`/v1` base URL, not `/v1/chat/completions`** — LiteLLM appends the path itself.
>
> Note the judge is an **Azure**-hosted GPT-4.1 deployment, while published τ-bench numbers use
> OpenAI-hosted `gpt-4.1-2025-04-14`. Usually close; Step 4 of the re-wire plan is what confirms it.
> Only `retail` is affected either way.

⚠️ **JSON-robustness warning.** `evaluator_nl_assertions.py:127` does a bare
`json.loads(assistant_message.content)` with **no** `response_format` constraint and no fence
stripping. GPT-4.1 happens to comply; a different judge that wraps its answer in ```json fences or
adds a preamble will raise `JSONDecodeError` and kill the evaluation. Adding
`"response_format": {"type": "json_object"}` (if the hub supports it) is strongly recommended when
swapping the judge, and the swap should be validated by re-scoring an existing run:
`uv run tau2 evaluate-trajs data/simulations/<run>`.

⚠️ **Comparability.** Changing the judge changes retail scores. Results with a non-default judge are
**not** comparable to the public leaderboard and cannot be submitted as-is. Recommended: run retail
scoring twice — once with the default judge for comparability, once with the Inference Hub judge —
and report both. `tau2 evaluate-trajs` makes this cheap, since it re-scores saved trajectories
without re-running simulations.

### Other LLM roles (not part of the score)

| Role | Default | Configurable? | Purpose |
|---|---|---|---|
| Conversation reviewer | `claude-opus-4-5` | ✅ `--review-model` (with `--auto-review`, or `tau2 review`) | Post-hoc qualitative error analysis. Does **not** affect reward. |
| Env interface LLM | `gpt-4.1-2025-04-14` | ❌ `config.py` only | Only used by the beta `make env-cli` tool. |

The reviewer flag takes a model name but no `api_base`, so an Inference Hub reviewer needs Option A
(env vars) from §3.

---

## 6. User-simulator choice — an explicit decision

The user simulator is part of the measurement apparatus, not the system under test. Published τ-bench
numbers all use `gpt-4.1-2025-04-14`.

Now that GPT-4.1 is available on the Inference Hub (`us/azure/openai/gpt-4.1`), the practical answer
is to run the **user simulator on the hub's GPT-4.1 too** — this is what the §4 commands do:

```bash
--user-llm 'openai/us/azure/openai/gpt-4.1' \
--user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1", "api_key": "sk-_____"}'
```

That gives an entirely self-contained, all-NVIDIA setup with no external OpenAI account, while still
using the *same model family* as every published τ-bench result — much better for comparability than
substituting a different model. The residual caveat is that this is an **Azure** GPT-4.1 deployment
rather than OpenAI-hosted `gpt-4.1-2025-04-14`; behaviour is usually close but not guaranteed
identical, so state it when reporting.

If you need strictly leaderboard-comparable numbers, use `--user-llm gpt-4.1-2025-04-14` against a
genuine OpenAI account instead (which rules out Option A's global base-URL override).

---

## 7. `banking_knowledge` (optional, decide before running)

97 `base` tasks. The default retrieval config is `alltools`, which needs an **OpenAI embeddings key**
(`text-embedding-3-large`) *and* Anthropic's `sandbox-runtime` npm package plus `ripgrep`,
`bubblewrap`, `socat` on Linux. Neither is satisfied by the Inference Hub endpoint.

Fully offline alternative — no extra keys, no sandbox:

```bash
uv run tau2 run --domain banking_knowledge --retrieval-config bm25 \
  --agent-llm 'openai/nvidia/nvidia/nemotron-3.5-lightning' \
  --user-llm gpt-4.1-2025-04-14 --num-trials 4 \
  --save-to nemotron35lightning_banking_bm25
```

`bm25` results are not comparable to `alltools` leaderboard entries. My recommendation: run the three
core domains first, treat `banking_knowledge` as a follow-up once the retrieval story is settled.

Voice/full-duplex mode is out of scope — it needs realtime WebSocket audio APIs (OpenAI/Gemini/xAI
realtime), ElevenLabs + Deepgram keys, and custom voice IDs. A text-only OpenAI-compatible endpoint
cannot participate.

---

## 8. Storage requirements

All figures measured on this checkout.

### Fixed footprint (already on disk / one-time)

| Item | Size | Notes |
|---|---|---|
| `data/` (checked into the repo) | **734 MB** | `data/tau2/domains` 140 MB (telecom 89 MB, retail 34 MB), `data/tau2/results/final` 577 MB (reference results from the paper — **deletable**, ~577 MB reclaimable), `data/voice` 19 MB |
| Repo source + git history | ~30 MB | |
| `.venv` (core + `websockets`) | **251 MB** | `uv sync --extra voice` / `--all-extras` is substantially larger (torch-free, but adds google-cloud, boto3, livekit) |

**Baseline: ~1.0 GB.**

### Per-run output (`data/simulations/<run_name>/results.json`)

Text runs write a single monolithic JSON per run. Measured from the reference 4-trial results in
`data/tau2/results/final/`:

| Domain | tasks × trials | sims | file size | per-simulation |
|---|---|---|---|---|
| `airline` | 50 × 4 | 200 | 7.6 – 10.6 MB | ~40–53 KB |
| `retail` | 114 × 4 | 456 | ~25 MB | ~55 KB |
| `telecom` | 114 × 4 | 456 | 37 – 41 MB | ~82–90 KB |
| `banking_knowledge` | 97 × 4 | 388 | ~25 MB *(estimated)* | ~60 KB |

**A full 4-trial sweep of the three core domains ≈ 75 MB.** Adding `banking_knowledge` ≈ 100 MB.

Size scales with trajectory length, so a verbose or looping model can run 2–3× larger than the
GPT-4.1 baselines above. Budget **~250 MB per full sweep** to be safe.

### `--verbose-logs`

Writes one JSON per LLM call (full request incl. system prompt + tool schemas, plus the response) to
`artifacts/task_<id>/sim_<uuid>/llm_debug/`. Domain system prompts and tool schemas are large and are
repeated in **every** call record, so this dominates everything else.

- Default `--llm-log-mode latest` keeps only the most recent file *per call-name per simulation*, so
  growth stays roughly linear in simulation count (a few hundred KB per sim).
- `--llm-log-mode all` keeps every call and can reach **multiple GB** for a full sweep.

Recommendation: `--verbose-logs` for the smoke test only; drop it for the full sweep.

### Other

- `banking_knowledge` embedding configs cache document embeddings at `data/.embeddings_cache`
  (gitignored) — tens of MB for the 8.1 MB document set. `bm25` needs none.
- Voice runs (out of scope) store WAV audio per simulation and are the only genuinely large consumer.

### Bottom line

| Scenario | Disk |
|---|---|
| Baseline (repo + data + venv) | ~1.0 GB |
| \+ full 4-trial sweep, 3 core domains, no verbose logs | ~1.1 GB |
| \+ `banking_knowledge` and headroom for a verbose model | ~1.3 GB |
| \+ `--verbose-logs --llm-log-mode all` across a full sweep | several GB |

Available on `/`: **1.4 TB free**. Storage is a non-issue here — the binding constraints are
Inference Hub rate limits and token spend, not disk.

---

## 9. Open items for you

1. **Judge model** — ✅ resolved: Inference Hub GPT-4.1 (`openai/us/azure/openai/gpt-4.1` at
   `https://inference-api.nvidia.com/v1`). Re-wiring plan written up in
   [`judge-rewire-plan.md`](judge-rewire-plan.md) — awaiting go-ahead to apply.
2. **User simulator** — ✅ resolved: Inference Hub `openai/us/azure/openai/gpt-4.1`, same endpoint.
   See §6 for the comparability caveat.
3. **`banking_knowledge`** — include with offline `bm25`, or defer? See §7. *(Still open — but not
   blocking; it is the last rung, not the first.)*
4. **Rate limit** — what concurrency does the Inference Hub allow for these models? Drives
   `--max-concurrency` and total wall-clock. Rungs 1–2 in §4a will surface 429s cheaply.
5. **Judge re-wire** — ✅ applied and verified on 2026-09-10; see the status box in
   [`judge-rewire-plan.md`](judge-rewire-plan.md). `.env` holds `TAU2_JUDGE_MODEL` and
   `TAU2_JUDGE_BASE_URL`; the key falls back to the exported `OPENAI_API_KEY`, so no secret is on disk.

### Suggested order of execution

1. §2 install (remember the `websockets` fix) → `tau2 check-data`.
2. §4a Rung 1 — `mock`, 2 tasks. Confirms endpoint + native tool calling.
3. §4a Rung 2 — `airline` `test` split, 20 tasks. Go/no-go gate.
4. §4a Rung 3 — `airline` `base`, 50 × 4. **First reportable number**, judge-independent.
5. Apply the judge re-wire, validate with `tau2 evaluate-trajs` (no simulations re-run).
6. §4b — `retail` and `telecom`, then decide on `banking_knowledge`.
