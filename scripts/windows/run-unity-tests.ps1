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
    [string]$RequestId = "",
    [string]$ProgressPath = "",
    [string]$CancelPath = "",
    [switch]$HealthOnly
)

$ErrorActionPreference = "Stop"
$script:ProgressSequence = 0

function Fail-Runner {
    param(
        [string]$Message,
        [int]$Code
    )

    [Console]::Error.WriteLine("RPG Kingdom Unity runner: $Message")
    exit $Code
}

function Write-ProgressState {
    param(
        [string]$Phase,
        [int]$UnityPid = 0,
        [long]$EditorLogBytes = -1,
        [long]$ResultsBytes = -1,
        [bool]$SummaryPresent = $false
    )

    if ([string]::IsNullOrWhiteSpace($ProgressPath)) {
        return
    }
    if ([string]::IsNullOrWhiteSpace($RequestId)) {
        Fail-Runner "ProgressPath requires RequestId" 86
    }

    $script:ProgressSequence += 1
    $progressDirectory = Split-Path -Parent $ProgressPath
    if (-not [string]::IsNullOrWhiteSpace($progressDirectory)) {
        New-Item -ItemType Directory -Force -Path $progressDirectory | Out-Null
    }

    $payload = [ordered]@{
        protocolVersion = 1
        requestId = $RequestId
        sequence = $script:ProgressSequence
        phase = $Phase
        observedAt = (Get-Date).ToUniversalTime().ToString("o")
        unityPid = if ($UnityPid -gt 0) { $UnityPid } else { $null }
        editorLogBytes = if ($EditorLogBytes -ge 0) { $EditorLogBytes } else { $null }
        resultsBytes = if ($ResultsBytes -ge 0) { $ResultsBytes } else { $null }
        summaryPresent = $SummaryPresent
    }
    $json = $payload | ConvertTo-Json -Compress
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $tempPath = "$ProgressPath.tmp.$PID"
    [System.IO.File]::WriteAllText($tempPath, $json, $utf8NoBom)
    Move-Item -LiteralPath $tempPath -Destination $ProgressPath -Force
}

function Get-UnityProgressPhase {
    param([string]$LogPath)

    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return "unity_startup"
    }

    try {
        $tail = (Get-Content -LiteralPath $LogPath -Tail 120 -ErrorAction Stop | Out-String).ToLowerInvariant()
    }
    catch {
        return "unity_running"
    }

    if ($tail -match "testrunner|test runner|running tests|run tests|test run") {
        return "tests_running"
    }
    if ($tail -match "scriptcompilation|compil|bee_backend|assembly updater") {
        return "compiling"
    }
    if ($tail -match "importing|asset import|refreshing native plugins|domain reload") {
        return "importing"
    }
    return "unity_running"
}

