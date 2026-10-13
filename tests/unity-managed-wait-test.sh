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
jq -e '.reason == "ready" and .matching[0].pidVisible == true and .owners == ["test-worker-1"]' "$TMP/live-diagnostic.json" >/dev/null

# A stale matching lease must not authorize a request.
jq -n --arg workspace "$WORKSPACE" '{workspace:$workspace,pid:999999999,runId:"stale"}' > "$TMP/state/workers/active/stale.json"
"${CLI[@]}" diagnose >"$TMP/stale-diagnostic.json"
jq -e '.reason == "ready" and ([.matching[].reason] | index("pid_not_visible_or_dead"))' "$TMP/stale-diagnostic.json" >/dev/null
rm "$TMP/state/workers/active/stale.json"

# Two live leases fail closed rather than selecting an arbitrary owner.
cp "$TMP/state/workers/active/test.json" "$TMP/state/workers/active/duplicate.json"
"${CLI[@]}" diagnose >"$TMP/ambiguous-diagnostic.json"
jq -e '.reason == "multiple_live_leases"' "$TMP/ambiguous-diagnostic.json" >/dev/null
rm "$TMP/state/workers/active/duplicate.json"

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
"${CLI[@]}" wait --job-id "$id" --wait-seconds 1 > "$TMP/wait-progress.json"
jq -e '.status == "running"' "$TMP/wait-progress.json" >/dev/null
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
echo "unity-managed-wait-test: PASS"
