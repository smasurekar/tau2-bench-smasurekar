# Runbook: τ³ voice evaluation of the Pipecat adapter (`fdh-voice`, Frontend Delegation Hermes, eval mode)

**Date:** 2026-10-13 · **Status:** `fdh-voice` is up in eval mode (image `nemotron-voice-agent-fdh:latest`, host
port **8776**, TLS off, `pipecat_realtime_eval.yaml` + `gateway.eval.yaml` + `adapters/pipecat.eval.yaml`, healthy);
`fdh-tau` is stopped. The tau2 side is ready. The `fdh_voice_eval/` scripts **do not know the Pipecat adapter** (§2):
`run.sh` hardcodes port 8775, and no script knows its log files. Until they are ported, use the `pipecat` arm overlay
below and the manual commands of this runbook · **Starts the container:** the agent repo skill
`nemotron-voice-agent-smasurekar/.agents/skills/nemotron-voice-agent-fdh-deploy/` (`SKILL.md` → Pipecat → eval,
`references/pipecat.md`) · **Sister runbook (the τ³ adapter `fdh-tau`; this one only lists what differs):**
[`fdh-tau-tau3-runbook.md`](fdh-tau-tau3-runbook.md) · **Parent runbook (methodology, metrics, gpt-5.5 command,
report reading):** [`../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md`](../prototypes/voice-frontend-delegation-hermes-tau3-runbook.md)
· **Scripts:** [`../prototypes/fdh_voice_eval/`](../prototypes/fdh_voice_eval/README.md)

This runbook takes the running Pipecat `fdh-voice` container to a τ³ voice report for **airline, retail, telecom and
banking_knowledge** (mock only for the smoke run), archived in the dump repo. It follows the `fdh-tau` runbook
section by section; everything it does not mention is as there (and, behind that, as in the parent runbook).
`src/tau2/` is not modified.

The rules all still apply:

