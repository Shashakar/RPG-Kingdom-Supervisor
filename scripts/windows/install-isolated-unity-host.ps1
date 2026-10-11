# Trusted operator/admin setup for on-demand restricted Unity task.
# Does not create Windows accounts, modify personal profile ACLs, or expose shell
# commands to model workers. -Install requires elevated PowerShell and a manual
# credential prompt; all other actions are read-only unless -Start is requested.
param(
  [Parameter(Mandatory=$true)][ValidateSet("plan","install","start","status")][string]$Action,
  [string]$SandboxUser = "RPGKUnitySandbox",
  [string]$SourceProjectPath = "",
  [string]$EditorPath = "",
  [string]$CliPath = "",
  [string[]]$ForbiddenReadFiles = @(),
  [string[]]$ForbiddenWriteDirectories = @(),
  [string]$HostId = ""
)
$ErrorActionPreference="Stop"
$Root=Join-Path $env:ProgramData "RPGKingdomSupervisor\IsolatedUnity"
$TaskFolder="\RPGKingdomSupervisor\"
function Fail([string]$reason) {
  [ordered]@{schemaVersion=1;action=$Action;status="blocked";reason=$reason} | ConvertTo-Json -Compress
  exit 82
}
function Emit([object]$payload) {
  $payload | ConvertTo-Json -Compress -Depth 5
}
function AccountSid([string]$Name) {
  try { return (New-Object Security.Principal.NTAccount($Name)).Translate([Security.Principal.SecurityIdentifier]).Value }
  catch { Fail "Dedicated local account missing: operator must provision a standard Windows account first" }
}
function AclOnly([string]$Path, [string]$SandboxSid, [string]$UserSid, [string]$SandboxRight, [string]$OperatorRight) {
  # Fresh, generated ProgramData files only. Never call on personal directories.
  $acl=Get-Acl -LiteralPath $Path
  $acl.SetAccessRuleProtection($true,$false)
  foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($rule) }
  $inherit=[Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
  $none=[Security.AccessControl.PropagationFlags]::None
  $allow=[Security.AccessControl.AccessControlType]::Allow
  foreach ($entry in @(
    @("S-1-5-18","FullControl"), @("S-1-5-32-544","FullControl"),
    @($SandboxSid,$SandboxRight), @($UserSid,$OperatorRight))) {
    $sid = New-Object Security.Principal.SecurityIdentifier($entry[0])
    $right = [Enum]::Parse([Security.AccessControl.FileSystemRights], [string]$entry[1])
    $ace = New-Object Security.AccessControl.FileSystemAccessRule($sid,$right,$inherit,$none,$allow)
    $acl.AddAccessRule($ace)
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
}
function ValidateAccount {
  if ($SandboxUser -notmatch '^[A-Za-z][A-Za-z0-9_-]{2,28}$') { Fail "Sandbox account name invalid" }
  $name = ".\" + $SandboxUser
  $sid = AccountSid $name
  $operator = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  if ($sid -eq $operator) { Fail "Sandbox must not be operator account" }
  $adminSid="S-1-5-32-544"
  $members = @(Get-LocalGroupMember -SID $adminSid -ErrorAction Stop)
  if (@($members | Where-Object { $_.SID.Value -eq $sid }).Count -gt 0) {
    Fail "Sandbox account must not be in local Administrators"
  }
  return @($name,$sid,$operator)
}
function ValidateRootPath([string]$Path,[string]$What) {
  if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { Fail "$What not an existing directory" }
  $item=Get-Item -LiteralPath $Path
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail "$What cannot be a symlink or junction" }
  return $item.FullName
}
function TaskName {
  if ($HostId -notmatch '^isolated-[0-9a-f]{32}$') { Fail "HostId must be a registered isolated-<uuid> instance" }
  return "RPGK-Unity-" + $HostId
}
if ($Action -eq "plan") {
  $account=ValidateAccount
  Emit ([ordered]@{schemaVersion=1;action="plan";status="no-changes";sandboxSid=$account[1]
    proposedRoot=$Root;onDemandTask=$true;editorWriteEnabled=$false
    arbitraryEvalEnabled=$false;credentialsStoredInRepository=$false})
  exit 0
}

