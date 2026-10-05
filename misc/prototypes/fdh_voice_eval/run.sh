#!/usr/bin/env bash
# One tau2 run of an fdh-voice arm (runbook §4). Concurrency: 1 for mock, 4 otherwise.
# usage: [TAU3_TAG=smoke] [TAU3_CONCURRENCY=N] [TAU3_RETRIEVAL=bm25] run.sh <geval|dlg|silentack> <domain> regular [tau2 args...]
# e.g.   TAU3_TAG=smoke run.sh geval mock regular --num-tasks 1     (the campaign uses regular for every domain)
#        TAU3_TAG=smoke run.sh geval airline regular --num-tasks 4
#        run.sh geval airline regular --auto-resume          # resume a stopped run
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
case ${1:-} in -h|--help|"") sed -n '2,7p' "$0"; exit 2;; esac
[ -n "$CAMPAIGN" ] || { echo "no campaign: run new_campaign.sh first" >&2; exit 1; }
fdh_run "$@"