> **ALWAYS ASK the user which Inference Hub model to use for the user simulator and the judge before any run:
> `azure/openai/gpt-5.2` (the scripts' default) or `openai/openai/gpt-5.5`.** Same model for user sim, NL-assertion
> judge and review model. Exact gpt-5.5 command: parent runbook §2.2.1 (with `fdh_run pipecat …`, §5).

> **Do not start any run (smoke included) until the user says go.**

> **Speech complexity `regular` for every domain, mock smoke included.** Never `control`.

> **ALWAYS ASK the user before changing τ²'s user-simulator turn-taking thresholds — every time, for every run,
> even if they were changed for an earlier run.** These are benchmark-side values, not agent settings; changing
> them makes the run incomparable with the `fdh-tau` runs, other campaigns and the leaderboard. Defaults
> (`src/tau2/config.py`, all in ticks of audio time, not wall time):
>
> | Flag (`tau2 run` / `fdh_run … <flag>`) | Default | Meaning |
> |---|---|---|
> | `--wait-to-respond-self` | **5.0 s** | the user may speak again only this long after its *own* last speech (its patience: the "Hello? Are you still there?" pings) |
> | `--wait-to-respond-other` | **1.0 s** | …and only this long after the *agent* last spoke (a pause > 1 s after a filler hands the user the floor) |
> | `--silence-annotation-threshold` | **4.0 s** | after this much two-sided silence `[Both parties silent for X s]` goes into the user LLM's context |
>
> The user may speak when both wait thresholds are met. If a run uses non-default values, put them in the run tag
> (e.g. `TAU3_TAG=r60-gpt55-tt10-2-8`), on the console log's first line, and in the run card, and report its
> numbers only as a labelled diagnostic, never next to default-threshold results.

> **Concurrency: 1 for mock, 4 for every other domain** (`fdh_concurrency`; `TAU3_CONCURRENCY` overrides). Same
> capacity as `fdh-tau`: voice `FDH_MAX_SESSIONS=4`, gateway `FDH_GATEWAY_MAX_SESSIONS=8`.

> **One adapter per reportable run: `fdh-tau` must be stopped** (both share `nemo-speech` and the Inference Hub).

> **MANDATORY: archive every run (smoke, stopped, invalid) in
> `/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump/tau-3-voice/<CAMPAIGN>/` before you
> restart, recreate or reconfigure `fdh-voice`, or change agent code.** `up -d fdh-voice` without the four eval
> variables recreates the *demo* and drops the old `docker logs` (§9).

## Where commands run

As in the `fdh-tau` runbook ([tau2], [agent], [hermes], [dump]). Shell setup for every command below — the same
lines, then the **`pipecat` arm overlay**, which teaches `fdh_lib.sh`'s helpers the Pipecat container, port and logs:

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/tau2-bench-smasurekar
source misc/prototypes/fdh_voice_eval/fdh_lib.sh     # TAU2 AGENT HERMES DUMP IHUB FDH_CONSOLES FDH_METRICS CAMPAIGN ...
E=misc/prototypes/fdh_voice_eval
F=docker/docker-compose.frontend-delegation-hermes.yaml   # relative to [agent]
C=fdh-voice                                               # container name

# pipecat arm overlay (this shell only; the scripts re-source fdh_lib.sh and don't see it, §2)
fdh_port()           { case $1 in pipecat) echo 8776;; *) echo "unknown arm: $1 (pipecat)" >&2; return 1;; esac; }
fdh_container()      { echo fdh-voice; }
fdh_profile()        { echo profiles/pipecat_realtime_eval.yaml; }
fdh_gateway_config() { echo gateway.eval.yaml; }
fdh_event_log()      { echo "$AGENT/logs/fdh_voice_events.pipecat.jsonl"; }
fdh_gateway_log()    { echo "$AGENT/logs/fdh_gateway_events.pipecat.jsonl"; }
fdh_worker_logs()    { echo "$AGENT/logs/fdh_workers_pipecat"; }
A=pipecat                                                 # the arm
```

With the overlay, `fdh_run_name pipecat retail regular` is `fdh_voice_pipecat_retail_regular`, its model tag
`pine-fdh-voice-pipecat-retail-regular`, and `fdh_legacy_log pipecat` is
`_metrics/_legacy/fdh_voice_events.pipecat.legacy.jsonl` — names that never collide with a τ³ arm (`geval`).

---

## 0. What is different from `fdh-tau`

| | **`fdh-tau`** (sister runbook) | **Pipecat `fdh-voice`** (eval mode) |
|---|---|---|
| Entry point | `tau_server.py`, `adapters/tau/` | `server.py`, `adapters/pipecat/` (`FDH_ADAPTER=pipecat`) |
| Container / Compose | `fdh-tau`, profile `tau`, `restart: unless-stopped` | **`fdh-voice`** (no profile), **`restart: "no"`**: never comes back by itself — if it dies, the run fails with connection errors |
| Voice port | 8775 | **8776**, plain `ws://` (eval sets `FDH_PIPECAT_TLS=false`) |
| Profile | `FDH_PROFILE=realtime_eval.yaml` | `FDH_PROFILE=pipecat_realtime_eval.yaml` (= `realtime_eval.yaml` + `voice/pipecat_realtime_eval.yaml`, spelling hold off, barge-in `cancel_and_merge`), `config_hash` **`e53e408b0b78`** |
| Gateway | `gateway.eval.yaml`, in the container on `127.0.0.1:8790` | **the same** (`docker exec fdh-voice curl -s 127.0.0.1:8790/health`) |
| Turn taking | FDH's own, **800 ms of client audio time** (the client's audio clock) | **Pipecat**: Silero VAD `stop_secs` **3.0 s of wall time** (`adapters/pipecat.eval.yaml`, `FDH_PIPECAT_SETTINGS`); Pipecat times silence on the wall clock, and τ²'s tick loop runs slower than real time, so 3 s of wall time stands in for the 800 ms of audio time — not a longer pause in the conversation. Smart Turn off |
| ASR / TTS | catalog `nemotron-asr-streaming-english` / `magpie-multilingual-tts` | the same catalog endpoints via `NvidiaForceEouSTTService` / `NvidiaTTSService` (`per_sentence`). **No `ASR/TTS channel ready` log lines** (§7) |
| Event / gateway / worker logs | `logs/fdh_voice_events.realtime_eval.jsonl`, `logs/fdh_gateway_events.realtime_eval.jsonl`, `logs/fdh_workers/` | **`logs/fdh_voice_events.pipecat.jsonl`**, **`logs/fdh_gateway_events.pipecat.jsonl`**, **`logs/fdh_workers_pipecat/`** (`FDH_PIPECAT_*_LOG`); never mixed with τ³ logs |
| Docker logs | `json-file` 50 MB × 3 | the same |
| `/health` | `"prototype":"frontend-delegation-hermes"` | the same, plus `"adapter":"pipecat"` and a `"pipecat"` block |

