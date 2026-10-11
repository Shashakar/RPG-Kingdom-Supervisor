#!/usr/bin/env bash
set -euo pipefail

# Operator-only end-to-end smoke: host broker -> CLI -> running Unity Pipeline.
# Uses a synthetic issue/workspace and temporary state. No Codex dispatch,
# scene edit, stage copyback, or persistent Editor lifecycle action.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE=""
SCENE="Assets/RPGKingdom/Scenes/PlaytestScene.unity"
usage() {
  echo "Usage: RPGK_UNITY_CLI_EXE=/mnt/c/.../unity.exe bash scripts/unity-cli-broker-smoke.sh --session-windows 'C:\...\pilot-<id>' [--scene Assets/.../*.unity]" >&2
}
while (($#)); do
  case "$1" in
    --session-windows) [[ $# -ge 2 ]] || { usage; exit 64; }; STAGE="$2"; shift 2 ;;
    --scene) [[ $# -ge 2 ]] || { usage; exit 64; }; SCENE="$2"; shift 2 ;;
    *) usage; exit 64 ;;
  esac
done
[[ "$STAGE" =~ ^[A-Za-z]:\\ && -n "$SCENE" ]] || { usage; exit 64; }
# Never compete with an already-owned production Supervisor Unity lease.
REAL_STATE="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
if [[ -d "$REAL_STATE/locks/unity-editor.lock" ]]; then
  echo "Real Supervisor owns the Unity resource; do not run pilot smoke concurrently" >&2
  exit 82
fi
[[ -n "${RPGK_UNITY_CLI_EXE:-}" && -f "$RPGK_UNITY_CLI_EXE" ]] || {
  echo "Set RPGK_UNITY_CLI_EXE to the WSL-accessible Windows unity.exe" >&2; exit 64;
}
for bin in python3 git jq wslpath sha256sum; do
  command -v "$bin" >/dev/null || { echo "$bin is required" >&2; exit 80; }
done

TEMP="$(mktemp -d)"
BROKER_PID=""
cleanup() {
  if [[ -n "$BROKER_PID" ]]; then
    kill "$BROKER_PID" 2>/dev/null || true
    wait "$BROKER_PID" 2>/dev/null || true
  fi
  rm -rf -- "$TEMP"
}
trap cleanup EXIT INT TERM

export RPGK_SUPERVISOR_STATE_ROOT="$TEMP/state"
export RPGK_SYMPHONY_WORKSPACE_ROOT="$TEMP/workspaces"
export RPGK_UNITY_CLI_READONLY_ENABLED=1
export RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS=5
export RPGK_UNITY_BROKER_TIMEOUT_SECONDS=120

ISSUE="GH-999999"
WORKSPACE="$RPGK_SYMPHONY_WORKSPACE_ROOT/$ISSUE"
mkdir -p "$WORKSPACE" "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock"
git -C "$WORKSPACE" init -q
git -C "$WORKSPACE" -c user.name="Supervisor Smoke" \
  -c user.email="smoke@localhost" commit --allow-empty -qm "Synthetic read-only broker smoke"
printf '%s\n' "$ISSUE" > "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock/owner"
printf '%s\n' "$WORKSPACE" > "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock/workspace"

python3 "$ROOT/scripts/unity-cli-readonly.py" grant \
  --workspace "$WORKSPACE" --state-root "$RPGK_SUPERVISOR_STATE_ROOT" \
  --session-windows "$STAGE" --scene "$SCENE" >/dev/null

STAGE_WSL="$(wslpath -u "$STAGE")"
BEFORE="$(sha256sum "$STAGE_WSL/$SCENE" | cut -d' ' -f1)"
python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$RPGK_SYMPHONY_WORKSPACE_ROOT" \
  --state-root "$RPGK_SUPERVISOR_STATE_ROOT" \
  --host-runner "$ROOT/scripts/unity-runner-host.sh" \
  --poll-ms 50 --command-timeout-seconds 150 \
  >"$TEMP/broker.log" 2>&1 &
BROKER_PID=$!

ready=0
for _ in $(seq 1 100); do
  if [[ -f "$RPGK_SUPERVISOR_STATE_ROOT/unity-broker/status.json" ]] &&
     jq -e '.state == "ready"' "$RPGK_SUPERVISOR_STATE_ROOT/unity-broker/status.json" >/dev/null; then
    ready=1; break
  fi
  kill -0 "$BROKER_PID" 2>/dev/null || break
  sleep 0.1
done
if [[ "$ready" != 1 ]]; then
  echo "Temporary Unity broker did not become ready:" >&2
  tail -n 20 "$TEMP/broker.log" >&2 || true
  exit 86
fi

result="$(bash "$ROOT/scripts/unity-runner.sh" editor-scene-summary \
  --project "$WORKSPACE" --scene "$SCENE")"
echo "$result" | jq -e 'select(.success == true and .operation == "editor-scene-summary" and .sceneDirty == false)' >/dev/null
AFTER="$(sha256sum "$STAGE_WSL/$SCENE" | cut -d' ' -f1)"
[[ "$BEFORE" == "$AFTER" ]] || {
  echo "Stage scene hash changed during read-only smoke: do not approve the lane" >&2; exit 90;
}
printf '%s\n' "$result" | jq -c '{
  success, operation, sceneName, scenePath, sceneDirty, rootCount, gameObjectCount,
  terrainCount, terrainPaths, sceneFileUnchanged: true
}'
echo "PASS: synthetic issue -> existing Supervisor broker -> live Unity Pipeline (read only)" >&2
