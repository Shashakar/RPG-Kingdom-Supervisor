#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
BROKER_PID=""
ACTIVE_CLIENT_PID=""
INHERITED_CHILD_PID=""
DURABLE_WORKER_PID=""
cleanup() {
  if [[ -n "$ACTIVE_CLIENT_PID" ]]; then
    kill "$ACTIVE_CLIENT_PID" 2>/dev/null || true
    wait "$ACTIVE_CLIENT_PID" 2>/dev/null || true
  fi
  if [[ -n "$INHERITED_CHILD_PID" ]]; then
    kill "$INHERITED_CHILD_PID" 2>/dev/null || true
    wait "$INHERITED_CHILD_PID" 2>/dev/null || true
  fi
  if [[ -n "$DURABLE_WORKER_PID" ]]; then
    kill "$DURABLE_WORKER_PID" 2>/dev/null || true
    wait "$DURABLE_WORKER_PID" 2>/dev/null || true
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
OTHER_WORKSPACE="$WORKSPACES/GH-322"
FAKE_HOST="$TMP/fake-unity-host.sh"
BROKER_DIR="$WORKSPACE/Logs/SymphonyUnity/.broker"
OTHER_BROKER_DIR="$OTHER_WORKSPACE/Logs/SymphonyUnity/.broker"
HEALTH_HANG_FILE="$TMP/hang-health"
mkdir -p \
  "$WORKSPACE/Assets" "$WORKSPACE/Packages" "$WORKSPACE/ProjectSettings" \
  "$OTHER_WORKSPACE/Assets" "$OTHER_WORKSPACE/Packages" "$OTHER_WORKSPACE/ProjectSettings" \
  "$STATE/locks/unity-editor.lock" \
  "$BROKER_DIR/requests" "$BROKER_DIR/acks" "$BROKER_DIR/responses" \
  "$OTHER_BROKER_DIR/requests" "$OTHER_BROKER_DIR/acks" "$OTHER_BROKER_DIR/responses"

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
progress="${RPGK_UNITY_PROGRESS_FILE:-}"
cancel="${RPGK_UNITY_CANCEL_FILE:-}"
request_id="${RPGK_UNITY_REQUEST_ID:-}"
write_progress() {
  local sequence="$1" phase="$2" unity_pid="${3:-0}"
  [[ -n "$progress" ]] || return 0
  mkdir -p "$(dirname "$progress")"
  local temp_progress="$progress.tmp.$$"
  local observed unity_json
  observed="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if (( unity_pid > 0 )); then unity_json="$unity_pid"; else unity_json="null"; fi
  printf '{"protocolVersion":1,"requestId":"%s","sequence":%s,"phase":"%s","observedAt":"%s","unityPid":%s,"editorLogBytes":%s,"resultsBytes":null,"summaryPresent":false}\n' \
    "$request_id" "$sequence" "$phase" "$observed" "$unity_json" "$((sequence * 10))" > "$temp_progress"
  mv "$temp_progress" "$progress"
}

if [[ "$operation" == "health" ]]; then
  if [[ -n "${RPGK_FAKE_HEALTH_HANG_FILE:-}" && -f "$RPGK_FAKE_HEALTH_HANG_FILE" ]]; then
    sleep 30
    exit 0
  fi
  echo '{"status":"ready","unityVersion":"test","sourceProject":"fake","stageProject":"fake"}'
  exit 0
fi

case "$filter" in
  progressing)
    for sequence in 1 2 3 4 5 6 7; do
      write_progress "$sequence" tests_running "$$"
      sleep 0.3
    done
    echo '{"unityVersion":"test","testPlatform":"EditMode","testFilter":"progressing","result":"Passed","total":1,"passed":1,"failed":0,"skipped":0,"unityExitCode":0,"runId":"fake-progress"}'
    ;;
  client-exit-worker-live)
    write_progress 1 staging 0
    sleep 0.8
    write_progress 2 tests_running "$BASHPID"
    echo '{"unityVersion":"test","testPlatform":"EditMode","testFilter":"client-exit-worker-live","result":"Passed","total":1,"passed":1,"failed":0,"skipped":0,"unityExitCode":0,"runId":"fake-worker-lease"}'
    ;;
  stall-owned|client-abandon-owned|worker-abandon-owned)
    write_progress 1 unity_running "$BASHPID"
    while [[ ! -f "$cancel" ]]; do sleep 0.05; done
    write_progress 2 recovery_cancelled "$$"
    echo 'fake request-owned Unity cancelled' >&2
    exit 91
    ;;
  stall-ambiguous)
    write_progress 1 unity_running 0
    sleep 30
    ;;
  no-tests)
    write_progress 1 tests_running "$$"
    echo '{"unityVersion":"test","testPlatform":"EditMode","testFilter":"no-tests","result":"Passed","total":0,"passed":0,"failed":0,"skipped":0,"unityExitCode":0,"runId":"fake-none"}'
    ;;
  *)
    write_progress 1 tests_running "$$"
    echo '{"unityVersion":"test","testPlatform":"EditMode","testFilter":"one-test","result":"Passed","total":1,"passed":1,"failed":0,"skipped":0,"unityExitCode":0,"runId":"fake-pass"}'
    ;;
