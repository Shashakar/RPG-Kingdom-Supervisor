#!/usr/bin/env bash
set -euo pipefail

# Operator shell only. Never expose this command to Codex workers.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-powershell.exe}"
action=""
source_project=""
session_windows=""
cli_windows=""
editor_windows=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/unity-cli-stage.sh start --source /path/to/RPG-Kingdom
  bash scripts/unity-cli-stage.sh status --session-windows 'C:\...\UnityCliPilots\pilot-...'
  bash scripts/unity-cli-stage.sh discover --session-windows 'C:\...\UnityCliPilots\pilot-...'
  bash scripts/unity-cli-stage.sh install-pipeline --session-windows 'C:\...\UnityCliPilots\pilot-...'
  bash scripts/unity-cli-stage.sh stop --session-windows 'C:\...\UnityCliPilots\pilot-...'

Optional: --cli-windows 'C:\path\to\unity.exe'.
Operator-only disposable NTFS Editor lifecycle. 'install-pipeline' explicitly
modifies the staged project's package manifest; it NEVER writes back to source.
EOF
}
[[ $# -gt 0 ]] || { usage >&2; exit 64; }
action="$1"
shift
case "$action" in
  start|status|discover|install-pipeline|stop) ;;
  -h|--help) usage; exit 0 ;;
  *) echo "Unsupported action: $action" >&2; exit 64 ;;
esac
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      [[ $# -ge 2 && -n "$2" ]] || exit 64
      source_project="$2"
      shift 2
      ;;
    --session-windows)
      [[ $# -ge 2 && -n "$2" ]] || exit 64
      session_windows="$2"
      shift 2
      ;;
    --editor-windows)
      [[ $# -ge 2 && -n "$2" ]] || exit 64
      editor_windows="$2"
      shift 2
      ;;
    --cli-windows)
      [[ $# -ge 2 && -n "$2" ]] || exit 64
      cli_windows="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; exit 64 ;;
  esac
done
if [[ "$action" == "start" ]]; then
  [[ -n "$source_project" && -z "$session_windows" ]] || {
    echo "start requires --source and does not accept --session-windows" >&2; exit 64;
  }
  [[ -d "$source_project/Assets" && -d "$source_project/Packages" && -d "$source_project/ProjectSettings" ]] || {
    echo "--source must be an accessible Unity project directory" >&2; exit 64;
  }
else
  [[ -z "$source_project" && "$session_windows" =~ ^[A-Za-z]:[\\/] ]] || {
    echo "$action requires a Windows drive --session-windows and no --source" >&2; exit 64;
  }
fi
# Do not overlap the operator pilot with a Supervisor-owned Unity lease.
# This is a conservative preflight, not a substitute for the Stage C broker.
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
if [[ -d "$STATE_ROOT/locks/unity-editor.lock" ]]; then
  echo "Supervisor currently holds the unity-editor lock; stop or finish that job first" >&2
  exit 82
fi

if [[ -n "$editor_windows" && ( "$action" != "start" || ! "$editor_windows" =~ ^[A-Za-z]:[\\/] ) ]]; then
  echo "--editor-windows is only valid for start and requires a Windows executable" >&2
  exit 64
fi
if [[ -n "$cli_windows" && ! "$cli_windows" =~ ^[A-Za-z]:[\\/] ]]; then
  echo "--cli-windows must identify a Windows executable" >&2
  exit 64
fi
command -v "$POWERSHELL_EXE" >/dev/null 2>&1 || { echo "Windows PowerShell is unavailable" >&2; exit 80; }
command -v wslpath >/dev/null 2>&1 || { echo "wslpath is unavailable" >&2; exit 80; }
script_windows="$(wslpath -w "$ROOT/scripts/windows/run-unity-cli-stage.ps1")"
args=(-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$script_windows" -Action "$action")
if [[ "$action" == "start" ]]; then
  source_windows="$(wslpath -w "$(cd "$source_project" && pwd)")"
  args+=(-SourceProjectPath "$source_windows")
else
  args+=(-SessionPath "$session_windows")
fi
if [[ -n "$cli_windows" ]]; then
  args+=(-CliPath "$cli_windows")
fi
if [[ -n "$editor_windows" ]]; then
  args+=(-EditorPath "$editor_windows")
fi
"$POWERSHELL_EXE" "${args[@]}"
