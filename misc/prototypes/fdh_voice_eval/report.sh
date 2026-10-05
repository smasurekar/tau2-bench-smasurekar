#!/usr/bin/env bash
# The full report of fdh-voice runs (runbook §7): report adapter, setup record, fba_voice_metrics.py,
# check summary (C6 may FAIL by design), exact join against task.log, measured filler latency.
# usage: report.sh [--arms geval|dlg[,silentack]] [--cx regular|control] [--tag TAG] [--out NAME] [domain...]
#        (default: --arms $FDH_ARM (geval) --cx regular, the four domains; domains without a run directory are skipped)
# e.g.   report.sh --tag smoke airline          -> _metrics/fdh_voice_geval_regular_smoke/
#        report.sh --tag smoke mock airline retail telecom banking_knowledge   (the smoke report)
# Exit 1 when the adapter, an unexpected check, or the exact join fails; every output is still written.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
arms=$FDH_ARM cx=regular tag="" out="" domains=()
while [ $# -gt 0 ]; do
  case $1 in
    --arms) arms=$2; shift 2;; --cx) cx=$2; shift 2;; --tag) tag=$2; shift 2;; --out) out=$2; shift 2;;
    -h|--help) sed -n '2,8p' "$0"; exit 0;;
    *) domains+=("$1"); shift;;
  esac
done
[ ${#domains[@]} -gt 0 ] || domains=("${FDH_DOMAINS[@]}")
IFS=, read -r -a arm_list <<<"$arms"
out=$FDH_METRICS/${out:-fdh_voice_${arms//,/-}_${cx}${tag:+_$tag}}
mkdir -p "$out" "$FDH_METRICS/_legacy" "$FDH_METRICS/_setup"
status=0

run_args=() log_args=() setup_args=() run_dirs=() event_logs=()
for arm in "${arm_list[@]}"; do
  fdh_port "$arm" >/dev/null || exit 2
  ev=$(fdh_event_log "$arm"); legacy=$(fdh_legacy_log "$arm"); c=$(fdh_container "$arm")

  echo "== $arm: report adapter ($ev)"
  if (cd "$AGENT" && PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.report_adapter \
        "$ev" --out "$legacy"); then :; else echo "ADAPTER CHECK FAILED ($arm): see the lines above"; status=1; fi

  setup=$FDH_METRICS/_setup/$arm.json
  if docker inspect "$c" >/dev/null 2>&1; then
    fe=$(docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^FRONTEND_LLM_MODEL=//p')
    be=$(curl -s -m5 "$FDH_GATEWAY_URL/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hermes"]["model"])' 2>/dev/null)
    asr=$(docker logs "$c" 2>&1 | sed -n 's/.*ASR channel ready: .* model=\([^ ]*\) .*/\1/p' | tail -1)
    tts=$(docker logs "$c" 2>&1 | sed -n 's/.*TTS channel ready: .* model=\([^ ]*\) voice=\([^ ]*\) .*/\1 (voice \2)/p' | tail -1)
    cat > "$setup" <<JSON
{"backend_llm": "${be:-unknown} (Hermes AIAgent, one worker per session)", "backend_reasoning": "on (enable_thinking, reasoning_budget 1024)",
 "frontend_llm": "${fe:-unknown}", "frontend_reasoning": "off (enable_thinking false)",
 "asr": "${asr:-unknown} (NeMo Speech, streaming)", "tts": "${tts:-unknown} (NeMo Speech)"}
JSON
    echo "setup $setup: $(tr -d '\n' < "$setup")"
  elif [ -f "$setup" ]; then
    echo "WARN $c not running: keeping the existing $setup"
  else
    echo "WARN $c not running and no $setup: the table's model columns will say 'unrecorded'"
  fi
  [ -f "$setup" ] && setup_args+=(--setup "$arm=$setup")
  log_args+=(--event-log "$arm=$legacy"); event_logs+=("$ev")

  for d in "${domains[@]}"; do
    r=$TAU2/data/simulations/fdh_voice_${arm}_${d}_${cx}${tag:+_$tag}
    if [ -f "$r/results.json" ]; then run_args+=(--run "$arm=$r"); run_dirs+=("$r")
    else echo "skip $(basename "$r"): no results.json"; fi
  done
done
[ ${#run_dirs[@]} -gt 0 ] || { echo "no runs found" >&2; exit 1; }

echo "== fba_voice_metrics.py -> $out"
(cd "$TAU2" && uv run python misc/prototypes/fba_voice_eval/fba_voice_metrics.py \
   "${run_args[@]}" "${log_args[@]}" "${setup_args[@]}" --out "$out") > "$out/console.txt" 2>&1
echo "metrics exit=$? (1 is expected: C6 FAILs by design); console: $out/console.txt"

echo "== checks"
fdh_logs checks "$out/fba_voice_metrics.json" --allow-fail C6 \
  --join "$out/join.csv" --event-logs "${event_logs[@]}" | tee "$out/checks.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || status=1

echo "== exact join against task.log"
fdh_logs exact-join "${run_dirs[@]}" --join "$out/join.csv" | tee "$out/exact_join.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || status=1

echo "== measured filler latency"
fdh_logs filler "${event_logs[@]}" --join "$out/join.csv" --json "$out/filler_measured.json" | tee "$out/filler_measured.txt"

echo "== results table: $out/fba_voice_results_table.md"
cat "$out/fba_voice_results_table.md" 2>/dev/null
echo "== report.sh $([ $status -eq 0 ] && echo OK || echo 'FAILED: see above')"
exit $status
