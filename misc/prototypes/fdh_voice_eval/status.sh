#!/usr/bin/env bash
# One-shot status of a run (runbook §4): tau2 progress, error counts, agent-side counts, server health.
# usage: status.sh <run name>          e.g. status.sh fdh_voice_dlg_airline_regular
#        status.sh                     every fdh_voice_* run of the current campaign
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
case ${1:-} in -h|--help) sed -n '2,4p' "$0"; exit 0;; esac
runs=("$@")
if [ ${#runs[@]} -eq 0 ]; then
  [ -n "$CAMPAIGN" ] || { echo "no campaign and no run given" >&2; exit 1; }
  mapfile -t runs < <(fdh_logs campaign-runs "$FDH_CONSOLES" "$CAMPAIGN")
fi
echo "now $(date -Is)  campaign=${CAMPAIGN:-unset}"
for n in "${runs[@]}"; do
  C=$FDH_CONSOLES; arm=$(fdh_arm_of "$n"); log=$C/$n.log
  echo "== $n  start=$(head -1 "$C/$n.start" 2>/dev/null) end=$(cat "$C/$n.end" 2>/dev/null || echo -)"
  [ -f "$log" ] && grep -aE "^Status:" "$log" | tail -1
  [ -f "$log" ] && echo "  disconnects=$(grep -ac 'Not connected to API' "$log") halluc_reruns=$(grep -ac 'Hallucination detected' "$log")" \
    "tracebacks=$(grep -ac Traceback "$log") badLLM=$(grep -acE 'AuthenticationError|Error in (backchannel|interruption) decision|ELEVENLABS_API_KEY not found' "$log")"
  ev=$(fdh_event_log "$arm")
  [ -f "$ev" ] && echo "  agent: $(fdh_logs status "$ev" "$n" --gateway "$(fdh_gateway_log "$arm")")"
done
for port in 8775 8777; do   # 8775: geval or dlg (fdh-voice); 8777: silentack
  h=$(curl -s -m5 "localhost:$port/health") && echo "voice :$port $h"
done
echo "gateway $(curl -s -m5 "$FDH_GATEWAY_URL/health")"
