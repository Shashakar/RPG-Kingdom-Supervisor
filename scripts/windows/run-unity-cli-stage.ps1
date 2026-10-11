# Operator-only, disabled for model workers. Windows NTFS disposable pilot.
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet("start","status","discover","install-pipeline","stop")]
  [string]$Action,
  [string]$SourceProjectPath = "",
  [string]$SessionPath = "",
  [string]$CliPath = "unity"
)
$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "RPGKingdomSupervisor\UnityCliPilots"
function Deny($reason, $code) {
  @{schemaVersion=1;action=$Action;status="blocked";reason=$reason} | ConvertTo-Json -Compress
  exit $code
}
function FindCli {
  $cli = Get-Command -Name $CliPath -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $cli) { Deny "Unity CLI not installed on Windows or -CliPath invalid" 20 }
  return $cli.Source
}
function ExecuteCli($exe, $commandArgs, $dir, $id, $seconds) {
  $out = Join-Path $dir ($id + ".out.txt")
  $err = Join-Path $dir ($id + ".err.txt")
  $p = Start-Process -FilePath $exe -ArgumentList $commandArgs -PassThru -WorkingDirectory $dir -RedirectStandardOutput $out -RedirectStandardError $err
  if (-not $p.WaitForExit($seconds * 1000)) {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    $p.WaitForExit()
    return $false
  }
  return ($p.ExitCode -eq 0)
}
function ReadSession {
  if (-not $SessionPath) { Deny "SessionPath required" 64 }
  $path = [IO.Path]::GetFullPath($SessionPath).TrimEnd('\')
  $rootPrefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
  if (-not $path.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Deny "Session must be within the dedicated pilot root" 64
  }
  $item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
  if (-not $item -or -not $item.PSIsContainer) { Deny "Session path not found" 64 }
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Deny "Junction/symlink session rejected" 64 }
  $receipt = Join-Path $path "session.json"
  if (-not (Test-Path -LiteralPath $receipt -PathType Leaf)) { Deny "Missing owned-session receipt" 64 }
  $session = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
  if ($session.schemaVersion -ne 1 -or $session.sessionId -ne [IO.Path]::GetFileName($path) -or
      -not [string]::Equals([string]$session.stagePath,$path,[StringComparison]::OrdinalIgnoreCase)) {
    Deny "Invalid session receipt" 64
  }
  return $session
}
function OwnedEditor($session) {
  $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$session.unityPid) -ErrorAction SilentlyContinue
  if (-not $p) { return $null }
  if ($p.Name -ne "Unity.exe" -or
      ($p.ExecutablePath -and -not [string]::Equals([string]$p.ExecutablePath,[string]$session.unityExe,[StringComparison]::OrdinalIgnoreCase))) {
    Deny "PID no longer identifies the owned Unity executable" 65
  }
  $pattern = '(?i)(?:^|\s)-projectPath\s+"' + [regex]::Escape([string]$session.stagePath) + '"(?:\s|$)'
  if (-not $p.CommandLine -or $p.CommandLine -notmatch $pattern) {
    Deny "PID project-path ownership could not be verified" 65
  }
  return $p
}
if ($Action -eq "start") {
  if (-not $SourceProjectPath) { Deny "Provide -SourceProjectPath" 64 }
  $source = [IO.Path]::GetFullPath($SourceProjectPath)
  foreach ($folder in @("Assets","Packages","ProjectSettings")) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $folder) -PathType Container)) {
      Deny "Source is missing required Unity project folders" 64
    }
  }
  $versionPath = Join-Path $source "ProjectSettings\ProjectVersion.txt"
  if (-not (Test-Path -LiteralPath $versionPath)) { Deny "Missing ProjectVersion.txt" 64 }
  $m = [regex]::Match((Get-Content -LiteralPath $versionPath -Raw),'(?m)^m_EditorVersion:\s*(6000\.\d+\.\d+[a-z]\d+)\s*$')
  if (-not $m.Success) { Deny "Unity 6 project version not recognized" 64 }
  $version = $m.Groups[1].Value
  $unityExe = Join-Path $env:ProgramFiles ("Unity\Hub\Editor\" + $version + "\Editor\Unity.exe")
  if (-not (Test-Path -LiteralPath $unityExe -PathType Leaf)) { Deny "Matching Unity Editor executable missing" 21 }
  $cli = FindCli
  if (@(Get-Process -Name Unity -ErrorAction SilentlyContinue).Count -gt 0) {
    Deny "Another Unity.exe exists; exclusive host pilot must not interfere" 22
  }
  if (-not (ExecuteCli $cli @("--version") $env:TEMP "rpgk-cli-version" 15)) { Deny "CLI version command failed" 23 }
  $id = "pilot-" + [guid]::NewGuid().ToString("N")
  $stage = Join-Path $Root $id
  New-Item -ItemType Directory -Path $stage -Force | Out-Null
  foreach ($folder in @("Assets","Packages","ProjectSettings")) {
    $dest = Join-Path $stage $folder
    New-Item -ItemType Directory -Path $dest | Out-Null
    & robocopy.exe (Join-Path $source $folder) $dest /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Deny "NTFS stage copy failed; source remains unchanged" 24 }
  }
  $log = Join-Path $stage "Editor.log"
  $owned = Start-Process -FilePath $unityExe -ArgumentList @("-projectPath",('"' + $stage + '"'),"-logFile",('"' + $log + '"')) -WorkingDirectory $stage -PassThru
  $receipt = [ordered]@{
    schemaVersion=1;sessionId=$id;stagePath=$stage;unityVersion=$version
    unityExe=$unityExe;unityPid=$owned.Id;createdAtUtc=[DateTime]::UtcNow.ToString("o")
  }
  $receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage "session.json") -Encoding UTF8
  @{schemaVersion=1;action=$Action;status="started";sessionPath=$stage;unityPid=$owned.Id;unityVersion=$version} | ConvertTo-Json -Compress
  exit 0
}
$session = ReadSession
$owned = OwnedEditor $session
if ($Action -eq "status") {
  @{schemaVersion=1;action=$Action;status=$(if ($owned) {"running"} else {"stopped"});sessionPath=$session.stagePath;unityPid=$session.unityPid} | ConvertTo-Json -Compress
  exit 0
}
if ($Action -eq "stop") {
  if (-not $owned) {
    @{schemaVersion=1;action=$Action;status="already-stopped";sessionPath=$session.stagePath} | ConvertTo-Json -Compress
    exit 0
  }
  Stop-Process -Id $owned.ProcessId -ErrorAction Stop
  @{schemaVersion=1;action=$Action;status="stop-requested";sessionPath=$session.stagePath;unityPid=$owned.ProcessId} | ConvertTo-Json -Compress
  exit 0
}
if (-not $owned) { Deny "Owned Editor not running" 30 }
$cli = FindCli
$target = '"' + $session.stagePath + '"'
if ($Action -eq "install-pipeline") {
  $ok = ExecuteCli $cli @("pipeline","install","--project-path",$target,"--non-interactive") $session.stagePath "pipeline-install" 180
  @{schemaVersion=1;action=$Action;status=$(if ($ok) {"install-command-succeeded"} else {"install-command-failed"});sessionPath=$session.stagePath;stageOnly=$true} | ConvertTo-Json -Compress
  if (-not $ok) { exit 31 }
  exit 0
}
if ($Action -eq "discover") {
  $v = ExecuteCli $cli @("--version") $session.stagePath "cli-version" 20
  $p = ExecuteCli $cli @("pipeline","list","--format","json") $session.stagePath "pipeline-list" 35
  $c = ExecuteCli $cli @("command","--project-path",$target,"--format","json") $session.stagePath "editor-commands" 45
  @{schemaVersion=1;action=$Action;status=$(if ($v -and $p -and $c) {"commands-available"} else {"discovery-incomplete"});sessionPath=$session.stagePath;cliVersionSucceeded=$v;pipelineListSucceeded=$p;projectCommandsSucceeded=$c} | ConvertTo-Json -Compress
  if (-not ($v -and $p -and $c)) { exit 32 }
  exit 0
}
Deny "Unsupported action" 64
