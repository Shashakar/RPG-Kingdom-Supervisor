#Requires -Version 5.1
# C5h: ELEVATED operator setup only from reviewed admin-only trusted-source.
# Never run elevated from a worker-writable repository or Unity staging folder.
# No asset promotion, service endpoint, tokens, or worker-controlled inputs.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ApprovedSourceSha256
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$service = 'RPGKSnapshotGuardian'
$base = Join-Path $env:ProgramData 'RPGKingdomSupervisor\C5h'
$sourceRoot = Join-Path $base 'trusted-source'
$bin = Join-Path $base 'bin'
$private = Join-Path $base 'private'
$workerTest = Join-Path $base 'worker-test'
$exe = Join-Path $bin 'RPGKSnapshotGuardian.exe'
$src = Join-Path $sourceRoot 'C5hSnapshotGuardian.cs'
$auditSrc = Join-Path $sourceRoot 'Test-C5hSnapshotGuardian.ps1'
$workerTestSrc = Join-Path $sourceRoot 'Test-C5hWorkerDenied.ps1'

function Require-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'C5h installer requires a deliberate elevated Administrator shell'
    }
}

function Assert-ProtectedDirectory([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "C5h trusted directory missing: $path"
    }
    $entry = Get-Item -LiteralPath $path -Force
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'C5h trusted source or destination contains a reparse point'
    }
    $acl = Get-Acl -LiteralPath $path
    if (-not $acl.AreAccessRulesProtected) {
        throw "C5h directory inherits permissions: $path"
    }
    $ownerSid = ([Security.Principal.NTAccount]$acl.Owner).Translate(
        [Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin @('S-1-5-18','S-1-5-32-544')) {
        throw "C5h trusted root is still owned by a user token: $ownerSid"
    }
    $trusted = @('S-1-5-18','S-1-5-32-544')
    foreach ($access in $acl.Access) {
        $sid = $access.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier]).Value
        if (($sid -notin $trusted) -and $access.AccessControlType -eq 'Allow') {
            throw "C5h trusted source is accessible to another principal: $sid"
        }
    }
}

function Invoke-Sc([string[]]$arguments) {
    & "$env:SystemRoot\System32\sc.exe" @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "sc.exe operation failed: $($arguments[0])" }
}

function Grant-ExactAcl([string]$path, [string[]]$grants) {
    & "$env:SystemRoot\System32\icacls.exe" $path '/setowner' '*S-1-5-32-544' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'failed to set Administrators as protected owner' }
    & "$env:SystemRoot\System32\icacls.exe" $path '/inheritance:r' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'failed to remove inherited ACL entries' }
    & "$env:SystemRoot\System32\icacls.exe" $path '/grant:r' @grants | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'failed to apply service-specific ACL' }
}

Require-Admin
if ([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') -ine
        [IO.Path]::GetFullPath($sourceRoot).TrimEnd('\')) {
    throw 'Run only from admin-protected trusted-source, not a Git checkout'
}
Assert-ProtectedDirectory $base
Assert-ProtectedDirectory $sourceRoot
foreach ($file in @($src, $PSCommandPath, $auditSrc, $workerTestSrc)) {
    $item = Get-Item -LiteralPath $file -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "C5h trusted source file is linked: $file"
    }
    $acl = Get-Acl -LiteralPath $file
    $ownerSid = ([Security.Principal.NTAccount]$acl.Owner).Translate(
        [Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin @('S-1-5-18','S-1-5-32-544')) {
        throw "C5h trusted source file is owned by a user: $ownerSid"
    }
    foreach ($access in $acl.Access) {
        $sid = $access.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier]).Value
        if ($sid -notin @('S-1-5-18','S-1-5-32-544') -and
            $access.AccessControlType -eq 'Allow') {
            throw "C5h trusted source file can be read/modified by untrusted principal: $sid"
        }
    }
}
$observed = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
if ($observed -ine $ApprovedSourceSha256) {
    throw 'C5h C# implementation differs from independently reviewed SHA256'
}
# Never adopt, stop or rewrite an existing potentially foreign service.
& "$env:SystemRoot\System32\sc.exe" 'query' $service *> $null
if ($LASTEXITCODE -eq 0) {
    throw 'C5h service already exists; refuse to adopt or reconfigure it'
}
foreach ($d in @($bin, $private, $workerTest)) {
    if (Test-Path -LiteralPath $d) {
        throw "C5h destination already exists; inspect before provisioning: $d"
    }
}
New-Item -ItemType Directory -Path $bin, $private, $workerTest | Out-Null
# LocalService needs read-execute on the binary, not write.
Grant-ExactAcl $bin @('*S-1-5-18:(OI)(CI)F',
                      '*S-1-5-32-544:(OI)(CI)F')
