# Runbook: τ³ voice evaluation of the Frontend Delegation Agent (Hermes backend)

**Date:** 2026-09-29 · **Status:** runbook and scripts ready. The stack is up (`fdh-voice` on 8775, gateway on 8790,
`nemo-speech`), and the agent runbook's §6 smoke run (`fdh_voice_dlg_mock_control_smoke`) finished clean. On that run,
`report.sh` and `archive.sh` were dry-run into `/tmp`: C1–C5, C7 and C8 PASS, C6 FAILs by design, and the exact join
matched 1/1 ·
**Updated 2026-10-05:** the default arm is now **`geval`**, the domain-agnostic profile (`profiles/realtime_eval.yaml`
with `gateway.eval.yaml`; agent runbook §6.2 and `tau3-voice-domain-specific-changes-genericization.md` in the agent
repo). The scripts check its profile, gateway config and logs; `dlg` and `silentack` remain as historical arms. The
stack runs `geval` (checked with `check_stack.sh`: READY); no `geval` run has been made yet ·
**Updated 2026-10-07:** `report.sh` runs the 429 gate (P0.1 of the agent repo's `tau3-geval-failure-fixes-plan.md`): a
run with a backend rate limit in its Hermes worker logs is invalid and must be re-run (§7, step 4) ·
**Updated 2026-10-09:** the user simulator / judge model is a per-campaign choice between Hub `gpt-5.2` and `gpt-5.5`;
always ask the user which one (§2.2.1, with the exact gpt-5.5 command) ·
**Scripts:** [`fdh_voice_eval/`](fdh_voice_eval/README.md) ·
**Agent runbook (starting the stack):** `nemotron-voice-agent-smasurekar/misc/prototypes/frontend-delegation-hermes/runbook.md` ·
**Agent design:** `nemotron-voice-agent-smasurekar/misc/prototypes/frontend-delegation-hermes/prototype-plan.md` ·
**Reference runbook (the FBA arms; tau2 setup, I0, metric definitions):** [`voice-frontend-backend-agent-tau3-runbook.md`](voice-frontend-backend-agent-tau3-runbook.md)

This runbook takes the running `fdh-voice` server (OpenAI Realtime, port 8775) to a τ³ voice report for
**airline, retail, telecom and banking_knowledge**. The mock domain is used only for the smoke run. It ends with
every log and artifact archived in the dump repository. `src/tau2/` is not modified.

> **Concurrency: `--max-concurrency 4` for every domain except `mock` (1).** `fdh_lib.sh` sets it
> (`fdh_concurrency`). Set `TAU3_CONCURRENCY` to override it.

> **ALWAYS ASK the user which Inference Hub model to use for the user simulator and the judge before any run:
> `azure/openai/gpt-5.2` (the scripts' default) or `openai/openai/gpt-5.5`.** Do not pick one yourself, and do not
> carry a choice over from an earlier campaign. The same model is used for the user simulator, the NL-assertion judge
> and the hallucination check / review model. The two are not comparable (see §2.2.1), so record the choice in the run
> card and keep it fixed for every domain of a campaign. For gpt-5.5, launch with the exact command in §2.2.1.

> **Speech complexity: `regular` for every domain, the mock smoke included.** Every command in this runbook passes
> `regular`. `control` (clean speech) is not used in this campaign. `run.sh` records the speech complexity on each
> console log's first line, and it prints `WARN speech complexity …` for anything other than `regular`.

> **Everything from every run goes to
> `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump/tau-3-voice/`**, under
> `$DUMP/tau-3-voice/<CAMPAIGN>/<run>/` (§9, `archive.sh`). That covers:
>
> - tau2 results, trajectories and audio;
> - the voice server's event log, and the gateway and Hermes worker logs;
> - container logs and configs;
> - the git provenance of all three repos, including the agent's **untracked** prototype source;
> - console output and computed metrics.
>
> A run is not finished until it has been archived.

> **MANDATORY: archive every campaign in the dump folder (§9) before you do anything else to the stack.** This
> covers smoke runs, stopped runs and invalid runs too.
>
> - **Archive before restarting or reconfiguring `fdh-voice` or the gateway, or changing agent code.**
>   `archive.sh` records the *current* container logs, container definition, configs and repo provenance. The
>   container is started with `--rm`, so after a restart the old logs are gone and the snapshot describes the wrong
>   stack.
> - **Finish with `archive.sh --all`, then `archive.sh --reports`, then write `$DUMP/tau-3-voice/$CAMPAIGN/README.md`
>   (the run card).** Do this before starting a new campaign with `new_campaign.sh`.
> - **Set `DUMP` on hosts where the default path does not exist.** For example, on the `/localhome/...` host:
>   `export DUMP=/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump`. `archive.sh` fails without it.
> - **Watch out for `--reports` when several campaigns share `_metrics/`.** It copies *every* `fdh_voice_*` report
>   folder and the current `_setup/`. Check that `_reports/` holds only this campaign's reports.
> - **If a campaign was archived late (after a restart):** move the wrong snapshot files into
>   `_NOT_THIS_RUN_post_restart_snapshot/`. Then add a `PROVENANCE_NOTE.md` giving the real commits and
>   `config_hash` (from `check_stack.sh` output and `fdh_session_start` in the event log). See
>   `2026-09-30_07-05-15Z_fdh-voice` for an example.

## Where commands run

| Tag | Directory |
|---|---|
| **[tau2]** | `/home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar` (every script runs from here) |
| **[agent]** | `/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar` |
| **[hermes]** | `/home/smasurekar/Desktop/Swapnil/github_repos/hermes-agent-smasurekar` (the backend's code; provenance only) |
| **[dump]** | `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump` |

The scripts set their own paths from `fdh_voice_eval/fdh_lib.sh`, which derives `TAU2` from its location and
assumes `AGENT` and `HERMES` are sibling checkouts. For the manual commands in this runbook, set the same variables
in your shell:

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
source misc/prototypes/fdh_voice_eval/fdh_lib.sh     # TAU2 AGENT HERMES DUMP IHUB DOCKER_HOST_IP CAMPAIGN ...
E=misc/prototypes/fdh_voice_eval
```

`CAMPAIGN` is the UTC start time of the campaign plus a label, e.g. `2026-09-30_08-00-00Z_fdh-voice`. It is kept in
its own file, `_consoles/CAMPAIGN_FDH`, so it never overwrites the FBA runbook's `_consoles/CAMPAIGN`. Create it
**once**, when the campaign's first run starts. It refuses to overwrite an existing campaign without `--force`:

```bash
$E/new_campaign.sh              # label defaults to fdh-voice
```

---

## 0. What you are measuring

The metrics are the reference runbook's §0: Pass^1, the tau2 interaction metrics (latency, responsiveness,
interrupts, selectivity), backend per-turn LLM latency, filler voice latency and tokens per task by role. The
agent-side ones come from the voice server's event log, after the report adapter (§7) turns it into the
per-turn records `fba_voice_metrics.py` reads.

**How this agent differs from the FBA arms. Read this before comparing numbers:**

| | FBA `paired` (reference runbook) | **This agent (`geval`, `dlg`)** |
|---|---|---|
| Frontend | nemotron-3.5-lightning, filler **`log_only` (silent)** | nemotron-3.5-lightning, one `delegate(delegate, filler_text, request)` call per turn; filler **spoken** (`speak_when_delegating: true`) |
| Backend | nemotron-3-ultra, one call per turn, in the voice container | a **Hermes `AIAgent`** (nemotron-3-ultra, reasoning on, budget 1024), one worker process per session, behind the host gateway; it can be steered, redirected or asked for status mid-task |
| Filler voice latency | projected (the filler is silent) | **measured** (`filler_timing.user_stop_to_first_audio_ms`, `filler_measured.txt`, §7). The script's *projected* column is only a cross-check |
| C6 (filler mode `log_only`) | PASS | **FAIL by design** (`speak`) |

- **Pass^1 is not comparable with the silent-filler FBA arms**, because the τ³ user hears the filler and can answer
  it. It *is* comparable with the FBA `verdictspk` arm (also spoken). For a silent-filler comparison, run the
  optional `silentack` arm (§3).
- **Not comparable with campaigns before 2026-10-05.** The agent's prompts changed for every arm on that date:
  the telecom phone note became a `<phone_numbers>` block in every domain, the recovery notes were reworded and
  renamed, and the prompt examples use invented values. `geval` also derives its tool-argument rules, result-hint
  targets and spelling-hold patterns from each session's tool schemas. Start a new campaign
  (`new_campaign.sh --force`) and compare only runs made on the same agent commit.
- **Latencies are not comparable with concurrency-1 campaigns.** At concurrency 4, four sessions share one voice
  container, one `nemo-speech`, the gateway and the Hub endpoints. The FBA campaign
  `2026-09-29_04-32-39Z_fba-voice` also ran at concurrency 4.

**Arms and run names.** Every run uses its own `pine-` model tag. The agent logs the tag in `session_start.model`,
which is how the logs of different runs are told apart in the shared log files.

| Arm | Container | Port | Agent profile | Gateway config | Event logs (host paths under [agent]) |
|---|---|---|---|---|---|
| **`geval`** (default) | `fdh-voice` (already running) | 8775 | `profiles/realtime_eval.yaml` (domain-agnostic, filler spoken) | `gateway.eval.yaml` | `logs/fdh_voice_events.realtime_eval.jsonl`, `logs/fdh_gateway_events.realtime_eval.jsonl` |
| `dlg` (historical) | `fdh-voice` | 8775 | `profiles/tau3_eval.yaml` (filler spoken) | `gateway.yaml` | `logs/fdh_voice_events.jsonl`, `logs/fdh_gateway_events.jsonl` |
| `silentack` (optional ablation, §3) | `fdh-voice-silentack` | 8777 | `profiles/tau3_eval_silent_ack.yaml` (filler of a delegated turn not spoken) | `gateway.yaml` | `logs/fdh_voice_silentack_events.jsonl`, `logs/fdh_gateway_events.jsonl` |

`geval` and `dlg` use the same container and port, so only one of them runs at a time. One gateway serves every
running arm, so arms that need different gateway configs (`geval` and `silentack`) cannot run together. Worker
logs go to `logs/fdh_workers/`. Every gateway record and worker log file is keyed by the voice session id
(`sess_…`). The per-arm settings live in `fdh_lib.sh` (`fdh_port`, `fdh_container`, `fdh_profile`,
`fdh_event_log`, `fdh_gateway_config`, `fdh_gateway_log`). `FDH_ARM` sets the default arm (`geval`), and
`FDH_GATEWAY_LOG` overrides the gateway log, and `FDH_WORKER_LOGS` overrides the worker log directory
(`fdh_worker_logs`, default `$AGENT/logs/fdh_workers`) that the 429 gate reads.

To start the `geval` stack, follow the agent runbook §6.2: §3 with `FDH_GATEWAY_CONFIG=gateway.eval.yaml` and
`FDH_GATEWAY_LOG=logs/fdh_gateway_events.realtime_eval.jsonl`, and §4 with `FDH_PROFILE=realtime_eval.yaml` and
`-e FDH_EVENT_LOG=logs/fdh_voice_events.realtime_eval.jsonl`.

Run name = model tag suffix: `fdh_voice_<arm>_<domain>_<complexity>[_<TAU3_TAG>]`, e.g.
`fdh_voice_geval_airline_regular` ↔ `pine-fdh-voice-geval-airline-regular`. Arm names must not contain `_`: the scripts
read the arm from the third `_` field. (`banking_knowledge` contains a `_`; it is the domain, after the arm, so
that is fine.)

**Concurrency.** `mock` runs at 1 and the other four domains at 4. Four is also the capacity of the stack as
configured:

- `fdh-voice` has `max_sessions: 4`;
- the agent's ASR channel has `max_streams=4`;
- the gateway allows 8 sessions (`fdh-voice` + `fdh-voice-web`, or + `fdh-voice-silentack`).

Don't raise the concurrency without raising all three.

---

## 1. Check the running stack [tau2]

`fdh-voice` is already running. The preflight checks it, the gateway, `nemo-speech` and the tau2 setup, and prints
the commits to record. It is read-only:

```bash
$E/check_stack.sh               # the default arm (geval); for the historical arms: check_stack.sh dlg [silentack]
```

What it checks (`PASS` / `WARN` / `FAIL`; exit 1 on any FAIL):

| Area | Checks |
|---|---|
| Voice server | `/health` ok with `prototype: frontend-delegation-hermes`; `max_sessions` ≥ 4; backend link `websocket`; the container runs the arm's profile (`profiles/realtime_eval.yaml` for `geval`); `FDH_EVENT_LOG` is the arm's file; ASR model and `max_streams` |
| Gateway | `/health` ok; `agent_kind: hermes`; backend model and reasoning; the process runs the arm's gateway config (`gateway.eval.yaml` for `geval`) and its `FDH_GATEWAY_LOG`; `/health` `backend_features`, `domains` and `backend_catalog_sha256` equal what that config declares (loaded with the agent's own loader; for `geval`: `phone_format` true, `domain_notes` false, `domains: []`); `max_sessions` ≥ the voice servers' total |
| Speech and browser | `nemo-speech` running. **WARN** if `fdh-voice-web` is running |
| tau2 | I0 files; `OPENAI_API_KEY`, `TAU2_JUDGE_MODEL` and `TAU2_JUDGE_BASE_URL`; pyaudio; rank_bm25; the §2.3 join fix; campaign set; dump folder exists |
| Provenance | commit and dirty-file count of the agent, tau2 and Hermes repos |

**`geval`, 2026-10-05:** every row passed (`READY`) on the stack started per the agent runbook §6.2. `CAMPAIGN`
still named the 2026-10-01 campaign: start a new one before the first `geval` run.

**First run on 2026-09-29:** every agent-stack row passed. It flagged `fdh-voice-web` running (WARN), and it
failed on rank_bm25 and the join fix until §2.1 and §2.3 are done.

**Stop the browser server for the campaign** (recommended). It shares the gateway, the Hub's frontend endpoint and
`nemo-speech`, so any use of the page during a run changes the run's latencies:

```bash
docker stop fdh-voice-web       # --rm removes it; restart it later with the agent runbook §5
```

Notes:

- The backend model is pinned on the **gateway** (host process), not in the container, so it is read from the
  gateway's `/health`.
- The voice server's `fdh_session_start.config.backend.gateway_config` says `gateway.fake.yaml`. That value is
  unused with `backend.link: websocket`. The gateway's `agent_kind: hermes` is what counts.
- If anything is not running, start it with the agent runbook: §2 `nemo-speech`, §3 the gateway, §4 `fdh-voice`.

**After any agent code or config change**, restart `fdh-voice` (and `fdh-voice-silentack`); `src/` is bind-mounted
and re-read at process start. Restart the gateway too if anything under `backend/`, `sidecar/` or `worker/`, a
`gateway*.yaml` file or `prompts.backend.yaml` changed. `check_stack.sh` fails the `/health` row when the running
gateway no longer matches its config. Every run of one campaign must use the same agent code: after a change, start a new
campaign (`new_campaign.sh --force`). Run the unit tests first:

```bash
(cd $AGENT && uv run pytest tests/unit/prototypes/delegation tests/unit/prototypes/voice -q)
```

## 2. One-time tau2 setup [tau2]

### 2.1 Install

```bash
sudo apt install portaudio19-dev ffmpeg          # pyaudio needs portaudio; tau2 imports it at run time
uv sync --extra voice --extra dev --extra knowledge   # knowledge: rank-bm25 for banking_knowledge
```

`uv sync` removes extras you don't list, so keep all three.

### 2.2 Endpoints, keys and I0

These are the same as the reference runbook §2.1–§2.3:

- the user LLM `azure/openai/gpt-5.2`;
- the judge and hallucination check `azure/openai/gpt-5.2`;
- the user TTS `openai/openai/gpt-4o-mini-tts`;
- the user decision LLM `azure/openai/gpt-4.1`.

All four are on the Inference Hub with one `sk-...` key, routed by I0 (`misc/prototypes/fba_voice_eval/tau2_ihub.py`,
which `fdh_run` always uses). **Never use plain `uv run tau2 run`**: tau2 would catch the failed calls and silently
run a degraded user.

`$TAU2/.env` currently has `OPENAI_API_KEY`, `TAU2_JUDGE_MODEL` and `TAU2_JUDGE_BASE_URL`. The other `TAU2_*`
variables are I0's defaults, so the file needs no change. Run the reference runbook §2.3 endpoint check once
(about a minute) if the key or `.env` changed since the last campaign.

#### 2.2.1 Choosing the user simulator / judge model: gpt-5.2 or gpt-5.5 (ask the user every time)

**Before every run, ask the user which Inference Hub model to use: `azure/openai/gpt-5.2` or `openai/openai/gpt-5.5`.**
The choice applies to three roles together: the user simulator LLM, the NL-assertion judge (`TAU2_JUDGE_MODEL`) and the
hallucination check / review model (`TAU2_REVIEW_MODEL`). The user TTS (`gpt-4o-mini-tts`) and the user decision LLM
(`gpt-4.1`) stay unchanged.

- **gpt-5.2** (default): run `run.sh` / `campaign.sh` as written in this runbook. Nothing else to set.
- **gpt-5.5**: `fdh_run` hardcodes gpt-5.2, so override it without editing the scripts. Extra arguments go after the
  defaults and win; the env vars beat `.env` (tau2's `load_dotenv()` does not override). Use a tag so the run names
  differ from gpt-5.2 runs. Exact command (LiteLLM needs the extra `openai/` provider prefix in front of the Hub name):

  ```bash
  M=openai/openai/openai/gpt-5.5          # Hub model openai/openai/gpt-5.5
  TAU2_JUDGE_MODEL=$M TAU2_REVIEW_MODEL=$M TAU3_TAG=gpt55 \
    $E/run.sh geval <domain> regular \
      --user-llm $M \
      --user-llm-args '{"temperature": 0.0, "api_base": "https://inference-api.nvidia.com/v1"}' \
      --review-model $M
  ```

  In tmux, also pass `-e OPENAI_API_KEY="$OPENAI_API_KEY"` (and `DUMP` on the `/localhome` host), and keep
  `TAU2_JUDGE_MODEL`/`TAU2_REVIEW_MODEL` exported in the shell that runs `report.sh` and `archive.sh`, so the results
  table names the right judge: `export TAU2_JUDGE_MODEL=openai/openai/openai/gpt-5.5`.

**Verify it took effect** (the `.env` still says gpt-5.2):

- the console log shows `REVIEW LLM OVERRIDE: openai/openai/openai/gpt-5.5` and `User: … → openai/openai/openai/gpt-5.5`;
- the call logs: `cat data/simulations/<run>/artifacts/*/sim_*/llm_debug/*nl_assertions_eval* | grep -o '"model": "[^"]*"' | sort | uniq -c`
  shows gpt-5.5 (also check `*user_streaming_response*`);
- `provenance/tau2_endpoints.txt` in the archive records the `.env` value (gpt-5.2), **not** the model used. Write the
  real judge in the run card.

**Report with gpt-5.5:** `report.sh --tag gpt55 <every finished domain>`. All domains with the same tag share
`_metrics/fdh_voice_<arm>_<cx>_gpt55/`, so a single-domain call overwrites the others' report.

**gpt-5.2 and gpt-5.5 numbers are not comparable.** On the 2026-10-08 campaign
(`2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr`), same agent and stack: retail 0.658 vs 0.623, airline 0.780 vs 0.740,
but telecom 0.439 vs 0.725 (clean tasks). The gpt-5.5 user follows "only share information when asked" (it doesn't
volunteer being abroad, so roaming is never fixed) and hangs up sooner during agent silence. The tau2 interaction
metrics (R_R, I_A, S_ND) also shift with the user model. Compare only runs with the same user/judge model.

### 2.3 The concurrency-safe session join in `fba_voice_metrics.py`

At concurrency > 1 the committed `fba_voice_metrics.py` picks the wrong session for some tasks. The simulations
overlap in time, and the time-window fallback joins by overlap. The fix was made for the FBA concurrency-4
campaign but was never committed. It is archived in that campaign's provenance and applies cleanly on the current
tau2 commit (`037f24f`). The script applies only the `fba_voice_eval` part, is idempotent, and runs that folder's
tests:

```bash
$E/apply_join_fix.sh
```

This leaves the tau2 tree dirty. `archive.sh` archives the diff and the metrics script with every run.
`report.sh` still checks every join exactly against tau2's own per-task logs (§7).

### 2.4 banking_knowledge retrieval config

`banking_knowledge` needs `--retrieval-config`. `fdh_run` passes `${TAU3_RETRIEVAL:-bm25}`:

| Config | Needs | Use |
|---|---|---|
| `bm25` (**default here**) | nothing (offline) | The campaign default. Works with the Hub key alone |
| `alltools` (tau2's default, leaderboard) | `OPENAI_API_KEY` for **api.openai.com** embeddings, plus `sandbox-runtime`, `ripgrep`, `bubblewrap` and `socat` | Only with a real OpenAI key and the sandbox installed (AGENTS.md). The Hub `sk-` key will not work for its embeddings |

Record the retrieval config in the run card (§9); `run.sh` also prints it on the console log's first line. Results
with different retrieval configs are not comparable.

## 3. Optional: the `silentack` arm [agent]

The `silentack` arm doesn't speak the filler of a delegated turn; direct replies are still spoken. It gives a
Pass^1 comparable with the FBA silent-filler arms, and it shows how much the spoken filler itself changes
Pass^1. It uses the agent runbook's §4 command with a new name, port, profile and log:

```bash
cd $AGENT && mkdir -p logs
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --no-deps --name fdh-voice-silentack -p 8777:7860 \
  -v "$PWD/logs:/app/logs" -e PYTHONPATH=/app/src -e PIPELINE_TLS=false \
  -e FDH_BACKEND_URL=ws://host.docker.internal:8790/v1/backend -e FDH_MAX_SESSIONS=4 \
  -e FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning -e FRONTEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e FDH_EVENT_LOG=logs/fdh_voice_silentack_events.jsonl \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_delegation_hermes_agent.server \
    --config src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_eval_silent_ack.yaml --port 7860
until curl -sf localhost:8777/health >/dev/null; do sleep 5; done
cd $TAU2 && $E/check_stack.sh dlg silentack
```

`silentack` pairs with `gateway.yaml`, so it cannot run next to `geval` (one gateway, one config): run it with
`dlg` on the historical stack. `fdh-voice-web` must be stopped (§1): 4 + 4 sessions fill the gateway's 8.
Running both arms at the same time also doubles the load on `nemo-speech` and the Hub. Run the two arms **back to
back**, not in parallel, if their latencies are to be compared.

## 4. The scripts [tau2]

Everything is in [`fdh_voice_eval/`](fdh_voice_eval/README.md). All scripts source `fdh_lib.sh`; `-h` prints the
usage.

| Script | Does |
|---|---|
| `check_stack.sh [arm...]` | preflight (§1) |
| `apply_join_fix.sh` | the §2.3 fix |
| `new_campaign.sh [label] [--force]` | writes `_consoles/CAMPAIGN_FDH` |
| `run.sh <arm> <domain> <cx> [tau2 args]` | one run (below) |
| `campaign.sh [--arm A] [--cx C] [domain...] [-- tau2 args]` | runs domain by domain (default: the four domains, `geval`, `regular`) |
| `status.sh [run...]` | progress. No argument = every run of the campaign |
| `report.sh [--arms A[,B]] [--cx C] [--tag T] [domain...]` | the full report (§7) |
| `archive.sh <run>... \| --all \| --reports` | archiving (§9) |

**`run.sh`** (`fdh_run` in `fdh_lib.sh`) is the reference runbook's `tau3_run` for this agent:

- It points tau2 at the arm's port (`PINE_REALTIME_BASE_URL`).
- It runs I0's `tau2_ihub.py` with the user LLM, the review model and `base` split, 1 trial, `--verbose-logs`.
- It sets `--max-concurrency` to 1 for mock and 4 otherwise (`TAU3_CONCURRENCY` overrides).
- It adds `--retrieval-config ${TAU3_RETRIEVAL:-bm25}` for banking_knowledge.
- It names the run `fdh_voice_<arm>_<domain>_<cx>[_$TAU3_TAG]` and derives the `pine-` tag from it.
- It writes the console log and the start/end stamps to `data/simulations/_consoles/<run>.{log,start,end}`, outside
  the run directory, so a resumed run never overwrites them. The first log line records the port, concurrency,
  campaign and retrieval config.

Extra arguments go after the defaults, so `--num-trials 3` overrides `--num-trials 1`. `--verbose-logs` is
required: the exact join (§7) reads each simulation's `task.log`.

**`status.sh`** prints, per run:

- the latest tau2 `Status:` line;
- counts of disconnects, hallucination reruns, tracebacks and failed LLM calls;
- agent-side counts for the run's sessions: sessions, open sessions, decisions, delegations, frontend repairs,
  backend runs by status, backend actions (start/continue/steer/status), tool outputs, spoken status updates,
  barge-ins, backend errors, gateway session opens and all-time `capacity_refused` (from the arm's gateway log);
- normalization counts: `argument_normalized`, `answered_locally` by reason, `result_hints`, `session_rules` (one
  per session with `geval`) and `tools_sha256`, the distinct tool-schema hashes (one per domain);
- the voice server and gateway `/health`.

## 5. Smoke runs [tau2]

Start a campaign (`new_campaign.sh`, or `new_campaign.sh --force` to replace an older campaign), then (all
`regular`):

```bash
TAU3_TAG=smoke $E/run.sh geval mock regular --num-tasks 1     # concurrency 1
TAU3_TAG=smoke $E/run.sh geval airline regular --num-tasks 4  # concurrency 4: the first 4 tasks at the same time
```

Two earlier mock runs used `control` and are not part of the reported results:

- the agent runbook's §6 run, `fdh_voice_dlg_mock_control_smoke`, made before this campaign;
- this campaign's first plumbing check, `fdh_voice_dlg_mock_control`, made on 2026-09-29 before the regular-only rule.

The airline smoke is the concurrency-4 check: four sessions, four Hermes workers, four ASR streams at once.
Before the full runs, also run one task of each of the remaining domains. They have tools and policies the mock
and airline runs don't exercise: telecom has user-side device tools, and banking_knowledge has retrieval tools
and a much larger tool set.

```bash
for d in retail telecom banking_knowledge; do TAU3_TAG=smoke $E/run.sh geval $d regular --num-tasks 1; done
```

Then report the smoke runs (one report for all five, `regular`):

```bash
$E/report.sh --tag smoke mock airline retail telecom banking_knowledge     # -> _metrics/fdh_voice_geval_regular_smoke/
```

### 5.1 Gate: check each smoke run before spending more

`status.sh <run>` and `report.sh` cover most rows. `RUN` is the run name; `MODEL=$(fdh_model $RUN)`.

| Check | Command / where | Pass when |
|---|---|---|
| I0 active | `grep OVERRIDE $FDH_CONSOLES/$RUN.log` | `USER TTS OVERRIDE`, `USER DECISION LLM OVERRIDE`, `REVIEW LLM OVERRIDE` |
| User sim / judge model | `grep -E "REVIEW LLM OVERRIDE\|User: " $FDH_CONSOLES/$RUN.log` | the model the user chose (§2.2.1): `…gpt-5.2` or `…gpt-5.5` |
| **No failed LLM calls** | `status.sh $RUN` → `badLLM` | `0` (tau2 catches these and runs a degraded user) |
| Concurrency | first line of `$FDH_CONSOLES/$RUN.log` | `max_concurrency=1` for mock, `4` otherwise |
| Speech complexity | the same line | `speech_complexity=regular`, and no `WARN speech complexity` line |
| No infrastructure errors | the metrics box at the end of the console log | `Infra Errors` absent or 0 |
| Run finished, reward | `fba_voice_per_task.csv` in the report | `user_stop` or `agent_stop`; a reward is present |
| Listen to it | `data/simulations/$RUN/artifacts/task_*/sim_*/audio/both.wav` | a spoken filler ("Sure, let me check…") then the answer; no long dead air |
| Agent saw the run | `status.sh $RUN` → `sessions` | = number of tasks (more if tau2 retried); `open_sessions` 0 after the run |
| Hermes backend, not the fake | `check_stack.sh` | gateway `agent_kind hermes` |
| Normalization on | `grep "\"model\": \"$MODEL\"" $(fdh_event_log geval) \| tail -1` | `"normalization": {"transcript": true, "tool_arguments": ["auto"], "retry_guard": true}` (`dlg`: `["get_user_details.user_id"]`) |
| Schema-derived rules (`geval`) | `status.sh $RUN` → `session_rules`, `tools_sha256`; the rules: `grep session_rules $(fdh_event_log geval) \| grep <a session id>` | `session_rules` = `sessions`; one `tools_sha256` per domain. Rules: airline `get_user_details.user_id`, `get_reservation_details.reservation_id`; retail `get_user_details.user_id`, `get_order_details.order_id`, `get_product_details.product_id`, `get_item_details.item_id`; telecom, banking_knowledge and mock none. Spelling-hold patterns: airline `^[A-Z0-9]{6}$`; retail `^#?[A-Z]\d{7}$`, `^\d{10}$`; the others none (agent repo `tests/unit/prototypes/delegation/fixtures/schema_rules_snapshot.json`) |
| Deployed fingerprint | in [agent]: `PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.fingerprint_check <the run's sessions from agent/events.jsonl or the event log> --profile src/prototypes/voice_delegation_hermes_agent/config/profiles/realtime_eval.yaml --gateway-config src/prototypes/voice_delegation_hermes_agent/config/gateway.eval.yaml` | `fingerprint: OK` (one log per domain, since `session_tools_sha256` differs by domain) |
| Tool calls went through tau2 | `report.sh` adapter step | adapter exit 0 (no orphaned tool call) |
| Concurrent workers | `status.sh $RUN` → `gateway`, and `grep -E '"event": "(session_open\|capacity_refused)"' $AGENT/logs/fdh_gateway_events.jsonl \| tail` | 4 `session_open` close together in time (airline smoke); `gateway_capacity_refused_all_time` unchanged |
| No agent failures | `docker logs --since "$(head -1 $FDH_CONSOLES/$RUN.start)" fdh-voice 2>&1 \| grep -Ei " failed\|error\|\b401\b" \| head` | nothing relevant |
| Worker health | `grep -il traceback $AGENT/logs/fdh_workers/sess_*.log` | no tracebacks ("Auxiliary Nous client unavailable" is harmless) |
| Report | `report.sh` | ends with `report.sh OK`: adapter exit 0; C1–C5, C7, C8 PASS, C6 FAIL (allowed); every `P0.1` line in `checks.txt` `OK`; `0 mismatch` in `exact_join.txt` |

**Dry run, 2026-09-29** (a `control` run, before the regular-only rule). `report.sh --cx control --tag smoke mock`
ran on the agent runbook's `fdh_voice_dlg_mock_control_smoke` (1 task), with outputs in `/tmp`:

- The adapter wrote 95 records (15 turns).
- C1–C5, C7 and C8 PASS; C6 FAIL (`speak`). The exact join was 1/1.
- Measured filler first audio: 1.60 s mean over 2 turns.
- Reward 0.0: ASR lowercased the title "Important Meeting" (an agent result).
- Backend per-turn LLM latency 13.2 s, realtime response latency 15.7 s, FE 3057 and BE 8133 tokens per task.

`archive.sh` then archived the run: 61 voice, 21 legacy and 12 gateway records, and 1 worker log. The secret scan
of the archive found no keys.

Use the mean `duration` of the airline smoke's simulations to re-estimate the wall time in §6.

## 6. Reportable runs [tau2]

The reportable condition is **`regular`**, because Selectivity needs it. Use the full `base` split (no
`--num-tasks`), **1 trial**, and concurrency 4 (the default outside mock). Runs take hours, so run them in `tmux`:

```bash
tmux new -s fdh
# inside tmux, in [tau2]:
misc/prototypes/fdh_voice_eval/campaign.sh          # airline retail telecom banking_knowledge, geval, regular
```

In another shell, watch progress with `$E/status.sh` (every run of the campaign). `campaign.sh` writes
`_consoles/fdh_campaign_<arm>_<cx>.out`, with a start and end line and the exit code per domain. It continues with
the next domain when one fails, and prints `CAMPAIGN DONE … failed=[…]` at the end.

| Domain | Tasks (`base`) | Rough wall time at concurrency 4 |
|---|---|---|
| airline | 50 | ~2.5 h |
| retail | 114 | ~5.5 h |
| telecom | 114 | ~5.5 h |
| banking_knowledge | 97 (voice configs) | ~4.5 h |

The estimates scale the FBA `verdictspk` airline run at concurrency 4 (50 tasks, 2 h 18 min). The Hermes backend's
latency differs, so re-estimate from the airline smoke (§5).

- **Resume:** rerun the same command. The same run name resumes, and a new `.start` stamp is appended. Add
  `--auto-resume` to rerun infrastructure errors and keep finished tasks:
  `$E/run.sh geval retail regular --auto-resume`, or `$E/campaign.sh retail telecom -- --auto-resume`.
- **Don't change the agent config, models or code, or the gateway, during a campaign.** If something must change,
  start a new campaign.
- **One frozen profile for every domain.** `geval` runs the same profile and gateway config in all four domains;
  don't switch profiles, prompt features or environment between domains.
- **`dlg` and `silentack` (historical, §3):** only on the historical stack (`tau3_eval.yaml` with `gateway.yaml`),
  on the same agent commit: `$E/campaign.sh --arm dlg`, then `$E/campaign.sh --arm silentack`.
- **Speech complexity is `regular` for every run.** Don't pass `--cx control` to `campaign.sh` or `control` to
  `run.sh`.

## 7. Compute the metrics [tau2]

```bash
$E/report.sh                                 # geval, regular, the four domains -> _metrics/fdh_voice_geval_regular/
$E/report.sh --arms dlg,silentack            # the historical silentack comparison -> _metrics/fdh_voice_dlg-silentack_regular/
```

It prints the results table at the end, and it exits 1 when the adapter, an unexpected check, the 429 gate or the
exact join fails. Every output is still written. The steps, in order:

1. **Report adapter** (agent repo, `cli/report_adapter.py`), run on the arm's whole shared event log. It writes
   `_metrics/_legacy/fdh_voice_<arm>_events.legacy.jsonl`. `fba_voice_metrics.py` expects one backend operation per
   turn, but this agent's turn can start a run, steer another turn's run, ask for status or stay local. The adapter
   credits each Hermes run (usage, latency, tool calls, answer) to the turn that started it. It fails on a client
   tool call with no backend run, or a run with no starting turn. The metrics script then selects each run's
   sessions by model tag. The adapter reruns on every `report.sh`, because it writes a snapshot of the log.
2. **Setup record** `_metrics/_setup/<arm>.json`. tau2 doesn't store the agent's setup, so the script records it
   from the running system:
   - the frontend model from the container env;
   - the backend model from the gateway `/health`;
   - ASR and TTS from the container's `ASR/TTS channel ready` log lines;
   - the reasoning settings from `gateway.yaml` (`hermes.request_overrides`; `gateway.eval.yaml` extends it and
     does not change them) and `delegation_agent.yaml` (`frontend.llm.extra_body`). Re-check them if either file
     changed.

   If the container is gone, it keeps an existing record.
3. **`fba_voice_metrics.py`**, with one `--run` per domain whose run directory exists. It writes
   `fba_voice_report.md`, the results table (`fba_voice_results_table.md|.csv`; the columns are in the reference
   runbook §7.3), per-task and per-turn CSVs, `join.csv`, and the console output in `console.txt`. Its exit code is
   1 because C6 fails by design.
4. **Check summary** (`checks.txt`). A FAIL other than C6 fails the report:

   | Check | Expected here |
   |---|---|
   | C1 join 1:1 | PASS. A FAIL at concurrency 4 means §2.3 was not applied, or a model tag was reused |
   | C2 per-role usage | PASS (the adapter writes `frontend`/`backend` usage) |
   | C3 exact vs derived backend latency | PASS |
   | C4 agent tokens vs tau2 `agent_usage` | PASS, or WARN when the user barged in (a cut-off response's usage never reaches tau2) |
   | C5 agent failures | PASS, or WARN with a count: read them in `docker logs` |
   | **C6 filler mode `log_only`** | **FAIL by design** (`speak`) for `geval` and `dlg`. For `silentack`, report what C6 says |
   | C7 filler records present | PASS |
   | C8 terminations | PASS (`user_stop`/`agent_stop`); `too_many_errors` or inactivity endings are agent results, but list them |

   Then the **429 gate** (`fdh_logs.py ratelimit`) appends one line per run to `checks.txt`. It reads every session
   of the run in `join.csv` (primary and retried) and counts `Error code: 429` and `Retrying API call` lines in its
   Hermes worker logs (`sess_<id>-<n>.log` in `fdh_worker_logs`). It also prints the backend-run p90 from the
   gateway log (`backend_run_dispatched` → `backend_run_done`). A run with any throttled session is
   `INVALID (re-run it)` and fails the report: its scores are not comparable in an A/B. On the geval dump, telecom
   was INVALID (30 throttled sessions) and retail r3 was OK.

5. **Exact join** (`exact_join.txt`). With `--verbose-logs`, each simulation's `task.log` records the Realtime
   session id the agent sent back (`OpenAI Realtime API: session created (session_id=sess_…)`). That id is the
   agent's `session_id`, so every join in `join.csv` is checked exactly, including for tasks without tool calls,
   which the metrics script joins by time.
   - A simulation whose `task.log` lists several ids was reconnected or retried; the last id is the attempt that was
     scored.
   - Pass when every run shows `0 mismatch`.
   - On a mismatch, don't report that task's agent-side metrics. Its Pass^1 and tau2 interaction metrics are
     unaffected.
6. **Measured filler latency** (`filler_measured.txt|.json`), over the primary sessions of each run:
   - `filler_timing.user_stop_to_first_audio_ms` (end of user speech → the filler's first audio);
   - `turn_latency.user_stop_to_first_audio_ms` (→ the answer's first audio).

   Both give n, mean and nearest-rank p90. The answer value is raw wall-clock time and includes tau2's tool round
   trips. Report the metrics script's *Mean realtime response latency*, which removes them, as the headline value
   (reference runbook §7.3). Report the measured filler latency next to the script's *projected* one.

**Pass^1 and the tau2 interaction metrics alone** don't need any of this: use the reference runbook §7.1 with
`RUN=fdh_voice_geval_<domain>_regular`.

The helpers behind steps 4–6 are `fdh_logs.py checks | ratelimit | exact-join | filler`, and they can be run on
their own. For example, to rerun them on archived copies:

```bash
python3 $E/fdh_logs.py ratelimit --join $DUMP/tau-3-voice/$CAMPAIGN/_reports/fdh_voice_geval_regular/join.csv \
  --worker-logs $DUMP/tau-3-voice/$CAMPAIGN/fdh_voice_geval_telecom_regular/agent/workers \
  --gateway-logs $DUMP/tau-3-voice/$CAMPAIGN/fdh_voice_geval_telecom_regular/agent/gateway_events.jsonl
python3 $E/fdh_logs.py exact-join data/simulations/fdh_voice_geval_airline_regular --join $FDH_METRICS/fdh_voice_geval_regular/join.csv
python3 $E/fdh_logs.py filler $DUMP/tau-3-voice/$CAMPAIGN/fdh_voice_geval_airline_regular/agent/events.jsonl \
  --join $DUMP/tau-3-voice/$CAMPAIGN/_reports/fdh_voice_geval_regular/join.csv
```

On archived copies, each run has its own worker logs, so `ratelimit` checks only the run whose `agent/workers/` you
pass; ignore the `OK` lines of the other runs in `join.csv`.

## 8. How to read the numbers

The reference runbook §8 applies: tau2's clock runs at about half real time; the user simulator is not official; S_VT
is approximate. In addition:

- **Concurrency 4.** Four sessions share every component, so latencies are higher than at concurrency 1. The
  frontend's 4 s decision budget, hedged at 1.5 s, may time out more often. `status.sh` shows the count
  (`repairs: {"timeout": N}`). The FBA verdict run at concurrency 4 saw frontend timeouts rise from 3 to 17.
- **Backend per-turn latency** is the Hermes run time credited to the turn that started it. It includes every
  Hermes tool round and model call of that run, but not the time tau2 took to return tool results. A run that
  spans several user turns (steered or asked for status) is counted once, on its starting turn.
- **Status and steer turns.** A turn with outcome `status` or `steer` in `fba_voice_per_turn.csv` did not start a
  run. Report their counts (from `status.sh`: `backend_actions`, `status_spoken`) next to the latency, since they
  are this agent's mid-task behaviour.
- **Tool-argument normalization (`geval`)** is derived per session from the tool schemas: rules for read-tool
  `*_id` arguments whose description quotes an example (airline 2, retail 4, telecom and banking_knowledge none;
  §5.1), never for write tools. The retry guard covers every tool. Recovery notes are appended to a not-found
  result that names a user or customer, in every domain (telecom and banking_knowledge included). The backend's
  `<phone_numbers>` block is in every domain. Transcript normalization is on everywhere. With `dlg` (historical),
  argument rules and the retry guard cover only `get_user_details.user_id`, and the recovery notes only
  `find_user_id_by_name_zip`, `find_user_id_by_email` and `get_user_details`.
- **Reward effect not yet measured.** The `geval` rules are designed to cover the cases the `dlg` rules were made
  for, but their effect on Pass^1 is unvalidated. Report measured numbers only, and don't claim parity with `dlg`.
- **banking_knowledge** results depend on `--retrieval-config` (`bm25` by default here). Name it next to every
  number.

## 9. Archive everything in the dump repo [tau2 → dump]

**Mandatory for every campaign, including smoke, stopped and invalid runs.** Archive before any restart of the
stack; see the note at the top of this runbook.

```bash
$E/archive.sh --all             # every fdh_voice_* run started since the campaign began (smoke runs included)
$E/archive.sh --reports         # the campaign's _metrics reports, _setup, CAMPAIGN_FDH, campaign console outputs
# or single runs: $E/archive.sh fdh_voice_geval_airline_regular
```

It only copies; the source logs are shared across runs. Rerun it after late changes; it overwrites the copy. For
each run it writes:

| Path under `$DUMP/tau-3-voice/$CAMPAIGN/<run>/` | Content |
|---|---|
| `tau2/<run>/`, `tau2/<run>.log\|.start\|.end` | results.json, simulations/, artifacts/ (audio, task.log, labels); console output and run window |
| `agent/events.jsonl`, `agent/events.legacy.jsonl` | this run's voice-server sessions (by model tag), before and after the report adapter |
| `agent/gateway_events.jsonl`, `agent/workers/` | this run's gateway records and Hermes worker logs (`sess_<id>-<n>.log`) |
| `agent/sessions.txt` | this run's session ids |
| `agent/raw/` | the full voice and gateway logs of the run's arm as of archiving, gateway stdout |
| `agent/docker_logs.txt`, `agent/nemo_speech_logs.txt` | voice container and ASR/TTS server logs since the run's first start |
| `agent/config/` | `delegation/` (the whole agent config folder: every profile, voice profile and gateway config, including `realtime_eval.yaml` and `gateway.eval.yaml`, and the prompts), `voice_agent.yaml`, `prompts.voice.yaml`, the speech catalog, `gateway_health.json` |
| `agent/container_inspect.json` | the container definition, secret-looking env values redacted |
| `provenance/` | commit, status and uncommitted diff of the agent, tau2 and Hermes repos; `agent_untracked.tgz` (the delegation prototype, untracked in the agent repo); `fdh_voice_eval.tgz` (these scripts, untracked in tau2); `fba_voice_metrics.py` and I0 as used; `.env` key names and judge/TTS endpoints (no values); `agent_llm_env.txt`; `gateway_env.txt` (the gateway process's `BACKEND_LLM_*`/`FDH_*`) |

`--reports` writes `_reports/` (each `fdh_voice_*` report folder and `_setup/`) and `_consoles/` (`CAMPAIGN_FDH`,
`fdh_campaign_*.out`, and a campaign-level `provenance/`).

Then write `$DUMP/tau-3-voice/$CAMPAIGN/README.md`, a run card (`archive.sh --reports` reminds you if it is
missing). Follow `2026-09-29_04-32-39Z_fba-voice/README.md`:

- date and time window, arms (with profile and gateway config), domains, speech complexity, trials;
- **`max_concurrency` per run (1 mock, 4 otherwise)**;
- `--retrieval-config` for banking_knowledge;
- models: frontend, backend (Hermes), ASR, TTS, user simulator, judge;
- commits of all three repos and their dirty state;
- the results table from `fba_voice_report.md`, `filler_measured.txt` and `exact_join.txt`;
- the C6 FAIL (by design) and any other failed check;
- anything unusual.

**Layout:**

```
voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/       e.g. 2026-09-30_08-00-00Z_fdh-voice/
├── README.md                          run card
├── _reports/                          report.sh outputs (fba_voice_report.md, CSVs, join.csv, checks.txt, exact_join.txt, filler_measured.*), _setup/
├── _consoles/                         CAMPAIGN_FDH, fdh_campaign_*.out, provenance/
└── fdh_voice_<arm>_<domain>_<cx>[_<tag>]/
    ├── tau2/ …   agent/ …   provenance/ …      (table above)
```

**Before committing to the dump repo:** audio and `task.log` files make runs large; the dump repo already tracks
them with Git LFS (`.gitattributes`). Check with `git -C $DUMP check-attr filter -- <a .wav path>` → `lfs`.

```bash
cd $DUMP
git add tau-3-voice/$CAMPAIGN
git commit -m "tau-3-voice: $CAMPAIGN fdh-voice (geval airline retail telecom banking_knowledge regular, concurrency 4)"
# git push   (only when you intend to publish the campaign)
```

Never copy `.env` files. `archive.sh` records key names only and redacts container environments.

## 10. Stop [agent]

```bash
docker stop fdh-voice-silentack 2>/dev/null     # only if §3 started it
# fdh-voice, the gateway and nemo-speech: stop per the agent runbook §10 only if nothing else needs them.
# Restart the browser page if wanted: agent runbook §5.
```

## 11. Troubleshooting

The reference runbook §11 covers tau2 and I0 problems (pyaudio, 401s, TTS 404s, `Not connected to API`, slow
airline tasks). Specific to this agent:

| Symptom | Check |
|---|---|
| `run.sh`: `no campaign` | run `new_campaign.sh` first |
| Sessions of a run missing in the agent log (`status.sh`: `sessions=0`) | wrong port for the arm (8775 `geval`/`dlg`, 8777 `silentack`), the container was started with another `FDH_EVENT_LOG`, or the run used another arm's name than the profile running on 8775 (`check_stack.sh <arm>`) |
| `check_stack.sh`: `fdh-voice does not run …` or `FDH_EVENT_LOG=…, expected …` | the container runs another arm's profile or log: check the arm (`geval` by default), or restart `fdh-voice` per the agent runbook §6.2 |
| `check_stack.sh`: `gateway does not run …`, `FDH_GATEWAY_LOG=…, expected …` or `/health does not match` | the gateway runs another config or log, or old code or prompts: restart it per the agent runbook §3 with the arm's `FDH_GATEWAY_CONFIG` and `FDH_GATEWAY_LOG` |
| `status.sh`: gateway counts empty | the gateway writes another log than the arm's: set `FDH_GATEWAY_LOG`, or restart the gateway with the arm's log |
| `Session configuration failed` | the server sent `error` on `session.update`; the voice log's `backend_error` has the code. `worker_start_timeout`/`backend_unavailable`: read `logs/fdh_workers/<sess>-*.log` (agent runbook §11) |
| Infrastructure errors in bursts at concurrency 4; `gateway_capacity_refused_all_time` grows | the gateway's 8 sessions are shared: stop `fdh-voice-web` (§1), and don't run `silentack` alongside `dlg`. Rerun with `--auto-resume` |
| Voice server refuses a 5th session | `fdh-voice` has `max_sessions: 4`. The concurrency is above 4 (check `TAU3_CONCURRENCY`), or a closing session was still open. Rerun with `--auto-resume` |
| ASR errors or empty transcripts under load | the agent's ASR channel has `max_streams=4`, so more than 4 simultaneous sessions exceed it. Keep concurrency ≤ 4 |
| Many `repairs: {"timeout": …}` in `status.sh` | the frontend endpoint is slow under 4× load (4 s budget, hedged at 1.5 s). Report the count; don't change the budget mid-campaign |
| Long dead air, then the simulation ends early | a Hermes run exceeded tau2's inactivity limit (`DEFAULT_AUDIO_NATIVE_MAX_INACTIVE_SECONDS = 40`). The filler and status updates only cover part of it. Compare with the backend turn p90; it is an agent result |
| Gateway `run_hard_deadline` / watchdog kills in `gateway_events.jsonl` | a Hermes run hit 330 s; the worker is killed and respawned (at most 2 per session). Count them per run |
| `ADAPTER CHECK FAILED`: `tool call … has no backend run` | a client tool call in the voice log whose Hermes run is missing (e.g. the worker died mid-run). Look up the session in `logs/fdh_workers/` and report it; the rest of the report is still valid |
| `UNEXPECTED FAIL: … C1` | §2.3 not applied (`check_stack.sh`), a reused model tag, or sessions from an aborted attempt under the same name. `exact_join.txt` shows which tasks |
| `exact_join.txt` lists `task.log None` | the run was made without `--verbose-logs`, or the simulation never connected (an infrastructure error) |
| `checks.txt`: `P0.1 <run>: … INVALID (re-run it)` | the backend was rate-limited (HTTP 429) in that run. List the throttled sessions with `grep -l "Error code: 429" $(fdh_worker_logs)/sess_*.log`, and re-run the domain in a quieter window or with a dedicated quota |
| `report.sh`: `skip …: no results.json` | the domain has not run yet (or ran under another `--tag`/`--cx`) |
| `archive.sh`: `gateway_env.txt` empty | the gateway is not running on this host; record its env from the agent runbook §3 command |
| banking_knowledge: `No module named 'rank_bm25'` | `uv sync --extra voice --extra dev --extra knowledge` (§2.1) |
| banking_knowledge with `alltools`: embedding or `SandboxRuntimeError` errors | use `bm25` (§2.4), or install the sandbox and a real OpenAI key |
| Filler never audible in `both.wav` (`geval`, `dlg`) | the container runs `tau3_eval_silent_ack.yaml` (`check_stack.sh` fails the profile row) |

## 12. What to record when you report

Record the following next to any number:

- the agent's models: frontend (nemotron-3.5-lightning, reasoning off), backend (Hermes `AIAgent` on nemotron-3-ultra,
  reasoning on, budget 1024), ASR and TTS;
- the filler is **spoken** (`geval`, `dlg`) or not (`silentack`);
- the user simulator LLM and the judge and hallucination-check model: `azure/openai/gpt-5.2` or `openai/openai/gpt-5.5`,
  whichever the user chose (§2.2.1; never assume), the user TTS (`openai/openai/gpt-4o-mini-tts`, via I0) and the
  decision model (`azure/openai/gpt-4.1`, via I0);
- speech complexity; domains and split; trials;
- **`max_concurrency` (1 for mock, 4 otherwise)**; the banking_knowledge `--retrieval-config`;
- the commits of the agent, tau2 and Hermes repos and whether each was dirty (the prototype is untracked in the
  agent repo);
- that `fba_voice_metrics.py` had the §2.3 join fix, and the exact-join result;
- the arm, its profile and gateway config, and the normalization setting (`session_start.normalization`);
- the fingerprints: `backend_catalog_sha256`, `backend_system_sha256` and `session_tools_sha256` per domain, and the
  `fingerprint_check` result;
- that the reward effect of the domain-agnostic rules is measured by this run, not assumed;
- the measured filler latency next to the *projected* one;
- failed checks (C6 by design) and the frontend timeout count;
- the dump path `voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/`.
