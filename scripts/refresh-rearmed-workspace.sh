#!/usr/bin/env bash
set -euo pipefail

MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

fail() {
  echo "RPG Kingdom continuation refresh: $*" >&2
  exit 73
}

# Fresh worker lifetimes do not need continuation refresh. A successful before-run guard only
# reaches this script with an existing marker when an explicit one-shot rearm was just consumed.
[[ -e "$MARKER" ]] || exit 0

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "current directory is not a Git worktree"

workspace_name="$(basename "$PWD")"
[[ "$workspace_name" =~ ^GH-[0-9]+$ ]] || fail "workspace '$workspace_name' is not a GH issue workspace"

current_branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ -n "$current_branch" ]] || fail "rearmed workspace is detached; refusing to refresh ambiguous Git state"

# A worker can halt before it ever creates its issue branch. In that case leave main alone: the
# worker's later host-owned prepare operation fetches and creates its codex/* branch from origin/main.
if [[ "$current_branch" == "main" ]]; then
  echo "RPG Kingdom continuation refresh: workspace is still on main; branch preparation will start from current origin/main"
  exit 0
fi

[[ "$current_branch" == codex/* ]] || fail "rearmed workspace is on unexpected branch '$current_branch'"

# The issue workspace intentionally runs with protected Git metadata. Do not fetch/merge directly
# here. The broker host process owns writable .git metadata and network access; its prepare request
# performs the reviewed-continuation reconciliation before normal branch preparation returns.
bash "$SUPERVISOR_ROOT/scripts/git-handoff.sh" prepare --branch "$current_branch" --project "$PWD" >/dev/null || \
  fail "host-owned Git prepare could not refresh '$current_branch'"

head_sha="$(git rev-parse HEAD)"
echo "RPG Kingdom continuation refresh: host-owned prepare refreshed $current_branch at $head_sha"