function Read-CancelRequest {
    if ([string]::IsNullOrWhiteSpace($CancelPath) -or -not (Test-Path -LiteralPath $CancelPath -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $CancelPath -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
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
    Write-ProgressState -Phase "health_ready"
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

Write-ProgressState -Phase "staging"
foreach ($directory in @("Assets", "Packages", "ProjectSettings")) {
    $sourceDirectory = Join-Path $SourceProjectPath $directory
    $destinationDirectory = Join-Path $StageProject $directory
    Invoke-ProjectMirror -Source $sourceDirectory -Destination $destinationDirectory
    Write-ProgressState -Phase ("staging_" + $directory.ToLowerInvariant())
}

$StageOutput = Join-Path (Join-Path $StageProject ".symphony-results") $RunId
$SourceOutput = Join-Path (Join-Path $SourceProjectPath "Logs\SymphonyUnity") $RunId
New-Item -ItemType Directory -Force -Path $StageOutput | Out-Null
New-Item -ItemType Directory -Force -Path $SourceOutput | Out-Null

$ResultsPath = Join-Path $StageOutput "results.xml"
$LogPath = Join-Path $StageOutput "Editor.log"
$SummaryPath = Join-Path $StageOutput "summary.json"
$SourceLogPath = Join-Path $SourceOutput "Editor.log"

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
Write-ProgressState -Phase "unity_startup"
$unityProcess = Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -PassThru
Write-ProgressState -Phase "unity_running" -UnityPid $unityProcess.Id

$cancelled = $false
$lastLogLength = -1L
$lastLogWrite = [datetime]::MinValue
$lastResultsLength = -1L

while (-not $unityProcess.HasExited) {
    $cancel = Read-CancelRequest
    if ($null -ne $cancel -and [string]$cancel.requestId -eq $RequestId) {
        Write-ProgressState -Phase "recovery_cancel_requested" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -ResultsBytes $lastResultsLength
        try {
            Stop-Process -Id $unityProcess.Id -Force -ErrorAction Stop
            $unityProcess.WaitForExit()
            $cancelled = $true
            Write-ProgressState -Phase "recovery_cancelled" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -ResultsBytes $lastResultsLength
            break
        }
        catch {
            # Do not broaden cleanup to other Unity processes. The broker will fail closed if
            # this request-owned PID cannot be stopped within its recovery grace period.
            Write-ProgressState -Phase "recovery_cancel_failed" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -ResultsBytes $lastResultsLength
        }
    }

    $progressChanged = $false
    $phase = "unity_running"
    $logLength = -1L
    $resultsLength = -1L

    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        try {
            $logInfo = Get-Item -LiteralPath $LogPath
            $logLength = [long]$logInfo.Length
            if ($logLength -ne $lastLogLength -or $logInfo.LastWriteTimeUtc -ne $lastLogWrite) {
                $lastLogLength = $logLength
                $lastLogWrite = $logInfo.LastWriteTimeUtc
                $progressChanged = $true
                $phase = Get-UnityProgressPhase -LogPath $LogPath
                try {
                    Copy-Item -LiteralPath $LogPath -Destination $SourceLogPath -Force -ErrorAction Stop
                }
                catch {
                    # Progress detection must not fail the validation merely because a live log
                    # cannot be copied during one poll. Final artifact copy is still authoritative.
                }
            }
        }
        catch {
            # Treat inability to stat the live log as no new progress for this poll.
        }
    }

    if (Test-Path -LiteralPath $ResultsPath -PathType Leaf) {
        try {
            $resultsInfo = Get-Item -LiteralPath $ResultsPath
            $resultsLength = [long]$resultsInfo.Length
            if ($resultsLength -ne $lastResultsLength) {
                $lastResultsLength = $resultsLength
                $progressChanged = $true
                $phase = "test_results"
            }
        }
        catch {
        }
    }

    if ($progressChanged) {
        Write-ProgressState -Phase $phase -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -ResultsBytes $lastResultsLength
    }

    Start-Sleep -Seconds 2
    $unityProcess.Refresh()
}

if (-not $cancelled) {
    $unityProcess.WaitForExit()
}
$unityExitCode = $unityProcess.ExitCode
Write-ProgressState -Phase "artifact_finalization" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -ResultsBytes $lastResultsLength

if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
    Copy-Item -LiteralPath $LogPath -Destination $SourceLogPath -Force
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
                Set-Content -LiteralPath $SourceLogPath -Encoding UTF8
        }
    }
}

if ($cancelled) {
    Fail-Runner "request-owned Unity PID $($unityProcess.Id) was cancelled after Supervisor detected a validation stall. Inspect '$SourceLogPath' when present." 91
}

if (-not (Test-Path -LiteralPath $ResultsPath -PathType Leaf)) {
    if (Test-Path -LiteralPath $SourceLogPath -PathType Leaf) {
        Fail-Runner "Unity exited with code $unityExitCode without producing test results. Inspect '$SourceLogPath'." 87
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
    if ($total -le 0) {
        $result = "NoTestsMatched"
    }
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
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($SummaryPath, $summaryJson, $utf8NoBom)
Copy-Item -LiteralPath $SummaryPath -Destination (Join-Path $SourceOutput "summary.json") -Force
Write-ProgressState -Phase "completed" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -ResultsBytes ((Get-Item -LiteralPath $ResultsPath).Length) -SummaryPresent $true
$summaryJson | Write-Output

if ($unityExitCode -ne 0 -or $failed -gt 0 -or $total -le 0 -or ($result -ne "Passed" -and $result -ne "Success")) {
    exit 1
}

exit 0