Grant-ExactAcl $private @('*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
# Worker can READ this verified negative test, but cannot change it.
Grant-ExactAcl $workerTest @('*S-1-5-18:(OI)(CI)F',
                            '*S-1-5-32-544:(OI)(CI)F',
                            '*S-1-5-11:(OI)(CI)RX')
$workerTestCopy = Join-Path $workerTest 'Test-C5hWorkerDenied.ps1'
Copy-Item -LiteralPath $workerTestSrc -Destination $workerTestCopy
& "$env:SystemRoot\System32\icacls.exe" $workerTestCopy '/setowner' '*S-1-5-32-544' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'negative-test file owner was not protected' }

# Compile from a second protected copy, not the mutable original location.
$copy = Join-Path $bin 'C5hSnapshotGuardian.cs'
Copy-Item -LiteralPath $src -Destination $copy
if ((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -ine $ApprovedSourceSha256) {
    throw 'C5h protected source changed while being copied'
}
Add-Type -Path $copy -OutputAssembly $exe -OutputType ConsoleApplication -ReferencedAssemblies @('System.dll','System.Core.dll','System.ServiceProcess.dll','System.Security.dll') -ErrorAction Stop
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw 'C5h compiler did not produce service executable'
}
# Individual source and executable owners also matter, even with a tight
# parent DACL. An unelevated worker sharing the administrator account's user
# SID must not be the owner with implicit WRITE_DAC.
foreach ($file in @($copy, $exe)) {
    & "$env:SystemRoot\System32\icacls.exe" $file '/setowner' '*S-1-5-32-544' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'protected service binary/file owner change failed' }
}

# The service starts disabled and is never auto-started by worker action.
Invoke-Sc @('create', $service, 'binPath=', ('"' + $exe + '"'), 'type=', 'own',
            'start=', 'disabled', 'obj=', ('NT SERVICE\' + $service),
            'DisplayName=', 'RPG Kingdom C5h Snapshot Guardian')
Invoke-Sc @('sidtype', $service, 'restricted')
Invoke-Sc @('sdset', $service,
    'D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCLCSWRPWPDTLOCRRC;;;BA)')
$serviceSid = ([Security.Principal.NTAccount]('NT SERVICE\' + $service)).Translate(
    [Security.Principal.SecurityIdentifier]).Value
# The unique virtual account needs read/execute only on its executable.
Grant-ExactAcl $bin @('*S-1-5-18:(OI)(CI)F',
                      '*S-1-5-32-544:(OI)(CI)F',
                      ('*' + $serviceSid + ':(OI)(CI)RX'))
# The executable already exists: do not rely on propagation of a newly
# installed parent inheritable ACE to previously created child files.
Grant-ExactAcl $exe @('*S-1-5-18:F', '*S-1-5-32-544:F',
                      ('*' + $serviceSid + ':RX'))
Grant-ExactAcl $private @('*S-1-5-18:(OI)(CI)F',
                          '*S-1-5-32-544:(OI)(CI)F',
                          ('*' + $serviceSid + ':(OI)(CI)F'))
# Explicit operator start only; no automatic startup at reboot.
Invoke-Sc @('config', $service, 'start=', 'demand')

[ordered]@{
    status = 'installed-manual-start-only'
    serviceName = $service
    serviceSid = $serviceSid
    protectedRoot = $private
    realStageUsed = $false
    snapshotOfRealAssets = $false
    stageFrozen = $false
    independentHumanAuthorization = $false
    promotionAuthorized = $false
    copyback = $false
} | ConvertTo-Json -Compress
