#!/usr/bin/env bash
# Start a new fdh-voice campaign: writes _consoles/CAMPAIGN_FDH (the dump folder name).
# usage: new_campaign.sh [label (default fdh-voice)] [--force]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fdh_lib.sh"
label=fdh-voice force=0
for a in "$@"; do case $a in --force) force=1;; -h|--help) sed -n '2,3p' "$0"; exit 0;; *) label=$a;; esac; done
file=$FDH_CONSOLES/CAMPAIGN_FDH
if [ -s "$file" ] && [ $force = 0 ]; then
  echo "campaign already set: $(cat "$file")  (--force starts a new one)" >&2; exit 1
fi
mkdir -p "$FDH_CONSOLES"
echo "$(date -u +%Y-%m-%d_%H-%M-%SZ)_$label" > "$file"
echo "CAMPAIGN=$(cat "$file")   (new shells pick it up from $file)"
