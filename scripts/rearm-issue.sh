#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[0-9]+$ ]]; then
  echo "Usage: $0 <issue-number>" >&2
  exit 64
fi

issue_number="$1"
repo="${RPGK_REPO:-Shashakar/RPG-Kingdom}"
workspace_root="${RPGK_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"
workspace="$workspace_root/GH-$issue_number"
marker="$workspace/.symphony-attempt-complete"

labels="$(gh issue view "$issue_number" --repo "$repo" --json labels --jq '.labels[].name')"
if grep -Fxiq 'symphony:halted' <<<"$labels"; then
  gh issue edit "$issue_number" --repo "$repo" --remove-label 'symphony:halted' >/dev/null
fi

rm -f "$marker"
gh issue edit "$issue_number" --repo "$repo" --add-label 'symphony:ready' >/dev/null

echo "Rearmed GH-$issue_number for one bounded Symphony worker lifetime."
