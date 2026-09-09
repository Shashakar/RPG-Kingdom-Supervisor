#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/GH-123"
cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  if [[ "$arg" == *"/issues/123/labels?per_page=100" ]]; then
    printf '%s\n' "${FAKE_LABELS_JSON:-[]}"
    exit 0
  fi
done
printf '{}\n'
EOF
chmod +x "$TMP/bin/curl"

export PATH="$TMP/bin:$PATH"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_UNITY_GUARD_DRY_RUN=1

run_guard() {
  (cd "$TMP/GH-123" && bash "$ROOT/scripts/unity-resource-guard.sh")
}

FAKE_LABELS_JSON='[]' run_guard >/dev/null

set +e
FAKE_LABELS_JSON='[{"name":"validation:unity-required"}]' run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 75 ]] || { echo "expected invalid required-without-resource policy to exit 75, got $status" >&2; exit 1; }

set +e
FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"}]' RPGK_UNITY_RUNNER_READY=0 run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 76 ]] || { echo "expected unavailable Unity runner to exit 76, got $status" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "runner-unavailable preflight must not leave a lock" >&2; exit 1; }

export RPGK_UNITY_GUARD_DRY_RUN=0
FAKE_LABELS_JSON='[{"name":"resource:unity-editor"},{"name":"validation:unity-required"}]' RPGK_UNITY_RUNNER_READY=1 run_guard >/dev/null
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-123" ]] || { echo "Unity lock owner was not recorded" >&2; exit 1; }

(cd "$TMP/GH-123" && bash "$ROOT/scripts/release-unity-resource.sh" >/dev/null)
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "Unity lock was not released" >&2; exit 1; }

echo "unity-resource-guard-test: PASS"
