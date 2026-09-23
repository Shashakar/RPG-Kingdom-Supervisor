#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=unity-runner-policy.sh
source "$ROOT/scripts/unity-runner-policy.sh"

POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-powershell.exe}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
LOCK_DIR="$STATE_ROOT/locks/unity-editor.lock"
PROVENANCE_DIR="$STATE_ROOT/new-scene-provenance"

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
command -v jq >/dev/null 2>&1 || { echo "RPG Kingdom Unity authoring: jq is required" >&2; exit 80; }
command -v git >/dev/null 2>&1 || { echo "RPG Kingdom Unity authoring: git is required" >&2; exit 80; }

project="$(cd "$project" && pwd)"
request="$(cd "$(dirname "$request")" && pwd)/$(basename "$request")"
workspace_name="$(basename "$project")"
[[ "$workspace_name" =~ ^GH-([0-9]+)$ ]] || { echo "RPG Kingdom Unity authoring: project is not a GH issue workspace" >&2; exit 81; }
issue_identifier="GH-${BASH_REMATCH[1]}"
expected_request_dir="$project/Logs/SymphonyUnity/.author-broker/requests"
[[ "$(dirname "$request")" == "$expected_request_dir" ]] || { echo "RPG Kingdom Unity authoring: request is outside the workspace authoring broker" >&2; exit 81; }

[[ -f "$LOCK_DIR/owner" && "$(cat "$LOCK_DIR/owner")" == "$issue_identifier" ]] || { echo "RPG Kingdom Unity authoring: unity-editor is not locked for $issue_identifier" >&2; exit 82; }
[[ -f "$LOCK_DIR/workspace" && "$(cd "$(cat "$LOCK_DIR/workspace")" && pwd)" == "$project" ]] || { echo "RPG Kingdom Unity authoring: Unity lock workspace mismatch" >&2; exit 82; }

requested_tier="$(jq -r '.authoring.tier // empty' "$request")"
case "$requested_tier" in
  mechanical|mechanical-structural|new-scene-composition) ;;
  *) echo "RPG Kingdom Unity authoring: unsupported requested tier '$requested_tier'" >&2; exit 64 ;;
esac

authorization="$STATE_ROOT/authoring/$issue_identifier.json"
if ! jq -e --arg issue "$issue_identifier" --arg workspace "$project" --arg tier "$requested_tier" '
  .protocolVersion == 1 and .issue == $issue and .workspace == $workspace and .tier == $tier
' "$authorization" >/dev/null 2>&1; then
  echo "RPG Kingdom Unity authoring: current dispatch is not authorized for requested tier '$requested_tier'" >&2
  exit 83
fi

composition_mode=""
authorized_source_scene=""
target_scene="$(jq -r '.authoring.scene // empty' "$request")"
if [[ "$requested_tier" == "new-scene-composition" ]]; then
  [[ "$target_scene" == Assets/*.unity && "$target_scene" != *".."* && "$target_scene" != *'\'* ]] || {
    echo "RPG Kingdom Unity authoring: invalid new-scene target '$target_scene'" >&2
    exit 64
  }

  if ! git -C "$project" rev-parse --verify origin/main >/dev/null 2>&1; then
    echo "RPG Kingdom Unity authoring: origin/main is unavailable; cannot prove target provenance" >&2
    exit 84
  fi
  if git -C "$project" cat-file -e "origin/main:$target_scene" 2>/dev/null || git -C "$project" cat-file -e "origin/main:$target_scene.meta" 2>/dev/null; then
    echo "RPG Kingdom Unity authoring: target '$target_scene' already exists on origin/main and is not eligible for new-scene composition" >&2
    exit 83
  fi

  mkdir -p "$PROVENANCE_DIR"
  provenance="$PROVENANCE_DIR/$issue_identifier.json"
  request_source_scene="$(jq -r '.authoring.sourceScene // empty' "$request")"
  branch_name="$(git -C "$project" branch --show-current)"
  [[ -n "$branch_name" ]] || { echo "RPG Kingdom Unity authoring: workspace is not on a named branch" >&2; exit 84; }

  if [[ -n "$request_source_scene" ]]; then
    composition_mode="initial"
    authorized_source_scene="$request_source_scene"
    [[ "$authorized_source_scene" == Assets/*.unity && "$authorized_source_scene" != *".."* && "$authorized_source_scene" != *'\'* ]] || {
      echo "RPG Kingdom Unity authoring: invalid source scene '$authorized_source_scene'" >&2
      exit 64
    }
    [[ "$authorized_source_scene" != "$target_scene" ]] || { echo "RPG Kingdom Unity authoring: source and target scenes must differ" >&2; exit 64; }
    [[ -f "$project/$authorized_source_scene" ]] || { echo "RPG Kingdom Unity authoring: source scene '$authorized_source_scene' does not exist" >&2; exit 82; }
    [[ ! -e "$project/$target_scene" && ! -e "$project/$target_scene.meta" ]] || {
      echo "RPG Kingdom Unity authoring: initial target '$target_scene' already exists in the workspace" >&2
      exit 83
    }
    [[ ! -f "$provenance" ]] || {
      echo "RPG Kingdom Unity authoring: provenance already exists for $issue_identifier; use an iterative request" >&2
      exit 83
    }
  else
    composition_mode="iterative"
    [[ -f "$provenance" ]] || { echo "RPG Kingdom Unity authoring: no new-scene provenance exists for iterative request" >&2; exit 83; }
    if ! jq -e --arg issue "$issue_identifier" --arg workspace "$project" --arg target "$target_scene" --arg branch "$branch_name" '
      .protocolVersion == 1 and .issue == $issue and .workspace == $workspace and .targetScene == $target and .branch == $branch
    ' "$provenance" >/dev/null; then
      echo "RPG Kingdom Unity authoring: iterative request does not match host-owned scene provenance" >&2
      exit 83
    fi
    authorized_source_scene="$(jq -r '.sourceScene' "$provenance")"
    [[ -f "$project/$authorized_source_scene" ]] || { echo "RPG Kingdom Unity authoring: recorded source scene '$authorized_source_scene' no longer exists" >&2; exit 82; }
    [[ -f "$project/$target_scene" && -f "$project/$target_scene.meta" ]] || {
      echo "RPG Kingdom Unity authoring: recorded target scene or meta is missing from the workspace" >&2
      exit 82
    }
  fi
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
if [[ "$requested_tier" == "new-scene-composition" ]]; then
  args+=( -AuthorizedSourceScene "$authorized_source_scene" -CompositionMode "$composition_mode" )
fi

set +e
output="$("$POWERSHELL_EXE" "${args[@]}")"
status=$?
set -e
[[ -z "$output" ]] || printf '%s\n' "$output"
(( status == 0 )) || exit "$status"

if [[ "$requested_tier" == "new-scene-composition" && "$composition_mode" == "initial" ]]; then
  provenance="$PROVENANCE_DIR/$issue_identifier.json"
  temp="$provenance.tmp.$$"
  jq -cn \
    --arg issue "$issue_identifier" \
    --arg workspace "$project" \
    --arg source "$authorized_source_scene" \
    --arg target "$target_scene" \
    --arg branch "$(git -C "$project" branch --show-current)" \
    --arg createdAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{protocolVersion:1,issue:$issue,workspace:$workspace,sourceScene:$source,targetScene:$target,branch:$branch,createdAt:$createdAt}' \
    > "$temp"
  mv "$temp" "$provenance"
fi
