#!/usr/bin/env bash
set -euo pipefail

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom budget guard: workspace '$workspace_name' is not a GH issue workspace; no action" >&2
  exit 0
fi
issue_number="${BASH_REMATCH[1]}"

if [[ -z "$TOKEN" ]]; then
  echo "RPG Kingdom budget guard: SYMPHONY_GITHUB_TOKEN is missing; cannot fail closed" >&2
  exit 70
fi

api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local args=(
    -fsS
    --retry 3
    --retry-all-errors
    -X "$method"
    -H "Authorization: Bearer $TOKEN"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
  )
  if [[ -n "$body" ]]; then
    args+=( -H "Content-Type: application/json" -d "$body" )
  fi
  curl "${args[@]}" "$API_ROOT/repos/$RPGK_REPO_OWNER/$RPGK_REPO_NAME$path"
}

ready_present() {
  local labels_json
  labels_json="$(api GET "/issues/$issue_number/labels?per_page=100")"
  jq -e '.[] | select((.name | ascii_downcase) == "symphony:ready")' >/dev/null <<<"$labels_json"
}

if ! ready_present; then
  # Successful workers remove the dispatch lease before the attempt ends.
  exit 0
fi

if [[ "${RPGK_GUARD_DRY_RUN:-0}" == "1" ]]; then
  echo "RPG Kingdom budget guard: would halt GH-$issue_number because symphony:ready remains after the worker attempt" >&2
  exit 0
fi

# Give the agent's final GitHub mutation a short consistency window before revoking the lease.
for _ in 1 2; do
  sleep 1
  if ! ready_present; then
    exit 0
  fi
done

# Remove the lease first so a subsequent polling tick cannot dispatch another fresh Codex session.
api DELETE "/issues/$issue_number/labels/symphony%3Aready" >/dev/null

# The halted label is informational. The dispatch lease is the actual execution gate.
api POST "/issues/$issue_number/labels" '{"labels":["symphony:halted"]}' >/dev/null

comment=$(cat <<'EOF'
Symphony's Phase 2 budget guard stopped automatic redispatch because this worker attempt ended while `symphony:ready` was still present.

This usually means the task exhausted the configured turn budget or the worker failed before completing the PR handoff. No new worker will start automatically. Review the Symphony/Codex logs and current workspace/PR state before retrying. To authorize another bounded run, remove `symphony:halted` and re-add `symphony:ready`.
EOF
)
comment_json="$(jq -n --arg body "$comment" '{body:$body}')"
api POST "/issues/$issue_number/comments" "$comment_json" >/dev/null

echo "RPG Kingdom budget guard: halted GH-$issue_number and removed symphony:ready" >&2
