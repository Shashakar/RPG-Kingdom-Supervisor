#!/usr/bin/env bash
set -euo pipefail

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"
MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"

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
fi

exit 0
