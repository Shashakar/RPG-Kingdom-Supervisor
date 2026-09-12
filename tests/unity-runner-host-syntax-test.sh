#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PS_SCRIPT="$ROOT/scripts/windows/run-unity-tests.ps1"

if ! grep -Fq 'Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -PassThru' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: Unity launch must return a process handle for progress/ownership tracking" >&2
  exit 1
fi

if grep -Fq 'Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -Wait -PassThru' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: Unity launch must not block before progress/cancellation polling can run" >&2
  exit 1
fi

if ! grep -Fq 'while (-not $unityProcess.HasExited)' "$PS_SCRIPT" || \
   ! grep -Fq '$unityProcess.Refresh()' "$PS_SCRIPT" || \
   ! grep -Fq '$unityProcess.WaitForExit()' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: host runner must explicitly poll and reap the launched Unity process" >&2
  exit 1
fi

if ! grep -Fq 'Stop-Process -Id $unityProcess.Id -Force' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: stall recovery must target only the request-owned Unity PID" >&2
  exit 1
fi

if ! grep -Fq 'Write-ProgressState -Phase "unity_running" -UnityPid $unityProcess.Id' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: request-owned Unity PID must be published through progress state" >&2
  exit 1
fi

if ! grep -Fq 'Get-Process -Name "Unity"' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: host health must reject existing Unity.exe processes" >&2
  exit 1
fi

if ! grep -Fq 'resource:unity-editor requires exclusive host access' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: busy-host error must explain exclusive Unity ownership" >&2
  exit 1
fi

if ! grep -Fq 'Unity\Editor\Editor.log' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: early startup failures must preserve the global Editor.log when available" >&2
  exit 1
fi

if ! grep -Fq 'Get-Content -LiteralPath $defaultEditorLog -Tail 4000' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: fallback Editor.log evidence must stay bounded for Codex context" >&2
  exit 1
fi

if ! grep -Fq 'New-Object System.Text.UTF8Encoding($false)' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: summary.json must be UTF-8 without a BOM" >&2
  exit 1
fi

if ! command -v powershell.exe >/dev/null 2>&1 || ! command -v wslpath >/dev/null 2>&1; then
  echo "unity-runner-host-syntax-test: SKIP (Windows PowerShell bridge unavailable)"
  exit 0
fi

script_windows="$(wslpath -w "$PS_SCRIPT")"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
  "\$tokens = \$null; \$errors = \$null; [System.Management.Automation.Language.Parser]::ParseFile('$script_windows', [ref]\$tokens, [ref]\$errors) | Out-Null; if (\$errors.Count -gt 0) { \$errors | ForEach-Object { [Console]::Error.WriteLine(\$_.Message) }; exit 1 }" \
  >/dev/null

echo "unity-runner-host-syntax-test: PASS"
