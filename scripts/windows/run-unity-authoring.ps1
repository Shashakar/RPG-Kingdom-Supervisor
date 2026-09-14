param(
    [Parameter(Mandatory = $true)]
    [string]$SourceProjectPath,

    [Parameter(Mandatory = $true)]
    [string]$UnityVersion,

    [Parameter(Mandatory = $true)]
    [string]$RequestPath,

    [Parameter(Mandatory = $true)]
    [string]$RequestId,

    [string]$UnityPath = "",
    [string]$StageRoot = "",
    [int]$AuthorTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

function Fail-Authoring {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine("RPG Kingdom Unity authoring: $Message")
    exit $Code
}

function Invoke-ProjectMirror {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /MIR /FFT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Fail-Authoring "robocopy failed for '$Source' -> '$Destination' with exit code $LASTEXITCODE" 84
    }
}

function Assert-UnityHostIdle {
    $unityProcesses = @(Get-Process -Name "Unity" -ErrorAction SilentlyContinue)
    if ($unityProcesses.Count -gt 0) {
        $ids = ($unityProcesses | Sort-Object Id | ForEach-Object { $_.Id }) -join ", "
        Fail-Authoring "Unity Editor host is busy (Unity.exe PID(s): $ids)" 89
    }
}

if ([string]::IsNullOrWhiteSpace($UnityPath)) {
    $UnityPath = Join-Path ${env:ProgramFiles} "Unity\Hub\Editor\$UnityVersion\Editor\Unity.exe"
}
if ([string]::IsNullOrWhiteSpace($StageRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Fail-Authoring "LOCALAPPDATA is unavailable" 80
    }
    $StageRoot = Join-Path $env:LOCALAPPDATA "RPGKingdomSupervisor\UnityStages"
}
if (-not (Test-Path -LiteralPath $UnityPath -PathType Leaf)) {
    Fail-Authoring "Unity $UnityVersion was not found at '$UnityPath'" 81
}
if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
    Fail-Authoring "authoring request '$RequestPath' does not exist" 82
}
if (-not (Get-Command robocopy.exe -ErrorAction SilentlyContinue)) {
    Fail-Authoring "robocopy.exe is unavailable" 83
}
foreach ($required in @("Assets", "Packages", "ProjectSettings")) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceProjectPath $required) -PathType Container)) {
        Fail-Authoring "source project is missing '$required'" 82
    }
}

try {
    $envelope = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
}
catch {
    Fail-Authoring "request JSON could not be parsed: $($_.Exception.Message)" 64
}
if ([int]$envelope.protocolVersion -ne 1 -or [string]$envelope.operation -ne "author") {
    Fail-Authoring "unsupported request protocol/operation" 64
}
$authoring = $envelope.authoring
if ($null -eq $authoring -or [int]$authoring.protocolVersion -ne 1) {
    Fail-Authoring "unsupported authoring protocol" 64
}
$tier = [string]$authoring.tier
if ($tier -ne "mechanical" -and $tier -ne "mechanical-structural") {
    Fail-Authoring "unsupported scene-authoring tier '$tier'" 64
}
$scene = [string]$authoring.scene
if ([string]::IsNullOrWhiteSpace($scene) -or -not $scene.StartsWith("Assets/") -or -not $scene.EndsWith(".unity") -or $scene.Contains("..")) {
    Fail-Authoring "scene must be a project-relative Assets/*.unity path" 64
}
if ($null -eq $authoring.operations -or @($authoring.operations).Count -eq 0) {
    Fail-Authoring "authoring request has no operations" 64
}