esac
EOF
chmod +x "$FAKE_HOST"

printf 'GH-321\n' > "$STATE/locks/unity-editor.lock/owner"
printf '%s\n' "$WORKSPACE" > "$STATE/locks/unity-editor.lock/workspace"

cat > "$BROKER_DIR/requests/stale-request.json" <<'EOF'
{"protocolVersion":1,"requestId":"stale-request","operation":"health","testFilter":""}
EOF

RPGK_FAKE_HEALTH_HANG_FILE="$HEALTH_HANG_FILE" python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$FAKE_HOST" \
  --poll-ms 25 \
  --command-timeout-seconds 3 \
  --stall-seconds 1 \
  --stall-recovery-grace-seconds 1 \
  --kill-grace-seconds 0.2 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 80); do
  if [[ -f "$STATE/unity-broker/status.json" ]] && jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "ready" and .protocolVersion == 1 and .stallThresholdSeconds == 1' "$STATE/unity-broker/status.json" >/dev/null

for _ in $(seq 1 40); do
  [[ -f "$BROKER_DIR/responses/stale-request.json" ]] && break
  sleep 0.05
done
jq -e '.status == "StaleRequest" and .exitCode == 89' "$BROKER_DIR/responses/stale-request.json" >/dev/null || {
  echo "unity-broker-test: stale request must fail closed instead of replaying" >&2
  exit 1
}

COMMON_ENV=(
  RPGK_SYMPHONY_WORKSPACE_ROOT="$WORKSPACES"
  RPGK_SUPERVISOR_STATE_ROOT="$STATE"
  RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS=2
  RPGK_UNITY_BROKER_TIMEOUT_SECONDS=6
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

# A run longer than the stall threshold remains healthy while actual progress advances.
env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter progressing \
  >"$TMP/progress-client.log" 2>&1 &
ACTIVE_CLIENT_PID=$!
for _ in $(seq 1 80); do
  if jq -e '.state == "running" and .activeRequest.testFilter == "progressing" and .activeRequest.phase == "tests_running"' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "running" and .activeRequest.issue == "GH-321" and .activeRequest.lastProgressAt != null' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not expose progress-aware active state" >&2
  exit 1
}

# A different issue cannot steal the slot while the first request is making progress.
set +e
busy_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" health --project "$OTHER_WORKSPACE" 2>&1)"
busy_status=$?
set -e
[[ "$busy_status" -eq 87 ]] || { echo "unity-broker-test: concurrent request should exit 87, got $busy_status" >&2; exit 1; }
grep -Fq 'HostBusy' <<<"$busy_output" || { echo "unity-broker-test: concurrent request must report HostBusy" >&2; exit 1; }
grep -Fq 'progressing' <<<"$busy_output" || { echo "unity-broker-test: HostBusy response should identify active work" >&2; exit 1; }

wait "$ACTIVE_CLIENT_PID"
ACTIVE_CLIENT_PID=""
grep -Fq '"total":1' "$TMP/progress-client.log" || { echo "unity-broker-test: progressing run did not finish successfully" >&2; exit 1; }

# A transient unity-runner client may disappear while the durable Codex worker is still alive.
# The worker telemetry lease is authoritative, so the request must continue instead of becoming
# ClientAbandoned. This reproduces the GH-186 post-#142 failure shape.
mkdir -p "$STATE/workers/active"
sleep 30 &
DURABLE_WORKER_PID=$!
jq -cn --arg workspace "$WORKSPACE" --argjson pid "$DURABLE_WORKER_PID"   '{protocolVersion:1,runId:"GH-321-implementation-worker-live",issue:321,identifier:"GH-321",role:"implementation",workspace:$workspace,pid:$pid}'   > "$STATE/workers/active/implementation.json"