if ($Action -eq "install") {
  $me=[Security.Principal.WindowsIdentity]::GetCurrent()
  $principal=New-Object Security.Principal.WindowsPrincipal($me)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Run install in an explicitly elevated operator PowerShell"
  }
  $account=ValidateAccount
  $source=ValidateRootPath $SourceProjectPath "Source project"
  if (@($ForbiddenReadFiles).Count -lt 2 -or @($ForbiddenWriteDirectories).Count -lt 1) {
    Fail "At least two existing forbidden read files and one forbidden write directory required"
  }
  foreach ($file in $ForbiddenReadFiles) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Fail "Operator read-denial probe does not exist" }
  }
  foreach ($dir in $ForbiddenWriteDirectories) { $null=ValidateRootPath $dir "Forbidden write directory" }
  foreach ($folder in @("Assets","Packages","ProjectSettings")) {
    $null=ValidateRootPath (Join-Path $source $folder) "Source project folder"
  }
  foreach ($path in @($EditorPath,$CliPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Fail "Pinned Editor/CLI executable missing" }
  }
  $projectVersion=Get-Content -LiteralPath (Join-Path $source "ProjectSettings\ProjectVersion.txt") -Raw
  if ($projectVersion -notmatch '(?m)^m_EditorVersion:\s*6000\.3\.10f1\s*$') { Fail "Project Editor version is not pinned 6000.3.10f1" }
  # Never copy an existing modified pilot; require the operator to provide an
  # independently reviewed CLEAN game checkout, followed by C2b attestation.
  if (Test-Path -LiteralPath $Root) { Fail "Isolated host root already exists; do not overwrite operator state" }
  $id="isolated-"+[guid]::NewGuid().ToString("N")
  $host=Join-Path $Root $id
  $stage=Join-Path $host "project"
  $queue=Join-Path $host "queue"
  $requests=Join-Path $queue "requests"
  $responses=Join-Path $queue "responses"
  foreach ($path in @($Root,$host,$stage,$queue,$requests,$responses)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
  # Source permissions are never edited, only destination ACLs.
  foreach ($folder in @("Assets","Packages","ProjectSettings")) {
    $dest=Join-Path $stage $folder
    New-Item -ItemType Directory -Path $dest | Out-Null
    & robocopy.exe (Join-Path $source $folder) $dest /E /XJ /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "Stage-only robocopy failed; abort without launching Editor" }
  }
  # Install ONLY the pinned experimental Pipeline package into the private
  # project clone; the source checkout is never mutated.
  $manifest=Join-Path $stage "Packages\manifest.json"
  $manifestObject=Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
  if (-not $manifestObject.dependencies) { Fail "Invalid Unity manifest dependencies" }
  $current=$manifestObject.dependencies.PSObject.Properties["com.unity.pipeline"]
  if ($current -and $current.Value -ne "0.8.0-exp.1") { Fail "Unsupported Pipeline version in stage" }
  if (-not $current) {
    $manifestObject.dependencies | Add-Member -MemberType NoteProperty -Name "com.unity.pipeline" -Value "0.8.0-exp.1"
    $manifestObject | ConvertTo-Json -Depth 64 | Set-Content -LiteralPath $manifest -Encoding UTF8
  }
  $agentSource=Join-Path $PSScriptRoot "run-isolated-unity-host.ps1"
  $agent=Join-Path $host "agent.ps1"
  Copy-Item -LiteralPath $agentSource -Destination $agent -ErrorAction Stop
  $config=Join-Path $host "config.json"
  $cfg=[ordered]@{
    schemaVersion=1;enabled=$true;mode="isolated-readonly";SandboxSid=$account[1]
    SourceProjectPath=$source;StagePath=$stage;QueueRoot=$queue
    EditorExe=[IO.Path]::GetFullPath($EditorPath);CliExe=[IO.Path]::GetFullPath($CliPath)
    AllowedScene="Assets/RPGKingdom/Scenes/PlaytestScene.unity"
    ForbiddenReadFiles=@($ForbiddenReadFiles);ForbiddenWriteDirectories=@($ForbiddenWriteDirectories)
    hostId=$id
  }
  $cfg | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $config -Encoding UTF8
  # The sandbox can only read the agent/config, mutate its staged project,
  # read requests, and write responses/heartbeat. It cannot modify the agent.
  AclOnly $Root $account[1] $account[2] "ReadAndExecute" "FullControl"
  AclOnly $host $account[1] $account[2] "ReadAndExecute" "FullControl"
  AclOnly $stage $account[1] $account[2] "Modify" "FullControl"
  AclOnly $queue $account[1] $account[2] "Modify" "FullControl"
  AclOnly $requests $account[1] $account[2] "ReadAndExecute" "Modify"
  AclOnly $responses $account[1] $account[2] "Modify" "ReadAndExecute"
  $taskName="RPGK-Unity-"+$id
  $exe=Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
  $args='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $agent + '" -ConfigPath "' + $config + '"'
  $taskAction=New-ScheduledTaskAction -Execute $exe -Argument $args -WorkingDirectory $host
  $taskSettings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  $cred=Get-Credential -UserName $account[0] -Message "Enter password for existing non-admin Unity sandbox account. Stored only by Windows Task Scheduler."
  if (-not $cred -or (AccountSid $cred.UserName) -ne $account[1]) { Fail "Account credential mismatch or cancelled" }
  $password=[Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($cred.Password))
  try {
    Register-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder -Action $taskAction -Settings $taskSettings -User $account[0] -Password $password -RunLevel Limited -Description "Operator-owned fixed read-only RPG Kingdom Unity stage (no code evaluation)" -ErrorAction Stop | Out-Null
  } finally {
    $password=$null; Remove-Variable cred -ErrorAction SilentlyContinue
  }
  Emit ([ordered]@{schemaVersion=1;action="install";status="registered-not-started"
    taskName=$taskName;taskPath=$TaskFolder;stagePath=$stage;hostId=$id
    sandboxSid=$account[1];readyForArbitraryEval=$false;readyForCopyback=$false})
  exit 0
}

