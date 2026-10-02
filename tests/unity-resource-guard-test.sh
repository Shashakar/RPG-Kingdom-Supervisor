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
    if [[ -n "${FAKE_OWNER_ISSUE_JSON:-}" ]]; then
      printf '%s\n' "$FAKE_OWNER_ISSUE_JSON"
    else
      printf '%s\n' '{"state":"open","labels":[{"name":"symphony:ready"}]}'
    fi
    ;;
  */issues/123|*/issues/124)
    if [[ -n "${FAKE_OTHER_ISSUE_JSON:-}" ]]; then
      printf '%s\n' "$FAKE_OTHER_ISSUE_JSON"
    else
      printf '%s\n' '{"state":"open","labels":[{"name":"symphony:ready"}]}'
    fi
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
command="${1:-}"
shift || true
case "$command" in
  health)
    if [[ "${FAKE_UNITY_HEALTH_STATUS:-0}" != "0" ]]; then
      echo "fake Unity health failure" >&2
      exit "$FAKE_UNITY_HEALTH_STATUS"
    fi
    printf '{"status":"ready","unityVersion":"6000.3.10f1"}\n'
    ;;
  editmode)
    filter=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --project) shift 2 ;;
        --filter) filter="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf '%s\n' "$filter" >> "${FAKE_UNITY_SMOKE_LOG:?}"
    if [[ "${FAKE_UNITY_SMOKE_STATUS:-0}" != "0" ]]; then
      echo "fake Unity smoke failure" >&2
      exit "$FAKE_UNITY_SMOKE_STATUS"
    fi
    printf '{"result":"Passed","total":1,"passed":1,"failed":0,"skipped":0}\n'
    ;;
  *)
    echo "unexpected fake runner command: $command" >&2
    exit 99
    ;;
esac
EOF
chmod +x "$TMP/fake-unity-runner.sh"

export PATH="$TMP/bin:$PATH"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_UNITY_GUARD_DRY_RUN=1
export RPGK_UNITY_RUNNER="$TMP/fake-unity-runner.sh"
export FAKE_UNITY_SMOKE_LOG="$TMP/smoke.log"
: > "$FAKE_UNITY_SMOKE_LOG"

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
export FAKE_UNITY_HEALTH_STATUS=0

# Explicit scene-authoring prose without the machine-readable authorization label
# must fail before Unity health/smoke or a Codex lifetime is consumed.
clear_lock
: > "$FAKE_UNITY_SMOKE_LOG"
export FAKE_OTHER_ISSUE_JSON='{"state":"open","body":"## Production Scene Authoring Authority\n\nThis issue explicitly grants **Tier 1 — mechanical production-scene configuration** authority.","labels":[{"name":"symphony:ready"}]}'
set +e
output="$(FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"}]' run_guard 2>&1)"
status=$?
set -e
[[ "$status" -eq 75 ]] || { echo "expected unlabeled explicit scene authority to exit 75, got $status" >&2; exit 1; }
grep -Fq 'add authoring:scene-mechanical before rearming' <<<"$output" || {
  echo "unlabeled scene-authority diagnosis did not name the Tier-1 label" >&2
  exit 1
}
[[ ! -s "$FAKE_UNITY_SMOKE_LOG" ]] || { echo "unlabeled scene authority must halt before Unity smoke" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "unlabeled scene authority must not acquire Unity" >&2; exit 1; }
export FAKE_OTHER_ISSUE_JSON=''

# Tier-1 and Tier-2 labels produce distinct exact-match authorization receipts.
clear_lock
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-mechanical"}]'
run_guard >/dev/null
jq -e '.tier == "mechanical" and .issue == "GH-123"' "$TMP/state/authoring/GH-123.json" >/dev/null
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)
[[ ! -f "$TMP/state/authoring/GH-123.json" ]] || { echo "Tier-1 authorization marker was not released" >&2; exit 1; }

clear_lock
export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-structural"}]'
run_guard >/dev/null
jq -e '.tier == "mechanical-structural" and .issue == "GH-123"' "$TMP/state/authoring/GH-123.json" >/dev/null
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)
[[ ! -f "$TMP/state/authoring/GH-123.json" ]] || { echo "Tier-2 authorization marker was not released" >&2; exit 1; }

# Conflicting authority stays fail-closed.
clear_lock
set +e
FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"},{"name":"authoring:scene-mechanical"},{"name":"authoring:scene-structural"}]' run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 75 ]] || { echo "expected conflicting authoring labels to exit 75, got $status" >&2; exit 1; }
[[ ! -f "$TMP/state/authoring/GH-123.json" ]] || { echo "conflicting authoring labels must not leave authority" >&2; exit 1; }

export FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"}]'
clear_lock
: > "$FAKE_UNITY_SMOKE_LOG"
run_guard >/dev/null
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-123" ]] || { echo "Unity lock owner was not recorded" >&2; exit 1; }
grep -Fxq 'RPGKingdom.Tests.EditMode.Resources.Core.ResourceRuntimeStateTests.OwnerCanHaveNoResources' "$FAKE_UNITY_SMOKE_LOG" || {
  echo "required Unity preflight did not execute the real EditMode readiness smoke" >&2
  exit 1
}
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "Unity lock was not released" >&2; exit 1; }

# Dry-run host review may intentionally skip the readiness smoke so a clean visual capture can
# rebuild the staged Library after acquiring the exclusive Unity lock.
export RPGK_UNITY_GUARD_DRY_RUN=1
clear_lock
: > "$FAKE_UNITY_SMOKE_LOG"
RPGK_UNITY_GUARD_SKIP_READINESS_SMOKE=1 run_guard >/dev/null
[[ -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "smoke-skipping dry-run review must retain the Unity lock" >&2; exit 1; }
[[ ! -s "$FAKE_UNITY_SMOKE_LOG" ]] || { echo "smoke-skipping dry-run review unexpectedly ran the readiness smoke" >&2; exit 1; }
(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)

# The same bypass is forbidden for normal non-dry-run dispatch.
clear_lock
export RPGK_UNITY_GUARD_DRY_RUN=0
set +e
RPGK_UNITY_GUARD_SKIP_READINESS_SMOKE=1 run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 75 ]] || { echo "expected non-dry-run smoke bypass to fail closed with 75, got $status" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "forbidden smoke bypass must release the Unity lock" >&2; exit 1; }
export RPGK_UNITY_GUARD_DRY_RUN=1

# A failed readiness smoke releases the just-acquired lock and blocks before Codex.
clear_lock
: > "$FAKE_UNITY_SMOKE_LOG"
set +e
FAKE_UNITY_SMOKE_STATUS=12 run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 79 ]] || { echo "expected failed readiness smoke to exit 79, got $status" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "failed readiness smoke must release Unity lock" >&2; exit 1; }

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
