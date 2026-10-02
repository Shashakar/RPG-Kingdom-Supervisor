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
WORKSPACE="$WORKSPACES/GH-701"
BROKER_DIR="$WORKSPACE/Logs/SymphonyUnity/.broker"
FAKE_HOST="$TMP/fake-host.sh"
mkdir -p "$WORKSPACE/Assets/RPGKingdom/Scenes" "$WORKSPACE/Packages" "$WORKSPACE/ProjectSettings" \
  "$STATE/locks/unity-editor.lock" "$BROKER_DIR/requests" "$BROKER_DIR/acks" "$BROKER_DIR/responses"
touch "$WORKSPACE/Assets/RPGKingdom/Scenes/PlaytestScene.unity"
printf 'GH-701\n' > "$STATE/locks/unity-editor.lock/owner"
printf '%s\n' "$WORKSPACE" > "$STATE/locks/unity-editor.lock/workspace"

cat > "$FAKE_HOST" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
operation="$1"
shift
scene=""
camera=""
width=""
height=""
project=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) project="$2"; shift 2 ;;
    --scene) scene="$2"; shift 2 ;;
    --camera) camera="$2"; shift 2 ;;
    --width) width="$2"; shift 2 ;;
    --height) height="$2"; shift 2 ;;
    --filter) shift 2 ;;
    *) exit 64 ;;
  esac
done
if [[ "$operation" == "health" ]]; then
  echo '{"status":"ready"}'
  exit 0
fi
[[ "$operation" == "capture" ]] || exit 64
[[ "$scene" == "Assets/RPGKingdom/Scenes/PlaytestScene.unity" ]] || exit 65
[[ "$camera" == "World/Cameras/Review Camera" ]] || exit 66
[[ "$width" == "1600" && "$height" == "900" ]] || exit 67
printf '{"result":"Captured","scene":"%s","cameraPath":"%s","width":%s,"height":%s,"image":"scene.png","artifactPath":"%s/Logs/SymphonyUnity/fake"}\n' \
  "$scene" "$camera" "$width" "$height" "$project"
EOF
chmod +x "$FAKE_HOST"

python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$FAKE_HOST" \
  --poll-ms 25 \
  --command-timeout-seconds 5 \
  --stall-seconds 3 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 80); do
  if [[ -f "$STATE/unity-broker/status.json" ]] && jq -e '.state == "ready"' "$STATE/unity-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done

COMMON_ENV=(
  RPGK_SYMPHONY_WORKSPACE_ROOT="$WORKSPACES"
  RPGK_SUPERVISOR_STATE_ROOT="$STATE"
  RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS=2
  RPGK_UNITY_BROKER_TIMEOUT_SECONDS=6
)

output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" capture \
  --project "$WORKSPACE" \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity \
  --camera 'World/Cameras/Review Camera' \
  --width 1600 \
  --height 900)"
grep -Fq '"result":"Captured"' <<<"$output"
grep -Fq '"width":1600' <<<"$output"

request="$(find "$BROKER_DIR/history/requests" -type f -name '*.json' | head -n 1)"
jq -e '.operation == "capture" and .scenePath == "Assets/RPGKingdom/Scenes/PlaytestScene.unity" and .cameraPath == "World/Cameras/Review Camera" and .captureWidth == 1600 and .captureHeight == 900' "$request" >/dev/null

set +e
missing_scene="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" capture --project "$WORKSPACE" 2>&1)"
missing_status=$?
bad_scene="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" capture --project "$WORKSPACE" --scene ../Other.unity 2>&1)"
bad_scene_status=$?
bad_size="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/unity-runner.sh" capture --project "$WORKSPACE" --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity --width 100 2>&1)"
bad_size_status=$?
set -e

[[ "$missing_status" -eq 64 ]]
[[ "$bad_scene_status" -eq 64 ]]
[[ "$bad_size_status" -eq 64 ]]
grep -Fq 'capture requires --scene' <<<"$missing_scene"
grep -Fq 'Assets/*.unity' <<<"$bad_scene"
grep -Fq 'outside the supported bounds' <<<"$bad_size"

if grep -Eq 'powershell\.exe|wslpath' "$ROOT/scripts/unity-runner.sh"; then
  echo "unity-visual-capture-policy-test: worker client must not cross the WSL/Windows boundary" >&2
  exit 1
fi
grep -Fq 'run-unity-capture.ps1' "$ROOT/scripts/unity-runner-host.sh"
grep -Fq 'SupervisorVisualCapture.Capture' "$ROOT/scripts/windows/run-unity-capture.ps1"
grep -Fq 'RenderTexture' "$ROOT/scripts/windows/run-unity-capture.ps1"
if grep -Fq '\\${env:' "$ROOT/scripts/windows/run-unity-capture.ps1"; then
  echo "unity-visual-capture-policy-test: PowerShell environment references must not be backslash-escaped" >&2
  exit 1
fi
bash -n "$ROOT/scripts/unity-runner.sh"
bash -n "$ROOT/scripts/unity-runner-host.sh"
python3 -m py_compile "$ROOT/scripts/unity-host-broker.py"

echo "unity-visual-capture-policy-test: PASS"
