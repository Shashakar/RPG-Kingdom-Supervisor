param(
    [Parameter(Mandatory = $true)]
    [string]$SourceProjectPath,

    [Parameter(Mandatory = $true)]
    [string]$UnityVersion,

    [string]$UnityPath = "",
    [string]$StageRoot = "",

    [ValidateSet("EditMode", "PlayMode")]
    [string]$TestPlatform = "EditMode",

    [string]$TestFilter = "",
    [string]$RunId = "",
    [switch]$HealthOnly
)

$ErrorActionPreference = "Stop"

function Fail-Runner {
    param(
        [string]$Message,
        [int]$Code
    )

    [Console]::Error.WriteLine("RPG Kingdom Unity runner: $Message")
    exit $Code
}

function Assert-UnityHostIdle {
    $unityProcesses = @(Get-Process -Name "Unity" -ErrorAction SilentlyContinue)
    if ($unityProcesses.Count -eq 0) {
        return
    }

    $processIds = ($unityProcesses | Sort-Object Id | ForEach-Object { $_.Id }) -join ", "
    Fail-Runner "Unity Editor host is busy (Unity.exe PID(s): $processIds). resource:unity-editor requires exclusive host access; close all Unity Editor instances and retry." 89
}

function Invoke-ProjectMirror {
    param(
        [string]$Source,
        [string]$Destination
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    & robocopy.exe $Source $Destination /MIR /FFT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Fail-Runner "robocopy failed for '$Source' -> '$Destination' with exit code $code" 84
    }
}

if ([string]::IsNullOrWhiteSpace($UnityPath)) {
    $UnityPath = Join-Path ${env:ProgramFiles} "Unity\Hub\Editor\$UnityVersion\Editor\Unity.exe"
}

if ([string]::IsNullOrWhiteSpace($StageRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Fail-Runner "LOCALAPPDATA is unavailable" 80
    }
    $StageRoot = Join-Path $env:LOCALAPPDATA "RPGKingdomSupervisor\UnityStages"
}

$StageProject = Join-Path (Join-Path $StageRoot $UnityVersion) "RPG-Kingdom"

if (-not (Test-Path -LiteralPath $UnityPath -PathType Leaf)) {
    Fail-Runner "Unity $UnityVersion was not found at '$UnityPath'" 81
}

foreach ($required in @("Assets", "Packages", "ProjectSettings")) {
    $path = Join-Path $SourceProjectPath $required
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        Fail-Runner "source project is missing '$required' at '$path'" 82
    }
}

if (-not (Get-Command robocopy.exe -ErrorAction SilentlyContinue)) {
    Fail-Runner "robocopy.exe is unavailable" 83
}

try {
    New-Item -ItemType Directory -Force -Path $StageProject | Out-Null
}
catch {
    Fail-Runner "cannot create or write staging project '$StageProject': $($_.Exception.Message)" 85
}

# Phase 3's resource:unity-editor contract is host-wide, not merely stage-project-wide.
# A human Editor or another Unity process means Symphony does not own the resource.
Assert-UnityHostIdle

if ($HealthOnly) {
    [ordered]@{
        status = "ready"
        unityVersion = $UnityVersion
        unityPath = $UnityPath
        sourceProject = $SourceProjectPath
        stageProject = $StageProject
    } | ConvertTo-Json -Compress | Write-Output
    exit 0
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
    Fail-Runner "RunId is required for a test run" 86
}

foreach ($directory in @("Assets", "Packages", "ProjectSettings")) {
    $sourceDirectory = Join-Path $SourceProjectPath $directory
    $destinationDirectory = Join-Path $StageProject $directory
    Invoke-ProjectMirror -Source $sourceDirectory -Destination $destinationDirectory
}

$StageOutput = Join-Path (Join-Path $StageProject ".symphony-results") $RunId
$SourceOutput = Join-Path (Join-Path $SourceProjectPath "Logs\SymphonyUnity") $RunId
New-Item -ItemType Directory -Force -Path $StageOutput | Out-Null
New-Item -ItemType Directory -Force -Path $SourceOutput | Out-Null

