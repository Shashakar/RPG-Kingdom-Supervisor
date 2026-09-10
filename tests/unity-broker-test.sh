#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
BROKER_PID=""
cleanup() {
  if [[ -n "$BROKER_PID" ]]; then
    kill "$BROKER_PID" 2>/dev/null || true
    wait "$BROKER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

STATE="$TMP/state"
WORKSPACES="$TMP/workspaces"
WORKSPACE="$WORKSPACES/GH-321"
FAKE_HOST="$TMP/fake-unity-host.sh"
mkdir -p "$WORKSPACE/Assets" "$WORKSPACE/Packages" "$WORKSPACE/ProjectSettings" "$STATE/locks/unity-editor.lock"

cat > "$FAKE_HOST" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
operation="$1"
shift
filter=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) shift 2 ;;
    --filter) filter="$2"; shift 2 ;;
    *) exit 64 ;;
  esac
done
if [[ "$operation" == "health" ]]; then
  echo '{"status":"ready","unityVersion":"test","sourceProject":"fake","stageProject":"fake"}'
  exit 0
fi
if [[ "$filter" == "no-tests" ]]; then
  echo '{"unityVersion":"test","testPlatform":"EditMode","testFilter":"no-tests","result":"Passed","total":0,"passed":0,"failed":0,"skipped":0,"unityExitCode":0,"runId":"fake-none"}'
  exit 0
fi
echo '{"unityVersion":"test","testPlatform":"EditMode","testFilter":"one-test","result":"Passed","total":1,"passed":1,"failed":0,"skipped":0,"unityExitCode":0,"runId":"fake-pass"}'
EOF
chmod +x "$FAKE_HOST"

printf 'GH-321\n' > "$STATE/locks/unity-editor.lock/owner"
printf '%s\n' "$WORKSPACE" > "$STATE/locks/unity-editor.lock/workspace"

python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$FAKE_HOST" \
  --poll-ms 25 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 50); do
  if [[ -f "$STATE/unity-broker/status.json" ]] && jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/unity-broker/status.json" >/dev/null

COMMON_ENV=(
  RPGK_SYMPHONY_WORKSPACE_ROOT="$WORKSPACES"
  RPGK_SUPERVISOR_STATE_ROOT="$STATE"
  RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS=2
  RPGK_UNITY_BROKER_TIMEOUT_SECONDS=5
)

health_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" health --project "$WORKSPACE")"
grep -Fq '"status":"ready"' <<<"$health_output" || { echo "unity-broker-test: health response missing" >&2; exit 1; }

run_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter one-test)"
grep -Fq '"total":1' <<<"$run_output" || { echo "unity-broker-test: passing result missing" >&2; exit 1; }

set +e
no_tests_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter no-tests 2>&1)"
no_tests_status=$?
set -e
[[ "$no_tests_status" -eq 88 ]] || { echo "unity-broker-test: zero matches should exit 88, got $no_tests_status" >&2; exit 1; }
grep -Fq 'NoTestsMatched' <<<"$no_tests_output" || { echo "unity-broker-test: zero matches must be explicit" >&2; exit 1; }

jq -e '.lastResult.status == "NoTestsMatched" and .lastResult.summary.total == 0' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker status must expose the last zero-match result" >&2
  exit 1
}

if grep -Eq 'powershell\.exe|wslpath' "$ROOT/scripts/unity-runner.sh"; then
  echo "unity-broker-test: worker-facing runner must not perform WSL/Windows interop" >&2
  exit 1
fi

bash -n "$ROOT/scripts/unity-runner.sh"
bash -n "$ROOT/scripts/unity-runner-host.sh"
python3 -m py_compile "$ROOT/scripts/unity-host-broker.py"

echo "unity-broker-test: PASS"
