#!/usr/bin/env bash
set -euo pipefail
# Operator-only live fixed-authoring acceptance. No real Codex worker or game
# workspace is used; no source copyback. Stage is modified under AgentBenchmarks.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE=""
while (($#)); do
  case "$1" in
    --session-windows) [[ $# -ge 2 ]] || exit 64; STAGE="$2"; shift 2 ;;
    *) echo "Usage: bash scripts/unity-cli-stage-benchmark-smoke.sh --session-windows 'C:\...\pilot-...'" >&2; exit 64 ;;
  esac
done
[[ "$STAGE" =~ ^[A-Za-z]:\\ ]] || { echo "Windows stage path required" >&2; exit 64; }
[[ -n "${RPGK_UNITY_CLI_EXE:-}" && -f "${RPGK_UNITY_CLI_EXE:-}" ]] || {
  echo "Set RPGK_UNITY_CLI_EXE to the WSL-accessible Windows unity.exe" >&2; exit 64;
}
for bin in git python3 jq wslpath sha256sum; do
  command -v "$bin" >/dev/null || { echo "Missing $bin" >&2; exit 80; }
done
REAL_STATE="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
if [[ -d "$REAL_STATE/locks/unity-editor.lock" ]]; then
  echo "Real Supervisor owns the Unity resource. Refusing concurrent Editor operation." >&2; exit 82
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
export RPGK_UNITY_CLI_BENCHMARK_ENABLED=1
export RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS=5
export RPGK_UNITY_BROKER_TIMEOUT_SECONDS=160

WORKSPACE="$RPGK_SYMPHONY_WORKSPACE_ROOT/GH-999999"
SCENE="Assets/RPGKingdom/Scenes/PlaytestScene.unity"
DEST="Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity"
STAGE_WSL="$(wslpath -u "$STAGE")"
[[ -f "$STAGE_WSL/$SCENE" ]] || {
  echo "Expected PlaytestScene missing in stage" >&2; exit 82;
}
[[ ! -e "$STAGE_WSL/$DEST" ]] || {
  echo "Benchmark already exists: use a fresh disposable Stage B session" >&2; exit 82;
}

mkdir -p "$WORKSPACE" "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock"
git -C "$WORKSPACE" init -q
git -C "$WORKSPACE" -c user.name="Supervisor Pilot" \
  -c user.email="pilot@localhost" commit --allow-empty -qm "Synthetic fixed-authoring smoke"
printf 'GH-999999\n' > "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock/owner"
printf '%s\n' "$WORKSPACE" > "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock/workspace"

BEFORE="$(sha256sum "$STAGE_WSL/$SCENE" | cut -d' ' -f1)"
python3 "$ROOT/scripts/unity-cli-stage-benchmark.py" grant \
  --workspace "$WORKSPACE" --state-root "$RPGK_SUPERVISOR_STATE_ROOT" \
  --session-windows "$STAGE" --scene "$SCENE" >/dev/null

python3 -u "$ROOT/scripts/unity-host-broker.py" \
  --workspace-root "$RPGK_SYMPHONY_WORKSPACE_ROOT" \
  --state-root "$RPGK_SUPERVISOR_STATE_ROOT" \
  --host-runner "$ROOT/scripts/unity-runner-host.sh" \
  --poll-ms 50 --command-timeout-seconds 200 \
  >"$TEMP/broker.log" 2>&1 &
BROKER_PID=$!

ready=0
for _ in $(seq 1 100); do
  if [[ -f "$RPGK_SUPERVISOR_STATE_ROOT/unity-broker/status.json" ]] &&
     jq -e '.state == "ready"' "$RPGK_SUPERVISOR_STATE_ROOT/unity-broker/status.json" >/dev/null; then
    ready=1
    break
  fi
  kill -0 "$BROKER_PID" 2>/dev/null || break
  sleep 0.1
done
if [[ "$ready" != 1 ]]; then
  echo "Temporary host broker did not become ready" >&2
  tail -n 20 "$TEMP/broker.log" >&2 || true
  exit 86
fi

result="$(bash "$ROOT/scripts/unity-runner.sh" editor-stage-benchmark \
  --project "$WORKSPACE" --scene "$SCENE")"
echo "$result" | jq -e 'select(
  .success == true and .operation == "editor-stage-benchmark"
  and .stageOnly == true and .copyback == false
  and .protectedFilesUnchanged == true)' >/dev/null
AFTER="$(sha256sum "$STAGE_WSL/$SCENE" | cut -d' ' -f1)"
[[ "$BEFORE" == "$AFTER" && -f "$STAGE_WSL/$DEST" ]] || {
  echo "Protected PlaytestScene changed or benchmark output missing" >&2; exit 90;
}
printf '%s\n' "$result" | jq -c '. + {originalSceneHashUnchanged: true}'
echo "PASS: synthetic issue -> Supervisor host broker -> fixed Unity stage scene authoring; no copyback" >&2
