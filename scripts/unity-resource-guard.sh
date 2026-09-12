#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=unity-resource-policy.sh
source "$ROOT/scripts/unity-resource-policy.sh"

RPGK_REPO_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
RPGK_REPO_NAME="${RPGK_REPO_NAME:-RPG-Kingdom}"
API_ROOT="${RPGK_GITHUB_API_ROOT:-https://api.github.com}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"
BROKER_STATUS="$STATE_ROOT/unity-broker/status.json"
UNITY_RUNNER="${RPGK_UNITY_RUNNER:-$ROOT/scripts/unity-runner.sh}"
DRY_RUN="${RPGK_UNITY_GUARD_DRY_RUN:-0}"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom Unity guard: workspace '$workspace_name' is not a GH issue workspace; refusing Unity policy evaluation" >&2
  exit 74
fi
issue_number="${BASH_REMATCH[1]}"
issue_identifier="GH-$issue_number"

if [[ -z "$TOKEN" ]]; then
  echo "RPG Kingdom Unity guard: SYMPHONY_GITHUB_TOKEN is missing; cannot inspect issue labels safely" >&2
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

halt_issue() {
  local reason="$1"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "RPG Kingdom Unity guard: would halt $issue_identifier: $reason" >&2
    return 0
  fi

  # Revoke the dispatch lease first so the next tracker poll cannot start Codex.
  api DELETE "/issues/$issue_number/labels/symphony%3Aready" >/dev/null 2>&1 || true
  api POST "/issues/$issue_number/labels" '{"labels":["symphony:halted"]}' >/dev/null

  local body
  body=$(cat <<EOF
Symphony halted this dispatch during the Unity preflight before launching Codex.

Reason: $reason

No Unity result was invented and no additional Codex worker lifetime was intentionally consumed. Resolve the Unity scheduling/preflight condition, then explicitly rearm the issue when another bounded attempt is justified.
EOF
)
  api POST "/issues/$issue_number/comments" "$(jq -n --arg body "$body" '{body:$body}')" >/dev/null
  echo "RPG Kingdom Unity guard: halted $issue_identifier: $reason" >&2
}

broker_is_idle() {
  [[ -f "$BROKER_STATUS" ]] || return 1
  jq -e '.protocolVersion == 1 and .state == "ready" and .activeRequest == null' "$BROKER_STATUS" >/dev/null 2>&1
}

owner_issue_is_inactive() {
  local owner="$1"
  local owner_number owner_json owner_state owner_ready owner_terminal

  [[ "$owner" =~ ^GH-([0-9]+)$ ]] || return 1
  owner_number="${BASH_REMATCH[1]}"

  set +e
  owner_json="$(api GET "/issues/$owner_number" 2>/dev/null)"
  local api_status=$?
  set -e
  (( api_status == 0 )) || return 1

  owner_state="$(jq -r '.state // "unknown"' <<<"$owner_json")"
  if [[ "$owner_state" == "closed" ]]; then
    return 0
  fi
  [[ "$owner_state" == "open" ]] || return 1

  owner_ready="$(jq -r '[.labels[]?.name | ascii_downcase] | any(. == "symphony:ready")' <<<"$owner_json")"
  [[ "$owner_ready" == "false" ]] || return 1

  owner_terminal="$(jq -r '[.labels[]?.name | ascii_downcase] | any(. == "symphony:halted" or . == "symphony:agent-review" or . == "symphony:human-review" or . == "symphony:human-attention")' <<<"$owner_json")"
  [[ "$owner_terminal" == "true" ]]
}

try_recover_stale_lock() {
  local owner="$1"
  local quarantine

  if ! broker_is_idle; then
    echo "RPG Kingdom Unity guard: retaining unity-editor lock owned by $owner; broker is active or its state is ambiguous" >&2
    return 1
  fi
  if ! owner_issue_is_inactive "$owner"; then
    echo "RPG Kingdom Unity guard: retaining unity-editor lock owned by $owner; owner issue is still dispatch-active or cannot be proven inactive" >&2
    return 1
  fi

  quarantine="$STATE_ROOT/locks/unity-editor.lock.reclaimed.$$.${RANDOM}"
  if ! mv -- "$LOCK_DIR" "$quarantine" 2>/dev/null; then
    # Another contender changed the lease after we inspected it. Do not guess; let the caller
    # re-attempt normal atomic acquisition against the current lock state.
    echo "RPG Kingdom Unity guard: stale-lock recovery raced with another contender; retrying acquisition" >&2
    return 2
  fi

  rm -rf -- "$quarantine"
  echo "RPG Kingdom Unity guard: recovered stale unity-editor lock previously owned by $owner"
  return 0
}

acquire_lock() {
  local attempt owner recovery_status

  for attempt in 1 2; do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      printf '%s\n' "$issue_identifier" > "$LOCK_DIR/owner"
      printf '%s\n' "$PWD" > "$LOCK_DIR/workspace"
      printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK_DIR/acquired-at"
      return 0
    fi

    owner="unknown"
    [[ -f "$LOCK_DIR/owner" ]] && owner="$(cat "$LOCK_DIR/owner")"

    set +e
    try_recover_stale_lock "$owner"
    recovery_status=$?
    set -e
    if (( recovery_status == 0 || recovery_status == 2 )); then
      continue
    fi

    LOCK_BLOCKING_OWNER="$owner"
    return 1
  done

  owner="unknown"
  [[ -f "$LOCK_DIR/owner" ]] && owner="$(cat "$LOCK_DIR/owner")"
  LOCK_BLOCKING_OWNER="$owner"
  return 1
}

labels_json="$(api GET "/issues/$issue_number/labels?per_page=100")"
labels="$(jq -r '.[].name' <<<"$labels_json")"

set +e
policy="$(rpgk_select_unity_policy "$labels" 2> >(cat >&2))"
policy_status=$?
set -e

if (( policy_status != 0 )); then
  halt_issue "invalid Unity scheduling labels (policy exit $policy_status)"
  exit 75
fi

IFS=$'\t' read -r resource_mode validation_mode <<<"$policy"

if [[ "$resource_mode" == "none" ]]; then
  echo "RPG Kingdom Unity guard: $issue_identifier does not request the Unity editor resource (validation=$validation_mode)"
  exit 0
fi

set +e
health_output="$(bash "$UNITY_RUNNER" health --project "$PWD" 2>&1)"
health_status=$?
set -e
if (( health_status != 0 )); then
  health_reason="$(tr '\n' ' ' <<<"$health_output" | sed -E 's/[[:space:]]+/ /g' | cut -c1-300)"
  halt_issue "Unity runner health check failed (exit $health_status): $health_reason"
  exit 76
fi

echo "$health_output"

mkdir -p "$STATE_ROOT/locks"
LOCK_BLOCKING_OWNER="unknown"
if ! acquire_lock; then
  halt_issue "the exclusive Unity editor resource is already locked by $LOCK_BLOCKING_OWNER"
  exit 77
fi

echo "RPG Kingdom Unity guard: acquired unity-editor for $issue_identifier (validation=$validation_mode)"