$taskName=TaskName
if ($Action -eq "start") {
  if (@(Get-Process -Name Unity -ErrorAction SilentlyContinue).Count -gt 0) { Fail "Another Unity Editor is running; no concurrent Editor allowed" }
  $task=Get-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue
  if (-not $task -or $task.Settings.MultipleInstances -ne "IgnoreNew") { Fail "Unregistered or altered task" }
  if ($task.State -eq "Running") { Fail "Editor task already running" }
  Start-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder -ErrorAction Stop
  Emit ([ordered]@{schemaVersion=1;action="start";status="start-requested";hostId=$HostId
    readyForArbitraryEval=$false;readyForCopyback=$false})
  exit 0
}
$task=Get-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue
if (-not $task) { Fail "Task not registered" }
$queue=Join-Path (Join-Path $Root $HostId) "queue"
$editorFile=Join-Path $queue "editor.json"
$probeFile=Join-Path $queue "probes.json"
Emit ([ordered]@{schemaVersion=1;action="status";taskState=[string]$task.State;hostId=$HostId
  editor=$(if (Test-Path -LiteralPath $editorFile) { Get-Content -LiteralPath $editorFile -Raw | ConvertFrom-Json } else { $null })
  probes=$(if (Test-Path -LiteralPath $probeFile) { Get-Content -LiteralPath $probeFile -Raw | ConvertFrom-Json } else { $null })
  readyForArbitraryEval=$false;readyForCopyback=$false})
