#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'status=$?; if (( status != 0 )); then echo "--- unity author broker log ---" >&2; cat "$TMP/broker.log" >&2 2>/dev/null || true; echo "--- responses ---" >&2; find "$TMP" -path "*/responses/*.json" -type f -print -exec cat {} \; >&2 2>/dev/null || true; fi; [[ -n "${BROKER_PID:-}" ]] && kill "$BROKER_PID" 2>/dev/null || true; rm -rf "$TMP"; exit $status' EXIT

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
request=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --request) request="$2"; shift 2 ;;
    *) shift ;;
  esac
done
tier="$(jq -r '.authoring.tier' "$request")"
echo "{\"protocolVersion\":1,\"success\":true,\"scene\":\"Assets/RPGKingdom/Scenes/VerticalSlice.unity\",\"changedAssets\":[\"Assets/RPGKingdom/Scenes/VerticalSlice.unity\"],\"appliedOperations\":[\"tier:$tier\"],\"error\":null}"
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
  local temp="$REQUESTS/.$id.json.tmp.$$.$RANDOM"
  cat > "$temp" <<JSON
{"protocolVersion":1,"requestId":"$id","operation":"author","authoring":{"protocolVersion":1,"tier":"$tier","scene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","operations":[{"kind":"set-string","objectPath":"Node","componentType":"StableIdentity","propertyPath":"stableId","stringValue":"node-02"}]}}
JSON
  mv "$temp" "$REQUESTS/$id.json"
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

# Tier-1 authorization must not grant Tier-2 structural authoring.
printf '{"protocolVersion":1,"issue":"GH-111","workspace":"%s","tier":"mechanical"}\n' "$GH" > "$STATE/authoring/GH-111.json"
write_request tier-mismatch mechanical-structural
wait_for_file "$RESPONSES/tier-mismatch.json"
jq -e '.status == "rejected" and .exitCode == 83 and (.stderr | contains("does not permit requested tier"))' "$RESPONSES/tier-mismatch.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 1 ]]

# Explicit Tier-2 authorization permits the Tier-2 request.
printf '{"protocolVersion":1,"issue":"GH-111","workspace":"%s","tier":"mechanical-structural"}\n' "$GH" > "$STATE/authoring/GH-111.json"
write_request structural-ok mechanical-structural
wait_for_file "$RESPONSES/structural-ok.json"
jq -e '.status == "completed" and .exitCode == 0 and .result.success == true and (.result.appliedOperations[0] == "tier:mechanical-structural")' "$RESPONSES/structural-ok.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 2 ]]

# Unknown tiers are rejected before the host runner.
write_request unknown structural
wait_for_file "$RESPONSES/unknown.json"
jq -e '.status == "rejected" and .exitCode == 64 and (.stderr | contains("unsupported scene-authoring tier"))' "$RESPONSES/unknown.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 2 ]]

printf 'GH-999\n' > "$STATE/locks/unity-editor.lock/owner"
write_request wrong-lock mechanical-structural
wait_for_file "$RESPONSES/wrong-lock.json"
jq -e '.status == "rejected" and .exitCode == 82' "$RESPONSES/wrong-lock.json" >/dev/null
[[ "$(wc -l < "$FAKE_HOST_CALLS")" -eq 2 ]]

kill "$BROKER_PID"
wait "$BROKER_PID" || true
BROKER_PID=""

echo "unity-author-broker-test: PASS"
