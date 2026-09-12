#!/usr/bin/env bash
set -euo pipefail

# Host-only direct WSL -> Windows adapter. Workers must call unity-runner.sh,
# which submits a typed request to the host-owned Unity broker.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=unity-runner-policy.sh
source "$ROOT/scripts/unity-runner-policy.sh"

POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-powershell.exe}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"
REQUEST_ID="${RPGK_UNITY_REQUEST_ID:-}"
PROGRESS_FILE="${RPGK_UNITY_PROGRESS_FILE:-}"
CANCEL_FILE="${RPGK_UNITY_CANCEL_FILE:-}"

usage() {
  cat >&2 <<'EOF'
Usage:
  unity-runner-host.sh health [--project PATH]
  unity-runner-host.sh editmode [--project PATH] [--filter FILTER]
  unity-runner-host.sh playmode [--project PATH] [--filter FILTER]

Environment overrides:
  RPGK_POWERSHELL_EXE
  RPGK_UNITY_EDITOR_WINDOWS
  RPGK_UNITY_STAGE_ROOT_WINDOWS
  RPGK_SUPERVISOR_STATE_ROOT

Broker-owned request metadata (set by unity-host-broker.py):
  RPGK_UNITY_REQUEST_ID
  RPGK_UNITY_PROGRESS_FILE
  RPGK_UNITY_CANCEL_FILE
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 64
fi

command="$1"
shift
project="$PWD"
test_filter=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { echo "Missing value for --project" >&2; exit 64; }
      project="$2"
      shift 2
      ;;
    --filter)
      [[ $# -ge 2 ]] || { echo "Missing value for --filter" >&2; exit 64; }
      test_filter="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown Unity runner argument: $1" >&2
      usage
      exit 64
      ;;
  esac
done

case "$command" in
  health|editmode|playmode) ;;
  *)
    echo "Unknown Unity runner command: $command" >&2
    usage
    exit 64
    ;;
esac

if ! command -v "$POWERSHELL_EXE" >/dev/null 2>&1; then
  echo "RPG Kingdom Unity runner: '$POWERSHELL_EXE' is unavailable from WSL" >&2
  exit 80
fi

if ! command -v wslpath >/dev/null 2>&1; then
  echo "RPG Kingdom Unity runner: wslpath is unavailable; the Windows bridge requires WSL" >&2
  exit 80
fi

project="$(cd "$project" && pwd)"
unity_version="$(rpgk_project_unity_version "$project")"
unity_editor_windows="${RPGK_UNITY_EDITOR_WINDOWS:-$(rpgk_default_unity_editor_windows "$unity_version")}" 
source_project_windows="$(wslpath -w "$project")"
runner_windows="$(wslpath -w "$ROOT/scripts/windows/run-unity-tests.ps1")"

powershell_args=(
  -NoProfile
  -ExecutionPolicy Bypass
  -File "$runner_windows"
  -SourceProjectPath "$source_project_windows"
  -UnityVersion "$unity_version"
  -UnityPath "$unity_editor_windows"
)

if [[ -n "${RPGK_UNITY_STAGE_ROOT_WINDOWS:-}" ]]; then
  powershell_args+=( -StageRoot "$RPGK_UNITY_STAGE_ROOT_WINDOWS" )
fi

if [[ -n "$REQUEST_ID" || -n "$PROGRESS_FILE" || -n "$CANCEL_FILE" ]]; then
  if [[ -z "$REQUEST_ID" || -z "$PROGRESS_FILE" || -z "$CANCEL_FILE" ]]; then
    echo "RPG Kingdom Unity runner: broker progress metadata is incomplete" >&2
    exit 86
  fi
  mkdir -p "$(dirname "$PROGRESS_FILE")" "$(dirname "$CANCEL_FILE")"
  progress_windows="$(wslpath -w "$PROGRESS_FILE")"
  cancel_windows="$(wslpath -w "$CANCEL_FILE")"
  powershell_args+=(
    -RequestId "$REQUEST_ID"
    -ProgressPath "$progress_windows"
    -CancelPath "$cancel_windows"
  )
fi

if [[ "$command" == "health" ]]; then
  "$POWERSHELL_EXE" "${powershell_args[@]}" -HealthOnly
  exit $?
fi

workspace_name="$(basename "$project")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom Unity runner: project '$project' is not a Symphony GH issue workspace" >&2
  exit 81
fi
issue_identifier="GH-${BASH_REMATCH[1]}"

if [[ ! -d "$LOCK_DIR" || ! -f "$LOCK_DIR/owner" ]]; then
  echo "RPG Kingdom Unity runner: unity-editor is not locked for $issue_identifier" >&2
  exit 82
fi

lock_owner="$(cat "$LOCK_DIR/owner")"
if [[ "$lock_owner" != "$issue_identifier" ]]; then
  echo "RPG Kingdom Unity runner: unity-editor belongs to '$lock_owner', not '$issue_identifier'" >&2
  exit 83
fi

platform="$(rpgk_normalize_test_platform "$command")"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-${command}-$$"

powershell_args+=(
  -TestPlatform "$platform"
  -RunId "$run_id"
)
if [[ -n "$test_filter" ]]; then
  powershell_args+=( -TestFilter "$test_filter" )
fi

set +e
"$POWERSHELL_EXE" "${powershell_args[@]}"
status=$?
set -e

artifact_dir="$project/Logs/SymphonyUnity/$run_id"
echo "RPG Kingdom Unity runner: artifacts -> $artifact_dir"

if [[ -f "$artifact_dir/summary.json" ]]; then
  echo "RPG Kingdom Unity runner: summary -> $(tr -d '\r\n' < "$artifact_dir/summary.json")"
fi

exit "$status"
