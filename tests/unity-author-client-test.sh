#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap '[[ -n "${BROKER_PID:-}" ]] && kill "$BROKER_PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

WORKSPACES="$TMP/workspaces"
STATE="$TMP/state"
GH="$WORKSPACES/GH-111"
mkdir -p "$GH/Assets" "$STATE/locks/unity-editor.lock" "$STATE/authoring"
printf 'GH-111\n' > "$STATE/locks/unity-editor.lock/owner"
printf '%s\n' "$GH" > "$STATE/locks/unity-editor.lock/workspace"
printf '{"protocolVersion":1,"issue":"GH-111","workspace":"%s","tier":"mechanical"}\n' "$GH" > "$STATE/authoring/GH-111.json"

HOST="$TMP/fake-host.sh"
cat > "$HOST" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
request=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --request) request="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ "$request" == *'/.author-broker/requests/'* ]] || { echo "request did not reach author broker directory: $request" >&2; exit 97; }
echo '{"protocolVersion":1,"success":true,"scene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","changedAssets":["Assets/RPGKingdom/Scenes/VerticalSlice.unity"],"appliedOperations":["set-string:X:Y:Z"],"error":null}'
SH
chmod +x "$HOST"

python3 -u "$ROOT/scripts/unity-author-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$HOST" \
  --poll-seconds 0.02 \
  --command-timeout-seconds 10 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 200); do
  [[ -f "$STATE/unity-author-broker/status.json" ]] && break
  sleep 0.02
done
[[ -f "$STATE/unity-author-broker/status.json" ]] || { cat "$TMP/broker.log" >&2; exit 1; }

REQUEST="$TMP/request.json"
cat > "$REQUEST" <<'JSON'
{"protocolVersion":1,"tier":"mechanical","scene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","operations":[{"kind":"set-string","objectPath":"Node","componentType":"StableIdentity","propertyPath":"stableId","stringValue":"node-02"}]}
JSON

output="$(cd "$GH" && RPGK_SYMPHONY_WORKSPACE_ROOT="$WORKSPACES" RPGK_UNITY_AUTHOR_BROKER_ACK_TIMEOUT_SECONDS=2 RPGK_UNITY_AUTHOR_BROKER_TIMEOUT_SECONDS=10 bash "$ROOT/scripts/unity-author.sh" apply --request "$REQUEST")"
grep -q '"success":true' <<<"$output"
find "$GH/Logs/SymphonyUnity/.author-broker/history/requests" -type f -name '*.json' | grep -q .
[[ ! -d "$GH/Logs/SymphonyUnity/.broker/requests" ]] || [[ -z "$(find "$GH/Logs/SymphonyUnity/.broker/requests" -type f -print -quit 2>/dev/null)" ]]

kill "$BROKER_PID"
wait "$BROKER_PID" || true
BROKER_PID=""

echo "unity-author-client-test: PASS"
