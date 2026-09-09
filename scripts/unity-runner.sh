#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=unity-runner-policy.sh
source "$ROOT/scripts/unity-runner-policy.sh"

POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-powershell.exe}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"

usage() {
  cat >&2 <<'EOF'
Usage:
  unity-runner.sh health [--project PATH]
  unity-runner.sh editmode [--project PATH] [--filter FILTER]
  unity-runner.sh playmode [--project PATH] [--filter FILTER]

Environment overrides:
  RPGK_POWERSHELL_EXE
  RPGK_UNITY_EDITOR_WINDOWS
  RPGK_UNITY_STAGE_ROOT_WINDOWS
  RPGK_SUPERVISOR_STATE_ROOT
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
