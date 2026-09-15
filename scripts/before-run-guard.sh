#!/usr/bin/env bash
set -euo pipefail

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"
MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
WORKER_STATUS=".symphony-worker-status.json"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"

ensure_supervisor_state_git_excluded() {
  local exclude_file path pattern
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi
  exclude_file="$(git rev-parse --git-path info/exclude)"
  mkdir -p "$(dirname "$exclude_file")"
  touch "$exclude_file"

  for path in "${MARKER#./}" "$WORKER_STATUS"; do
    if [[ -z "$path" || "$path" == ".." || "$path" == ../* || "$path" == */../* || "$path" == */.. ]]; then
      continue
    fi
    if git ls-tree -r --name-only HEAD -- "$path" 2>/dev/null | grep -Fxq -- "$path"; then
      echo "RPG Kingdom budget guard: Supervisor runtime path '$path' is tracked in HEAD; refusing to hide repository-owned state" >&2
      exit 73
    fi
    pattern="/$path"
    if ! grep -Fxq -- "$pattern" "$exclude_file"; then
      printf '%s\n' "$pattern" >> "$exclude_file"
    fi
  done
}

sanitize_marker_git_index() {
  local marker_path="${MARKER#./}"

  if [[ "$MARKER" = /* || "$marker_path" == ".." || "$marker_path" == ../* || "$marker_path" == */../* || "$marker_path" == */.. ]]; then
    return 0
  fi
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  if git ls-tree -r --name-only HEAD -- "$marker_path" 2>/dev/null | grep -Fxq -- "$marker_path"; then
    echo "RPG Kingdom budget guard: Supervisor attempt marker '$marker_path' is tracked in HEAD; refusing to sanitize repository-owned state" >&2
    exit 73
  fi

  if git diff --cached --name-only -- "$marker_path" | grep -Fxq -- "$marker_path"; then
    if ! git rm --cached -f --quiet --ignore-unmatch -- "$marker_path"; then
      echo "RPG Kingdom budget guard: failed to unstage stale Supervisor attempt marker '$marker_path'" >&2
      exit 73
    fi
    echo "RPG Kingdom budget guard: unstaged stale Supervisor attempt marker '$marker_path' while preserving the runtime marker"
  fi
}

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  if [[ -e "$MARKER" ]]; then
    echo "RPG Kingdom budget guard: workspace '$workspace_name' is not a GH issue workspace; refusing to bypass an existing worker-lifetime marker" >&2
    exit 73
  fi
  exit 0
fi
issue_number="${BASH_REMATCH[1]}"
issue_identifier="GH-$issue_number"

ensure_supervisor_state_git_excluded
sanitize_marker_git_index

lock_owner=""
if [[ -d "$LOCK_DIR" && -f "$LOCK_DIR/owner" ]]; then
  lock_owner="$(cat "$LOCK_DIR/owner")"
fi

needs_rearm=0
[[ -e "$MARKER" ]] && needs_rearm=1
[[ "$lock_owner" == "$issue_identifier" ]] && needs_rearm=1

if [[ -z "$TOKEN" ]]; then
  if (( needs_rearm == 1 )); then
    echo "RPG Kingdom budget guard: prior worker state exists for $issue_identifier, but SYMPHONY_GITHUB_TOKEN is missing so an explicit rearm request cannot be verified" >&2
    exit 73
  fi
  exit 0
fi

api() {
  local method="$1"
  local path="$2"
  local args=(
    -fsS
    --retry 3
    --retry-all-errors
    -X "$method"
    -H "Authorization: Bearer $TOKEN"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
  )
  curl "${args[@]}" "$API_ROOT/repos/$RPGK_REPO_OWNER/$RPGK_REPO_NAME$path"
}

labels_json="$(api GET "/issues/$issue_number/labels?per_page=100")"
rearm_requested=0
if jq -e '.[] | select((.name | ascii_downcase) == "symphony:rearm")' >/dev/null <<<"$labels_json"; then
  rearm_requested=1
fi

if (( needs_rearm == 1 && rearm_requested == 0 )); then
  echo "RPG Kingdom budget guard: this dispatch has prior worker state; refusing to launch another Codex App Server session without the one-shot symphony:rearm approval" >&2
  echo "RPG Kingdom budget guard: review the prior run, then use the Supervisor rearm path rather than re-adding symphony:ready by itself" >&2
  exit 73
fi

if (( rearm_requested == 1 )); then
  if [[ "$lock_owner" == "$issue_identifier" ]]; then
    rm -rf -- "$LOCK_DIR"
    echo "RPG Kingdom budget guard: cleared stale unity-editor lock owned by $issue_identifier"
  fi

  api DELETE "/issues/$issue_number/labels/symphony%3Arearm" >/dev/null
  api DELETE "/issues/$issue_number/labels/symphony%3Ahalted" >/dev/null 2>&1 || true
  echo "RPG Kingdom budget guard: consumed one-shot rearm approval for $issue_identifier"

  if [[ ! -e "$MARKER" ]]; then
    printf 'completed worker lifetime for %s at %s\n' "$issue_identifier" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"
    echo "RPG Kingdom budget guard: synthesized missing continuation boundary for $issue_identifier"
  fi

  bash "$(dirname "${BASH_SOURCE[0]}")/refresh-rearmed-workspace.sh"
fi

exit 0