env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter client-exit-worker-live   >"$TMP/worker-lease-client.log" 2>&1 &
ACTIVE_CLIENT_PID=$!
for _ in $(seq 1 80); do
  if jq -e '.state == "running" and .activeRequest.testFilter == "client-exit-worker-live" and .activeRequest.ownershipSource == "worker-lease" and .activeRequest.workerAlive == true' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e --argjson pid "$DURABLE_WORKER_PID" '.activeRequest.workerRunId == "GH-321-implementation-worker-live" and .activeRequest.workerPid == $pid' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not bind request to durable worker lease" >&2
  exit 1
}
worker_lease_request_id="$(jq -r '.activeRequest.requestId' "$STATE/unity-broker/status.json")"
kill -KILL "$ACTIVE_CLIENT_PID" 2>/dev/null || true
wait "$ACTIVE_CLIENT_PID" 2>/dev/null || true
ACTIVE_CLIENT_PID=""
for _ in $(seq 1 80); do
  response="$BROKER_DIR/responses/$worker_lease_request_id.json"
  [[ -f "$response" ]] && jq -e '.status == "completed" and .exitCode == 0' "$response" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.status == "completed" and .summary.total == 1' "$BROKER_DIR/responses/$worker_lease_request_id.json" >/dev/null || {
  echo "unity-broker-test: transient client exit incorrectly cancelled a live worker request" >&2
  exit 1
}
for _ in $(seq 1 80); do
  jq -e '.state == "ready" and .lastResult.status == "completed"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done
rm -f "$STATE/workers/active/implementation.json"
kill "$DURABLE_WORKER_PID" 2>/dev/null || true
wait "$DURABLE_WORKER_PID" 2>/dev/null || true
DURABLE_WORKER_PID=""

# If the durable worker lease ends while the request is active, the broker still reclaims
# the request even if the transient unity-runner client is alive.
sleep 30 &
DURABLE_WORKER_PID=$!
jq -cn --arg workspace "$WORKSPACE" --argjson pid "$DURABLE_WORKER_PID" \
  '{protocolVersion:1,runId:"GH-321-implementation-worker-dead",issue:321,identifier:"GH-321",role:"implementation",workspace:$workspace,pid:$pid}' \
  > "$STATE/workers/active/implementation.json"

env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter worker-abandon-owned \
  >"$TMP/worker-abandoned-client.log" 2>&1 &
ACTIVE_CLIENT_PID=$!
for _ in $(seq 1 80); do
  if jq -e '.state == "running" and .activeRequest.testFilter == "worker-abandon-owned" and .activeRequest.ownershipSource == "worker-lease" and .activeRequest.workerAlive == true' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
worker_abandon_request_id="$(jq -r '.activeRequest.requestId' "$STATE/unity-broker/status.json")"
kill -KILL "$DURABLE_WORKER_PID" 2>/dev/null || true
wait "$DURABLE_WORKER_PID" 2>/dev/null || true
DURABLE_WORKER_PID=""
set +e
wait "$ACTIVE_CLIENT_PID"
worker_abandon_status=$?
set -e
ACTIVE_CLIENT_PID=""
[[ "$worker_abandon_status" -eq 93 ]] || {
  echo "unity-broker-test: dead durable worker should terminate request with exit 93, got $worker_abandon_status" >&2
  exit 1
}
jq -e '.status == "ClientAbandoned" and .exitCode == 93 and .details.reason == "worker_process_exited" and .details.recoveryAction == "request_owned_unity_cancel"' "$BROKER_DIR/responses/$worker_abandon_request_id.json" >/dev/null || {
  echo "unity-broker-test: dead durable worker was not reclaimed through request-owned cancellation" >&2
  exit 1
}
rm -f "$STATE/workers/active/implementation.json"
for _ in $(seq 1 80); do
  jq -e '.state == "ready" and .lastResult.status == "ClientAbandoned"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done

# If the submitting worker/client disappears, a positively-owned Unity request is cancelled
# without waiting for the much longer stall threshold. This reproduces the GH-182 failure shape.
env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter client-abandon-owned \
  >"$TMP/abandoned-client.log" 2>&1 &
