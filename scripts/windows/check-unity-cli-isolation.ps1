# Operator-only Windows evidence check. Does not provision users, start Unity,
# edit ACLs, grant a model tool, or treat SID separation as proof of isolation.
param(
  [Parameter(Mandatory=$true)][string]$SessionPath,
  [string]$ExpectedSandboxSid = ""
)
$ErrorActionPreference = "Stop"

function Emit($status, $reason, $editorSidSeparated, $verifiedOwner, $sessionId) {
  [ordered]@{
    schemaVersion = 1
    operation = "c2b-windows-identity-preflight"
    status = $status
    reason = $reason
    editorSidSeparated = $editorSidSeparated
    verifiedEditorOwnership = $verifiedOwner
    filesystemDenyProbePassed = $false
    readyForArbitraryEval = $false
    readyForCopyback = $false
    sessionId = $sessionId
  } | ConvertTo-Json -Compress -Depth 3
}
function Deny($message) {
  Emit "blocked" $message $false $false ""
  exit 82
}

$path = [IO.Path]::GetFullPath($SessionPath).TrimEnd('\')
if (-not (Test-Path -LiteralPath $path -PathType Container)) {
  Deny "Stage project folder missing"
}
$folder = Get-Item -LiteralPath $path
if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
  Deny "Stage reparse point is forbidden"
}
$receipt = Join-Path $path "session.json"
if (-not (Test-Path -LiteralPath $receipt -PathType Leaf)) {
  Deny "Stage has no owned session receipt"
}
try {
  $session = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
} catch {
  Deny "Invalid session receipt JSON"
}
if ($session.schemaVersion -ne 1 -or $session.sessionId -ne [IO.Path]::GetFileName($path) -or
    -not [string]::Equals([string]$session.stagePath, $path, [StringComparison]::OrdinalIgnoreCase) -or
    [int]$session.unityPid -le 0) {
  Deny "Session receipt does not match stage"
}
$pidToCheck = [int]$session.unityPid
$proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $pidToCheck) -ErrorAction SilentlyContinue
if (-not $proc -or $proc.Name -ne "Unity.exe") {
  Deny "Receipt-owned Unity Editor process is not running"
}
if (-not $proc.CommandLine -or
    $proc.CommandLine -notmatch ('(?i)(?:^|\s)-projectPath\s+"' + [regex]::Escape($path) + '"(?:\s|$)') -or
    ($proc.ExecutablePath -and -not [string]::Equals([string]$proc.ExecutablePath,
      [string]$session.unityExe, [StringComparison]::OrdinalIgnoreCase))) {
  Deny "Unity process command line or executable does not match the stage receipt"
}

try {
  $sidResult = Invoke-CimMethod -InputObject $proc -MethodName GetOwnerSid -ErrorAction Stop
  $editorSid = [string]$sidResult.Sid
  $operatorSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
} catch {
  Deny "Cannot verify Unity process Windows security identity"
}
if (-not $editorSid -or $sidResult.ReturnValue -ne 0) {
  Deny "Unity process SID could not be verified"
}
if ($editorSid -eq $operatorSid) {
  Emit "not-isolated" "Unity Editor is running under the operator Windows identity" $false $true $session.sessionId
  exit 82
}
if (-not $ExpectedSandboxSid -or $ExpectedSandboxSid -ne $editorSid) {
  Emit "blocked" "Different owner SID observed, but it does not match an explicitly expected sandbox SID" $true $true $session.sessionId
  exit 82
}

# SID separation is necessary but insufficient: the Editor identity could still
# read operator secrets via permissive Users, Everyone, or inherited ACLs.
# Before enabling generic eval, a command running AS the sandbox identity
# must prove denied reads of operator profile/secrets/repositories and write
# denial outside the staged project. This script performs no impersonation.
Emit "incomplete" "Separate expected SID verified; filesystem denial probe has not been established" $true $true $session.sessionId
exit 82
