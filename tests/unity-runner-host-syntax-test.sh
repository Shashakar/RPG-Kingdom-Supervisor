#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v powershell.exe >/dev/null 2>&1 || ! command -v wslpath >/dev/null 2>&1; then
  echo "unity-runner-host-syntax-test: SKIP (Windows PowerShell bridge unavailable)"
  exit 0
fi

script_windows="$(wslpath -w "$ROOT/scripts/windows/run-unity-tests.ps1")"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
  "\$tokens = \$null; \$errors = \$null; [System.Management.Automation.Language.Parser]::ParseFile('$script_windows', [ref]\$tokens, [ref]\$errors) | Out-Null; if (\$errors.Count -gt 0) { \$errors | ForEach-Object { [Console]::Error.WriteLine(\$_.Message) }; exit 1 }" \
  >/dev/null

echo "unity-runner-host-syntax-test: PASS"
