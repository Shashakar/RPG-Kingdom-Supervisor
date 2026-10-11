# Syntax-only parser for Windows-specific Stage C2c scripts.
# Runs under PowerShell 7 in CI when available; NEVER executes task changes.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$files = @(
  (Join-Path $root "scripts/windows/install-isolated-unity-host.ps1"),
  (Join-Path $root "scripts/windows/run-isolated-unity-host.ps1")
)
foreach ($file in $files) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
  if (@($errors).Count -gt 0) {
    foreach ($e in $errors) { Write-Error ("PowerShell parse error in " + $file + ": " + $e.Message) }
    throw "Windows isolated-host PowerShell script contains parse errors"
  }
}
Write-Output "unity-isolated-powershell-parse-test: PASS"
