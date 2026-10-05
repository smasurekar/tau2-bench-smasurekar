#!/usr/bin/env bash
# Archive fdh-voice runs in the dump repo (runbook §9): $DUMP/tau-3-voice/$CAMPAIGN/<run>/.
# Copies only (the agent logs are shared across runs). Never copies .env files; container envs are redacted.
# usage: archive.sh <run name>...     e.g. archive.sh fdh_voice_dlg_airline_regular
#        archive.sh --all              every fdh_voice_* run started since the campaign began
#        archive.sh --reports          the campaign's reports, setup, helper scripts and console outputs
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
case ${1:-} in -h|--help|"") sed -n '2,6p' "$0"; exit 0;; esac
[ -n "$CAMPAIGN" ] || { echo "no campaign (run new_campaign.sh, or export CAMPAIGN)" >&2; exit 1; }
BASE=$DUMP/tau-3-voice/$CAMPAIGN

redacted_inspect() {  # <container> -> docker inspect with secret-looking env values redacted
  docker inspect "$1" 2>/dev/null | python3 -c '
import json, re, sys
d = json.load(sys.stdin)
for c in d:
    c["Config"]["Env"] = [e if not re.search("KEY|TOKEN|SECRET|PASS", e.split("=")[0]) else e.split("=")[0] + "=<redacted>" for e in c["Config"]["Env"]]
json.dump(d, sys.stdout, indent=2)'
}

provenance() {  # <dest dir>: commits, status and diffs of the three repos, untracked agent source, key names
  local P=$1 R d
  mkdir -p "$P"
  for R in AGENT TAU2 HERMES; do
    d=${!R}
    git -C "$d" rev-parse HEAD > "$P/${R,,}_commit.txt" 2>/dev/null || continue
    git -C "$d" status --short > "$P/${R,,}_status.txt"
    git -C "$d" diff > "$P/${R,,}_uncommitted.diff"
  done
  # The delegation prototype is untracked in the agent repo, so `git diff` misses it: archive its source.
  git -C "$AGENT" ls-files --others --exclude-standard -z -- src tests misc \
    | grep -zvE -e '__pycache__' -e '\.pyc$' -e '(^|/)\.env' \
    | tar --null -czf "$P/agent_untracked.tgz" -C "$AGENT" -T -
  # These helper scripts, as used (tau2's git state is in tau2_*.txt / tau2_uncommitted.diff).
  tar -czf "$P/fdh_voice_eval.tgz" -C "$(dirname "$FDH_EVAL")" --exclude __pycache__ "$(basename "$FDH_EVAL")"
  sed -n 's/^\([A-Z0-9_]*\)=.*/\1/p' "$TAU2/.env" > "$P/tau2_env_keys.txt"
  grep -E '^(TAU2_JUDGE_(MODEL|BASE_URL|JSON_MODE)|TAU2_USER_TTS_(MODEL|BASE_URL))=' "$TAU2/.env" > "$P/tau2_endpoints.txt"
  cp "$TAU2"/misc/prototypes/fba_voice_eval/{tau2_ihub_overrides.py,tau2_ihub.py,fba_voice_metrics.py} "$P/"
  # The gateway's own python process (logs/fdh_gateway.pid may hold a wrapper shell's pid).
  local pid; pid=$(pgrep -f 'bin/python[0-9.]* -m prototypes\.voice_delegation_hermes_agent\.sidecar\.gateway_server' | tail -1)
  { [ -n "$pid" ] && tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null; } \
    | grep -E '^(BACKEND_LLM_|FDH_)' | grep -vE 'KEY|TOKEN|SECRET|PASS' > "$P/gateway_env.txt"
}

