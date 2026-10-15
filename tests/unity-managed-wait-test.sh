#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/workspaces/GH-321" "$TMP/state/workers/active"
export RPGK_SYMPHONY_WORKSPACE_ROOT="$TMP/workspaces"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
WORKSPACE="$TMP/workspaces/GH-321"
CLI=(python3 "$ROOT/scripts/unity-managed-wait.py" --workspace "$WORKSPACE")

# Never submit from a shell without a live worker lease.
set +e
"${CLI[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/unowned.json" 2>/dev/null
code=$?
set -e
[[ "$code" -eq 75 ]]
[[ ! -s "$TMP/unowned.json" ]] # denied submission has no success payload
[[ ! -d "$WORKSPACE/Logs/SymphonyUnity/.broker/requests" || -z "$(ls -A "$WORKSPACE/Logs/SymphonyUnity/.broker/requests")" ]]
"${CLI[@]}" diagnose >"$TMP/no-lease-diagnostic.json"
jq -e '.reason == "no_matching_workspace_lease" and (.matching | length == 0)' "$TMP/no-lease-diagnostic.json" >/dev/null


jq -n --arg workspace "$WORKSPACE" --argjson pid "$$" \
  '{workspace:$workspace,pid:$pid,runId:"test-worker-1"}' > "$TMP/state/workers/active/test.json"
"${CLI[@]}" diagnose >"$TMP/live-diagnostic.json"
jq -e '.reason == "host_verification_required" and .matching[0].pidVisible == null and .matching[0].hostPidLiveness == "unverifiable_from_client" and .owners == ["test-worker-1"]' "$TMP/live-diagnostic.json" >/dev/null

# A stale host PID cannot be verified in this namespace; only the host broker may reject it.
jq -n --arg workspace "$WORKSPACE" '{workspace:$workspace,pid:999999999,runId:"stale"}' > "$TMP/state/workers/active/stale.json"
"${CLI[@]}" diagnose >"$TMP/stale-diagnostic.json"
jq -e '.reason == "ambiguous_matching_leases" and ([.matching[].reason] | index("host_liveness_unverified"))' "$TMP/stale-diagnostic.json" >/dev/null
rm "$TMP/state/workers/active/stale.json"

# Two live leases fail closed rather than selecting an arbitrary owner.
cp "$TMP/state/workers/active/test.json" "$TMP/state/workers/active/duplicate.json"
"${CLI[@]}" diagnose >"$TMP/ambiguous-diagnostic.json"
jq -e '.reason == "ambiguous_matching_leases"' "$TMP/ambiguous-diagnostic.json" >/dev/null
rm "$TMP/state/workers/active/duplicate.json"
# A single lease whose host PID is invisible from this client is not proof of death.
# The client submits only the run identity; the host broker decides liveness.
jq -n --arg workspace "$WORKSPACE" '{workspace:$workspace,pid:999999999,runId:"host-worker"}' > "$TMP/state/workers/active/host.json"
mv "$TMP/state/workers/active/test.json" "$TMP/state/workers/active/visible.saved"
"${CLI[@]}" diagnose > "$TMP/host-namespace.json"
jq -e '.reason == "host_verification_required" and .matching[0].pidVisible == null
  and .owners == ["host-worker"]' "$TMP/host-namespace.json" >/dev/null
rm "$TMP/state/workers/active/host.json"
mv "$TMP/state/workers/active/visible.saved" "$TMP/state/workers/active/test.json"


"${CLI[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/start.json"
jq -e '.status == "queued" and .operation == "inspect"' "$TMP/start.json" >/dev/null
id="$(jq -r .jobId "$TMP/start.json")"
request="$WORKSPACE/Logs/SymphonyUnity/.broker/requests/$id.json"
history="$WORKSPACE/Logs/SymphonyUnity/.broker/history/requests/$id.json"
jq -e '.protocolVersion == 1 and .operation == "inspect" and (.clientPid == null)
  and .requiresWorkerLease == true and .expectedWorkerRunId == "test-worker-1"' "$request" >/dev/null
cmp "$history" "$request"

