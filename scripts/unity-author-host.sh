#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=unity-runner-policy.sh
source "$ROOT/scripts/unity-runner-policy.sh"

POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-powershell.exe}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"

usage() {
  echo "Usage: unity-author-host.sh --project PATH --request PATH" >&2
}

project=""
request=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) [[ $# -ge 2 ]] || exit 64; project="$2"; shift 2 ;;
    --request) [[ $# -ge 2 ]] || exit 64; request="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 64 ;;
  esac
done

[[ -n "$project" && -n "$request" ]] || { usage; exit 64; }
command -v "$POWERSHELL_EXE" >/dev/null 2>&1 || { echo "RPG Kingdom Unity authoring: '$POWERSHELL_EXE' is unavailable from WSL" >&2; exit 80; }
command -v wslpath >/dev/null 2>&1 || { echo "RPG Kingdom Unity authoring: wslpath is required" >&2; exit 80; }

project="$(cd "$project" && pwd)"
request="$(cd "$(dirname "$request")" && pwd)/$(basename "$request")"
workspace_name="$(basename "$project")"
[[ "$workspace_name" =~ ^GH-([0-9]+)$ ]] || { echo "RPG Kingdom Unity authoring: project is not a GH issue workspace" >&2; exit 81; }
issue_identifier="GH-${BASH_REMATCH[1]}"
expected_request_dir="$project/Logs/SymphonyUnity/.author-broker/requests"
[[ "$(dirname "$request")" == "$expected_request_dir" ]] || { echo "RPG Kingdom Unity authoring: request is outside the workspace authoring broker" >&2; exit 81; }

[[ -f "$LOCK_DIR/owner" && "$(cat "$LOCK_DIR/owner")" == "$issue_identifier" ]] || { echo "RPG Kingdom Unity authoring: unity-editor is not locked for $issue_identifier" >&2; exit 82; }
[[ -f "$LOCK_DIR/workspace" && "$(cd "$(cat "$LOCK_DIR/workspace")" && pwd)" == "$project" ]] || { echo "RPG Kingdom Unity authoring: Unity lock workspace mismatch" >&2; exit 82; }

authorization="$STATE_ROOT/authoring/$issue_identifier.json"
if ! jq -e --arg issue "$issue_identifier" --arg workspace "$project" '
  .protocolVersion == 1 and .issue == $issue and .workspace == $workspace and .tier == "mechanical"
' "$authorization" >/dev/null 2>&1; then
  echo "RPG Kingdom Unity authoring: current dispatch lacks Tier-1 mechanical authoring authorization" >&2
  exit 83
fi

unity_version="$(rpgk_project_unity_version "$project")"
unity_editor_windows="${RPGK_UNITY_EDITOR_WINDOWS:-$(rpgk_default_unity_editor_windows "$unity_version")}" 
source_project_windows="$(wslpath -w "$project")"
request_windows="$(wslpath -w "$request")"
author_runner_windows="$(wslpath -w "$ROOT/scripts/windows/run-unity-authoring.ps1")"
request_id="$(jq -r '.requestId' "$request")"

args=(
  -NoProfile
  -ExecutionPolicy Bypass
  -File "$author_runner_windows"
  -SourceProjectPath "$source_project_windows"
  -UnityVersion "$unity_version"
  -UnityPath "$unity_editor_windows"
  -RequestPath "$request_windows"
  -RequestId "$request_id"
)
if [[ -n "${RPGK_UNITY_STAGE_ROOT_WINDOWS:-}" ]]; then
  args+=( -StageRoot "$RPGK_UNITY_STAGE_ROOT_WINDOWS" )
fi

"$POWERSHELL_EXE" "${args[@]}"