$ResultsPath = Join-Path $StageOutput "results.xml"
$LogPath = Join-Path $StageOutput "Editor.log"
$SummaryPath = Join-Path $StageOutput "summary.json"

$unityArgs = @(
    "-batchmode",
    "-accept-apiupdate",
    "-projectPath", $StageProject,
    "-runTests",
    "-testPlatform", $TestPlatform,
    "-testResults", $ResultsPath,
    "-logFile", $LogPath
)

if (-not [string]::IsNullOrWhiteSpace($TestFilter)) {
    $unityArgs += @("-testFilter", $TestFilter)
}

$launchStartedAt = Get-Date
$unityProcess = Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -Wait -PassThru
$unityExitCode = $unityProcess.ExitCode

if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
    Copy-Item -LiteralPath $LogPath -Destination (Join-Path $SourceOutput "Editor.log") -Force
}
else {
    # Some early Unity startup crashes happen before the requested -logFile is created.
    # Preserve only a bounded tail of the global Editor.log when it was touched by this
    # run so Codex gets useful diagnostics without ingesting a multi-megabyte log.
    $defaultEditorLog = Join-Path $env:LOCALAPPDATA "Unity\Editor\Editor.log"
    if (Test-Path -LiteralPath $defaultEditorLog -PathType Leaf) {
        $defaultEditorLogInfo = Get-Item -LiteralPath $defaultEditorLog
        if ($defaultEditorLogInfo.LastWriteTime -ge $launchStartedAt.AddSeconds(-2)) {
            Get-Content -LiteralPath $defaultEditorLog -Tail 4000 |
                Set-Content -LiteralPath (Join-Path $SourceOutput "Editor.log") -Encoding UTF8
        }
    }
}

if (-not (Test-Path -LiteralPath $ResultsPath -PathType Leaf)) {
    $copiedLogPath = Join-Path $SourceOutput "Editor.log"
    if (Test-Path -LiteralPath $copiedLogPath -PathType Leaf) {
        Fail-Runner "Unity exited with code $unityExitCode without producing test results. Inspect '$copiedLogPath'." 87
    }
    Fail-Runner "Unity exited with code $unityExitCode without producing test results or an Editor log." 87
}

Copy-Item -LiteralPath $ResultsPath -Destination (Join-Path $SourceOutput "results.xml") -Force

try {
    [xml]$resultsXml = Get-Content -LiteralPath $ResultsPath -Raw
    $testRun = $resultsXml.'test-run'
    if ($null -eq $testRun) {
        throw "results XML does not contain a test-run root"
    }

    $total = [int]$testRun.total
    $passed = [int]$testRun.passed
    $failed = [int]$testRun.failed
    $skipped = [int]$testRun.skipped
    $result = [string]$testRun.result
}
catch {
    Fail-Runner "could not parse Unity test results: $($_.Exception.Message)" 88
}

$summary = [ordered]@{
    unityVersion = $UnityVersion
    testPlatform = $TestPlatform
    testFilter = $TestFilter
    result = $result
    total = $total
    passed = $passed
    failed = $failed
    skipped = $skipped
    unityExitCode = $unityExitCode
    runId = $RunId
    artifactPath = $SourceOutput
    stageProject = $StageProject
}

$summaryJson = $summary | ConvertTo-Json -Compress
Set-Content -LiteralPath $SummaryPath -Value $summaryJson -Encoding UTF8
Copy-Item -LiteralPath $SummaryPath -Destination (Join-Path $SourceOutput "summary.json") -Force
$summaryJson | Write-Output

if ($unityExitCode -ne 0 -or $failed -gt 0 -or $total -le 0 -or ($result -ne "Passed" -and $result -ne "Success")) {
    exit 1
}

exit 0