ACTIVE_CLIENT_PID=$!
for _ in $(seq 1 80); do
  if jq -e '.state == "running" and .activeRequest.testFilter == "client-abandon-owned" and .activeRequest.clientAlive == true' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "running" and .activeRequest.clientPid != null and .activeRequest.clientAlive == true' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not expose live submitting client" >&2
  exit 1
}
abandoned_request_id="$(jq -r '.activeRequest.requestId' "$STATE/unity-broker/status.json")"
kill -KILL "$ACTIVE_CLIENT_PID" 2>/dev/null || true
wait "$ACTIVE_CLIENT_PID" 2>/dev/null || true
ACTIVE_CLIENT_PID=""
for _ in $(seq 1 80); do
  response="$BROKER_DIR/responses/$abandoned_request_id.json"
  [[ -f "$response" ]] && jq -e '.status == "ClientAbandoned" and .exitCode == 93 and .details.reason == "client_process_exited" and .details.recoveryAction == "request_owned_unity_cancel"' "$response" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.status == "ClientAbandoned" and .exitCode == 93 and .details.recoveryResult == "host_runner_exited"' "$BROKER_DIR/responses/$abandoned_request_id.json" >/dev/null || {
  echo "unity-broker-test: abandoned client must cancel and terminalize its owned Unity request" >&2
  exit 1
}
for _ in $(seq 1 80); do
  jq -e '.state == "ready" and .lastResult.status == "ClientAbandoned"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.state == "ready" and .lastResult.status == "ClientAbandoned"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: abandoned request did not release Unity slot" >&2
  exit 1
}
post_abandon_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter one-test)"
grep -Fq '"total":1' <<<"$post_abandon_output" || { echo "unity-broker-test: slot was not reusable after abandoned client recovery" >&2; exit 1; }

# A positively-owned Unity stall is cancelled through the bounded request control path.
set +e
stall_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter stall-owned 2>&1)"
stall_status=$?
set -e
[[ "$stall_status" -eq 91 ]] || { echo "unity-broker-test: owned stall should exit 91, got $stall_status" >&2; exit 1; }
grep -Fq 'Stalled' <<<"$stall_output" || { echo "unity-broker-test: owned stall must report Stalled" >&2; exit 1; }
stall_response="$(find "$BROKER_DIR/responses" -name '*.json' -print0 | xargs -0 grep -l 'stall-owned' | tail -n 1)"
jq -e '.status == "Stalled" and .details.reason == "no_observed_progress" and .details.recoveryAction == "request_owned_unity_cancel"' "$stall_response" >/dev/null || {
  echo "unity-broker-test: stalled response must preserve recovery evidence" >&2
  exit 1
}
for _ in $(seq 1 40); do
  jq -e '.state == "ready" and .lastResult.status == "Stalled"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.state == "ready" and .lastResult.status == "Stalled"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not recover after owned stall" >&2
  exit 1
}

retry_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter one-test)"
grep -Fq '"total":1' <<<"$retry_output" || { echo "unity-broker-test: explicit retry after stall did not work" >&2; exit 1; }

# Absolute timeout still exists for operations that do not use test-progress stall detection.
touch "$HEALTH_HANG_FILE"
set +e
timeout_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" health --project "$WORKSPACE" 2>&1)"
timeout_status=$?
set -e
rm -f "$HEALTH_HANG_FILE"
[[ "$timeout_status" -eq 124 ]] || { echo "unity-broker-test: hung health request should exit 124, got $timeout_status" >&2; exit 1; }
grep -Fq 'TimedOut' <<<"$timeout_output" || { echo "unity-broker-test: absolute timeout must remain explicit" >&2; exit 1; }

# Ambiguous Unity ownership fails closed: requester receives a terminal result, but the broker
# leaves the process alive and blocks the slot rather than killing something it cannot prove it owns.
set +e
ambiguous_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" editmode --project "$WORKSPACE" --filter stall-ambiguous 2>&1)"
ambiguous_status=$?
set -e
[[ "$ambiguous_status" -eq 92 ]] || { echo "unity-broker-test: ambiguous stall should exit 92, got $ambiguous_status" >&2; exit 1; }
grep -Fq 'StallRecoveryBlocked' <<<"$ambiguous_output" || { echo "unity-broker-test: ambiguous stall must report blocked recovery" >&2; exit 1; }
jq -e '.state == "blocked" and .activeRequest.recoveryBlocked == true and .activeRequest.testFilter == "stall-ambiguous"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker must expose blocked ambiguous recovery" >&2
  exit 1
}
ambiguous_pid="$(jq -r '.activeRequest.childPid' "$STATE/unity-broker/status.json")"
kill -0 "$ambiguous_pid" 2>/dev/null || { echo "unity-broker-test: ambiguous stall process was destructively killed" >&2; exit 1; }
ambiguous_cancel="$(find "$BROKER_DIR/control" -name '*.cancel.json' -print0 2>/dev/null | xargs -0 -r grep -l 'stall-ambiguous' | tail -n 1 || true)"
[[ -z "$ambiguous_cancel" ]] || { echo "unity-broker-test: ambiguous stall must not issue a Unity cancel" >&2; exit 1; }

