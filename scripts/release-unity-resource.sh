#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom Unity resource release: workspace '$workspace_name' is not a GH issue workspace; no action" >&2
  exit 0
fi
issue_identifier="GH-${BASH_REMATCH[1]}"

if [[ ! -d "$LOCK_DIR" ]]; then
  exit 0
fi

owner="unknown"
[[ -f "$LOCK_DIR/owner" ]] && owner="$(cat "$LOCK_DIR/owner")"

if [[ "$owner" != "$issue_identifier" ]]; then
  echo "RPG Kingdom Unity resource release: refusing to release unity-editor owned by '$owner' from '$issue_identifier'" >&2
  exit 78
fi

rm -rf -- "$LOCK_DIR"
echo "RPG Kingdom Unity resource release: released unity-editor for $issue_identifier"
