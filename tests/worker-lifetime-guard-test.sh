#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-456"
git -C "$TMP/GH-456" init -q

cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_CURL_LOG:?}"
for arg in "$@"; do
  if [[ "$arg" == *"/issues/456/labels?per_page=100" ]]; then
    printf '%s\n' "${FAKE_LABELS_JSON:-[]}"
    exit 0
  fi
  if [[ "$arg" == *"/issues/456/labels/symphony%3Arearm" || "$arg" == *"/issues/456/labels/symphony%3Ahalted" ]]; then
    printf '{}\n'
    exit 0
  fi
done
printf '{}\n'
EOF
chmod +x "$TMP/bin/curl"

export PATH="$TMP/bin:$PATH"
export FAKE_CURL_LOG="$TMP/curl.log"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"

run_guard() {
  (cd "$TMP/GH-456" && FAKE_LABELS_JSON="${FAKE_LABELS_JSON:-[]}" bash "$ROOT/scripts/before-run-guard.sh")
}

# First lifetime has no local gate and needs no rearm. The guard still registers its runtime
# marker in the workspace-local Git exclude so a later rearmed handoff cannot stage it by accident.
FAKE_LABELS_JSON='[]' run_guard >/dev/null
exclude_file="$(git -C "$TMP/GH-456" rev-parse --git-path info/exclude)"
grep -Fxq '/.symphony-attempt-complete' "$TMP/GH-456/$exclude_file" 2>/dev/null || \
  grep -Fxq '/.symphony-attempt-complete' "$exclude_file" || {
    echo "attempt marker was not registered in the local Git exclude" >&2
    exit 1
  }

# A completed-attempt marker cannot be bypassed by re-adding ready alone, and ordinary Git
# staging must leave that Supervisor-owned runtime artifact untracked.
touch "$TMP/GH-456/.symphony-attempt-complete"
git -C "$TMP/GH-456" check-ignore -q -- .symphony-attempt-complete || {
  echo "attempt marker is not ignored by the workspace-local Git exclude" >&2
  exit 1
}
git -C "$TMP/GH-456" add -A -- .
if git -C "$TMP/GH-456" diff --cached --name-only | grep -Fxq '.symphony-attempt-complete'; then
  echo "ordinary Git staging included the Supervisor attempt marker" >&2
  exit 1
fi
set +e
FAKE_LABELS_JSON='[{"name":"symphony:ready"}]' run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected prior-attempt guard to exit 73 without rearm, got $status" >&2; exit 1; }
[[ -e "$TMP/GH-456/.symphony-attempt-complete" ]] || { echo "blocked retry unexpectedly removed the attempt marker" >&2; exit 1; }

# Explicit rearm authorizes exactly this before_run invocation. The marker remains as the durable
# lifetime boundary, while a stale same-issue Unity lock is cleared before the Unity guard runs.
mkdir -p "$TMP/state/locks/unity-editor.lock"
printf 'GH-456\n' > "$TMP/state/locks/unity-editor.lock/owner"
printf '%s\n' "$TMP/GH-456" > "$TMP/state/locks/unity-editor.lock/workspace"
: > "$TMP/curl.log"
FAKE_LABELS_JSON='[{"name":"symphony:ready"},{"name":"symphony:rearm"},{"name":"symphony:halted"}]' run_guard >/dev/null
[[ -e "$TMP/GH-456/.symphony-attempt-complete" ]] || { echo "explicit rearm should not delete the durable attempt marker" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "explicit rearm did not clear stale same-issue Unity lock" >&2; exit 1; }
grep -Fq '/issues/456/labels/symphony%3Arearm' "$TMP/curl.log" || { echo "explicit rearm was not consumed" >&2; exit 1; }
git -C "$TMP/GH-456" check-ignore -q -- .symphony-attempt-complete || {
  echo "rearmed lifetime lost local Git exclusion for the durable marker" >&2
  exit 1
}

# A one-shot approval cannot be reused after it has been consumed.
set +e
FAKE_LABELS_JSON='[{"name":"symphony:ready"}]' run_guard >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected consumed rearm approval to require another explicit rearm, got $status" >&2; exit 1; }

# Even an explicit rearm must never clear another issue's resource lock.
mkdir -p "$TMP/state/locks/unity-editor.lock"
printf 'GH-999\n' > "$TMP/state/locks/unity-editor.lock/owner"
FAKE_LABELS_JSON='[{"name":"symphony:ready"},{"name":"symphony:rearm"}]' run_guard >/dev/null
[[ -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "rearm incorrectly cleared another issue's Unity lock" >&2; exit 1; }
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-999" ]] || { echo "rearm changed another issue's Unity lock owner" >&2; exit 1; }

echo "worker-lifetime-guard-test: PASS"
