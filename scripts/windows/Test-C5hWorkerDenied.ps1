#Requires -Version 5.1
# C5h unprivileged-worker negative test. Run in the SAME normal Windows
# identity and UAC elevation level as Unity.exe. Never run as administrator.
# This is host acceptance evidence, not an authorization grant.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$service = 'RPGKSnapshotGuardian'
$private = Join-Path $env:ProgramData 'RPGKingdomSupervisor\C5h\private'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run as the unelevated Unity worker account, not an administrator token'
}
$unprivileged = $identity.User.Value
$readDenied = $false
try {
    Get-ChildItem -LiteralPath $private -Force -ErrorAction Stop | Out-Null
} catch [System.UnauthorizedAccessException] {
    $readDenied = $true
} catch {
    # Missing storage or other ambiguity is NOT proof of access denial.
    throw ('Unexpected protected storage error: ' + $_.Exception.Message)
}
if (-not $readDenied) {
    throw 'Worker token could enumerate service-private snapshot storage'
}
# Query service status is allowed, but requesting SCM start is forbidden.
# This fixture-only service has no asset writer or promotion function.
$startOutput = & "$env:SystemRoot\System32\sc.exe" start $service 2>&1 | Out-String
$startExit = $LASTEXITCODE
if ($startExit -eq 0) {
    throw 'Worker token was unexpectedly allowed to start protected service'
}
# Require a permission-denied error, not a missing service/invalid request.
if ($startOutput -notmatch '(?i)(access is denied|access denied|failed 5)') {
    throw 'Worker SCM start failed for an ambiguous reason; not an ACL proof'
}
[ordered]@{
    status = 'negative-worker-access-tests-passed'
    workerSid = $unprivileged
    privateStorageEnumerationDenied = $true
    serviceStartDenied = $true
    independentHumanAuthorization = $false
    stageFrozen = $false
    promotionAuthorized = $false
    copyback = $false
} | ConvertTo-Json -Compress
