#!/usr/bin/env bash
set -euo pipefail

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"
MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"

ensure_marker_git_excluded() {
  local marker_path="${MARKER#./}"
  local exclude_file
  local pattern

  # The attempt marker is Supervisor-owned workspace state, not repository source. Keep the
  # default/root-relative marker out of ordinary Git staging without changing the project's
  # checked-in .gitignore. If a custom marker points outside the workspace, leave it alone.
  if [[ "$MARKER" = /* || "$marker_path" == ".." || "$marker_path" == ../* || "$marker_path" == */../* || "$marker_path" == */.. ]]; then
    return 0
  fi
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  exclude_file="$(git rev-parse --git-path info/exclude)"
  pattern="/$marker_path"
  mkdir -p "$(dirname "$exclude_file")"
  touch "$exclude_file"
  if ! grep -Fxq -- "$pattern" "$exclude_file"; then
    printf '%s\n' "$pattern" >> "$exclude_file"
  fi
}

sanitize_marker_git_index() {
  local marker_path="${MARKER#./}"

  # Only sanitize a workspace-relative Supervisor marker. Never make assumptions about custom
  # paths that escape the issue workspace.
  if [[ "$MARKER" = /* || "$marker_path" == ".." || "$marker_path" == ../* || "$marker_path" == */../* || "$marker_path" == */.. ]]; then
    return 0
  fi
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  # A marker committed to repository history is not stale runtime residue. Fail closed rather than
  # silently rewriting repository state or hiding a source-controlled path.
  if git ls-tree -r --name-only HEAD -- "$marker_path" 2>/dev/null | grep -Fxq -- "$marker_path"; then
    echo "RPG Kingdom budget guard: Supervisor attempt marker '$marker_path' is tracked in HEAD; refusing to sanitize repository-owned state" >&2
    exit 73
  fi

  # A previous failed handoff may have left the untracked runtime marker staged in the index before
  # local Git exclusion was installed. Remove only that index entry and preserve the marker on disk.
  # If the marker is force-staged again after preflight, git-handoff-host.py still rejects it via
  # ForbiddenPath.
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

# Register the Supervisor-owned marker as local-only state before any worker can reach Git
# handoff, then clean up any stale index entry left by a handoff that failed before this exclusion
# existed. The marker itself remains durable on disk.
ensure_marker_git_excluded
sanitize_marker_git_index

lock_owner=""
if [[ -d "$LOCK_DIR" && -f "$LOCK_DIR/owner" ]]; then
  lock_owner="$(cat "$LOCK_DIR/owner")"
fi

needs_rearm=0
[[ -e "$MARKER" ]] && needs_rearm=1
[[ "$lock_owner" == "$issue_identifier" ]] && needs_rearm=1

# A normal first lifetime remains local-only if the tracker credential is unavailable.
# Any prior-attempt state, however, may only be bypassed by an explicit remote rearm request.
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
  # The explicit rearm approval is one-shot. Keep the completed-attempt marker in place: this
  # before_run invocation has already authorized the new lifetime, and consuming the label means
  # another invocation cannot reuse that approval. after_run refreshes the marker for this lifetime.
  if [[ "$lock_owner" == "$issue_identifier" ]]; then
    rm -rf -- "$LOCK_DIR"
    echo "RPG Kingdom budget guard: cleared stale unity-editor lock owned by $issue_identifier"
  fi

  # Consume rearm before Codex starts. If a later preflight fails, another explicit rearm is required.
  api DELETE "/issues/$issue_number/labels/symphony%3Arearm" >/dev/null
  # The halted label is informational; remove it when present without making absence an error.
  api DELETE "/issues/$issue_number/labels/symphony%3Ahalted" >/dev/null 2>&1 || true
  echo "RPG Kingdom budget guard: consumed one-shot rearm approval for $issue_identifier"

  # A reviewed continuation must not launch against a checkout that predates changes merged to
  # main while the prior worker was halted. Refresh here, while Git metadata/network access is
  # still host-owned, and fail closed rather than asking Codex to rebase protected .git state.
  bash "$(dirname "${BASH_SOURCE[0]}")/refresh-rearmed-workspace.sh"
fi

exit 0
