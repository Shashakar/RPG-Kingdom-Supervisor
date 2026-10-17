#!/usr/bin/env bash
set -euo pipefail

# Operator-only, read-only diagnostics. Do NOT add to Codex worker tools.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-powershell.exe}"
UNITY_CLI_WINDOWS="${RPGK_UNITY_CLI_WINDOWS:-}"
PROJECT_WINDOWS=""

usage() {
  cat <<'EOF'
Usage: scripts/unity-cli-pilot.sh [--project-windows 'C:\path\to\NTFS\UnityStage'] [--cli-windows 'C:\path\to\unity.exe']

Read-only Windows probe for official Unity CLI/Pipeline.
No parameters: CLI version and Pipeline discovery only.
With --project-windows: list Editor commands for that exact project.
The script does not install, configure, write, or run Unity Editor code.
Run from the operator WSL shell, never from a Codex worker.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-windows)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Missing --project-windows value" >&2; exit 64; }
      PROJECT_WINDOWS="$2"
      shift 2
      ;;
    --cli-windows)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Missing --cli-windows value" >&2; exit 64; }
      UNITY_CLI_WINDOWS="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 64 ;;
  esac
done
if [[ -n "$PROJECT_WINDOWS" && ! "$PROJECT_WINDOWS" =~ ^[A-Za-z]:[\\/] ]]; then
  echo "--project-windows must be a Windows drive path, not a WSL UNC path" >&2
  exit 64
fi
if ! command -v "$POWERSHELL_EXE" >/dev/null 2>&1; then
  echo "Windows PowerShell bridge unavailable: $POWERSHELL_EXE" >&2
  exit 80
fi
if ! command -v wslpath >/dev/null 2>&1; then
  echo "wslpath unavailable; run this diagnostic from WSL" >&2
  exit 80
fi
probe_windows="$(wslpath -w "$ROOT/scripts/windows/probe-unity-cli.ps1")"
arguments=(-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$probe_windows")
if [[ -n "$UNITY_CLI_WINDOWS" ]]; then
  arguments+=(-CliPath "$UNITY_CLI_WINDOWS")
fi
if [[ -n "$PROJECT_WINDOWS" ]]; then
  arguments+=(-ProjectPath "$PROJECT_WINDOWS")
fi
"$POWERSHELL_EXE" "${arguments[@]}"