**Pipecat scores are reported separately from the τ³ adapter** (skill, `references/pipecat.md`): the two adapters
time turns differently, so latency, interruption and turn metrics are not comparable, and neither is the reward
without a paired run. Known limits that show in the metrics (`references/pipecat.md` → Known Limits): heard audio is
an *estimate* (Pipecat writes audio at 2× real time; τ²'s tick loop hears less than estimated), speech onset is
logged ~200 ms late, server VAD only (τ²'s `turn_detection` values are ignored), the catalog voice is used whatever
the client asks, no spelling hold.

---

## 1. Start or check `fdh-voice` [agent]

Start and stop it only through the deploy skill (`SKILL.md` → Pipecat → eval). The commands, for reference:

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar
F=docker/docker-compose.frontend-delegation-hermes.yaml
docker ps --filter name='^fdh-tau$' --format '{{.Names}} {{.Status}}'   # expect nothing (τ³ adapter stopped)
export FDH_PIPECAT_PROFILE=pipecat_realtime_eval.yaml FDH_PIPECAT_GATEWAY_CONFIG=gateway.eval.yaml \
       FDH_PIPECAT_SETTINGS=adapters/pipecat.eval.yaml FDH_PIPECAT_TLS=false   # export: a later up -d keeps eval mode
docker compose --env-file .env -f $F up -d fdh-voice                     # add --build only after a rebuild trigger
```

Checks (the expected values are those seen on 2026-10-13, agent `e89ddbb`):

```bash
curl -s localhost:8776/health; echo
#   "status":"ok", "max_sessions":4, "config_hash":"e53e408b0b78", "adapter":"pipecat",
#   "pipecat":{"settings_file":".../adapters/pipecat.eval.yaml","vad":{"stop_secs":3.0},"tts_synthesis_mode":"per_sentence","smart_turn":false},
#   "backend":{"link":"websocket",...}, "frontend":{"decider":"llm","model":"nvidia/nvidia/nemotron-3.5-lightning"}
docker exec fdh-voice curl -s 127.0.0.1:8790/health; echo
#   as fdh-tau: "ok":true, "max_sessions":8, "agent_kind":"hermes", nemotron-3-ultra, reasoning true,
#   "domains":["telecom"], "backend_catalog_sha256":"55639a8b238b7eec"
docker inspect fdh-voice --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^FDH_(ADAPTER|PROFILE|GATEWAY_CONFIG|PIPECAT_SETTINGS|PIPECAT_TLS|EVENT_LOG|GATEWAY_LOG|WORKER_LOG_DIR|MAX_SESSIONS|GATEWAY_MAX_SESSIONS)='
#   FDH_ADAPTER=pipecat  FDH_PROFILE=pipecat_realtime_eval.yaml  FDH_GATEWAY_CONFIG=gateway.eval.yaml
#   FDH_PIPECAT_SETTINGS=adapters/pipecat.eval.yaml  FDH_PIPECAT_TLS=false
#   FDH_EVENT_LOG=logs/fdh_voice_events.pipecat.jsonl  FDH_GATEWAY_LOG=logs/fdh_gateway_events.pipecat.jsonl
#   FDH_WORKER_LOG_DIR=logs/fdh_workers_pipecat  FDH_MAX_SESSIONS=4  FDH_GATEWAY_MAX_SESSIONS=8
docker logs fdh-voice 2>&1 | grep -E 'config hash|Pipecat adapter settings|backend gateway|warm-up'
docker ps --format '{{.Names}}' | grep -x nemotron-voice-agent-nemo-speech-1                # nemo-speech running
```

`FDH_BACKEND_DELAY_S=5` is in the env but only the demo profiles use it; the eval profile never delays. A
`frontend warm-up failed (TimeoutError)` line at start-up only means the first turn may be slow.

The optional smoke test of `references/pipecat.md` (Smoke Test, `voice_chat --io wav` with `--tools`) writes
`pine-fdh-pipecat-audio` sessions into the same event log; harmless, but not during a campaign run.

**After any agent code, prompt or config change:** recreate with the same exported `up -d` — **archive first (§9)**,
then start a new campaign (§3).

---

## 2. Eval-script status against the Pipecat adapter [tau2]

Checked on 2026-10-13 (agent `e89ddbb`, tau2 `2b6ab79`). The container is named `fdh-voice` again, so the scripts'
`docker` calls hit the right container, but every port and log path is the τ³ one:

| Script | Works? | Why / what to do instead |
|---|---|---|
| `new_campaign.sh` | **yes** | |
| `run.sh`, `campaign.sh` | **no** | `fdh_port` knows only `geval\|dlg` (8775) and `silentack`: `geval` would talk to the stopped τ³ port. Use **`fdh_run pipecat …`** in the overlaid shell (§5, §6) |
| `apply_join_fix.sh` | **yes** (already applied) | |
| `status.sh` | **no** | reads `fdh_voice_events.realtime_eval.jsonl` (arm `geval`) and polls 8775/8777. Use §6's status lines |
| `check_stack.sh` | **tau2 rows only** | agent rows check profile `realtime_eval.yaml` in `.Config.Cmd`, port 8775 and the host gateway. Use §1 |
| `report.sh` | **no** | `--arms pipecat` fails in `fdh_port`; it also runs the old prototype adapter and reads the host gateway. Use §7 |
| `archive.sh` | **`--reports` only** | per-run archiving fails on `fdh_port pipecat`; it would also copy `logs/fdh_workers/` and the old prototype config. Use §9 |

> **Danger:** if `fdh-tau` is running, `geval` commands (and the unported scripts) silently evaluate *it* instead.

**Porting** (not done; ask before changing the scripts, then `uv run pytest misc/prototypes/fdh_voice_eval/tests -q`):
add a `pipecat` arm to `fdh_lib.sh` (the overlay above as `case` branches), plus the `fdh-tau` runbook §2 list
(report adapter module `examples.frontend_delegation_hermes.cli.report_adapter`, in-container gateway health,
`archive.sh` worker dir `$(fdh_worker_logs)` and config from `src/examples/frontend_delegation_hermes/config/`,
`status.sh` port 8776). After that this runbook is the `fdh-tau` one with `geval` → `pipecat`.

---

## 3. One-time tau2 setup and the campaign [tau2]

The tau2 setup is done (as in the `fdh-tau` runbook §3). `banking_knowledge` uses `--retrieval-config bm25`.

### 3.1 Close out the current campaign first

`_consoles/CAMPAIGN_FDH` names **`2026-10-12_16-32-53Z_fdh-tau-retail60-gpt55`**, the `fdh-tau` retail 60-task run.
It is archived (run card in the dump), so just replace it.

### 3.2 New campaign and run names

```bash
$E/new_campaign.sh fdh-pipecat --force    # e.g. 2026-10-13_05-00-00Z_fdh-pipecat
```

Pipecat run names (`fdh_voice_pipecat_*`) don't exist yet in `data/simulations/`, so no tag is needed to avoid a
resume of an old run. Still tag what differs from a plain campaign run (no `_` in the tag): `TAU3_TAG=smoke`,
`TAU3_TAG=gpt55`, `TAU3_TAG=r60-gpt55` → e.g. `fdh_voice_pipecat_retail_regular_gpt55`, model
`pine-fdh-voice-pipecat-retail-regular-gpt55`, and pass the same tag to §7.

---

## 4. Preflight [tau2 + agent]

```bash
$E/check_stack.sh 2>&1 | sed -n '/== tau2/,$p'     # tau2 + provenance rows only (§2)
```

then the §1 checks. Ready when: both `/health` as in §1, **`fdh-tau` not running**, `nemo-speech` running, the
tau2 rows all PASS, and a campaign is set. Record the provenance lines for the run card (the agent repo now has the
example committed, `e89ddbb`; check `git status` for local changes).

---

## 5. Smoke runs [tau2] — only after the user says go and has chosen the user/judge model

In the overlaid shell (Shell setup), gpt-5.2 (the default):

```bash
TAU3_TAG=smoke fdh_run pipecat mock regular --num-tasks 1      # concurrency 1
TAU3_TAG=smoke fdh_run pipecat airline regular --num-tasks 4   # concurrency 4
for d in retail telecom banking_knowledge; do TAU3_TAG=smoke fdh_run pipecat $d regular --num-tasks 1; done
```

gpt-5.5: the same, with the parent runbook §2.2.1 overrides (later flags win over `fdh_run`'s defaults):

```bash
M=openai/openai/openai/gpt-5.5
export TAU2_JUDGE_MODEL=$M TAU2_REVIEW_MODEL=$M
TAU3_TAG=smoke-gpt55 fdh_run pipecat mock regular --num-tasks 1 \
  --user-llm $M --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" --review-model $M
```

Then the report (§7) with `tag=smoke` and the five domains.

### 5.1 Gate (the `fdh-tau` runbook §5.1, with these changes)

`RUN` is the run name, `MODEL=$(fdh_model $RUN)`, `EV=$(fdh_event_log pipecat)`, `SINCE=$(head -1 $FDH_CONSOLES/$RUN.start)`.

| Check | Command | Pass when |
|---|---|---|
| I0, user/judge model, concurrency, `regular`, no infra errors, reward, audio | parent §5.1 (unchanged) | as there |
| No failed LLM calls, agent saw the run | §6 status lines → `badLLM`, `sessions`, `open_sessions` | `0`; sessions = tasks; 0 open after the run |
| Right adapter | `curl -s localhost:8776/health` | `"adapter":"pipecat"`, `"stop_secs":3.0`, `config_hash` `e53e408b0b78` |
| Hermes backend | `docker exec fdh-voice curl -s 127.0.0.1:8790/health` | `agent_kind hermes`, `refused` 0 |
| Deployed fingerprint | in [agent]: `PYTHONPATH=src uv run python -m examples.frontend_delegation_hermes.cli.fingerprint_check <events> --profile src/examples/frontend_delegation_hermes/config/profiles/pipecat_realtime_eval.yaml --gateway-config src/examples/frontend_delegation_hermes/config/gateway.eval.yaml` (events: `fdh_logs filter $EV $RUN --out /tmp/$RUN`) | `fingerprint: OK` |
| Concurrent workers (airline) | `grep -E '"event": "(session_open\|capacity_refused)"' $(fdh_gateway_log pipecat) \| tail` | 4 `session_open` close in time |
| No agent failures | `docker logs --since "$SINCE" fdh-voice 2>&1 \| grep -Ei " failed\|error\|\b401\b\|\b429\b" \| head` | nothing relevant |
| Container did not restart / die | `docker inspect fdh-voice --format '{{.RestartCount}} {{.State.Status}} {{.State.StartedAt}}'` | `0 running`, `StartedAt` before `$SINCE` (`restart: "no"`: a dead container stays dead and fails the run) |
| Worker health | `grep -il traceback $(fdh_worker_logs)/sess_*.log` (this run's sessions) | no tracebacks |
| Pipecat turn timing | the airline smoke's `task.log`/audio: the agent's reply gap after the user stops, in audio time | no cut-off user turns; reply gaps comparable to `fdh-tau`; note any |
| Report | §7 | adapter exit 0; C1–C5, C7, C8 PASS, C6 FAIL (by design); every `P0.1` line OK; `0 mismatch` |

**First Pipecat smoke:** the report adapter, join and filler steps have only been run on τ³ adapter logs so far.
Look at the smoke report's join and C-checks before starting the full runs. Re-estimate §6's wall times from the
airline smoke's mean `duration` (the 3 s wall-time VAD adds wall time per turn, so expect somewhat longer runs than `fdh-tau`).

---

## 6. Reportable runs [tau2]

Full `base` split, 1 trial, `regular`, concurrency 4, in tmux. The tmux shell needs the Shell setup (with the
overlay) — `campaign.sh` can't be used (§2):

```bash
tmux new -s fdh-pipecat
# inside tmux: Shell setup, then
export TAU3_TAG=<tag or empty>
out=$FDH_CONSOLES/fdh_campaign_pipecat_regular.out
for d in airline retail telecom banking_knowledge; do
  echo "== $(date -Is) start $d" | tee -a $out; fdh_run pipecat $d regular; echo "== $(date -Is) end $d exit=$?" | tee -a $out
done
```

Status (instead of `status.sh`):

```bash
RUN=$(fdh_run_name pipecat retail regular); tail -c 2000 $FDH_CONSOLES/$RUN.log | grep -a Status: | tail -1
grep -rah 'FINISHED SIMULATION' --include=task.log data/simulations/$RUN/artifacts | wc -l   # if Status: freezes (counts retried attempts too)
fdh_logs status "$(fdh_event_log pipecat)" $RUN --gateway "$(fdh_gateway_log pipecat)"
curl -s localhost:8776/health; echo; docker exec fdh-voice curl -s 127.0.0.1:8790/health; echo
```

Wall times: unknown for this adapter (re-estimate from the smoke; `fdh-tau` retail ran ~4.8 h for 60 tasks with
user-TTS timeouts). Resume, freeze and log-rotation rules are the `fdh-tau` runbook §6's, with `fdh_run pipecat …
--auto-resume` and **`fdh-tau` kept stopped**. Never `up -d fdh-voice` during a campaign (without the exported eval
variables it comes back as the demo).

---

## 7. Compute the metrics [tau2]

`report.sh` can't run for `pipecat` (§2). The same steps by hand, in the overlaid shell (`tag` and the domains as
run; the output folder is named like `report.sh`'s, so `archive.sh --reports` picks it up):

```bash
tag=smoke; doms=(mock airline retail telecom banking_knowledge); cx=regular
out=$FDH_METRICS/fdh_voice_${A}_${cx}${tag:+_$tag}; mkdir -p $out $FDH_METRICS/_legacy $FDH_METRICS/_setup
ev=$(fdh_event_log $A); legacy=$(fdh_legacy_log $A); gl=$(fdh_gateway_log $A)

# 1. report adapter (new module): exit 0 = no orphaned tool call
(cd $AGENT && PYTHONPATH=src uv run python -m examples.frontend_delegation_hermes.cli.report_adapter $ev --out $legacy)

# 2. setup record. The Pipecat services log no "ASR/TTS channel ready" line: the speech models are the catalog's
H=$(docker exec fdh-voice curl -s 127.0.0.1:8790/health)
fe=$(docker inspect fdh-voice --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^FRONTEND_LLM_MODEL=//p')
be=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hermes"]["model"])' <<<"$H")
cat > $FDH_METRICS/_setup/$A.json <<JSON
{"backend_llm": "$be (Hermes AIAgent, one worker per session, in fdh-voice)", "backend_reasoning": "on (enable_thinking, reasoning_budget 1024)",
 "frontend_llm": "$fe (Pipecat adapter, 3 s VAD silence)", "frontend_reasoning": "off (enable_thinking false)",
 "asr": "nemotron-speech-streaming-en-0.6b (NeMo Speech, streaming, Pipecat NvidiaForceEouSTTService)",
 "tts": "magpie-tts-multilingual (catalog voice, NeMo Speech, Pipecat NvidiaTTSService per_sentence)"}
JSON
# (check the catalog models still match: the fdh-tau logs of 2026-10-12 show these two)

# 3. metrics, checks, 429 gate, exact join, filler (as report.sh)
runs=() dirs=(); for d in "${doms[@]}"; do r=$TAU2/data/simulations/fdh_voice_${A}_${d}_${cx}${tag:+_$tag}
  [ -f $r/results.json ] && { runs+=(--run "$A=$r"); dirs+=($r); } || echo "skip $r"; done
uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py "${runs[@]}" --event-log "$A=$legacy" \
  --setup "$A=$FDH_METRICS/_setup/$A.json" --out $out > $out/console.txt 2>&1; echo "metrics exit=$? (1 expected: C6)"
fdh_logs checks $out/fba_voice_metrics.json --allow-fail C6 --join $out/join.csv --event-logs $ev | tee $out/checks.txt
fdh_logs ratelimit --join $out/join.csv --worker-logs "$(fdh_worker_logs)" --gateway-logs $gl | tee -a $out/checks.txt
fdh_logs exact-join "${dirs[@]}" --join $out/join.csv | tee $out/exact_join.txt
fdh_logs filler $ev --join $out/join.csv --json $out/filler_measured.json | tee $out/filler_measured.txt
cat $out/fba_voice_results_table.md
```

Expected results: the parent runbook §7 table (C6 FAIL by design), to be confirmed by the first smoke (§5.1).

---

## 8. How to read the numbers

The `fdh-tau` runbook §8 (one container holds voice server, gateway and workers; telecom note on; M1 proactive
status after 15 s), plus:

- **Turn timing is Pipecat's.** Its end of turn is 3 s of *wall* time (Pipecat's clock), the τ³ adapter's 800 ms of
  *audio* time (the client's clock); the two are meant to be equivalent under τ²'s slower-than-real-time ticks, but
  the mapping is not exact, so turn-taking metrics can still differ. Compare with `fdh-tau`
  only as "adapter A vs B", never as the same agent.
- **Heard-audio metrics are estimates** (audio written at 2× real time, capped playout estimate) and speech onset is
  ~200 ms late: delivery notes and heard/unheard splits are less exact than on `fdh-tau`.
- **Barge-in** is a plain Pipecat interruption (`cancel_and_merge` while thinking).
- **No spelling hold:** spelled names/IDs that span a pause are split by Pipecat's VAD; look at those failures first.

---

## 9. Archive everything [tau2 → dump]

Mandatory, before any `fdh-voice` restart/recreate, and right after each domain (log rotation). `archive.sh <run>`
fails for `pipecat` (§2); the same steps by hand, in the overlaid shell:

```bash
RUN=$(fdh_run_name pipecat airline regular); D=$DUMP/tau-3-voice/$CAMPAIGN/$RUN; SINCE=$(head -1 $FDH_CONSOLES/$RUN.start)
mkdir -p $D/{tau2,agent/raw,agent/workers,agent/config/delegation,agent/config/docker,provenance}
rsync -a $TAU2/data/simulations/$RUN/ $D/tau2/$RUN/; cp $FDH_CONSOLES/$RUN.{log,start,end} $D/tau2/ 2>/dev/null
fdh_logs filter $(fdh_event_log $A) $RUN --legacy $(fdh_legacy_log $A) --gateway $(fdh_gateway_log $A) --out $D/agent
while read -r s; do [ -n "$s" ] && cp $(fdh_worker_logs)/$s-*.log $D/agent/workers/ 2>/dev/null; done < $D/agent/sessions.txt
ls $D/agent/workers | wc -l
cp $(fdh_event_log $A) $(fdh_gateway_log $A) $D/agent/raw/
docker logs --since "$SINCE" fdh-voice > $D/agent/docker_logs.txt 2>&1
docker logs --since "$SINCE" $FDH_SPEECH_CONTAINER > $D/agent/nemo_speech_logs.txt 2>&1
docker inspect fdh-voice | python3 -c 'import json,re,sys; d=json.load(sys.stdin)
for c in d: c["Config"]["Env"]=[re.sub(r"^([^=]*(KEY|TOKEN|SECRET|PASS)[^=]*)=.*", r"\1=<redacted>", e) for e in c["Config"]["Env"]]
print(json.dumps(d, indent=1))' > $D/agent/container_inspect.json
docker inspect fdh-voice --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(FRONTEND|BACKEND)_LLM_|^FDH_' \
  | grep -vE 'KEY|TOKEN|SECRET|PASS' | tee $D/provenance/agent_llm_env.txt > $D/provenance/gateway_env.txt
docker exec fdh-voice curl -s 127.0.0.1:8790/health > $D/agent/config/gateway_health.json
curl -s localhost:8776/health > $D/agent/config/voice_health.json
cp -r $AGENT/src/examples/frontend_delegation_hermes/config/. $D/agent/config/delegation/
cp $AGENT/docker/docker-compose.frontend-delegation-hermes.yaml $AGENT/docker/Dockerfile.fdh $AGENT/docker/fdh/* $D/agent/config/docker/
docker image inspect nemotron-voice-agent-fdh:latest --format '{{.Id}} {{.Created}}' > $D/provenance/image.txt
for R in AGENT TAU2 HERMES; do d=${!R}; git -C $d rev-parse HEAD > $D/provenance/${R,,}_commit.txt
  git -C $d status --short > $D/provenance/${R,,}_status.txt; git -C $d diff > $D/provenance/${R,,}_uncommitted.diff; done
tar -czf $D/provenance/fdh_voice_eval.tgz -C $TAU2/misc/prototypes --exclude __pycache__ fdh_voice_eval
cp $TAU2/misc/examples/fdh-pipecat-tau3-runbook.md $D/provenance/
cp $TAU2/misc/prototypes/fba_voice_eval/{tau2_ihub_overrides.py,tau2_ihub.py,fba_voice_metrics.py} $D/provenance/
sed -n 's/^\([A-Z0-9_]*\)=.*/\1/p' $TAU2/.env > $D/provenance/tau2_env_keys.txt      # key names only
du -sh $D
```

Then `$E/archive.sh --reports` (works: it copies `_metrics/fdh_voice_*` and `_setup/`, incl. `pipecat.json`),
check `_reports/` holds only this campaign's reports, and write the run card `$DUMP/tau-3-voice/$CAMPAIGN/README.md`
(parent runbook §9 list), adding: **Pipecat adapter**, container `fdh-voice`, image id, `FDH_PROFILE`,
`FDH_GATEWAY_CONFIG`, `FDH_PIPECAT_SETTINGS` (3 s VAD), the run tag, and that the per-run archive was done by hand.
Secret scan, Git LFS check and commit: parent runbook §9. Never copy `.env`. Files under `[agent]/logs` are
root-owned (`sudo chown -R "$USER" $AGENT/logs` before moving or deleting; copying works).

---

## 10. Stop [agent]

Only after archiving, and only if nothing else needs it:

```bash
cd $AGENT && docker compose -f docker/docker-compose.frontend-delegation-hermes.yaml --profile tau down   # both adapters, up to 75 s
# or just this one: docker compose -f docker/docker-compose.frontend-delegation-hermes.yaml stop fdh-voice
```

`fdh-voice` has `restart: "no"`: it stays down after a reboot or a Docker restart.

---

## 11. Troubleshooting (specific to the Pipecat adapter)

The `fdh-tau` runbook §11 and the skill's `references/troubleshooting.md` still apply. In addition:

| Symptom | Check |
|---|---|
| Every task `infrastructure_error` / connection refused | `fdh-voice` died (`restart: "no"`) or you ran arm `geval` (port 8775). `docker ps -a`, `docker logs fdh-voice`; the overlay's `fdh_port pipecat` must print 8776 |
| `/health` has `"settings_file"` `adapters/pipecat.yaml`, `vad: {}`, or is HTTPS-only | the **demo** came back (an `up -d` without the four exported variables). Archive, then §1 again |
| Run hangs on the first task, no session in `$(fdh_event_log pipecat)` | `fdh_run` used port 8775 (overlay missing in this shell) or the run name/tag differs from the one you query |
| `unknown arm: pipecat` from a script | expected: the scripts re-source `fdh_lib.sh` without the overlay (§2). Use the manual commands |
| `Status:` line stops moving | count `FINISHED SIMULATION … Reward:` lines in the run's `task.log`s (§6) |
| User turns cut in two | Pipecat's 3 s VAD ended the turn on a long pause (no spelling hold). Expected behaviour of this adapter; note it |
| Report: setup models `unknown` | the Pipecat container has no `ASR/TTS channel ready` lines; write `_setup/pipecat.json` as in §7 |

---

## 12. What to record when you report

The `fdh-tau` runbook §12, with: **Pipecat adapter** (`FDH_ADAPTER=pipecat`), container `fdh-voice`, port 8776,
`FDH_PROFILE=pipecat_realtime_eval.yaml`, `FDH_GATEWAY_CONFIG=gateway.eval.yaml`,
`FDH_PIPECAT_SETTINGS=adapters/pipecat.eval.yaml` (Silero `stop_secs` 3.0 s of wall time, the counterpart of the τ³ adapter's 800 ms of audio time; Smart Turn off),
`config_hash e53e408b0b78`, agent commit, the run tag, the user/judge model the user chose, the telecom note on,
`bm25` for banking_knowledge, and that Pipecat numbers are reported separately from the τ³ adapter.