$StageProject = Join-Path (Join-Path $StageRoot $UnityVersion) "RPG-Kingdom"
$SourceScene = Join-Path $SourceProjectPath ($scene -replace '/', '\')
if (-not (Test-Path -LiteralPath $SourceScene -PathType Leaf)) {
    Fail-Authoring "scene authoring may only modify an existing scene; '$scene' does not exist in the source workspace" 82
}

Assert-UnityHostIdle
New-Item -ItemType Directory -Force -Path $StageProject | Out-Null
foreach ($directory in @("Assets", "Packages", "ProjectSettings")) {
    Invoke-ProjectMirror -Source (Join-Path $SourceProjectPath $directory) -Destination (Join-Path $StageProject $directory)
}

$StageScene = Join-Path $StageProject ($scene -replace '/', '\')
if (-not (Test-Path -LiteralPath $StageScene -PathType Leaf)) {
    Fail-Authoring "staged scene '$scene' does not exist after mirroring" 82
}

$StageOutput = Join-Path (Join-Path $StageProject ".symphony-authoring") $RequestId
$SourceOutput = Join-Path (Join-Path $SourceProjectPath "Logs\SymphonyUnityAuthoring") $RequestId
New-Item -ItemType Directory -Force -Path $StageOutput | Out-Null
New-Item -ItemType Directory -Force -Path $SourceOutput | Out-Null
$StageRequest = Join-Path $StageOutput "request.json"
$StageResult = Join-Path $StageOutput "result.json"
$StageLog = Join-Path $StageOutput "Editor.log"
$SourceResult = Join-Path $SourceOutput "result.json"
$SourceLog = Join-Path $SourceOutput "Editor.log"

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$authoringJson = $authoring | ConvertTo-Json -Depth 100 -Compress
[System.IO.File]::WriteAllText($StageRequest, $authoringJson, $utf8NoBom)

$unityArgs = @(
    "-batchmode",
    "-accept-apiupdate",
    "-quit",
    "-projectPath", $StageProject,
    "-executeMethod", "RPGKingdom.Editor.SymphonyMechanicalSceneAuthoring.ApplyFromCommandLine",
    "-rpgkAuthoringRequest", $StageRequest,
    "-rpgkAuthoringResult", $StageResult,
    "-logFile", $StageLog
)

$process = Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -PassThru
$deadline = (Get-Date).AddSeconds([Math]::Max(30, $AuthorTimeoutSeconds))
while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
}
if (-not $process.HasExited) {
    try { Stop-Process -Id $process.Id -Force -ErrorAction Stop } catch { }
    try { $process.WaitForExit() } catch { }
    if (Test-Path -LiteralPath $StageLog -PathType Leaf) {
        Copy-Item -LiteralPath $StageLog -Destination $SourceLog -Force
    }
    Fail-Authoring "Unity authoring exceeded ${AuthorTimeoutSeconds}s and request-owned Unity PID $($process.Id) was stopped" 91
}
$process.WaitForExit()
$unityExitCode = $process.ExitCode
if (Test-Path -LiteralPath $StageLog -PathType Leaf) {
    Copy-Item -LiteralPath $StageLog -Destination $SourceLog -Force
}
if (-not (Test-Path -LiteralPath $StageResult -PathType Leaf)) {
    Fail-Authoring "Unity exited with code $unityExitCode without producing structured authoring result; inspect '$SourceLog'" 87
}
Copy-Item -LiteralPath $StageResult -Destination $SourceResult -Force

try {
    $result = Get-Content -LiteralPath $StageResult -Raw | ConvertFrom-Json
}
catch {
    Fail-Authoring "structured authoring result could not be parsed: $($_.Exception.Message)" 88
}
if ($unityExitCode -ne 0 -or $result.success -ne $true) {
    $errorText = [string]$result.error
    Fail-Authoring "Unity authoring failed (Unity exit $unityExitCode): $errorText" 1
}
if ([string]$result.scene -ne $scene) {
    Fail-Authoring "executor returned scene '$($result.scene)' but request authorized '$scene'" 92
}
$changedAssets = @($result.changedAssets)
if ($changedAssets.Count -ne 1 -or [string]$changedAssets[0] -ne $scene) {
    Fail-Authoring "executor changed-assets evidence does not exactly match the one authorized scene '$scene'" 92
}
if (-not (Test-Path -LiteralPath $StageScene -PathType Leaf)) {
    Fail-Authoring "executor reported success but the staged scene is missing" 92
}

# Source mutation happens only after every staged Unity/editor/result check succeeds. Copy to a
# sibling temporary file and atomically replace the one authorized source asset.
$SourceTemp = "$SourceScene.symphony-authoring.tmp.$PID"
try {
    Copy-Item -LiteralPath $StageScene -Destination $SourceTemp -Force
    Move-Item -LiteralPath $SourceTemp -Destination $SourceScene -Force
}
finally {
    Remove-Item -LiteralPath $SourceTemp -Force -ErrorAction SilentlyContinue
}

$result | ConvertTo-Json -Depth 100 -Compress | Write-Output
exit 0
