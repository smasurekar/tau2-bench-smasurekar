# Plan: re-wire the τ-bench LLM judge to Inference Hub GPT-4.1

> ## ✅ IMPLEMENTED — 2026-09-10
>
> Steps 1–4 are applied and verified end to end against the live endpoint. Step 5 is handled by the
> minimal (naming) option; Step 6 is a reporting convention, not code.
>
> | Step | Status | Notes |
> |---|---|---|
> | 1 — env-driven judge in `config.py` | ✅ applied | Defaults verified unchanged when `TAU2_JUDGE_*` are unset |
> | 2 — tolerant JSON parsing | ✅ applied | `_parse_judge_response()`; unit-checked on bare/fenced/padded/empty/garbage input |
> | 3 — `.env` | ✅ applied | **Key deliberately omitted** — see deviation below |
> | 4 — validation | ✅ passed | Direct judge probe + `tau2 evaluate-trajs --fresh-tasks` on a 6-sim retail slice |
> | 5 — record the judge | ➖ minimal option | Encode in `--save-to`; the `Info` field remains available if you want it later |
> | 6 — comparability | ➖ convention | Score retail twice when reporting |
>
> **Deviation from the plan, on purpose:** `TAU2_JUDGE_API_KEY` is *not* written to `.env`. When it
> is unset, `_build_nl_assertions_args()` omits `api_key` entirely and LiteLLM falls back to
> `OPENAI_API_KEY` from the environment — which you exported. So no key is written to disk anywhere,
> and the value was never read or printed during implementation. Set `TAU2_JUDGE_API_KEY` explicitly
> only if the judge ever needs a *different* key from the agent/user models.
>
> **Verification evidence**
> - Judge probe returned correct verdicts including a negative control
>   ("agent offers a free upgrade" → `met=False`).
> - `tau2 evaluate-trajs --fresh-tasks` on 6 retail simulations produced `NL_ASSERTION` in the reward
>   breakdown with 7 assertion verdicts and **no** `JSONDecodeError`.
> - `ruff check` + `ruff format --check` clean on both changed files.
> - Core suite: **234 passed, 17 failed** — all 17 are pre-existing `AuthenticationError`s from tests
>   that hardcode OpenAI-hosted models and hit `api.openai.com`; reproduced identically with the
>   changes stashed, so they are environmental and unrelated.
> - Known cosmetic noise: LiteLLM logs `This model isn't mapped yet. model=us/azure/openai/gpt-4.1`
>   on every judge call. That is the cost lookup only; it returns `0.0` and does not affect scoring.

**Judge target**
- Base URL for LiteLLM: `https://inference-api.nvidia.com/v1` — use the **`/v1` form, not
  `/v1/chat/completions`**. LiteLLM's OpenAI handler appends `/chat/completions` itself; passing the
  full path yields a 404 on `.../v1/chat/completions/chat/completions`.
- Model string: **`openai/us/azure/openai/gpt-4.1`**
- Key: `sk-_____` (kept out of source; loaded from `.env`)

**Routing verified locally.** `litellm.get_llm_provider("openai/us/azure/openai/gpt-4.1")` resolves to
`('us/azure/openai/gpt-4.1', 'openai', None, None)` — provider `openai`, model sent on the wire
`us/azure/openai/gpt-4.1`. A completion with `api_base` + `response_format={"type":"json_object"}` is
accepted and dispatched. No code change is needed to *route* the model; the change below is only to
make the judge **configurable**, since it is currently hardcoded.

---

## Why a code change is required at all

The agent and user simulator are configurable from the CLI (`--agent-llm`, `--agent-llm-args`), but
the judge is not:

- `src/tau2/config.py:24-26` hardcodes `DEFAULT_LLM_NL_ASSERTIONS = "gpt-4.1-2025-04-14"` and its args.
- `src/tau2/evaluator/evaluator_nl_assertions.py:8` imports those constants at module load and uses
  them at the single call site, line 121-125.
- There is **no** CLI flag and **no** environment variable for either value.

So the judge can only be changed by editing `config.py`. The plan below makes it env-var driven so no
credentials land in git and the default behaviour is unchanged when the vars are unset.

Confirmed single call site: `FullDuplexNLAssertionsEvaluator.evaluate_nl_assertions`
(line 233) delegates to `NLAssertionsEvaluator.evaluate_nl_assertions`, so patching one place covers
both text and voice paths.

---

## Step 1 — make the judge configurable in `src/tau2/config.py`

`config.py` currently has zero imports. Add `os` + `load_dotenv` (both already core dependencies;
`python-dotenv` is in `pyproject.toml`).

Replace lines 24-26:

```python
DEFAULT_LLM_NL_ASSERTIONS = "gpt-4.1-2025-04-14"
DEFAULT_LLM_NL_ASSERTIONS_TEMPERATURE = 0.0
DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"temperature": DEFAULT_LLM_NL_ASSERTIONS_TEMPERATURE}
```

with:

```python
DEFAULT_LLM_NL_ASSERTIONS = os.getenv("TAU2_JUDGE_MODEL", "gpt-4.1-2025-04-14")
DEFAULT_LLM_NL_ASSERTIONS_TEMPERATURE = 0.0


def _build_nl_assertions_args() -> dict:
    """Judge LLM args. Endpoint/credentials come from the environment so that
    switching the judge to an OpenAI-compatible gateway needs no code edit."""
    args: dict = {"temperature": DEFAULT_LLM_NL_ASSERTIONS_TEMPERATURE}
    if base_url := os.getenv("TAU2_JUDGE_BASE_URL"):
        args["api_base"] = base_url
    if api_key := os.getenv("TAU2_JUDGE_API_KEY"):
        args["api_key"] = api_key
    if os.getenv("TAU2_JUDGE_JSON_MODE", "1") == "1":
        args["response_format"] = {"type": "json_object"}
    return args


DEFAULT_LLM_NL_ASSERTIONS_ARGS = _build_nl_assertions_args()
```

