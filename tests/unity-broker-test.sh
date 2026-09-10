#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
BROKER_PID=""
HANG_CLIENT_PID=""
cleanup() {
  if [[ -n "$HANG_CLIENT_PID" ]]; then
    kill "$HANG_CLIENT_PID" 2>/dev/null || true
    wait "$HANG_CLIENT_PID" 2>/dev/null || true
  fi
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
BROKER_DIR="$WORKSPACE/Logs/SymphonyUnity/.broker"
mkdir -p \
  "$WORKSPACE/Assets" \
  "$WORKSPACE/Packages" \
  "$WORKSPACE/ProjectSettings" \
  "$STATE/locks/unity-editor.lock" \
  "$BROKER_DIR/requests" \
  "$BROKER_DIR/acks" \
  "$BROKER_DIR/responses"

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
if [[ "$filter" == "hang" ]]; then
  sleep 30
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

cat > "$BROKER_DIR/requests/stale-request.json" <<'EOF'
{"protocolVersion":1,"requestId":"stale-request","operation":"health","testFilter":""}
EOF

python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$FAKE_HOST" \
  --poll-ms 25 \
  --command-timeout-seconds 2 \
  --kill-grace-seconds 0.2 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 80); do
  if [[ -f "$STATE/unity-broker/status.json" ]] && jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/unity-broker/status.json" >/dev/null

for _ in $(seq 1 40); do
  [[ -f "$BROKER_DIR/responses/stale-request.json" ]] && break
  sleep 0.05
done
jq -e '.status == "StaleRequest" and .exitCode == 89' "$BROKER_DIR/responses/stale-request.json" >/dev/null || {
  echo "unity-broker-test: stale request must fail closed instead of replaying" >&2
  exit 1
}
[[ ! -f "$BROKER_DIR/requests/stale-request.json" ]] || {
  echo "unity-broker-test: stale request file was not consumed" >&2
  exit 1
}

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

env "${COMMON_ENV[@]}" \
  bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter hang \
  >"$TMP/hang-client.log" 2>&1 &
HANG_CLIENT_PID=$!

for _ in $(seq 1 80); do
  if jq -e '.state == "running" and .activeRequest.testFilter == "hang"' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "running" and .activeRequest.testFilter == "hang" and .activeRequest.issue == "GH-321"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not expose active request state" >&2
  exit 1
}

sleep 1.1
jq -e '.state == "running" and .activeRequest.elapsedSeconds >= 1' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: active request heartbeat did not advance" >&2
  exit 1
}

set +e
busy_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" health --project "$WORKSPACE" 2>&1)"
busy_status=$?
set -e
[[ "$busy_status" -eq 87 ]] || { echo "unity-broker-test: concurrent request should exit 87, got $busy_status" >&2; exit 1; }
grep -Fq 'HostBusy' <<<"$busy_output" || { echo "unity-broker-test: concurrent request must report HostBusy" >&2; exit 1; }
grep -Fq 'hang' <<<"$busy_output" || { echo "unity-broker-test: HostBusy response should identify the active operation" >&2; exit 1; }

set +e
wait "$HANG_CLIENT_PID"
hang_status=$?
set -e
HANG_CLIENT_PID=""
[[ "$hang_status" -eq 124 ]] || { echo "unity-broker-test: hung host request should exit 124, got $hang_status" >&2; exit 1; }
grep -Fq 'TimedOut' "$TMP/hang-client.log" || { echo "unity-broker-test: hung request must report TimedOut" >&2; exit 1; }
grep -Fq 'process group was terminated' "$TMP/hang-client.log" || { echo "unity-broker-test: timeout must report process-group cleanup" >&2; exit 1; }

for _ in $(seq 1 40); do
  if jq -e '.state == "ready" and .lastResult.status == "TimedOut" and .lastResult.exitCode == 124' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "ready" and .lastResult.status == "TimedOut" and .lastResult.exitCode == 124' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not recover to ready after timeout" >&2
  exit 1
}

recovery_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" health --project "$WORKSPACE")"
grep -Fq '"status":"ready"' <<<"$recovery_output" || { echo "unity-broker-test: broker was not usable after timeout" >&2; exit 1; }

# Unit-level coverage for the race guard: an exited child is immediately reapable, while a truly
# running child remains busy. The broker calls this helper both at loop start and immediately before
# emitting HostBusy so a just-finished operation cannot create a stale busy lease.
python3 - "$ROOT/scripts/unity-host-broker.py" <<'PY'
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time

module_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("rpgk_unity_host_broker", module_path)
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load unity-host-broker.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(prefix="rpgk-unity-reap-test-") as temp:
    root = Path(temp)
    request_path = root / "request.json"
    request_path.write_text("{}\n", encoding="utf-8")
    request_spec = module.RequestSpec(
        request_path=request_path,
        request_id="race-test",
        operation="health",
        test_filter="",
        workspace=root,
    )

    finished_stdout = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    finished_stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    finished = subprocess.Popen(
        ["bash", "-lc", "printf '%s\\n' '{\"status\":\"ready\"}'"],
        stdout=finished_stdout,
        stderr=finished_stderr,
        text=True,
    )
    finished.wait(timeout=2)
    finished_active = module.ActiveOperation(
        spec=request_spec,
        response_path=root / "finished-response.json",
        process=finished,
        stdout_handle=finished_stdout,
        stderr_handle=finished_stderr,
        started_monotonic=time.monotonic(),
        started_at=module.utc_now(),
    )
    finished_response = module.complete_finished_operation(finished_active)
    assert finished_response is not None
    assert finished_response["status"] == "completed"
    module.close_operation(finished_active)

    running_stdout = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    running_stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    running = subprocess.Popen(
        ["bash", "-lc", "sleep 30"],
        stdout=running_stdout,
        stderr=running_stderr,
        text=True,
        start_new_session=True,
    )
    running_active = module.ActiveOperation(
        spec=request_spec,
        response_path=root / "running-response.json",
        process=running,
        stdout_handle=running_stdout,
        stderr_handle=running_stderr,
        started_monotonic=time.monotonic(),
        started_at=module.utc_now(),
    )
    assert module.complete_finished_operation(running_active) is None
    module.terminate_process_group(running, 0.1)
    module.close_operation(running_active)
PY

reap_call_count="$(grep -c 'reap_completed_active()' "$ROOT/scripts/unity-host-broker.py")"
[[ "$reap_call_count" -ge 3 ]] || {
  echo "unity-broker-test: completed-operation reaper must run at loop start and before HostBusy" >&2
  exit 1
}

if grep -Eq 'powershell\.exe|wslpath' "$ROOT/scripts/unity-runner.sh"; then
  echo "unity-broker-test: worker-facing runner must not perform WSL/Windows interop" >&2
  exit 1
fi

grep -Fq 'start_new_session=True' "$ROOT/scripts/unity-host-broker.py" || {
  echo "unity-broker-test: host operations must start in their own process group" >&2
  exit 1
}
grep -Fq 'os.killpg' "$ROOT/scripts/unity-host-broker.py" || {
  echo "unity-broker-test: broker must terminate the host operation process group" >&2
  exit 1
}

bash -n "$ROOT/scripts/unity-runner.sh"
bash -n "$ROOT/scripts/unity-runner-host.sh"
python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' "$ROOT/scripts/unity-host-broker.py"

echo "unity-broker-test: PASS"