# A short-lived caller exits; polling continues using broker records only.
"${CLI[@]}" status --job-id "$id" > "$TMP/queued.json"
jq -e '.status == "queued"' "$TMP/queued.json" >/dev/null
jq -n --arg id "$id" '{requestId:$id,accepted:true}' > "$WORKSPACE/Logs/SymphonyUnity/.broker/acks/$id.json"
"${CLI[@]}" status > "$TMP/running.json"
jq -e '.status == "running"' "$TMP/running.json" >/dev/null
set +e
"${CLI[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity > "$TMP/repeat.json" 2>/dev/null
code=$?
set -e
[[ "$code" -eq 0 ]]
[[ "$(jq -r .jobId "$TMP/repeat.json")" == "$id" ]]
[[ "$(find "$WORKSPACE/Logs/SymphonyUnity/.broker/requests" -name '*.json' | wc -l)" -eq 1 ]]

# Waiting must not enqueue duplicate requests and must return bounded progress.
progress_dir="$WORKSPACE/Logs/SymphonyUnity/.broker/progress"
mkdir -p "$progress_dir"
jq -n --arg id "$id" '{requestId:$id,sequence:8,phase:"capture_running",observedAt:"probe-time",editorLogBytes:100}' \
  > "$progress_dir/$id.json"
"${CLI[@]}" status --job-id "$id" > "$TMP/canonical-progress.json"
jq -e '.progress.sequence == 8 and .progress.observedAt == "probe-time"' "$TMP/canonical-progress.json" >/dev/null
jq -n --arg id "$id" '{requestId:$id,sequence:10,phase:"capture_running",observedAt:"snapshot-time",editorLogBytes:200}' \
  > "$progress_dir/$id.json.seq.0000000010"
printf '{broken' > "$progress_dir/$id.json.seq.0000000011"
printf '{"requestId":"wrong","sequence":12}' > "$progress_dir/$id.json.seq.0000000012"
printf '{"requestId":"wrong","sequence":99}' > "$progress_dir/$id.json.seq.0000000099.tmp.1"
"${CLI[@]}" wait --job-id "$id" --wait-seconds 1 > "$TMP/wait-progress.json"
jq -e '.status == "running" and .progress.sequence == 10 and .progress.editorLogBytes == 200
  and .progress.observedAt == "snapshot-time"' "$TMP/wait-progress.json" >/dev/null
[[ "$(find "$WORKSPACE/Logs/SymphonyUnity/.broker/requests" -name '*.json' | wc -l)" -eq 1 ]]
set +e
"${CLI[@]}" wait --wait-seconds 91 > "$TMP/wait-invalid.json" 2>/dev/null
invalid_code=$?
set -e
[[ "$invalid_code" -ne 0 ]]

# Deliberately cross 30 seconds without any helper process alive.
sleep 33
jq -n --arg id "$id" '{requestId:$id,status:"Passed",exitCode:0,stdout:"inspected"}' \
  > "$WORKSPACE/Logs/SymphonyUnity/.broker/responses/$id.json"
"${CLI[@]}" wait --job-id "$id" --wait-seconds 1 > "$TMP/done.json"
jq -e '.status == "completed" and .exitCode == 0 and .brokerStatus == "Passed"' "$TMP/done.json" >/dev/null

# Failed Unity operations are terminal results, never reported as passes.
"${CLI[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/next.json"
next="$(jq -r .jobId "$TMP/next.json")"
jq -n --arg id "$next" '{requestId:$id,status:"NoTestsMatched",exitCode:91}' \
  > "$WORKSPACE/Logs/SymphonyUnity/.broker/responses/$next.json"
"${CLI[@]}" status >"$TMP/failure.json"
jq -e '.status == "completed" and .exitCode == 91 and .brokerStatus == "NoTestsMatched"' "$TMP/failure.json" >/dev/null
for args in '--operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity --position 0,200,20' \
            '--operation capture --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity --position 0,Infinity,20' \
            '--operation capture --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity --rotation 90,0' ; do
  set +e
  # Controlled fixture input only; argument splitting is intentional.
  "${CLI[@]}" start $args > "$TMP/invalid-pose.json" 2>/dev/null
  pose_code=$?
  set -e
  [[ "$pose_code" -ne 0 ]]
done
# Pose metadata must be forwarded as typed request fields for capture only.
"${CLI[@]}" start --operation capture --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity \
  --position 0,200,20 --rotation 90,0,0 > "$TMP/pose.json"
pose_id="$(jq -r .jobId "$TMP/pose.json")"
pose_request="$WORKSPACE/Logs/SymphonyUnity/.broker/requests/$pose_id.json"
jq -e '.operation == "capture" and .cameraPosition == "0,200,20" and .cameraRotation == "90,0,0"
  and .requiresWorkerLease == true' "$pose_request" >/dev/null
echo "unity-managed-wait-test: PASS"