and at the top of the file:

```python
import os

from dotenv import load_dotenv

load_dotenv()  # judge settings below are read from the environment at import time
```

Notes:
- `load_dotenv()` is idempotent and safe. `tau2/utils/utils.py:13` already calls it, and in practice
  it runs first (verified: `tau2.utils.utils` is in `sys.modules` by the time `config.py` executes),
  but `config.py` reads env vars at module scope so the explicit call removes any dependence on
  import order.
- `response_format` is gated behind `TAU2_JUDGE_JSON_MODE` (default on). `litellm.drop_params = True`
  is already set in `llm_utils.py:69`, so if a gateway rejects the parameter LiteLLM drops it rather
  than failing. The judge's system prompt already contains the literal word "JSON", which OpenAI's
  json_object mode requires.
- Defaults are byte-for-byte the previous behaviour when no `TAU2_JUDGE_*` var is set — nothing
  breaks for anyone not opting in.

## Step 2 — harden judge JSON parsing in `src/tau2/evaluator/evaluator_nl_assertions.py`

Line 127 is currently `result_data = json.loads(assistant_message.content)` with no guard. If the
judge ever returns a ```json fence, a preamble, or `None` content, this raises and takes down the
whole evaluation (and with it the reward for that simulation). Add a small helper and call it:

```python
def _parse_judge_response(content: str | None) -> dict:
    """Parse the judge's JSON reply, tolerating markdown fences and prose padding."""
    if not content:
        raise ValueError("NL assertions judge returned empty content")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : start + (end - start + 1)])
```

This is defensive only — it does not change scoring when the judge complies, which GPT-4.1 does.

## Step 3 — configure `.env`

```bash
# --- NL-assertions judge (Inference Hub GPT-4.1) ---
TAU2_JUDGE_MODEL=openai/us/azure/openai/gpt-4.1
TAU2_JUDGE_BASE_URL=https://inference-api.nvidia.com/v1
TAU2_JUDGE_API_KEY=sk-_____
```

`.env` is already covered by `.gitignore`. Keeping the judge on its own `TAU2_JUDGE_*` variables —
rather than the global `OPENAI_BASE_URL` — means the judge, the agent under test, and the user
simulator can each point at a different endpoint independently.

## Step 4 — validate without re-running simulations

`tau2 evaluate-trajs` re-scores saved trajectories, so the judge swap can be validated against
existing reference results for a few cents:

```bash
# retail is the only domain where the judge affects the score
cp data/tau2/results/final/gpt-4.1-2025-04-14_retail_default_gpt-4.1-2025-04-14_4trials.json /tmp/judge_check.json
uv run tau2 evaluate-trajs /tmp/judge_check.json
```

Pass criteria:
1. No `JSONDecodeError` / empty-content errors.
2. The recomputed retail reward is close to the value the file already carries (that file was scored
   with OpenAI-hosted `gpt-4.1-2025-04-14`). A few tasks flipping is expected — the judge is
   non-deterministic at the margin even at `temperature=0`. A large systematic gap means the Azure
   `gpt-4.1` deployment is materially different and the judge swap must be reported alongside results.

Also worth one direct probe before the full run:

```bash
uv run python -c "
from tau2.evaluator.evaluator_nl_assertions import NLAssertionsEvaluator as E
from tau2.data_model.message import AssistantMessage, UserMessage
traj=[UserMessage(role='user',content='Cancel my order.'),
      AssistantMessage(role='assistant',content='Done, order #123 is cancelled. Refund in 5-7 days.')]
print(E.evaluate_nl_assertions(traj,['The agent confirms the cancellation.','The agent mentions a refund timeline.']))
"
```

## Step 5 — record which judge scored a run

`Info` (`src/tau2/data_model/simulation.py:1246`) records `agent_info` and `user_info` but **not** the
judge, so a results file scored with a swapped judge is indistinguishable from one scored with the
default. Two options:

- **Minimal (recommended now):** encode it in the run name, e.g.
  `--save-to nemotron35lightning_retail_judge-ihub-gpt41`.
- **Proper:** add an optional `nl_assertions_judge: Optional[str]` field to `Info`, populated from
  `DEFAULT_LLM_NL_ASSERTIONS`. Optional with a default keeps old results files loadable.

## Step 6 — comparability

Scores produced with a non-default judge are not directly comparable to the public leaderboard.
Since re-scoring is cheap, score retail **twice** and report both:

```bash
uv run tau2 evaluate-trajs data/simulations/<run> -o data/simulations/<run>_judge-ihub
```

`airline`, `telecom` and `banking_knowledge` are unaffected by the judge (see the task-count table
below), so their numbers stay comparable regardless.

---

## Files touched

| File | Change | Risk |
|---|---|---|
| `src/tau2/config.py` | judge model/args become env-driven; add `os` + `load_dotenv` imports | Low — defaults unchanged |
| `src/tau2/evaluator/evaluator_nl_assertions.py` | tolerant JSON parsing; add `import re` | Low — defensive only |
| `.env` | three `TAU2_JUDGE_*` vars | None (gitignored) |
| `src/tau2/data_model/simulation.py` | *(optional, Step 5)* record judge in `Info` | Low — optional field |

No changes needed to `cli.py`, `run.py`, or the evaluator dispatch.
