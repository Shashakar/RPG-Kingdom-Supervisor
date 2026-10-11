# Installed admin-owned Task Scheduler entrypoint (restricted Windows account).
# Fixed read-only mailbox only. Never accepts model-provided C#, commands or paths.
param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = "Stop"

function AtomicJson([string]$Path, [object]$Value) {
  $tmp = "$Path.tmp.$PID"
  try {
    $Value | ConvertTo-Json -Depth 18 -Compress | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
  } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}
function IsReadDenied([string]$Path) {
  try {
    $s = [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    $s.Dispose()
    return $false
  } catch [UnauthorizedAccessException] {
    return $true
  } catch {
    throw "Read probe inconclusive: only explicit access denied is accepted"
  }
}
function IsWriteDenied([string]$Directory) {
  $marker = Join-Path $Directory ("rpgk-denial-probe-" + [guid]::NewGuid().ToString("N"))
  try {
    $s = [IO.File]::Open($marker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    $s.Dispose()
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    return $false
  } catch [UnauthorizedAccessException] {
    return $true
  } catch {
    throw "Write probe inconclusive: only explicit access denied is accepted"
  }
}
function CliJson([string[]]$CliArgs) {
  $tmp = Join-Path $cfg.QueueRoot ("cli-" + [guid]::NewGuid().ToString("N"))
  $stdout=$tmp+".out"; $stderr=$tmp+".err"
  try {
    $p = Start-Process -FilePath $cfg.CliExe -WorkingDirectory $cfg.StagePath -ArgumentList $CliArgs -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $handle=$p.Handle
    if (-not $p.WaitForExit(90000)) {
      Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
      throw "Unity CLI timed out"
    }
    $p.Refresh()
    if ($p.ExitCode -ne 0) { throw "Unity CLI command failed" }
    if ((Get-Item -LiteralPath $stdout).Length -gt 16777216) { throw "Unity CLI output exceeded limit" }
    $json = Get-Content -LiteralPath $stdout -Raw | ConvertFrom-Json
    if ($json.success -ne $true) { throw "Unity CLI command rejected" }
    return $json
  } finally {
    Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
  }
}

$cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($cfg.schemaVersion -ne 1 -or $cfg.enabled -ne $true -or $cfg.mode -ne "isolated-readonly" -or -not $cfg.StagePath -or -not $cfg.QueueRoot) { throw "Configuration disabled or invalid" }
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($sid -ne $cfg.SandboxSid) { throw "Task identity does not match configured sandbox SID" }
$p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Editor identity must not be an administrator" }
if (@($cfg.ForbiddenReadFiles).Count -lt 2 -or @($cfg.ForbiddenWriteDirectories).Count -lt 1) { throw "Insufficient independent negative probes" }
foreach ($f in @($cfg.ForbiddenReadFiles)) {
  if (-not (IsReadDenied ([string]$f))) { throw "Forbidden operator file was readable" }
}
foreach ($d in @($cfg.ForbiddenWriteDirectories)) {
  if (-not (IsWriteDenied ([string]$d))) { throw "Nonstage directory was writable" }
}
AtomicJson (Join-Path $cfg.QueueRoot "probes.json") ([ordered]@{
  schemaVersion=1;status="denial-probes-passed";sandboxSid=$sid
  readProbeCount=@($cfg.ForbiddenReadFiles).Count
  writeProbeCount=@($cfg.ForbiddenWriteDirectories).Count
  fullIsolationProven=$false;readyForArbitraryEval=$false;readyForCopyback=$false
})
foreach ($path in @($cfg.StagePath,$cfg.EditorExe,$cfg.CliExe)) {
  if (-not (Test-Path -LiteralPath $path)) { throw "Required stage/Unity executable is inaccessible" }
}
$editorLog = Join-Path $cfg.StagePath "IsolatedEditor.log"
$editor = Start-Process -FilePath $cfg.EditorExe -WorkingDirectory $cfg.StagePath -ArgumentList @("-projectPath",('"' + $cfg.StagePath + '"'),"-logFile",('"' + $editorLog + '"')) -PassThru
AtomicJson (Join-Path $cfg.QueueRoot "editor.json") ([ordered]@{
  schemaVersion=1;status="launched";sandboxSid=$sid;unityPid=$editor.Id
  stagePath=$cfg.StagePath;startedAtUtc=[DateTime]::UtcNow.ToString("o")
})
try {
  while (-not $editor.HasExited) {
    $pending = @(Get-ChildItem -LiteralPath (Join-Path $cfg.QueueRoot "requests") -Filter "*.json" -File -ErrorAction SilentlyContinue | Sort-Object Name)
    foreach ($requestFile in $pending) {
      $id = $requestFile.BaseName
      if ($id -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{5,79}$') { continue }
      $response = [ordered]@{schemaVersion=1;requestId=$id;success=$false;operation="denied"}
      try {
        if ($requestFile.Length -gt 4096) { throw "Request too large" }
        $req = Get-Content -LiteralPath $requestFile.FullName -Raw | ConvertFrom-Json
        if ($req.schemaVersion -ne 1 -or $req.requestId -ne $id) { throw "Invalid request envelope" }
        if ($req.operation -eq "get_scene_hierarchy" -and $req.scenePath -eq $cfg.AllowedScene -and @($req.PSObject.Properties.Name).Count -eq 4) {
          $response.operation="get_scene_hierarchy"
          $json = CliJson @("command","get_scene_hierarchy","--project-path",('"' + $cfg.StagePath + '"'),"--format","json")
          $scene = $json.data.result
          if ($scene.scenePath -ne $cfg.AllowedScene -or $scene.isDirty -ne $false) { throw "Active scene wrong or dirty" }
          $stack = New-Object System.Collections.ArrayList
          foreach ($root in @($scene.roots)) { [void]$stack.Add($root) }
          $count=0
          $terrains = New-Object System.Collections.ArrayList
          while ($stack.Count -gt 0) {
            $last=$stack.Count-1; $node=$stack[$last]; $stack.RemoveAt($last)
            $count++
            if ($count -gt 200000) { throw "Hierarchy limit exceeded" }
            if (@($node.components) -contains "Terrain") { [void]$terrains.Add([string]$node.hierarchyPath) }
            foreach ($child in @($node.children)) { [void]$stack.Add($child) }
          }
          $response.success=$true
          $response.scenePath=[string]$scene.scenePath
          $response.rootCount=@($scene.roots).Count
          $response.gameObjectCount=$count
          $response.terrainPaths=@($terrains.ToArray())
          $response.stageOnly=$true
          $response.copyback=$false
        } else {
          throw "Unsupported command"
        }
      } catch {
        $response.error="denied-or-editor-unavailable"
      }
      AtomicJson (Join-Path (Join-Path $cfg.QueueRoot "responses") ($id+".json")) $response
      Remove-Item -LiteralPath $requestFile.FullName -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 400
    $editor.Refresh()
  }
} finally {
  AtomicJson (Join-Path $cfg.QueueRoot "editor.json") ([ordered]@{
    schemaVersion=1;status="stopped";sandboxSid=$sid;unityPid=$editor.Id
    stagePath=$cfg.StagePath;stoppedAtUtc=[DateTime]::UtcNow.ToString("o")
  })
}
