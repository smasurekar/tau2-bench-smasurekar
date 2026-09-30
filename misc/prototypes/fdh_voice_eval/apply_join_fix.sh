#!/usr/bin/env bash
# Apply the concurrency-safe session join to fba_voice_metrics.py (runbook §2.3). Idempotent.
# The fix exists only in the provenance of the FBA campaign 2026-09-29_04-32-39Z_fba-voice.
# usage: apply_join_fix.sh [path to tau2_uncommitted.diff]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
case ${1:-} in -h|--help) sed -n '2,4p' "$0"; exit 0;; esac
fix=${1:-$DUMP/tau-3-voice/2026-09-29_04-32-39Z_fba-voice/fba_voice_verdictspk_airline_regular_normhist/provenance/tau2_uncommitted.diff}
cd "$TAU2"
if grep -q "Pass 1: tool call ids" misc/prototypes/fba_voice_eval/fba_voice_metrics.py; then
  echo "join fix already applied"
else
  [ -f "$fix" ] || { echo "missing $fix" >&2; exit 1; }
  git apply --include='misc/prototypes/fba_voice_eval/*' "$fix"
  echo "join fix applied from $fix"
fi
uv run pytest misc/prototypes/fba_voice_eval/tests -q
