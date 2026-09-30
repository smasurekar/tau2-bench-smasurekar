#!/usr/bin/env bash
# The reportable runs, domain by domain (runbook §6). Run it inside tmux.
# usage: campaign.sh [--arm dlg|silentack] [--cx regular] [domain...]   (default: the four domains, regular: the campaign rule)
#        extra tau2 args after `--`, e.g. campaign.sh -- --auto-resume
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
arm=dlg cx=regular domains=() extra=()
while [ $# -gt 0 ]; do
  case $1 in
    --arm) arm=$2; shift 2;; --cx) cx=$2; shift 2;;
    --) shift; extra=("$@"); break;;
    -h|--help) sed -n '2,4p' "$0"; exit 0;;
    *) domains+=("$1"); shift;;
  esac
done
[ ${#domains[@]} -gt 0 ] || domains=("${FDH_DOMAINS[@]}")
[ -n "$CAMPAIGN" ] || { echo "no campaign: run new_campaign.sh first" >&2; exit 1; }
fdh_port "$arm" >/dev/null || exit 2
out=$FDH_CONSOLES/fdh_campaign_${arm}_${cx}.out
echo "== CAMPAIGN $CAMPAIGN arm=$arm cx=$cx domains=${domains[*]} start $(date -Is)" | tee -a "$out"
failed=()
for domain in "${domains[@]}"; do
  echo "== $(date -Is) start $domain" | tee -a "$out"
  if fdh_run "$arm" "$domain" "$cx" "${extra[@]}"; then rc=0; else rc=$?; failed+=("$domain"); fi
  echo "== $(date -Is) end $domain exit=$rc" | tee -a "$out"
done
echo "== CAMPAIGN DONE $(date -Is) failed=[${failed[*]}]" | tee -a "$out"
[ ${#failed[@]} -eq 0 ]