# Simulate the operator resolving the ambiguous host process; the broker should reconcile back to ready.
kill -TERM -- "-$ambiguous_pid" 2>/dev/null || true
for _ in $(seq 1 80); do
  jq -e '.state == "ready" and .lastResult.status == "StallRecoveryBlocked"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.state == "ready" and .lastResult.status == "StallRecoveryBlocked"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: broker did not reconcile after ambiguous process exited" >&2
  exit 1
}

# Restart reconciliation: do not advertise ready while a previously recorded host child is alive.
kill "$BROKER_PID"
wait "$BROKER_PID" 2>/dev/null || true
BROKER_PID=""

sleep 30 &
INHERITED_CHILD_PID=$!
printf '{"protocolVersion":1,"state":"running","pid":999999,"activeRequest":{"requestId":"inherited-request","operation":"editmode","childPid":%s}}\n' "$INHERITED_CHILD_PID" > "$STATE/unity-broker/status.json"

RPGK_FAKE_HEALTH_HANG_FILE="$HEALTH_HANG_FILE" python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$WORKSPACES" --state-root "$STATE" --host-runner "$FAKE_HOST" \
  --poll-ms 25 --command-timeout-seconds 3 --stall-seconds 1 \
  --stall-recovery-grace-seconds 1 --kill-grace-seconds 0.2 \
  >"$TMP/restarted-broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 80); do
  jq -e '.state == "blocked" and .activeRequest.requestId == "inherited-request" and .activeRequest.recoveryReason == "previous_broker_child_still_alive"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.state == "blocked" and .activeRequest.requestId == "inherited-request"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: restarted broker must retain a live inherited owner" >&2
  exit 1
}

kill "$INHERITED_CHILD_PID"
wait "$INHERITED_CHILD_PID" 2>/dev/null || true
INHERITED_CHILD_PID=""
for _ in $(seq 1 80); do
  jq -e '.state == "ready" and .lastResult.status == "RecoveredStaleOwner"' "$STATE/unity-broker/status.json" >/dev/null 2>&1 && break
  sleep 0.05
done
jq -e '.state == "ready" and .lastResult.status == "RecoveredStaleOwner" and .lastResult.details.reason == "inherited_host_child_exited"' "$STATE/unity-broker/status.json" >/dev/null || {
  echo "unity-broker-test: restarted broker did not reconcile after inherited child exit" >&2
  exit 1
}

# Unit-level coverage for the completion-race guard remains intact with the richer active state.
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
    request_path = root / "requests" / "race-test.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text("{}\n", encoding="utf-8")
    request_spec = module.RequestSpec(
        request_path=request_path,
        request_id="race-test",
        operation="health",
        test_filter="",
        workspace=root,
    )

    def active_for(process, stdout_handle, stderr_handle):
        now = time.monotonic()
        return module.ActiveOperation(
            spec=request_spec,
            response_path=root / "response.json",
            process=process,
            stdout_handle=stdout_handle,
            stderr_handle=stderr_handle,
            started_monotonic=now,
            started_at=module.utc_now(),
            progress_path=root / "progress.json",
            cancel_path=root / "cancel.json",
            last_progress_monotonic=now,
            last_progress_sequence=-1,
            last_progress=None,
        )

    finished_stdout = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    finished_stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    finished = subprocess.Popen(
        [sys.executable, "-c", "print('{\\\"status\\\":\\\"ready\\\"}', flush=True)"],
        stdout=finished_stdout,
        stderr=finished_stderr,
        text=True,
    )
    finished.wait(timeout=5)
    finished_active = active_for(finished, finished_stdout, finished_stderr)
    assert module.complete_finished_operation(finished_active) is not None
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
    running_active = active_for(running, running_stdout, running_stderr)
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
grep -Fq 'StallRecoveryBlocked' "$ROOT/scripts/unity-host-broker.py" || {
  echo "unity-broker-test: broker must have an explicit ambiguous-recovery state" >&2
  exit 1
}
grep -Fq 'RPGK_UNITY_PROGRESS_FILE' "$ROOT/scripts/unity-runner-host.sh" || {
  echo "unity-broker-test: host runner must forward broker progress metadata" >&2
  exit 1
}

bash -n "$ROOT/scripts/unity-runner.sh"
bash -n "$ROOT/scripts/unity-runner-host.sh"
python3 -m py_compile "$ROOT/scripts/unity-host-broker.py"

echo "unity-broker-test: PASS"