archive_run() {
  local RUN=$1 ARM CONTAINER EV MODEL DEST SINCE
  ARM=$(fdh_arm_of "$RUN"); fdh_port "$ARM" >/dev/null || return 1
  CONTAINER=$(fdh_container "$ARM"); EV=$(fdh_event_log "$ARM"); MODEL=$(fdh_model "$RUN")
  DEST=$BASE/$RUN
  [ -d "$TAU2/data/simulations/$RUN" ] || { echo "skip $RUN: no data/simulations/$RUN" >&2; return 1; }
  echo "== $RUN -> $DEST"
  mkdir -p "$DEST"/{tau2,agent/raw,agent/workers,agent/config,provenance}

  # 1. tau2 results, trajectories, audio, task logs, console output
  rsync -a "$TAU2/data/simulations/$RUN/" "$DEST/tau2/$RUN/"
  cp "$FDH_CONSOLES/$RUN".{log,start,end} "$DEST/tau2/" 2>/dev/null

  # 2. Agent logs: this run's sessions from the voice, legacy and gateway logs; its Hermes worker logs; raw files
  fdh_logs filter "$EV" "$RUN" --legacy "$(fdh_legacy_log "$ARM")" \
    --gateway "$(fdh_gateway_log "$ARM")" --out "$DEST/agent"
  local sid n=0
  while read -r sid; do
    [ -n "$sid" ] || continue
    for f in "$AGENT/logs/fdh_workers/$sid"-*.log; do [ -f "$f" ] && cp "$f" "$DEST/agent/workers/" && n=$((n + 1)); done
  done < "$DEST/agent/sessions.txt"
  echo "  worker logs: $n"
  cp "$EV" "$(fdh_gateway_log "$ARM")" "$AGENT/logs/fdh_gateway.out" "$DEST/agent/raw/" 2>/dev/null
  SINCE=$(head -1 "$FDH_CONSOLES/$RUN.start" 2>/dev/null)
  if [ -n "$SINCE" ]; then
    docker logs --since "$SINCE" "$CONTAINER" > "$DEST/agent/docker_logs.txt" 2>&1
    docker logs --since "$SINCE" "$FDH_SPEECH_CONTAINER" > "$DEST/agent/nemo_speech_logs.txt" 2>&1
  fi

  # 3. Agent config actually used, and the container definition with secrets redacted
  local P=$AGENT/src/prototypes
  mkdir -p "$DEST/agent/config/delegation"
  cp -r "$P/voice_delegation_hermes_agent/config/." "$DEST/agent/config/delegation/"
  cp "$P/voice_frontend_backend_agent/config/voice_agent.yaml" "$P/voice_frontend_backend_agent/config/prompts.voice.yaml" \
     "$AGENT/src/examples/frontend_backend_agent/services.local.yaml" "$DEST/agent/config/" 2>/dev/null
  curl -s -m5 "$FDH_GATEWAY_URL/health" > "$DEST/agent/config/gateway_health.json"
  redacted_inspect "$CONTAINER" > "$DEST/agent/container_inspect.json"
  docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | grep -E '^(FRONTEND|BACKEND)_LLM_|^FDH_' > "$DEST/provenance/agent_llm_env.txt"

  # 4. Provenance
  provenance "$DEST/provenance"
  du -sh "$DEST"
}

archive_reports() {
  echo "== reports -> $BASE"
  mkdir -p "$BASE/_reports" "$BASE/_consoles"
  for d in "$FDH_METRICS"/fdh_voice_*/ "$FDH_METRICS/_setup/"; do
    [ -d "$d" ] && rsync -a "$d" "$BASE/_reports/$(basename "$d")/"
  done
  cp "$FDH_CONSOLES/CAMPAIGN_FDH" "$FDH_CONSOLES"/fdh_campaign_*.out "$BASE/_consoles/" 2>/dev/null
  provenance "$BASE/_consoles/provenance"
  [ -f "$BASE/README.md" ] || echo "WRITE the run card: $BASE/README.md (runbook §9)"
  du -sh "$BASE"
}

rc=0
case $1 in
  --reports) archive_reports || rc=1;;
  --all)
    mapfile -t runs < <(fdh_logs campaign-runs "$FDH_CONSOLES" "$CAMPAIGN")
    [ ${#runs[@]} -gt 0 ] || { echo "no fdh_voice_* runs since $CAMPAIGN"; exit 1; }
    for r in "${runs[@]}"; do archive_run "$r" || rc=1; done;;
  *) for r in "$@"; do archive_run "$r" || rc=1; done;;
esac
exit $rc
