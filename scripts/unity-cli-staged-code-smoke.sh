#!/usr/bin/env bash
set -euo pipefail
# Live same-account bridge smoke. Creates one new scene in an already disposable
# Unity stage using synthetic GH issue; never copies to game source.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE=""
while (($#)); do
  case "$1" in
    --session-windows) [[ $# -ge 2 ]] || exit 64; STAGE="$2"; shift 2 ;;
    *) echo "Usage: bash scripts/unity-cli-staged-code-smoke.sh --session-windows 'C:\...\pilot-...'" >&2; exit 64 ;;
  esac
done
[[ "$STAGE" =~ ^[A-Za-z]:\\ ]] || { echo "Windows stage path required" >&2; exit 64; }
[[ -f "${RPGK_UNITY_CLI_EXE:-}" ]] || {
  echo "Set RPGK_UNITY_CLI_EXE to the existing Windows unity.exe as a WSL path" >&2; exit 64;
}
for bin in git jq python3 sha256sum wslpath; do
  command -v "$bin" >/dev/null || { echo "$bin required" >&2; exit 80; }
done
STATE="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
if [[ -d "$STATE/locks/unity-editor.lock" ]]; then
  echo "Real Supervisor holds Unity; refuse concurrent trial" >&2; exit 82
fi
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
export RPGK_UNITY_STAGED_CODE_ENABLED=1
export RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS=5
export RPGK_UNITY_BROKER_TIMEOUT_SECONDS=180
WS="$RPGK_SYMPHONY_WORKSPACE_ROOT/GH-999999"
SRC="Assets/RPGKingdom/Scenes/PlaytestScene.unity"
DEST="Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCodeSmoke.unity"
STAGE_WSL="$(wslpath -u "$STAGE")"
[[ -f "$STAGE_WSL/$SRC" ]] || { echo "Source scene missing in stage" >&2; exit 82; }
[[ ! -e "$STAGE_WSL/$DEST" ]] || { echo "Smoke scene already exists" >&2; exit 82; }

mkdir -p "$WS/AgentScripts" "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock"
cat > "$WS/AgentScripts/Smoke.cs" <<'CSHARP'
var original = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
if (original.path != "Assets/RPGKingdom/Scenes/PlaytestScene.unity" || original.isDirty)
    throw new System.InvalidOperationException("PlaytestScene must be clean and active");
var scenePath = "Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCodeSmoke.unity";
var fullPath = System.IO.Path.Combine(UnityEngine.Application.dataPath,
    "RPGKingdom/Generated/AgentBenchmarks/SupervisorCodeSmoke.unity");
if (System.IO.File.Exists(fullPath))
    throw new System.InvalidOperationException("Smoke scene already exists");
System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(fullPath));
var scene = UnityEditor.SceneManagement.EditorSceneManager.NewScene(
    UnityEditor.SceneManagement.NewSceneSetup.EmptyScene,
    UnityEditor.SceneManagement.NewSceneMode.Additive);
try {
    var root = new UnityEngine.GameObject("SupervisorCodeSmokeRoot");
    UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(root, scene);
    var cube = UnityEngine.GameObject.CreatePrimitive(UnityEngine.PrimitiveType.Cube);
    cube.name = "SupervisorCodeSmokeCube";
    UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(cube, scene);
    cube.transform.SetParent(root.transform, false);
    cube.transform.localPosition = new UnityEngine.Vector3(0f, 0.5f, 0f);
    if (!UnityEditor.SceneManagement.EditorSceneManager.SaveScene(scene, scenePath, false))
        throw new System.InvalidOperationException("Saving smoke scene failed");
}
finally {
    UnityEngine.SceneManagement.SceneManager.SetActiveScene(original);
    UnityEditor.SceneManagement.EditorSceneManager.CloseScene(scene, true);
}
return scenePath;
CSHARP
git -C "$WS" init -q
git -C "$WS" add AgentScripts/Smoke.cs
git -C "$WS" -c user.name="Supervisor Smoke" -c user.email="pilot@localhost" \
  commit -qm "Synthetic same-account Editor code smoke"
printf '%s\n' GH-999999 > "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock/owner"
printf '%s\n' "$WS" > "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock/workspace"
ORIGINAL_HASH="$(sha256sum "$STAGE_WSL/$SRC" | cut -d' ' -f1)"

python3 "$ROOT/scripts/unity-cli-staged-code.py" grant \
  --workspace "$WS" --session-windows "$STAGE" \
  --state-root "$RPGK_SUPERVISOR_STATE_ROOT" --scene "$SRC" >/dev/null
python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$RPGK_SYMPHONY_WORKSPACE_ROOT" \
  --state-root "$RPGK_SUPERVISOR_STATE_ROOT" \
  --host-runner "$ROOT/scripts/unity-runner-host.sh" \
  --poll-ms 50 --command-timeout-seconds 180 \
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
  echo "Temporary host broker not ready" >&2
  tail -n 20 "$TEMP/broker.log" >&2 || true
  exit 86
fi
RESULT="$(bash "$ROOT/scripts/unity-runner.sh" editor-staged-code \
  --project "$WS" --scene "$SRC" --script AgentScripts/Smoke.cs)"
printf '%s\n' "$RESULT" | jq -e --arg dest "$DEST" \
  'select(.success == true and .operation == "editor-staged-code" and
  .stageOnly == true and .copyback == false and (.addedAssets | index($dest)))' >/dev/null
[[ -f "$STAGE_WSL/$DEST" ]] || { echo "Smoke scene absent" >&2; exit 90; }
[[ "$ORIGINAL_HASH" == "$(sha256sum "$STAGE_WSL/$SRC" | cut -d' ' -f1)" ]] || {
  echo "Original staged scene changed" >&2; exit 90;
}
printf '%s\n' "$RESULT" | jq -c '{
  success,operation,scriptPath,stageOnly,copyback,assetCounts,addedAssets,
  originalStageSceneUnchanged:true
}'
echo "PASS: synthetic GH issue -> Supervisor broker -> agent C# -> staged scene; no copyback" >&2
