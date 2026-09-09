#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PS_SCRIPT="$ROOT/scripts/windows/run-unity-tests.ps1"

if ! grep -Fq 'Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -Wait -PassThru' "$PS_SCRIPT"; then
  echo "unity-runner-host-syntax-test: Unity launch must use Start-Process -Wait -PassThru" >&2
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
