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
[[ ! -d "$WORKSPACE/Logs/SymphonyUnity/.broker/requests" || -z "$(ls -A "$WORKSPACE/Logs/SymphonyUnity/.broker/requests")" ]]

jq -n --arg workspace "$WORKSPACE" --argjson pid "$$" \
  '{workspace:$workspace,pid:$pid,runId:"test-worker-1"}' > "$TMP/state/workers/active/test.json"
"${CLI[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/start.json"
jq -e '.status == "queued" and .operation == "inspect"' "$TMP/start.json" >/dev/null
id="$(jq -r .jobId "$TMP/start.json")"
request="$WORKSPACE/Logs/SymphonyUnity/.broker/requests/$id.json"
history="$WORKSPACE/Logs/SymphonyUnity/.broker/history/requests/$id.json"
jq -e '.protocolVersion == 1 and .operation == "inspect" and (.clientPid == null)' "$request" >/dev/null
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

# Deliberately cross 30 seconds without any helper process alive.
sleep 33
jq -n --arg id "$id" '{requestId:$id,status:"Passed",exitCode:0,stdout:"inspected"}' \
  > "$WORKSPACE/Logs/SymphonyUnity/.broker/responses/$id.json"
"${CLI[@]}" status --job-id "$id" > "$TMP/done.json"
jq -e '.status == "completed" and .exitCode == 0 and .brokerStatus == "Passed"' "$TMP/done.json" >/dev/null

# Failed Unity operations are terminal results, never reported as passes.
"${CLI[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/next.json"
next="$(jq -r .jobId "$TMP/next.json")"
jq -n --arg id "$next" '{requestId:$id,status:"NoTestsMatched",exitCode:91}' \
  > "$WORKSPACE/Logs/SymphonyUnity/.broker/responses/$next.json"
"${CLI[@]}" status >"$TMP/failure.json"
jq -e '.status == "completed" and .exitCode == 91 and .brokerStatus == "NoTestsMatched"' "$TMP/failure.json" >/dev/null
echo "unity-managed-wait-test: PASS"
