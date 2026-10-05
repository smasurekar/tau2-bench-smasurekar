# τ³ Voice leaderboard submission: requirements, audit and action plan

How a submission to the τ³ Voice leaderboard
(<https://taubench.com/leaderboard/?benchmark=voice>) works, what the rules
are, how our current evaluation setup measures up against them, and the
changes needed for a **standard** (or, failing that, an honestly disclosed
**custom**) submission.

- Status as of 2026-10-04.
- Upstream reference: `sierra-research/tau2-bench` `main` @ `5bfa7e3`
  (2026-09-28). `docs/leaderboard-submission.md` in this fork matches upstream
  (last rewrite: commit `0440136`, "Clarify standard submission boundaries
  (#483)", 2026-08-18).
- Systems audited:
  - τ-bench fork: this repo, branch `dev/smasurekar/prototypes`.
  - Voice agent: `nemotron-voice-agent-smasurekar`, branch
    `dev/smasurekar/agent-prototypes` (HEAD `7bc823a` at audit time),
    prototypes `src/prototypes/voice_delegation_hermes_agent/` (FDH) and
    `src/prototypes/voice_frontend_backend_agent/` (FBA).
  - Runbooks: `voice-frontend-delegation-hermes-tau3-runbook.md`,
    `voice-frontend-backend-agent-tau3-runbook.md` (this folder), and in the
    agent repo `misc/prototypes/frontend-delegation-hermes/runbook.md`,
    `misc/prototypes/voice/runbook.md`.

> **Bottom line:** the current evaluation is **not** valid for a standard
> submission, and not even for a correctly labelled custom one as-is (the
> recorded metadata is wrong). The blockers are mostly on the **τ-bench
> side** (non-official user simulator, swapped judge, swapped user LLM). On the
> **agent side** the architecture is fine, but some prompt content and fixes
> were derived from the scored `base` tasks and must be cleaned up or
> disclosed.

---

## Contents

1. [How submission works](#1-how-submission-works)
2. [Per-domain vs single submission; scoring](#2-per-domain-vs-single-submission-scoring)
3. [Standard vs custom](#3-standard-vs-custom)
4. [Voice-specific requirements](#4-voice-specific-requirements)
5. [Precedents: cascaded / multi-agent entries](#5-precedents-cascaded--multi-agent-entries)
6. [Our setup as it stands](#6-our-setup-as-it-stands)
7. [Audit: τ-bench side](#7-audit-τ-bench-side)
8. [Audit: voice agent side](#8-audit-voice-agent-side)
9. [Can each domain have different code/config?](#9-can-each-domain-have-different-codeconfig)
10. [Deep dive: telecom phone-number dashes](#10-deep-dive-telecom-phone-number-dashes)
11. [Requirements checklist (fulfilled / to address)](#11-requirements-checklist)
12. [Action plan for a standard submission](#12-action-plan-for-a-standard-submission)
13. [Alternative: custom submission](#13-alternative-custom-submission)
14. [Submission mechanics step by step](#14-submission-mechanics-step-by-step)
15. [`submission.json` fields to fill](#15-submissionjson-fields-to-fill)
16. [Open questions for Sierra](#16-open-questions-for-sierra)
17. [References](#17-references)

---

## 1. How submission works

**You submit results (trajectories + a JSON summary), not code.** Sierra
does not run a code submission. However, for voice, Sierra in practice
**re-runs the evaluation itself**, so your system must be runnable by them.

Mechanism (`docs/leaderboard-submission.md`, "DOC" below):

1. Run `tau2 run --audio-native --speech-complexity regular --verbose-logs ...`
   once per domain.
2. `tau2 submit prepare <run dirs> --output <dir> [--voice]` builds
   `submission.json` plus a trajectories directory (one experiment directory
   per domain, each with `results.json`, `simulations/`, and
   `artifacts/task_*/sim_*/audio/` canonical audio only).
3. `tau2 submit validate <dir>`.
4. Open a PR to `sierra-research/tau2-bench` that adds **only**
   `web/leaderboard/public/submissions/<model>_<org>_<YYYY-MM-DD>/submission.json`
   and appends that directory name to the `voice_submissions` array in
   `web/leaderboard/public/submissions/manifest.json`.
5. **Trajectories are not committed.** Host them yourself (HuggingFace,
   Google Drive, …) and link them in the PR description; a maintainer copies
   them to `s3://sierra-tau-bench-public`.

Maintainer verification (`src/tau2/scripts/leaderboard/MAINTAINER.md`,
`review_submission.py`): they download the trajectories, validate format,
task coverage and trial counts, **recompute pass^k** and compare with the
submitted numbers. For voice they **recompute `interaction_metrics`** and
overwrite whatever was submitted.

Voice-specific stance (DOC ~line 187):

> "The voice user simulator is a multi-component system … Because of this
> complexity, we recommend that you **open a PR and contact us** so we can
> coordinate running the evaluation."

and (DOC ~line 222):

> "Sierra runs all final/published evaluations with its own voices to ensure
> parity across leaderboard results."

Observed practice:

- Pine (PR #481): "independently evaluated by Sierra" on the full base splits.
- Pickle (PR #386): 10-task parity check with Sierra-internal held-out voices,
  plus a partial full re-run.

**Consequence for us:** plan for Sierra to run our server. It needs to be
reachable (endpoint) or reproducible (container plus pinned models).

Adapters:

- **Existing provider** (OpenAI, Gemini, xAI, …): open a PR with
  `submission.json`, contact Sierra, and link trajectories if you ran it
  yourself.
- **New provider (no adapter):** implement an audio-native adapter in
  `src/tau2/voice/audio_native/`, open a PR with it, and coordinate.
- **Our case:** we connect through the **existing OpenAI-Realtime provider**
  via its built-in `pine-` path
  (`src/tau2/voice/audio_native/openai/provider.py:38-40, 139-148`): model names
  starting with `pine-` go to `$PINE_REALTIME_BASE_URL?model=<tag>` with bearer
  `$PINE_API_KEY`. Pine's own standard entry used the same path. **No new
  adapter is needed**, though it is worth asking Sierra whether they want a
  named/generic "OpenAI-Realtime-compatible endpoint" path instead of reusing
  `pine-`.

---

## 2. Per-domain vs single submission; scoring

- **One `submission.json` covers several domains**: `results` has optional
  keys per domain.
- **Partial coverage is allowed** (DOC line 18): "You may submit results for a
  single domain; the leaderboard ranks submissions per domain."
- **Each domain exactly once** (DOC line 20); `prepare` errors on duplicates.
- Voice tabs: **Overall, Retail, Airline, Telecom, Banking**
  (`web/leaderboard/src/components/Leaderboard.jsx` ~45-79).
- **Voice Overall = unweighted mean of pass^1 over retail, airline, telecom**
  (whichever are present; LB.jsx ~951-958). **Banking is excluded from
  Overall.** Banking-only entries exist.
- Metric: **pass^1** in practice (DOC line 23: "Voice submissions typically
  only report Pass^1 … higher Pass^k values may be `null`").
- There is also an **interaction** ranking mode: response/yield latency,
  response/yield rate, agent interruption rate, selectivity for backchannels /
  vocal tics / non-directed speech. Sierra recomputes these.
- The voice tab shows standard **and** custom entries by default
  (`setShowCustom(true)` on switching to voice).
- "Unverified" badge for voice (LB.jsx ~435): an entry is verified iff
  `omitted_questions === false` **and** (`submission_type === 'custom'` **or**
  `modified_prompts === false`).

---

## 3. Standard vs custom

### 3.1 What every standard submission must satisfy (DOC 36-47)

- The standard task set, domain policies, tools, and evaluator.
- The default user simulator and evaluation protocol for the track.
- No benchmark-side prompt, tool, orchestration, task-selection, or grading
  modifications.
- No access to hidden task goals, reference actions, evaluator state, or
  other benchmark data outside the standard agent interface.
- A model or system that was **not trained specifically on τ-bench tasks,
  rewards, or evaluation data**.

### 3.2 Standard τ-voice (DOC 56-73), quoted

> "A standard voice submission presents one τ-voice-compatible agent interface
> to the benchmark and uses the default τ-voice harness, prompts, domain
> policies, tool schemas and results, user simulator, task set, and evaluator.
>
> The voice system may use any internal product architecture behind that
> interface, including proprietary ASR and TTS, multiple models or agents,
> internal prompts and tools, routing, and orchestration. Those implementation
> details do **not** make the submission custom as long as they are contained
> inside the submitted system and require no benchmark-side evaluation
> changes. A transport or protocol adapter that only connects the system to the
> standard τ-voice agent interface is also allowed."

So **our architecture** (Nemotron ASR → Nemotron frontend LLM → Hermes backend
on Nemotron Ultra → Magpie TTS, plus our own internal prompts and tool relay)
**is allowed under standard**, provided it is entirely behind the interface.

### 3.3 What makes a submission custom (DOC 77-92)

Modified scaffolds:

- Routers, ensembles, or additional agents **implemented by the
  benchmark-side submission code**.
- Additional tools beyond the standard τ-bench tool set.
- Modified τ-bench / τ-voice orchestration or control flow.
- Modified benchmark-supplied prompts or system instructions.
- **A non-default user simulator, task selection, speech configuration, or
  evaluation protocol.**

Domain-specific training:

- Models trained or fine-tuned specifically on τ-bench domains.
- Models trained using τ-bench tasks, reward signals, or evaluation data.
- Training data that significantly overlaps τ-bench evaluation scenarios.

### 3.4 Custom submission obligations (DOC 94-100)

1. `submission_type: "custom"`.
2. Comprehensive `methodology.notes`: what was modified, why, and how it works.
3. Link to the implementation in `references` (repo, paper, blog).
4. `methodology.verification.modified_prompts: true` if any
   benchmark-supplied prompt was modified.

### 3.5 The key distinction

- **Benchmark side** (harness, user simulator, judge, tasks, tools, prompts
  τ-bench sends): any change means custom, or invalid if undisclosed.
- **Agent side** (inside our server): free architecture, **but** the system
  must not be tuned specifically on τ-bench tasks, rewards, or evaluation
  data.
- What matters on the agent side is **provenance, not feature names**. A
  generic phone formatter or retry guard is fine. The same rule chosen
  because it fixed failures in the scored `base` run is test-set tuning and
  must at least be disclosed.

---

## 4. Voice-specific requirements

| Requirement | Value | Source |
|---|---|---|
| Speech complexity | `--speech-complexity regular` (not `control`). `prepare` drops non-regular results and aborts if none remain | DOC 23, 252, 282; `prepare_submission.py` ~493-515 |
| Task split | `base` (default), **all tasks**, no `--task-ids` / `--num-tasks` | DOC 21, 25 |
| Task counts (base) | retail 114, airline 50, telecom 114, banking_knowledge 97 | live entries |
| Trials | 4+ "strongly preferred" in general; **voice entries all use 1 trial / pass^1** | DOC 22-23 |
| User simulator version | `VOICE_USER_SIMULATOR_VERSION = "v1.0"` (`src/tau2/config.py:126`), git tag `voice-user-sim-v1.0`; set `methodology.user_simulator: "v1.0"` | DOC 189, 634 |
| User LLM | default `gpt-4.1-2025-04-14` (`DEFAULT_LLM_USER`, `config.py:27`) | config |
| User decision model (backchannel / interruption) | `gpt-4.1` (`VOICE_USER_SIMULATOR_DECISION_MODEL`, `config.py:127`) | config |
| Hallucination / review model | `claude-opus-4-5` (`DEFAULT_LLM_EVAL_USER_SIMULATOR`, `config.py:66`) | config |
| NL-assertions judge | `gpt-4.1-2025-04-14` (upstream hardcoded) | upstream config |
| User TTS | ElevenLabs `eleven_v3`, persona voices | DOC 187, 206-222 |
| User-side transcription | Deepgram | DOC 187 |
| Voices for local runs | Sierra's voice IDs are internal; create your own with `python -m tau2.voice.scripts.setup_voices` (writes `TAU2_VOICE_ID_*` for `.env`) | DOC 206-222, `docs/voice-personas.md` |
| Logging | `--verbose-logs` (audio + tick data needed for verification) | DOC 253 |
| Tick / max time / seed | 0.2 s / `max_steps_seconds` 1200 / seed 300 (defaults) | `config.py:15, 138-139` |
| banking_knowledge retrieval | default `alltools` (non-default is shown as a `retrieval_config` badge and is not comparable) | AGENTS.md, leaderboard |
| Consistency | **same agent model and user simulator with identical args across all domains**; `prepare` hashes `agent_info.llm/llm_args` and `user_info.llm/llm_args` | DOC 19; `prepare_submission.py` ~146-161 |
| τ-bench version | `tau2_bench_version` "1.0.0" or "1.0.1" (banking results before 1.0.1 not comparable) | README |

Every leaderboard entry that changed the user LLM (e.g. to gpt-5.5 xhigh) is
labelled **custom / "not comparable"**.

---

## 5. Precedents: cascaded / multi-agent entries

These show what Sierra accepts as standard.

- **Pine Voice Preview: standard** (`pine-voice-preview-user-sim-v1-0_pineai_2026-08-17`,
  PR #481).
  - Architecture: "customized ASR → LLM → TTS pipeline coordinated by a
    custom interaction model" plus a background agent (gemini-3.5-flash)
    making tool calls.
  - "No files in the tau-voice repository are modified. Pine prepends a
    scaffold system message that defines its internal agents' identities…"
  - Connected through the OpenAI-Realtime-compatible adapter (trajectory
    names `*_regular_openai_pine-voice-preview`).
  - Independently re-run by Sierra.
  - A companion entry using a gpt-5.5 user simulator is labelled **custom**.
  - **This is the closest precedent to our system.**
- **LiveKit "Cascaded baseline": standard**, run by Sierra.
  `voice_config.provider: "Cascaded"`, `model: "STT-LLM-TTS"`,
  `pipeline: {asr: "Deepgram nova-3", llm: "OpenAI gpt-4.1", tts: "Deepgram aura-asteria-en"}`.
- **Pickle "grok-voice-think-fast-1.0 + tool-mentor": custom** (PR #386).
  - Mentor LLM gating tool calls **on the harness side**.
  - gpt-5.5 xhigh user simulator.
  - Non-default xAI VAD and input gain.
  - Disclosed via a public fork tag, a methodology doc, and HuggingFace
    trajectories. `verification.details` states standard prompts were
    byte-identical and records Sierra's re-verification.
- **gpt-live-1 (Sierra/OpenAI)**: provider-owned frontend/backend prompt
  templates plus a backend model; the variant on the v1.0 simulator is
  standard.

**Pattern:** internal multi-model / cascaded / frontend-backend architectures
are standard. What makes entries custom is **changing the user simulator** or
**adding harness-side components**.

---

## 6. Our setup as it stands

### 6.1 Voice agent (FDH: `voice_delegation_hermes_agent`)

- **Wire:** OpenAI-Realtime-compatible WebSocket server (`server.py`, port
  8775, `/v1/realtime`). μ-law 8 kHz.
- **ASR / TTS:** local `nemo-speech` container on gRPC :50051.
  - ASR: Nemotron Speech Streaming (`nemotron-speech-streaming-en-0.6b.q8_0.gguf`).
  - TTS: Magpie (`nvidia/magpie_tts_multilingual_357m`).
  - VAD: Silero (energy VAD in stub mode).
- **Frontend LLM:** `nvidia/nvidia/nemotron-3.5-lightning` via Inference Hub,
  thinking off, temperature 0 (`config/delegation_agent.yaml:16-23`). It only
  routes: one forced `delegate(...)` call per user turn, and speaks
  fillers/status lines.
- **Backend:** gateway (`sidecar/gateway_server.py`, :8790) spawning a Hermes
  `AIAgent` worker per session.
  - Hermes fork `../hermes-agent-smasurekar` @ `af26acab73` (~v0.21.0).
  - Model `nvidia/nvidia/nemotron-3-ultra`, thinking on, `reasoning_budget`
    1024, `max_iterations` 30 (`config/gateway.yaml:32-52`).
- **Tools:** exactly the τ-bench session tools. Hermes builtins are disabled;
  only toolset `fdh_client`; `skip_memory=True`; `check_tool_surface` asserts
  the match (`worker/hermes_adapter.py:109-148, 209-257`). Calls go back to
  τ-bench as Realtime `function_call` items (`executor: wire`).
- **Policy:** `session.update.instructions` goes verbatim into
  `<policy>…</policy>` for the **backend only**. It is wrapped by our own text
  (SOUL, voice/spelling paragraph, `<domain_notes>`, `<spoken_output>`,
  `<consent>`). The frontend gets capability lines built from tool
  descriptions.
- **No** DB access, **no** τ-bench imports in the server (only in offline
  CLIs), **no** oracle data, **no** text bypass of the audio channel. The only
  text injection is the fixed greeting "Hi! How can I help you today?"
  (`voice/tau3.yaml:9-11`), because τ-bench greets in text.
- **Variants** are selected only by config files (`FDH_PROFILE`,
  `FDH_GATEWAY_CONFIG`); control = `tau3_eval_baseline.yaml` +
  `gateway.baseline.yaml`. There are **no per-domain branches or builds**.
- The older FBA prototype (`voice_frontend_backend_agent/`) uses the same
  ASR/TTS and models, a plain-LLM backend, and appends a
  `cascade_voice_addendum` to the policy.

### 6.2 τ-bench invocation (FDH, `misc/prototypes/fdh_voice_eval/fdh_lib.sh:78-86`)

```bash
PINE_REALTIME_BASE_URL=ws://localhost:8775/v1/realtime PINE_API_KEY=unused \
uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain $domain --audio-native \
  --audio-native-provider openai --audio-native-model pine-fdh-voice-dlg-<domain>-<cx>[-<tag>] \
  --speech-complexity regular --user-llm openai/azure/openai/gpt-5.2 \
  --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1"}' \
  --review-model openai/azure/openai/gpt-5.2 \
  --task-split-name base --num-trials 1 --max-concurrency <1 mock | 4 otherwise> \
  --save-to fdh_voice_dlg_<domain>_<cx>[_tag] --verbose-logs \
  [--retrieval-config bm25 for banking_knowledge]
```

FBA arms (`voice-frontend-backend-agent-tau3-runbook.md:451-469`) use the same
flags with `--max-concurrency 1`.

### 6.3 Run state at audit time

Campaign `2026-10-01_09-31-09Z_fdh-voice-identity`:

| Run | State | Pass^1 | Notes |
|---|---|---|---|
| `fdh_voice_dlg_airline_regular_identity` | 50/50 | 0.66 | 44 `user_stop`, 6 `agent_stop`. **13 hallucination-discarded sims.** One crash (user-TTS `httpx.ReadTimeout` in `tau2_ihub_overrides.py:124` during task 43's 3rd hallucination re-run), resumed; task 43 restarted with a fresh retry budget |
| `fdh_voice_dlg_retail_regular_identity` | 20/114 (incomplete) | — | 3 hallucination-discarded |
| telecom, banking_knowledge | not run | — | — |

No best-of selection across reruns was found. `tau2 submit prepare` has never
been run on these outputs.

---

## 7. Audit: τ-bench side

These are the blockers. Any of them alone rules out a standard submission.

### 7.1 Non-official user simulator: monkeypatch launcher (**blocker**)

All runs go through `misc/prototypes/fba_voice_eval/tau2_ihub.py`, which
imports `tau2_ihub_overrides.py` and patches τ-bench at runtime:

- **L159**: `tau2.voice.synthesis.synthesize.tts_elevenlabs = ihub_tts`. User
  voice becomes OpenAI `gpt-4o-mini-tts` (Inference Hub) instead of ElevenLabs
  `eleven_v3`.
  - Persona→voice remap: L56-64.
  - Text rewrite (L102-115): `[pause]` → "...", other tags stripped,
    `[cough]`/`[sneeze]`/`[sniffle]` → sound-words ("Ahem!", "Hkh-hkh"). This
    changes vocal-tic realism and therefore the vocal-tic selectivity metric.
  - 15 s timeout, `max_retries=0` (L96-99).
- **L160**: backchannel/interruption decision calls → `openai/azure/openai/gpt-4.1`
  on the Hub (same nominal model, different endpoint).
- **L161-167**: hallucination check, `--auto-review` judges and
  `classify_authentication` → `gpt-5.2` (upstream default
  `claude-opus-4-5`). This model decides **which sims are discarded and
  re-run**.
- **L170-182**: Realtime client websocket keepalive disabled
  (`ping_interval=None`).

The launcher itself logs: *"USER TTS OVERRIDE … (non-official user voices; not
leaderboard-comparable)"* (`tau2_ihub.py:14-17`). The FBA runbook says the
same (`voice-frontend-backend-agent-tau3-runbook.md:719-731`).

### 7.2 User LLM swapped (**blocker**)

`--user-llm openai/azure/openai/gpt-5.2`. The v1.0 voice simulator default is
`gpt-4.1-2025-04-14`. Every entry with a swapped user LLM is labelled custom.

### 7.3 NL-assertions judge swapped and parser changed (**blocker**)

The only two `src/` files that differ from `upstream/main`:

- `src/tau2/config.py`:
  - `load_dotenv()` at import.
  - Judge model from `TAU2_JUDGE_MODEL`; `api_base` / `api_key` from
    `TAU2_JUDGE_BASE_URL` / `TAU2_JUDGE_API_KEY`.
  - `response_format=json_object` added **by default** (`TAU2_JUDGE_JSON_MODE`
    defaults to "1"). Even with no env vars set, the judge call is therefore
    **not** identical to upstream.
  - Current `.env`: `TAU2_JUDGE_MODEL=openai/azure/openai/gpt-5.2` (Hub),
    `TAU2_JUDGE_JSON_MODE=0`. `.env.gpt41-judge.bak` points to Hub `gpt-4.1`.
  - The diff also changes the Gemini input rate 16000 → 8000 (irrelevant to
    us, but it is a diff).
- `src/tau2/evaluator/evaluator_nl_assertions.py:17-36, 150`: new
  `_parse_judge_response` strips ```` ```json ```` fences and extracts the
  outermost `{...}`. Upstream does `json.loads` directly. This changes
  outcomes wherever upstream would have errored.

The judge model is not recorded in `results.json`, so this would be
**invisible** in a submission.

### 7.4 Metadata misreports the user TTS (**blocker for honesty**)

`tau2 submit prepare` → `_extract_voice_config`
(`src/tau2/scripts/leaderboard/prepare_submission.py:48-71`) reads
`user_info.voice_settings.synthesis_config`. That still says
`provider: elevenlabs, model_id: eleven_v3`, because only the function was
patched. The generated `submission.json` would **falsely claim ElevenLabs**.
Decision and review models are not recorded at all.

### 7.5 Per-domain model tag (**must fix**)

`--audio-native-model pine-fdh-voice-dlg-<domain>-...` varies per domain.
That breaks the "identical args across domains" rule (`prepare` hashes it)
and leaks the domain name to the server. The server appears only to log it
(`server.py:270`, `engine/session.py:178`), but it should go.

### 7.6 banking_knowledge retrieval `bm25` (**must fix or disclose**)

Default/leaderboard is `alltools`. `bm25` would show as a non-comparable
`retrieval_config` badge. Banking is not part of Overall anyway, so it can be
dropped.

### 7.7 Coverage (**must fix**)

Only airline is complete. Retail 20/114; telecom and banking not run. All
must be run on the full `base` split.

### 7.8 Re-runs / resume (**OK, with care**)

- Upstream mechanisms in play:
  - hallucination retries (3), currently judged by gpt-5.2 because of 7.1;
  - resume skips finished `(trial, task, seed)` combinations
    (`src/tau2/runner/batch.py:796-821`);
  - `--auto-resume` re-runs infra errors;
  - `compute_metrics` excludes infra-error sims
    (`src/tau2/metrics/agent_metrics.py:132-143`).
- These are standard behaviour. But **no manual reruns / cherry-picking /
  best-of**: one clean run per domain.
- The airline crash/resume gave task 43 extra attempts. That is acceptable as
  infra recovery, but cleaner to avoid (the crash was caused by the patched
  TTS).

### 7.9 Concurrency (**advisable**)

Concurrency 4 inflates latency, which affects interaction metrics (not
pass^1). Prefer 1, or disclose.

### 7.10 What is clean on the τ-bench side

No changes to task data, policies, DBs (`data/tau2`), tool schemas,
orchestrator, turn-taking/interruption logic, tick parameters, inactivity
limit, metrics, CLI, runner, or voice providers. The fork is `upstream/main`
minus one leaderboard-data commit, plus 18 commits (prototype tooling + the
judge changes above).

---

## 8. Audit: voice agent side

The architecture is allowed. The concerns are **content derived from the
benchmark** and **fixes tuned on the scored tasks**.

### 8.1 Real τ-bench values in live prompts (**must fix**)

| Location | Content | Evidence |
|---|---|---|
| `voice_delegation_hermes_agent/config/prompts.backend.yaml:28` (`spelling_v2`, all domains, on by default) | "spelled letters are already joined (for example ROSSI or IFOYYZ)" | `IFOYYZ` = reservation in airline tasks 9 and 37 (task 9: "cancel two of your upcoming reservations (IFOYYZ and NQNU5R)"; 4 occurrences in airline `tasks.json`). **"Rossi"** appears 13× in airline `tasks.json`, 15× in retail `tasks.json`, and in 33 user IDs across the airline/retail DBs. Added in commit `36df307` (2026-09-30), not in baseline `3a7e04a` |
| `voice_delegation_hermes_agent/config/prompts.yaml:123` (frontend, all domains, **including baseline**) | "Identifiers in user turns may already be written out (for example mia_kim_4397 or a booking code)." | `mia_kim_4397` = user ID of airline task 24, present in `airline/db.json` |
| `voice_frontend_backend_agent/config/prompts.voice.yaml:372-381` (FBA) | "such as mia_kim_4397 … (latest attempt: mia_fancy_4739)" | same |

**ROSSI is not a generic example**, even though it looks like one. It sits
next to `IFOYYZ`, arrived in the same commit, and is a surname used heavily in
the evaluation data. Replace all three with invented values.

Code comments / docstrings only (not sent to a model, but clean up before
sharing the implementation):

- `normalization/transcript.py:24`: "I, F, O, Y, Y, Z and N, Q, N, U, five, R"
  (literally airline task 9).
- `config/profiles/tau3_eval.yaml:17` and `tau3_arm_m3_spelling.yaml:17`:
  "# airline reservation code (e.g. IFOYYZ, K1NW8N)". `K1NW8N` is from
  airline tasks 14 and 23.
- `voice_frontend_backend_agent/config/profiles/tau3_eval.yaml:79`:
  `mia_kim_4397` error string.

Our own plan says examples must be "a generic example rather than a value from
the benchmark" (`tau3-identity-fixes-plan.md:200`). These prompts break that
rule.

### 8.2 Fixes derived from failure analysis on the scored `base` split (**decide: remove, or keep + disclose**)

All of M1-M3, G1-G4, I1, I3, I4 were designed from per-task failure analysis
of earlier runs on the τ³ `base` split, which is the same task set we score.

- `tau3-identity-fixes-mapping.csv` maps failing (domain, task id) → fix
  (e.g. airline 14, 15, 16, 17, 25, 37 → I4; retail 0, 3, 4 → I3).
- The runbook gates arms on named "sentinel" tasks ("airline 0, 26; retail 25,
  60, 65, 80", `runbook.md:415-416`) and reports offline replay counts on
  specific tasks (`runbook.md:55-57`).
- `misc/prototypes/observations/id_error_analysis/`,
  `tau3-failure-fixes-plan.md`, `tau3-identity-fixes-plan.md`, and arm
  profiles `tau3_arm_*` document it.
- Not found: ground-truth actions, answer lists, per-task hints, or DB entity
  lists in the running agent.

Specific items whose rules encode DB formats not stated in the policy or tool
docs:

| Fix | What it does | Where | Issue |
|---|---|---|---|
| **I1 telecom domain note** | "Phone numbers are stored with dashes…" plus retry in XXX-XXX-XXXX | `prompts.backend.yaml:69-85`, enabled by `prompt_features.domain_notes` (`gateway.yaml:61`) | See [§10](#10-deep-dive-telecom-phone-number-dashes) |
| **User-ID pattern** `^[a-z]+_[a-z]+_\d{4}$`, `on_invalid: answer_locally` | Rejects IDs locally | `voice/tau3.yaml:16-33`; `voice_frontend_backend_agent/config/profiles/tau3_eval.yaml:58-80` | All 500 airline and 500 retail IDs have exactly 4 digits, which is a DB fact. The benchmark's own docstring example `sara_doe_496` (3 digits; airline `tools.py:205`, retail `tools.py:49`) would be **rejected** |
| **6-char reservation spelling-hold pattern** `^[A-Za-z0-9]{6}$` | Treats a 6-char run as complete | `profiles/tau3_eval.yaml:16-17` (applies globally) | Matches all 2000 airline reservations; the docs show one example (`ZFA04Y`). Milder: a generic "complete code" heuristic |
| **I3 / I4 recovery hints** | Appends notes after "Error: … not found" outputs of identity tools (I3 mentions zip codes "not five digits", "offer the other method your policy allows") | `tools/result_hints.py:46-67`; texts `prompts.voice.yaml:410-432`; `voice/tau3_recovery_hint.yaml`, `voice/tau3_fixes.yaml:10-13` | Explicitly built as τ³ failure fixes |
| **Already-failed / invalid local answers** | Identical repeat of a failed call → `tool_call_already_failed`; invalid ID → "Not looked up: … Read back what you heard…" | `tools/relay.py` `_screen` (~197-236); `normalization/arguments.py:207-251`; `prompts.voice.yaml:383-408` | The mechanism is internal and fine; its **provenance** is I3/I4 base-split failures |

### 8.3 Tool relay behaviour (**allowed; disclose**)

Verified in `tools/relay.py`:

- `_screen` (before τ-bench): rewrites `get_user_details.user_id` (strip
  " .-", collapse separators, lowercase, spoken forms) and answers
  invalid/already-failed calls locally, so τ-bench never sees those calls.
- `on_function_output` (~line 122): τ-bench returns and **records the real
  result first**. `_annotate` then appends hints to the **copy sent to our
  backend**.

This is **internal agent behaviour**. τ-bench's tool implementation, DB,
recorded result, and evaluator are untouched. It is allowed under "internal
prompts and tools, routing, orchestration". There is **no blanket
pass-through requirement**. Agent-side formatting before a tool call can be
legitimate. Disclose substantive internal transformations. (The concern is
only the **provenance** of specific rules, per §8.2.)

### 8.4 Domain detection (**allowed**)

`backend/controller.py:255-269`, `config/gateway.yaml:63-69`:

- `telecom` ← `get_customer_by_phone`
- `retail` ← `find_user_id_by_name_zip` or `find_user_id_by_email`
- `airline` ← `get_reservation_details`
- mock and banking_knowledge: not listed

Choosing internal guidance from the tool schemas supplied through the
standard interface is part of one agent and **does not need to be removed**.
The concern is what it triggers (benchmark-tuned notes), not the act of
recognising the domain.

### 8.5 VAD (**disclose**)

`config/voice/base.yaml:17-20`: `honor_client_values: false`, 800 ms
end-of-turn silence. τ-bench sends 500 ms. This is an internal voice-system
choice with the harness unchanged, so it is allowed. Record it in
methodology, because it affects latency and interaction metrics.

### 8.6 Other prompt additions (**allowed; disclose**)

`<spoken_output>` adds a "Confirmation override: before a booking,
modification, cancellation, return or exchange, state every detail the policy
requires…" (`prompts.backend.yaml:51-58`). `<consent>` and SOUL are similar.
These are internal prompts wrapping, not replacing, the τ-bench policy, so
they are allowed. Make sure they are generic, not task-derived.

### 8.7 Generic layer (**fine**)

Transcript ITN of spoken identifiers ("underscore", number words), joining
spelled letter/digit runs (`voice/tau3_spelling.yaml:11-12`), 1500 ms
spelling hold (`delegation_agent.yaml:52`), 15 s proactive status line, filler
de-duplication, markdown cleanup. The ITN word lists are generic English only
(`normalization/rules.py:53-115`): no names or airport-code lists.

### 8.8 Reproducibility (**must fix**)

- Speech models are pinned (Magpie `--revision` plus sha256;
  `scripts/download-nemo-speech-models.sh:87, 133-186`).
- **LLMs are floating Inference Hub aliases** (`nemotron-3.5-lightning`,
  `nemotron-3-ultra`) with no checkpoint or date.
- **Hermes** is a private-fork checkout, not pinned in config. The runbook's
  Python requirement moved between 3.13 and 3.14.
- Session fingerprints are logged (`fingerprint_check`,
  `backend_catalog_sha256`, `backend_system_sha256`, `backend_domain`). Good
  for proving one frozen config.
- Code lives only on personal forks (`smasurekar/nemotron-voice-agent-smasurekar`,
  `smasurekar/hermes-agent-smasurekar`); `src/prototypes` is not merged
  upstream (`NVIDIA-AI-Blueprints/nemotron-voice-agent`). Public visibility
  not verified.

### 8.9 Consolidated classification: domain-specific vs generic

Every agent-side behaviour in one place. Three questions per row:

- **Scope:** which domains it actually fires in under the default
  `tau3_eval.yaml` + `gateway.yaml`.
- **Generic?** Would it plausibly exist in a product never exposed to
  τ-bench?
- **Provenance:** was it chosen or tuned from failures on the scored `base`
  split?

Verdict for a standard submission:

- 🟢 keep as is
- 🟡 keep and disclose (or make generic)
- 🔴 remove/replace, or keep only as disclosed custom / after Sierra agrees

| Behaviour | Where | Scope | Generic? | Base-split provenance? | Verdict |
|---|---|---|---|---|---|
| Transcript ITN of spoken identifiers ("underscore", number words, double/triple) | `normalization/rules.py:53-115`, `normalization/transcript.py` | All | Yes (generic English word lists, no names or codes) | General voice robustness | 🟢 |
| Joining spelled letter/digit runs | `voice/tau3_spelling.yaml:11-12` | All | Yes | Partly motivated by τ³ spelling misses | 🟢 (disclose) |
| Spelling hold of 1500 ms | `delegation_agent.yaml:52` | All | Yes | General | 🟢 |
| 6-char alphanumeric "complete code" pattern in the spelling hold | `profiles/tau3_eval.yaml:16-17` | All (written for airline) | Mostly. A generic heuristic, but the length matches all 2000 airline reservations | Yes (the comment cites `IFOYYZ`, `K1NW8N`) | 🟡 make the pattern generic (e.g. 5-8 chars) and clean the comment, or disclose |
| Proactive status line (15 s), filler de-duplication, markdown cleanup | frontend config | All | Yes | General | 🟢 |
| Frontend-only routing (`delegate`), policy only to backend | `prompts.yaml:10-71`, `delegation_agent.yaml:86` | All | Yes (architecture) | No | 🟢 |
| SOUL / voice-call paragraph / `<spoken_output>` "confirmation override" / `<consent>` | `prompts.backend.yaml:11-67` | All | Yes, as long as the wording is generic | Some wording from τ³ analysis (`write_consent`, `spoken_output` arms) | 🟡 disclose; check for task-derived wording |
| Example "ROSSI or IFOYYZ" in the spelling prompt | `prompts.backend.yaml:28` | All | **No.** Literal eval values | Yes | 🔴 replace with invented values |
| Example `mia_kim_4397` | `prompts.yaml:123`; FBA `prompts.voice.yaml:372-381` | All | **No.** Airline task 24 user ID | Yes | 🔴 replace |
| Domain detection from tool names | `backend/controller.py:255-269`, `gateway.yaml:63-69` | telecom / retail / airline (not mock or banking) | Yes, as a mechanism | No | 🟢 (judged by what it triggers) |
| **I1 telecom `<domain_notes>`** ("stored with dashes", 555-123-4567, retry in XXX-XXX-XXXX) | `prompts.backend.yaml:69-85`; `gateway.yaml:61` | **Telecom only** | **No** as written (states a DB fact; example uses the DB prefix) | **Yes** (144 failed calls in the scored telecom run) | 🔴 replace with a domain-agnostic phone-format rule (§10.4) + disclose |
| `get_user_details.user_id` argument canonicalisation (strip " .-", collapse separators, lowercase, spoken forms) | `voice/tau3.yaml:16-33`; `normalization/arguments.py:207-251`; `tools/relay.py` `_screen` | Airline + retail (tools with that name) | Mostly. Generic identifier cleanup | Yes (ID-error analysis) | 🟡 keep as generic normalisation + disclose |
| User-ID regex `^[a-z]+_[a-z]+_\d{4}$` with `on_invalid: answer_locally` | `voice/tau3.yaml:28`; FBA `profiles/tau3_eval.yaml:58-80` | Airline + retail | **No.** Encodes a DB fact (exactly 4 digits); rejects the docstring's own example `sara_doe_496` | Yes | 🔴 remove (or loosen to `\d+` and disclose) |
| Local answer for an identical repeat of an already-failed call (`tool_call_already_failed`) | `tools/relay.py` `_screen`; `prompts.voice.yaml:403-408` | Any tool flagged as permanent-failure (identity tools in practice) | Yes, as a mechanism (loop guard) | Yes (I3/I4) | 🟡 keep as a generic loop guard + disclose |
| I3 / I4 recovery hints appended to "not found" results of identity tools (I3 mentions zip codes "not five digits", "offer the other method your policy allows") | `tools/result_hints.py:46-67`; `prompts.voice.yaml:410-432`; `voice/tau3_recovery_hint.yaml`, `voice/tau3_fixes.yaml:10-13` | Airline (`get_user_details`) + retail (`find_user_id_by_name_zip`, `find_user_id_by_email`, `get_user_details`) | **Partly.** "Read back and ask again" is generic; zip-code and "other method" wording is retail-specific | **Yes** (mapped to failing airline/retail tasks in `tau3-identity-fixes-mapping.csv`) | 🔴 remove, or rewrite domain-neutrally + disclose + ask Sierra |
| Hints added to the backend's copy of tool results (after τ-bench records the real result) | `tools/relay.py` `on_function_output` → `_annotate` | As above | Yes, as a mechanism | — | 🟢 mechanism allowed; content per the rows above |
| VAD 800 ms, `honor_client_values: false` | `config/voice/base.yaml:17-20` | All | Yes | Partly (spelling / turn-taking tuning) | 🟡 disclose |
| Fixed greeting seeded into history | `voice/tau3.yaml:9-11` | All | Yes (mirrors τ-bench's text greeting) | No | 🟢 (disclose) |
| M1-M3 / G1-G4 arm features (spelling, filler, status, write-consent, etc.) | `profiles/tau3_arm_*`, `gateway.*.yaml` | Mostly all | Mostly yes | **Yes** (from `tau3-failure-fixes-plan.md`) | 🟡 disclose provenance in methodology |

Summary:

- **Domain-specific and not generic (🔴):**
  - I1 telecom note;
  - user-ID `\d{4}` regex;
  - I3/I4 hint wording;
  - prompt examples `IFOYYZ` / `ROSSI` / `mia_kim_4397`.
- **Generic mechanisms whose tuning came from the base split (🟡):**
  - ID canonicalisation;
  - loop guard;
  - 6-char pattern;
  - VAD;
  - M/G arm features;
  - spoken-output/consent wording.
- **Clean (🟢):**
  - ITN;
  - spelling join/hold;
  - status lines;
  - routing architecture;
  - domain detection as a mechanism;
  - the hint-injection mechanism;
  - the greeting.

---

## 9. Can each domain have different code/config?

- **Benchmark side: no.** Rule 2 requires the same agent model and
  user-simulator args across domains, enforced by a hash in `prepare`. Our
  per-domain model tag already violates this.
- **Agent side: one frozen system.** Freeze one identifiable server
  configuration and commit for all domains. The system **may react
  differently per domain** to the policy and tools it is given (e.g. tool-name
  based domain detection). **Do not** manually swap builds, profiles,
  `FDH_PROFILE` / `FDH_GATEWAY_CONFIG`, or hidden settings between domain
  runs.
- There is no explicit rule against domain-specific internal prompts. Hand-tuned
  per-domain content close to the "trained specifically on τ-bench" spirit is
  a grey area. Precedents (OpenAI Live templates, Pine scaffold) are
  domain-agnostic. Safest: a domain-agnostic configuration; disclose anything
  else.
- **Current state:** one code path, one config. Domain behaviour comes from
  tool-name detection. Under the default `tau3_eval.yaml` + `gateway.yaml`:
  telecom gets the I1 note; airline/retail get identity handling
  (argument rules, local answers, I3/I4 hints); mock/banking get only the
  generic layer.

---

## 10. Deep dive: telecom phone-number dashes

### 10.1 The facts (verified)

- `get_customer_by_phone` does an **exact string match**:
  `customer.phone_number == phone_number` (`src/tau2/domains/telecom/tools.py:61`).
- All DB numbers are stored dashed: `phone_number = "555-123-2001"`, … in
  `data/tau2/domains/telecom/db.toml`.
- The tool docstring says only "The phone number to search for." and
  `main_policy.md` mentions "phone number" with **no format**.
- The user scenario holds the dashed form (`"555-123-2002"` in `tasks.json`).
  In **text** mode that string reaches the agent verbatim. In **voice** mode
  the user speaks it, ASR yields digits (`5551232002`), and the lookup fails.

So the failure is a real artefact of the voice channel plus the DB format.
Every voice agent faces it, and handling it (noticing the miss, trying another
format, reading back) is part of what the benchmark tests.

### 10.2 Acceptable (generic, product-plausible)

- Canonicalising a spoken 10-digit US number to the standard NANP display
  form `XXX-XXX-XXXX` for **any** phone-type argument in **every** domain.
- A domain-agnostic rule: "if a lookup by a formatted identifier fails, retry
  once with common alternative formats (digits only, dashed) before asking the
  user; never add, remove or change digits."

### 10.3 Problematic: current I1 (`prompts.backend.yaml:69-85`)

Full text:

> "Phone numbers are stored with dashes, in the form 555-123-4567. Always pass
> a phone number to a tool in that form, even if the user did not say "dash".
> If a lookup by phone number fails and the failed argument contains exactly
> ten digits with only the formatting wrong, retry once with those same digits
> in XXX-XXX-XXXX form without asking the user anything; this is a format
> correction, not a guess. Never add, remove or change digits. Otherwise, or
> if that retry fails, read the digits you heard back in groups (for example
> "555, 123, 4567") and ask the user to correct the number before asking for
> any other identifier."

Problems:

1. "**are stored** with dashes" asserts a DB fact the benchmark never gives the
   agent.
2. Gated to **telecom only** via tool-name detection: a one-domain fix, not a
   general behaviour.
3. The example `555-123-4567` shares the `555-123-` prefix used by **every**
   DB number.
4. Derived from **144 failed undashed `get_customer_by_phone` calls in the
   scored telecom run** (`tau3-identity-fixes-plan.md:209-215`, citing
   specific task IDs). That is test-set tuning.

### 10.4 Recommendation

1. Replace I1 with a **domain-agnostic** rule phrased as a **format
   preference**, not a storage fact: "prefer XXX-XXX-XXXX; on a failed lookup
   retry once in digits-only or dashed form; never change digits". Use a
   neutral example (e.g. `415-867-5309`).
2. **Disclose its origin anyway** in `methodology.notes`: "added after
   observing phone-format lookup failures on τ-bench". Making it generic does
   not erase its provenance.
3. If we want the standard label, raise it in the PR/message to Sierra and let
   them decide. The rules do not cover this case explicitly.

---

## 11. Requirements checklist

Legend: ✅ fulfilled · ❌ not fulfilled (blocker) · ⚠️ partially / needs
decision or disclosure

### 11.1 τ-bench side

| # | Requirement | Status | Note |
|---|---|---|---|
| T1 | Unmodified tasks, policies, DBs, tool schemas | ✅ | `data/tau2` identical to upstream |
| T2 | Unmodified orchestrator / turn-taking / ticks / metrics | ✅ | — |
| T3 | Default voice user simulator v1.0 (ElevenLabs `eleven_v3` + Deepgram + persona voices) | ❌ | `tau2_ihub_overrides.py` replaces TTS with `gpt-4o-mini-tts`, rewrites tics |
| T4 | Default user LLM `gpt-4.1-2025-04-14` | ❌ | `gpt-5.2` |
| T5 | Default decision model `gpt-4.1` (official endpoint) | ⚠️ | Same model, Hub endpoint via monkeypatch |
| T6 | Default hallucination/review model `claude-opus-4-5` | ❌ | `gpt-5.2` |
| T7 | Default NL-assertions judge `gpt-4.1-2025-04-14`, upstream parsing | ❌ | `gpt-5.2`, lenient parser, JSON-mode default |
| T8 | Accurate metadata in `results.json` / `submission.json` | ❌ | Claims ElevenLabs; judge/decision/review not recorded |
| T9 | Same agent model / args across domains | ❌ | Model tag contains the domain |
| T10 | `--speech-complexity regular` | ✅ | — |
| T11 | `base` split, all tasks, no filters | ⚠️ | Correct flags; coverage incomplete (only airline done) |
| T12 | `--verbose-logs` | ✅ | — |
| T13 | Banking `alltools` (or omit) | ⚠️ | `bm25` |
| T14 | One clean run per domain, no best-of | ✅ | Airline task 43 re-run after a crash (infra resume) |
| T15 | Defaults for seed / tick / max_steps_seconds | ✅ | 300 / 0.2 / 1200 |
| T16 | Concurrency not distorting interaction metrics | ⚠️ | 4; prefer 1 or disclose |
| T17 | Connects via a standard adapter | ✅ | OpenAI-Realtime `pine-` path |

### 11.2 Agent side

| # | Requirement | Status | Note |
|---|---|---|---|
| A1 | One interface, architecture behind it | ✅ | — |
| A2 | Only τ-bench tools exposed, no extra tools | ✅ | `check_tool_surface` |
| A3 | No DB / task / oracle / evaluator access | ✅ | — |
| A4 | No text bypass of audio | ✅ | Only the fixed greeting |
| A5 | τ-bench policy passed through (not modified) | ✅ | Wrapped, verbatim inside `<policy>` |
| A6 | No τ-bench task values in prompts | ❌ | `IFOYYZ`, `ROSSI`, `mia_kim_4397` |
| A7 | Not tuned specifically on τ-bench eval tasks | ❌/⚠️ | I1, I3, I4, ID regex, M/G fixes from base-split failure analysis: remove, or disclose and ask |
| A8 | One frozen config/commit across domains | ⚠️ | Supported by design; must be enforced in the runs |
| A9 | Tool relay transformations disclosed | ⚠️ | Allowed; must be written up |
| A10 | VAD override disclosed | ⚠️ | 800 ms vs 500 ms |
| A11 | Pinned/reproducible models and code | ❌ | Floating Hub aliases; Hermes fork unpinned |
| A12 | Runnable/reachable by Sierra | ❌ | Not arranged |
| A13 | Domain-name leakage via model tag | ⚠️ | Logged only; remove with T9 |

---

## 12. Action plan for a standard submission

### τ-bench side (mandatory)

1. Run plain `tau2 run`, **not** `tau2_ihub.py`. This drops the TTS,
   decision-LLM, review-LLM and websocket-ping monkeypatches.
2. Run from a **clean upstream checkout** (`voice-user-sim-v1.0` tag or current
   `main`), or revert this fork's `src/tau2/config.py` and
   `evaluator_nl_assertions.py` changes. The judge goes back to
   `gpt-4.1-2025-04-14` with upstream parsing and no JSON-mode default.
3. Remove `TAU2_JUDGE_*` from `.env`.
4. Default user LLM `gpt-4.1` (v1.0): drop `--user-llm`, `--user-llm-args`,
   `--review-model`. The review model reverts to `claude-opus-4-5`.
5. ElevenLabs `eleven_v3` user TTS with voices from
   `python -m tau2.voice.scripts.setup_voices`, and Deepgram transcription.
   Required keys: `OPENAI_API_KEY` (official OpenAI for `gpt-4.1`),
   `ANTHROPIC_API_KEY` (review model), `ELEVENLABS_API_KEY`,
   `DEEPGRAM_API_KEY`.
6. A single `--audio-native-model` for every domain (e.g.
   `pine-nemotron-voice`); no domain in the name.
7. Banking: default `alltools` retrieval (needs `OPENAI_API_KEY` and the
   sandbox-runtime + `ripgrep bubblewrap socat`), or leave banking out (not in
   Overall).
8. Full `base` split for retail (114), airline (50), telecom (114) [+ banking
   (97)], with `--speech-complexity regular --verbose-logs`; no
   `--num-tasks` / `--task-ids`.
9. `--max-concurrency 1` (or disclose the concurrency used).
10. One clean run per domain. Resuming after an infra crash is fine;
    re-running or cherry-picking is not.

### Agent side

11. Replace `IFOYYZ`, `ROSSI`, `mia_kim_4397` in runtime prompts
    (`prompts.backend.yaml:28`, `prompts.yaml:123`, FBA `prompts.voice.yaml`)
    with invented values. Clean up code comments (`transcript.py:24`,
    `tau3_eval.yaml:17`, FBA `tau3_eval.yaml:79`) before sharing the code.
12. For the base-split-derived fixes (I1, I3, I4, `\d{4}` ID rule, local
    already-failed/invalid answers, M/G fixes), either:
    - **(a) remove** them, which gives the cleanest standard claim; or
    - **(b) keep** them, make them generic (e.g. I1 → domain-agnostic
      phone-format rule, §10.4), **disclose provenance**, and ask Sierra whether
      that is standard or custom. **Do not claim clean standard without that
      conversation.**
13. Keep tool-relay transformations and domain detection; **disclose** them.
14. Freeze **one** server config and commit for all domains; record the
    fingerprints (`backend_system_sha256`, …) per run as evidence.
15. Keep the 800 ms VAD if preferred; **disclose** it.
16. Pin the LLM versions/checkpoints (frontend Nemotron 3.5 Lightning, backend
    Nemotron 3 Ultra) and the Hermes commit; pin the Python version.
17. Make the system runnable by Sierra (hosted endpoint or container plus
    instructions). Decide code visibility (the `references` link).

### Submission

18. `tau2 submit prepare <retail_dir> <airline_dir> <telecom_dir> [<banking_dir>] --output <out> --voice`
19. `tau2 submit validate <out>`
20. Fill in `submission.json` (§15), host the trajectories, open the PR
    (submission + manifest), and contact Sierra.

---

## 13. Alternative: custom submission

If we cannot use ElevenLabs/official models, or want to keep the tuned fixes
without negotiation:

- Items 1-10 are **still strongly advised**. At minimum the metadata must be
  truthful (T8), because the current `results.json` misreports the user TTS.
- `submission_type: "custom"`.
- `methodology.notes` must explain:
  - every τ-bench-side deviation (user TTS, user LLM, decision/review models,
    judge model and parser, endpoints);
  - every agent-side behaviour (relay rewriting, local answers, hints, domain
    notes, VAD);
  - that the fixes came from failure analysis on the `base` split.
- `references`: link to the code (fork tag) and a methodology doc.
- `verification.modified_prompts`: `true` only if τ-bench-supplied prompts
  were modified (they are not; we wrap the policy). `verification.details`
  explains.
- Expect the entry to be shown as **custom / not comparable**.

---

## 14. Submission mechanics step by step

```bash
# 0. Clean τ-bench environment (upstream), keys in .env:
#    OPENAI_API_KEY, ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, DEEPGRAM_API_KEY
python -m tau2.voice.scripts.setup_voices          # paste TAU2_VOICE_ID_* into .env

# 1. Start the frozen agent server (one config for all domains), then per domain:
PINE_REALTIME_BASE_URL=ws://localhost:8775/v1/realtime PINE_API_KEY=unused \
tau2 run --domain retail --audio-native \
  --audio-native-provider openai --audio-native-model pine-nemotron-voice \
  --speech-complexity regular --task-split-name base --num-trials 1 \
  --max-concurrency 1 --verbose-logs --save-to nemotron_voice_retail
# repeat for airline, telecom (and banking_knowledge with default alltools)

# 2. Package
tau2 submit prepare data/simulations/nemotron_voice_{retail,airline,telecom} \
  --output submission_out --voice
tau2 submit validate submission_out
# (optional) tau2 submit interaction-metrics <dirs>   # Sierra recomputes these anyway

# 3. PR to sierra-research/tau2-bench:
#    web/leaderboard/public/submissions/<model>_<org>_<YYYY-MM-DD>/submission.json
#    + append dir name to voice_submissions in manifest.json
#    PR description: trajectory link, methodology, contact; ask Sierra to coordinate a run.
```

Directory naming: `{model}_{org}_{YYYY-MM-DD}`, e.g.
`nemotron-voice-fdh_nvidia_2026-10-XX`.

---

## 15. `submission.json` fields to fill

Schema: `src/tau2/scripts/leaderboard/submission.py`; DOC 422-493.

- `model_name`, `model_organization`, `submitting_organization`,
  `submission_date`, `contact_info`.
- `submission_type`: `"standard"` (default) or `"custom"`.
- `results.{retail,airline,telecom,banking_knowledge}`: `pass_1` (higher
  `pass_k` may be `null`), cost if available.
- `voice_config`:
  - `provider`: e.g. `"Cascaded"` or `"NVIDIA"`
  - `model`: e.g. `"nemotron-voice-fdh"`
  - `tick_duration_seconds`: 0.2
  - `max_steps_seconds`: 1200
  - `user_tts_provider`: `"elevenlabs/eleven_v3"` (must be **true**)
  - `pipeline`:
    - `asr`: "NVIDIA Nemotron Speech Streaming en 0.6b"
    - `llm`: "Nemotron 3.5 Lightning (frontend) + Nemotron 3 Ultra / Hermes (backend)"
    - `tts`: "NVIDIA Magpie TTS multilingual 357m"
- `methodology`:
  - `evaluation_date`
  - `tau2_bench_version`
  - `user_simulator: "v1.0"`
  - `notes`: architecture, internal prompts, relay behaviour, VAD 800 ms,
    concurrency, fix provenance
  - `verification.modified_prompts`
  - `verification.omitted_questions: false`
  - `verification.details`
- `references`: code / methodology links (required for custom).
- `trajectories_available: true` (all live voice entries use `true`; DOC 716
  saying `false` appears stale).
- `reasoning_effort` / `model_release` if applicable (e.g. backend
  `reasoning_budget` 1024).

---

## 16. Open questions for Sierra

1. Do agent-side fixes that were **derived from failure analysis on the `base`
   split** but implemented generically (phone-format retry, identity-lookup
   recovery hints, ID validation) count as standard if disclosed?
2. Is routing through the OpenAI provider's `pine-` path acceptable, or do they
   want a generic OpenAI-Realtime-compatible endpoint option or a new adapter
   PR?
3. How do they want to run our system: hosted endpoint access, or a container
   they run?
4. Is a server-side VAD override (800 ms instead of the client's 500 ms)
   acceptable without a note in the interaction metrics?
5. Is banking with a non-`alltools` retrieval config worth submitting, or should
   it be omitted?

---

## 17. References

- Leaderboard: <https://taubench.com/leaderboard/?benchmark=voice>
- Upstream repo: <https://github.com/sierra-research/tau2-bench>
  - Submission guide: `docs/leaderboard-submission.md`
    (<https://github.com/sierra-research/tau2-bench/blob/main/docs/leaderboard-submission.md>)
  - Voice personas: `docs/voice-personas.md`
  - Leaderboard UI logic: `web/leaderboard/src/components/Leaderboard.jsx`
  - Submissions: `web/leaderboard/public/submissions/` (+ `manifest.json`)
  - Tooling: `src/tau2/scripts/leaderboard/{submission.py, prepare_submission.py, review_submission.py, MAINTAINER.md}`
  - Precedent PRs: #386 (Pickle, custom), #481 (Pine, standard), #483 (standard
    boundaries), #523 / #527 (gpt-live-1 adapters)
- This fork:
  - `src/tau2/config.py`, `src/tau2/evaluator/evaluator_nl_assertions.py`
    (judge diffs)
  - `src/tau2/voice/audio_native/openai/provider.py` (`pine-` routing)
  - `misc/prototypes/fba_voice_eval/{tau2_ihub.py, tau2_ihub_overrides.py}`
    (monkeypatches)
  - `misc/prototypes/fdh_voice_eval/{fdh_lib.sh, campaign.sh}`
  - `misc/prototypes/voice-frontend-delegation-hermes-tau3-runbook.md`,
    `misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md`
  - `src/tau2/domains/telecom/tools.py:49-61`, `data/tau2/domains/telecom/db.toml`
- Agent repo (`nemotron-voice-agent-smasurekar`):
  - `src/prototypes/voice_delegation_hermes_agent/config/{prompts.backend.yaml, prompts.yaml, gateway.yaml, delegation_agent.yaml, profiles/tau3_eval.yaml, voice/base.yaml, voice/tau3*.yaml}`
  - `src/prototypes/voice_delegation_hermes_agent/{tools/relay.py, tools/result_hints.py, backend/controller.py, worker/hermes_adapter.py, server.py}`
  - `src/prototypes/voice_frontend_backend_agent/{config/prompts.voice.yaml, config/profiles/tau3_eval.yaml, normalization/*.py}`
  - `misc/prototypes/frontend-delegation-hermes/{runbook.md, tau3-identity-fixes-plan.md, tau3-identity-fixes-mapping.csv, tau3-failure-fixes-plan.md}`
  - `misc/prototypes/voice/runbook.md`
