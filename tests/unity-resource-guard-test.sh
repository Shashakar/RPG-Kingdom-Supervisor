#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/GH-123" "$TMP/GH-124"
cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url="${*: -1}"
case "$url" in
  */issues/123/labels?per_page=100|*/issues/124/labels?per_page=100)
    printf '%s\n' "${FAKE_LABELS_JSON:-[]}"
    ;;
  */issues/98)
    printf '%s\n' "${FAKE_OWNER_ISSUE_JSON:-{\"state\":\"open\",\"labels\":[{\"name\":\"symphony:ready\"}]} }"
    ;;
  */issues/123|*/issues/124)
    printf '%s\n' "${FAKE_OTHER_ISSUE_JSON:-{\"state\":\"open\",\"labels\":[{\"name\":\"symphony:ready\"}]} }"
    ;;
  *)
    printf '{}\n'
    ;;
esac
EOF
chmod +x "$TMP/bin/curl"

cat > "$TMP/fake-unity-runner.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "health" ]] || { echo "unexpected fake runner command" >&2; exit 99; }
if [[ "${FAKE_UNITY_HEALTH_STATUS:-0}" != "0" ]]; then
  echo "fake Unity health failure" >&2
  exit "$FAKE_UNITY_HEALTH_STATUS"
fi
printf '{"status":"ready","unityVersion":"6000.3.10f1"}\n'
EOF
chmod +x "$TMP/fake-unity-runner.sh"

export PATH="$TMP/bin:$PATH"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_UNITY_GUARD_DRY_RUN=1
export RPGK_UNITY_RUNNER="$TMP/fake-unity-runner.sh"

run_guard() {
  local workspace="${1:-GH-123}"
  (cd "$TMP/$workspace" && bash "$ROOT/scripts/unity-resource-guard.sh")
}

write_broker_status() {
  local state="${1:-ready}"
  local active="${2:-null}"
  mkdir -p "$TMP/state/unity-broker"
  printf '{"protocolVersion":1,"state":"%s","activeRequest":%s}\n' "$state" "$active" > "$TMP/state/unity-broker/status.json"
}

write_lock() {
  local owner="$1"
  rm -rf "$TMP/state/locks/unity-editor.lock"
  mkdir -p "$TMP/state/locks/unity-editor.lock"
  printf '%s\n' "$owner" > "$TMP/state/locks/unity-editor.lock/owner"
  printf '/tmp/%s\n' "$owner" > "$TMP/state/locks/unity-editor.lock/workspace"
  printf '2026-09-11T00:00:00Z\n' > "$TMP/state/locks/unity-editor.lock/acquired-at"
}

clear_lock() {
  rm -rf "$TMP/state/locks/unity-editor.lock" "$TMP/state/locks"/unity-editor.lock.reclaimed.*
}

FAKE_LABELS_JSON='[]' run_guard >/dev/null

set +e
FAKE_LABELS_JSON='[{"name":"validation:unity-required"}]' run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 75 ]] || { echo "expected invalid required-without-resource policy to exit 75, got $status" >&2; exit 1; }

set +e
FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"}]' FAKE_UNITY_HEALTH_STATUS=9 run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 76 ]] || { echo "expected failed Unity health check to exit 76, got $status" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "health-failed preflight must not leave a lock" >&2; exit 1; }

export RPGK_UNITY_GUARD_DRY_RUN=0
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"}]'
export FAKE_UNITY_HEALTH_STATUS=0
clear_lock
run_guard >/dev/null
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-123" ]] || { echo "Unity lock owner was not recorded" >&2; exit 1; }
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "Unity lock was not released" >&2; exit 1; }

# A live owner remains authoritative even while the broker is between Unity requests.
write_broker_status ready null
write_lock GH-98
export FAKE_OWNER_ISSUE_JSON='{"state":"open","labels":[{"name":"symphony:ready"}]}'
set +e
run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 77 ]] || { echo "expected active owner lock to remain blocking, got $status" >&2; exit 1; }
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-98" ]] || { echo "active owner lock was incorrectly reclaimed" >&2; exit 1; }

# Closed owner + idle broker is positive stale evidence and may be reclaimed.
write_broker_status ready null
write_lock GH-98
export FAKE_OWNER_ISSUE_JSON='{"state":"closed","labels":[]}'
run_guard >/dev/null
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-123" ]] || { echo "closed stale owner lock was not reclaimed" >&2; exit 1; }
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)

# A halted owner without ready is also no longer dispatch-active and is safe to recover when idle.
write_broker_status ready null
write_lock GH-98
export FAKE_OWNER_ISSUE_JSON='{"state":"open","labels":[{"name":"symphony:halted"}]}'
run_guard >/dev/null
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-123" ]] || { echo "halted stale owner lock was not reclaimed" >&2; exit 1; }
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)

# Ambiguous/active broker state always fails closed even if the issue itself is inactive.
write_broker_status running '{"requestId":"GH-98-playmode-test"}'
write_lock GH-98
export FAKE_OWNER_ISSUE_JSON='{"state":"closed","labels":[]}'
set +e
run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 77 ]] || { echo "expected active broker to keep stale-looking lock blocked, got $status" >&2; exit 1; }
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-98" ]] || { echo "ambiguous broker state incorrectly reclaimed lock" >&2; exit 1; }

# Two contenders racing the same positively stale lock must never both acquire it.
write_broker_status ready null
write_lock GH-98
export FAKE_OWNER_ISSUE_JSON='{"state":"closed","labels":[]}'
export FAKE_OTHER_ISSUE_JSON='{"state":"open","labels":[{"name":"symphony:ready"}]}'
set +e
run_guard GH-123 >/dev/null 2>&1 &
pid1=$!
run_guard GH-124 >/dev/null 2>&1 &
pid2=$!
wait "$pid1"; status1=$?
wait "$pid2"; status2=$?
set -e
successes=0
[[ "$status1" -eq 0 ]] && successes=$((successes + 1))
[[ "$status2" -eq 0 ]] && successes=$((successes + 1))
[[ "$successes" -eq 1 ]] || { echo "expected exactly one stale-lock contender to acquire Unity, got statuses $status1/$status2" >&2; exit 1; }
owner="$(cat "$TMP/state/locks/unity-editor.lock/owner")"
[[ "$owner" == "GH-123" || "$owner" == "GH-124" ]] || { echo "unexpected contention winner: $owner" >&2; exit 1; }

rm -rf "$TMP/state/locks/unity-editor.lock"
echo "unity-resource-guard-test: PASS"
