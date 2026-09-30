# shellcheck shell=bash
# Paths, per-arm settings and the run helper of the fdh-voice τ³ campaign.
# Source it (the scripts in this folder do): `source misc/prototypes/fdh_voice_eval/fdh_lib.sh`.
# Every path can be overridden from the environment. Runbook:
# misc/prototypes/voice-frontend-delegation-hermes-tau3-runbook.md

FDH_EVAL=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export FDH_EVAL
export TAU2=${TAU2:-$(cd "$FDH_EVAL/../../.." && pwd)}
export AGENT=${AGENT:-$(dirname "$TAU2")/nemotron-voice-agent-smasurekar}
export HERMES=${HERMES:-$(dirname "$TAU2")/hermes-agent-smasurekar}
export DUMP=${DUMP:-/home/smasurekar/Desktop/Swapnil/gitlab_repos/voice-agent-evaluation-dump}
export IHUB=${IHUB:-https://inference-api.nvidia.com/v1}
export DOCKER_HOST_IP=${DOCKER_HOST_IP:-$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)}
export FDH_CONSOLES=${FDH_CONSOLES:-$TAU2/data/simulations/_consoles}
export FDH_METRICS=${FDH_METRICS:-$TAU2/data/simulations/_metrics}
export FDH_GATEWAY_URL=${FDH_GATEWAY_URL:-http://$DOCKER_HOST_IP:8790}
export FDH_SPEECH_CONTAINER=${FDH_SPEECH_CONTAINER:-nemotron-voice-agent-nemo-speech-1}
# The campaign's dump folder name; written once by new_campaign.sh.
export CAMPAIGN=${CAMPAIGN:-$(cat "$FDH_CONSOLES/CAMPAIGN_FDH" 2>/dev/null)}

# The four reportable domains (mock is smoke-only).
FDH_DOMAINS=(airline retail telecom banking_knowledge)

# -- per-arm settings ----------------------------------------------------------------------------
fdh_port() {       # <arm> -> host port of its voice server
  case $1 in dlg) echo 8775;; silentack) echo 8777;; *) echo "unknown arm: $1 (dlg|silentack)" >&2; return 1;; esac
}
fdh_profile() {    # <arm> -> the profile its container must run
  case $1 in dlg) echo profiles/tau3_eval.yaml;; silentack) echo profiles/tau3_eval_silent_ack.yaml;; *) return 1;; esac
}
fdh_container() {  # <arm> -> container name
  if [ "$1" = dlg ]; then echo fdh-voice; else echo "fdh-voice-$1"; fi
}
fdh_event_log() {  # <arm> -> voice server event log (host path)
  if [ "$1" = dlg ]; then echo "$AGENT/logs/fdh_voice_events.jsonl"; else echo "$AGENT/logs/fdh_voice_${1}_events.jsonl"; fi
}
fdh_legacy_log() { # <arm> -> report-adapter output of that log
  echo "$FDH_METRICS/_legacy/$(basename "$(fdh_event_log "$1")" .jsonl).legacy.jsonl"
}

# -- names ---------------------------------------------------------------------------------------
fdh_run_name() {   # <arm> <domain> <cx> -> run name (TAU3_TAG appended when set)
  echo "fdh_voice_${1}_${2}_${3}${TAU3_TAG:+_$TAU3_TAG}"
}
fdh_model() {      # <run name> -> pine- model tag
  echo "pine-${1//_/-}"
}
fdh_arm_of() {     # <run name> -> arm (third `_` field; arm names never contain `_`)
  echo "$1" | cut -d_ -f3
}
fdh_concurrency() { # <domain> -> max_concurrency: 1 for mock, 4 otherwise; TAU3_CONCURRENCY overrides
  if [ -n "${TAU3_CONCURRENCY:-}" ]; then echo "$TAU3_CONCURRENCY"
  elif [ "$1" = mock ]; then echo 1; else echo 4; fi
}
fdh_logs() {       # the stdlib log helpers (fdh_logs.py)
  python3 "$FDH_EVAL/fdh_logs.py" "$@"
}

# -- one tau2 run --------------------------------------------------------------------------------
# usage: [TAU3_TAG=smoke] [TAU3_CONCURRENCY=N] [TAU3_RETRIEVAL=bm25] fdh_run <arm> <domain> <cx> [extra tau2 args...]
fdh_run() {
  if [ $# -lt 3 ]; then echo "usage: fdh_run <dlg|silentack> <domain> regular [tau2 args...]   (regular for every domain)" >&2; return 2; fi
  local arm=$1 domain=$2 cx=$3; shift 3
  local port; port=$(fdh_port "$arm") || return 2
  local conc; conc=$(fdh_concurrency "$domain")
  local extra=()
  [ "$domain" = banking_knowledge ] && extra=(--retrieval-config "${TAU3_RETRIEVAL:-bm25}")
  local name; name=$(fdh_run_name "$arm" "$domain" "$cx")
  local model; model=$(fdh_model "$name")
  mkdir -p "$FDH_CONSOLES"
  date -Is >> "$FDH_CONSOLES/$name.start"     # one line per (re)start; line 1 = first start
  echo "== $name  model=$model  port=$port  max_concurrency=$conc  speech_complexity=$cx  campaign=${CAMPAIGN:-unset}  ${extra[*]}" \
    | tee -a "$FDH_CONSOLES/$name.log"
  # The campaign uses regular for every domain (runbook, top); anything else is flagged in the console log.
  [ "$cx" = regular ] || echo "WARN speech complexity '$cx' is not regular: not comparable with the campaign's runs" \
    | tee -a "$FDH_CONSOLES/$name.log"
  ( cd "$TAU2" && PINE_REALTIME_BASE_URL=ws://localhost:${port}/v1/realtime PINE_API_KEY=unused \
    uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain "$domain" --audio-native \
      --audio-native-provider openai --audio-native-model "$model" \
      --speech-complexity "$cx" \
      --user-llm openai/azure/openai/gpt-5.2 \
      --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" \
      --review-model openai/azure/openai/gpt-5.2 \
      --task-split-name base --num-trials 1 --max-concurrency "$conc" \
      --save-to "$name" --verbose-logs "${extra[@]}" "$@" ) 2>&1 | tee -a "$FDH_CONSOLES/$name.log"
  local rc=${PIPESTATUS[0]}
  date -Is > "$FDH_CONSOLES/$name.end"
  return "$rc"
}
