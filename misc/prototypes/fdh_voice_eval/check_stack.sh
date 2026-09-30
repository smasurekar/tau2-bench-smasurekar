#!/usr/bin/env bash
# Preflight for the fdh-voice τ³ campaign (runbook §1, §2): agent stack, tau2 setup, provenance.
# Prints PASS / WARN / FAIL lines; exit 1 on any FAIL. Read-only: it starts and stops nothing.
# usage: check_stack.sh [arm...]      (default: dlg; add silentack when §3 runs)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
case ${1:-} in -h|--help) sed -n '2,4p' "$0"; exit 0;; esac
arms=("$@"); [ ${#arms[@]} -gt 0 ] || arms=(dlg)
fails=0
pass() { echo "PASS  $*"; }
warn() { echo "WARN  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {'d': d}))" "$1" 2>/dev/null; }

echo "== agent stack"
wanted=0
for arm in "${arms[@]}"; do
  port=$(fdh_port "$arm") || { fail "unknown arm $arm"; continue; }
  c=$(fdh_container "$arm")
  h=$(curl -s -m5 "localhost:$port/health")
  if [ "$(json 'd["status"]' <<<"$h")" = ok ] && [ "$(json 'd["prototype"]' <<<"$h")" = frontend-delegation-hermes ]; then
    max=$(json 'd["max_sessions"]' <<<"$h"); wanted=$((wanted + max))
    if [ "$max" -ge 4 ]; then pass "$c :$port healthy, max_sessions=$max"; else fail "$c max_sessions=$max < concurrency 4"; fi
    [ "$(json 'd["backend"]["link"]' <<<"$h")" = websocket ] && pass "$c backend link websocket" || fail "$c backend link is not websocket"
    pass "$c frontend $(json 'd["frontend"]["model"]' <<<"$h")"
  else
    fail "$c :$port not healthy: ${h:-no answer}"; continue
  fi
  cmd=$(docker inspect "$c" --format '{{json .Config.Cmd}}' 2>/dev/null)
  [[ $cmd == *"$(fdh_profile "$arm")"* ]] && pass "$c runs $(fdh_profile "$arm")" || fail "$c does not run $(fdh_profile "$arm"): $cmd"
  env=$(docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
  evlog=$(sed -n 's/^FDH_EVENT_LOG=//p' <<<"$env")
  [ "logs/$(basename "$(fdh_event_log "$arm")")" = "$evlog" ] && pass "$c FDH_EVENT_LOG=$evlog" \
    || fail "$c FDH_EVENT_LOG=$evlog, expected logs/$(basename "$(fdh_event_log "$arm")")"
  asr=$(docker logs "$c" 2>&1 | grep -m1 'ASR channel ready' | sed -n 's/.* model=\([^ ]*\) .*max_streams=\([0-9]*\).*/\1 max_streams=\2/p')
  [ -n "$asr" ] && pass "$c ASR $asr" || warn "$c: no 'ASR channel ready' line in docker logs"
done

g=$(curl -s -m5 "$FDH_GATEWAY_URL/health")
if [ "$(json 'd["ok"]' <<<"$g")" = True ]; then
  pass "gateway $FDH_GATEWAY_URL ok"
  [ "$(json 'd["agent_kind"]' <<<"$g")" = hermes ] && pass "gateway agent_kind hermes" || fail "gateway agent_kind $(json 'd["agent_kind"]' <<<"$g") (not hermes)"
  pass "gateway backend $(json 'd["hermes"]["model"]' <<<"$g") reasoning=$(json 'd["hermes"]["reasoning"]' <<<"$g")"
  gmax=$(json 'd["max_sessions"]' <<<"$g")
  web=0; docker ps --format '{{.Names}}' | grep -qx fdh-voice-web && web=4
  [ $((wanted + web)) -le "$gmax" ] && pass "gateway max_sessions=$gmax >= voice servers ($wanted + web $web)" \
    || fail "gateway max_sessions=$gmax < voice servers ($wanted + web $web)"
else
  fail "gateway $FDH_GATEWAY_URL not healthy: ${g:-no answer}"
fi
docker ps --format '{{.Names}}' | grep -qx "$FDH_SPEECH_CONTAINER" && pass "$FDH_SPEECH_CONTAINER running" || fail "$FDH_SPEECH_CONTAINER not running"
docker ps --format '{{.Names}}' | grep -qx fdh-voice-web \
  && warn "fdh-voice-web is running: it shares the gateway, Hub and nemo-speech (stop it for reportable runs: docker stop fdh-voice-web)" \
  || pass "fdh-voice-web stopped"

echo "== tau2"
cd "$TAU2" || exit 1
[ -f misc/prototypes/fba_voice_eval/tau2_ihub.py ] && [ -f misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py ] \
  && pass "I0 launcher and overrides present" || fail "I0 files missing (misc/prototypes/fba_voice_eval/tau2_ihub*.py)"
for k in OPENAI_API_KEY TAU2_JUDGE_MODEL TAU2_JUDGE_BASE_URL; do
  if grep -q "^$k=." .env 2>/dev/null || [ -n "${!k:-}" ]; then pass ".env/env has $k"; else fail "$k missing in .env and environment"; fi
done
uv run python -c "import pyaudio" 2>/dev/null && pass "pyaudio importable" || fail "pyaudio missing: sudo apt install portaudio19-dev; uv sync --extra voice --extra dev --extra knowledge"
uv run python -c "import rank_bm25" 2>/dev/null && pass "rank_bm25 importable (banking_knowledge)" || fail "rank_bm25 missing: uv sync --extra voice --extra dev --extra knowledge"
grep -q "Pass 1: tool call ids" misc/prototypes/fba_voice_eval/fba_voice_metrics.py \
  && pass "fba_voice_metrics.py has the concurrency-safe join" || fail "join fix not applied: $FDH_EVAL/apply_join_fix.sh"
[ -n "$CAMPAIGN" ] && pass "CAMPAIGN=$CAMPAIGN" || warn "no campaign yet: $FDH_EVAL/new_campaign.sh"
[ -d "$DUMP/tau-3-voice" ] && pass "dump folder $DUMP/tau-3-voice" || fail "dump folder $DUMP/tau-3-voice missing"

echo "== provenance (record in the run card)"
for R in AGENT TAU2 HERMES; do
  d=${!R}
  if git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then
    echo "  $R $(git -C "$d" log --oneline -1)  dirty=$(git -C "$d" status --short | wc -l)"
  else
    warn "$R=$d is not a git checkout"
  fi
done

echo "== $([ $fails -eq 0 ] && echo READY || echo "$fails FAIL(s)")"
[ $fails -eq 0 ]
