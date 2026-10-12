#Requires -Version 5.1
# C5h: read-only post-install Windows SCM/NTFS identity attestation.
# Run elevated on actual host; does not stop/start services or touch Unity.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$service = 'RPGKSnapshotGuardian'
$base = Join-Path $env:ProgramData 'RPGKingdomSupervisor\C5h'
$private = Join-Path $base 'private'
$exe = Join-Path $base 'bin\RPGKSnapshotGuardian.exe'

function Deny([string]$message) { throw ("C5h service attestation denied: " + $message) }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Deny 'requires a privileged, explicitly operator-owned audit terminal'
}
$svc = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + $service + "'")
if ($null -eq $svc -or $svc.Name -cne $service) { Deny 'named Windows SCM service missing' }
if ($svc.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE')) {
    Deny 'unexpected Windows service process logon identity'
}
if ([int]$svc.ProcessId -le 0 -or $svc.State -ne 'Running') {
    Deny 'Windows service is not actively running'
}
if ($svc.PathName -notlike ('*' + $exe + '*')) { Deny 'unexpected service executable path' }
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { Deny 'service executable missing' }
$binary = Get-Item -LiteralPath $exe
if (($binary.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Deny 'service binary is a reparse point'
}
$process = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + [int]$svc.ProcessId)
if ($null -eq $process -or $process.Name -ine 'RPGKSnapshotGuardian.exe') {
    Deny 'running service PID is not the expected executable'
}
$owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
if ($owner.Sid -ne 'S-1-5-19') { Deny 'service process does not run as LocalService' }

$serviceSid = ([Security.Principal.NTAccount]('NT SERVICE\' + $service)).Translate(
    [Security.Principal.SecurityIdentifier]).Value
$privateItem = Get-Item -LiteralPath $private -Force
if (($privateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Deny 'protected root is a reparse point'
}
$acl = Get-Acl -LiteralPath $private
if (-not $acl.AreAccessRulesProtected) { Deny 'protected root inherits parent ACL' }
$ownerSid = $acl.Owner.Translate([Security.Principal.SecurityIdentifier]).Value
if ($ownerSid -notin @('S-1-5-18','S-1-5-32-544')) {
    Deny 'protected root is owned by worker-usable user identity'
}
foreach ($path in @($base, (Join-Path $base 'bin'), $exe)) {
    $security = Get-Acl -LiteralPath $path
    $ownerSid = $security.Owner.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin @('S-1-5-18','S-1-5-32-544')) {
        Deny "service parent or executable has an ordinary user owner: $path"
    }
    if (-not $security.AreAccessRulesProtected) {
        Deny "service parent or executable inherits ACL entries: $path"
    }
}
$allowed = @('S-1-5-18','S-1-5-32-544',$serviceSid)
$hasService = $false
foreach ($entry in $acl.Access) {
    $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($entry.AccessControlType -eq 'Allow' -and $sid -notin $allowed) {
        Deny "protected root has unexpected allow ACE: $sid"
    }
    if ($sid -eq $serviceSid -and $entry.AccessControlType -eq 'Allow') {
        $hasService = $true
    }
}
if (-not $hasService) { Deny 'protected service SID lacks explicit access' }

$generations = @(Get-ChildItem -LiteralPath $private -Directory -Filter 'fixture-*' -Force |
    Sort-Object LastWriteTimeUtc -Descending)
if ($generations.Count -eq 0) { Deny 'service never wrote protected fixture' }
$latest = $generations[0]
if (($latest.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Deny 'latest service fixture directory is linked'
}
$receiptFile = Join-Path $latest.FullName 'receipt.json'
$receipt = Get-Content -LiteralPath $receiptFile -Raw | ConvertFrom-Json
if ($receipt.schemaVersion -ne 1 -or
    $receipt.mode -cne 'protected-service-synthetic-fixture-only' -or
    $receipt.windowsIdentitySid -ne 'S-1-5-19' -or
    $receipt.serviceSid -ne $serviceSid -or
    $receipt.generation -cne $latest.Name) { Deny 'service fixture receipt mismatch' }
foreach ($key in @('realStageUsed','stageFrozen','independentHumanAuthorization',
                   'promotionAuthorized','copyback')) {
    if ($receipt.$key -ne $false) { Deny "fixture incorrectly claims authority: $key" }
}
$source = Join-Path $latest.FullName 'source-fixture.txt'
$snapshot = Join-Path $latest.FullName 'snapshot-fixture.txt'
$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
$copyHash = (Get-FileHash -LiteralPath $snapshot -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne $copyHash -or
    $receipt.sourceFixtureSha256 -cne $sourceHash -or
    $receipt.snapshotFixtureSha256 -cne $copyHash) {
    Deny 'service synthetic fixture bytes changed or mismatched'
}

[ordered]@{
    status = 'protected-service-synthetic-proof-only'
    service = $service
    scmPidObserved = [int]$svc.ProcessId
    serviceWindowsSid = 'S-1-5-19'
    virtualServiceSid = $serviceSid
    rootAclExplicitAndBounded = $true
    fixtureSha256 = $copyHash
    fixtureGeneration = $latest.Name
    hostVerifiedForPromotion = $false
    realStageUsed = $false
    immutableSnapshotVerified = $false
    independentHumanAuthorization = $false
    stageFrozen = $false
    promotionAuthorized = $false
    copyback = $false
} | ConvertTo-Json -Compress
