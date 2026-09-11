#!/usr/bin/env bash
set -euo pipefail

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"
MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
USAGE_LIMIT_MARKER="${RPGK_USAGE_LIMIT_MARKER:-.symphony-usage-limit.json}"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom budget guard: workspace '$workspace_name' is not a GH issue workspace; no action" >&2
  exit 0
fi
issue_number="${BASH_REMATCH[1]}"

# Persist the local execution boundary before making any network-dependent tracker mutation.
# Upstream Symphony preserves the issue workspace across worker lifetimes, so before_run can use
# this marker to reject any accidental fresh Codex session even if GitHub is temporarily unavailable.
printf 'completed worker lifetime for GH-%s at %s\n' "$issue_number" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"

quota_json=""
if [[ -f "$USAGE_LIMIT_MARKER" ]]; then
  quota_json="$(cat "$USAGE_LIMIT_MARKER")"
fi

if [[ -z "$TOKEN" ]]; then
  echo "RPG Kingdom budget guard: SYMPHONY_GITHUB_TOKEN is missing; local redispatch marker is active but tracker cleanup could not run" >&2
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
  # Successful workers remove the dispatch lease before the attempt ends. Keep the local marker so
  # any later re-dispatch of this same issue must be an explicit rearm rather than an accidental one.
  rm -f -- "$USAGE_LIMIT_MARKER"
  exit 0
fi

if [[ "${RPGK_GUARD_DRY_RUN:-0}" == "1" ]]; then
  if [[ -n "$quota_json" ]]; then
    echo "RPG Kingdom budget guard: would halt GH-$issue_number because Codex usage quota was exhausted" >&2
  else
    echo "RPG Kingdom budget guard: would halt GH-$issue_number because symphony:ready remains after the worker attempt" >&2
  fi
  exit 0
fi

# Give the agent's final GitHub mutation a short consistency window before revoking the lease.
for _ in 1 2; do
  sleep 1
  if ! ready_present; then
    rm -f -- "$USAGE_LIMIT_MARKER"
    exit 0
  fi
done

# Remove the lease first so a subsequent polling tick cannot dispatch another routable issue.
api DELETE "/issues/$issue_number/labels/symphony%3Aready" >/dev/null

# The halted label is informational. The local marker plus absence of the ready lease are the actual gates.
api POST "/issues/$issue_number/labels" '{"labels":["symphony:halted"]}' >/dev/null

if [[ -n "$quota_json" ]] && jq -e '.reason == "usage_limit_exceeded"' >/dev/null 2>&1 <<<"$quota_json"; then
  retry_at="$(jq -r '.retry_at // empty' <<<"$quota_json")"
  quota_message="$(jq -r '.message // empty' <<<"$quota_json")"

  comment=$(cat <<EOF
Symphony stopped this worker lifetime because Codex reported \`usage_limit_exceeded\`.

No continuation turns were launched after the quota terminal condition, so this is **not** an \`agent.max_turns\` or implementation-budget exhaustion. The issue has been halted and its dispatch lease removed. A reviewed rearm is appropriate once Codex quota is available again.$([[ -n "$retry_at" ]] && printf '\n\nReported retry/reset time: `%s`.' "$retry_at")$([[ -n "$quota_message" ]] && printf '\n\nCodex message: `%s`' "$quota_message")
EOF
)
  comment_json="$(jq -n --arg body "$comment" '{body:$body}')"
  api POST "/issues/$issue_number/comments" "$comment_json" >/dev/null
  rm -f -- "$USAGE_LIMIT_MARKER"
  echo "RPG Kingdom budget guard: halted GH-$issue_number for Codex usage quota and removed symphony:ready" >&2
  exit 0
fi

comment=$(cat <<'EOF'
Symphony's Phase 2 budget guard stopped automatic redispatch because this worker attempt ended while `symphony:ready` was still present.

This usually means the task exhausted the configured turn budget or the worker failed before completing the PR handoff. The workspace is also locally marked as having consumed its worker-lifetime budget, so another Codex App Server session will not start even if the GitHub lease cleanup is delayed. Review the Symphony/Codex logs and current workspace/PR state before retrying. Explicitly rearm the issue only after deciding another bounded run is justified.
EOF
)
comment_json="$(jq -n --arg body "$comment" '{body:$body}')"
api POST "/issues/$issue_number/comments" "$comment_json" >/dev/null
rm -f -- "$USAGE_LIMIT_MARKER"

echo "RPG Kingdom budget guard: halted GH-$issue_number and removed symphony:ready" >&2
