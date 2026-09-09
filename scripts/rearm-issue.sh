#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[0-9]+$ ]]; then
  echo "Usage: $0 <issue-number>" >&2
  exit 64
fi

issue_number="$1"
issue_identifier="GH-$issue_number"
repo="${RPGK_REPO:-Shashakar/RPG-Kingdom}"
workspace_root="${RPGK_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"
workspace="$workspace_root/$issue_identifier"
marker="$workspace/.symphony-attempt-complete"
state_root="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
unity_lock="$state_root/locks/unity-editor.lock"

labels="$(gh issue view "$issue_number" --repo "$repo" --json labels --jq '.labels[].name')"
if grep -Fxiq 'symphony:halted' <<<"$labels"; then
  gh issue edit "$issue_number" --repo "$repo" --remove-label 'symphony:halted' >/dev/null
fi

rm -f "$marker"

# A hard-killed host can leave the Phase 3 directory lock behind because after_run never executes.
# Explicit rearm is the human-approved recovery point, but only clear a lock owned by this issue.
if [[ -d "$unity_lock" && -f "$unity_lock/owner" ]]; then
  lock_owner="$(cat "$unity_lock/owner")"
  if [[ "$lock_owner" == "$issue_identifier" ]]; then
    rm -rf -- "$unity_lock"
    echo "Cleared stale unity-editor lock owned by $issue_identifier."
  fi
fi

gh issue edit "$issue_number" --repo "$repo" --add-label 'symphony:ready' >/dev/null

echo "Rearmed $issue_identifier for one bounded Symphony worker lifetime."
