#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap '[[ -n "${BROKER_PID:-}" ]] && kill "$BROKER_PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

WORKSPACES="$TMP/workspaces"
STATE="$TMP/state"
GH="$WORKSPACES/GH-111"
REQUESTS="$GH/Logs/SymphonyUnity/.author-broker/requests"
RESPONSES="$GH/Logs/SymphonyUnity/.author-broker/responses"
mkdir -p "$REQUESTS" "$RESPONSES" "$STATE/locks/unity-editor.lock" "$STATE/authoring"
printf 'GH-111\n' > "$STATE/locks/unity-editor.lock/owner"
printf '%s\n' "$GH" > "$STATE/locks/unity-editor.lock/workspace"
printf '{"protocolVersion":1,"issue":"GH-111","workspace":"%s","tier":"mechanical"}\n' "$GH" > "$STATE/authoring/GH-111.json"

HOST="$TMP/fake-host.sh"
cat > "$HOST" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo x >> "${FAKE_HOST_CALLS:?}"
echo '{"protocolVersion":1,"success":true,"scene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","changedAssets":["Assets/RPGKingdom/Scenes/VerticalSlice.unity"],"appliedOperations":["set-string:X:Y:Z"],"error":null}'
SH
chmod +x "$HOST"
export FAKE_HOST_CALLS="$TMP/host-calls"

python3 -u "$ROOT/scripts/unity-author-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$HOST" \
  --poll-seconds 0.02 \
  --command-timeout-seconds 10 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

wait_for_file() {
  local path="$1"
  for _ in $(seq 1 200); do
    [[ -f "$path" ]] && return 0
    sleep 0.02
  done
  echo "timed out waiting for $path" >&2
  cat "$TMP/broker.log" >&2 || true
  return 1
}
wait_for_file "$STATE/unity-author-broker/status.json"

write_request() {
  local id="$1" tier="${2:-mechanical}"
  cat > "$REQUESTS/$id.json" <<JSON
{"protocolVersion":1,"requestId":"$id","operation":"author","authoring":{"protocolVersion":1,"tier":"$tier","scene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","operations":[{"kind":"set-string","objectPath":"Node","componentType":"StableIdentity","propertyPath":"stableId","stringValue":"node-02"}]}}
JSON
}

write_request ok-1
wait_for_file "$RESPONSES/ok-1.json"
jq -e '.status == "completed" and .exitCode == 0 and .result.success == true' "$RESPONSES/ok-1.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 1 ]]

rm -f "$STATE/authoring/GH-111.json"
write_request no-auth
wait_for_file "$RESPONSES/no-auth.json"
jq -e '.status == "rejected" and .exitCode == 83 and (.stderr | contains("no current scene-authoring authorization"))' "$RESPONSES/no-auth.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 1 ]]

printf '{"protocolVersion":1,"issue":"GH-111","workspace":"%s","tier":"mechanical"}\n' "$GH" > "$STATE/authoring/GH-111.json"
write_request structural structural
wait_for_file "$RESPONSES/structural.json"
jq -e '.status == "rejected" and .exitCode == 64' "$RESPONSES/structural.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 1 ]]

printf 'GH-999\n' > "$STATE/locks/unity-editor.lock/owner"
write_request wrong-lock
wait_for_file "$RESPONSES/wrong-lock.json"
jq -e '.status == "rejected" and .exitCode == 82' "$RESPONSES/wrong-lock.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 1 ]]

kill "$BROKER_PID"
wait "$BROKER_PID" || true
BROKER_PID=""

echo "unity-author-broker-test: PASS"
