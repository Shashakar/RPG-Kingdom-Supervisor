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
    [int]$AuthorTimeoutSeconds = 600,
    [string]$AuthorizedSourceScene = "",
    [ValidateSet("", "initial", "iterative")]
    [string]$CompositionMode = ""
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

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Assert-ScenePath {
    param([string]$Value, [string]$FieldName)
    if ([string]::IsNullOrWhiteSpace($Value) -or -not $Value.StartsWith("Assets/") -or -not $Value.EndsWith(".unity") -or $Value.Contains("..") -or $Value.Contains("\")) {
        Fail-Authoring "$FieldName must be a project-relative Assets/*.unity path" 64
    }
}

function Assert-UnityHostIdle {
    $unityProcesses = @(Get-Process -Name "Unity" -ErrorAction SilentlyContinue)
    if ($unityProcesses.Count -gt 0) {
        $ids = ($unityProcesses | Sort-Object Id | ForEach-Object { $_.Id }) -join ", "
        Fail-Authoring "Unity Editor host is busy (Unity.exe PID(s): $ids)" 89
    }
}

$GeneratedAssetRoot = "Assets/RPGKingdom/Navigation/Generated/"

function Assert-GeneratedAssetPath {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or
        -not $Value.StartsWith($GeneratedAssetRoot, [System.StringComparison]::Ordinal) -or
        $Value.Contains("..") -or
        $Value.Contains("\") -or
        $Value.EndsWith(".unity", [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail-Authoring "generated asset '$Value' is outside the reviewed navigation generated-asset root" 92
    }
}

function Publish-AssetsAtomically {
    param([Parameter(Mandatory = $true)][string[]]$AssetPaths)

    $entries = @()
    $index = 0
    foreach ($assetPath in $AssetPaths) {
        $stagePath = Join-Path $StageProject ($assetPath -replace '/', '\\')
        if (-not (Test-Path -LiteralPath $stagePath -PathType Leaf)) {
            Fail-Authoring "executor reported changed asset '$assetPath' but the staged file is missing" 92
        }

        $destination = Join-Path $SourceProjectPath ($assetPath -replace '/', '\\')
        $destinationDirectory = Split-Path -Parent $destination
        New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null

        $temp = "$destination.symphony-authoring.tmp.$PID.$index"
        $backup = "$destination.symphony-authoring.bak.$PID.$index"
        $existed = Test-Path -LiteralPath $destination -PathType Leaf
        Copy-Item -LiteralPath $stagePath -Destination $temp -Force
        if ($existed) {
            Copy-Item -LiteralPath $destination -Destination $backup -Force
        }

        $entries += [PSCustomObject]@{
            AssetPath = $assetPath
            Destination = $destination
            Temp = $temp
            Backup = $backup
            Existed = $existed
            Published = $false
        }
        $index++
    }

    try {
        foreach ($entry in $entries) {
            Move-Item -LiteralPath $entry.Temp -Destination $entry.Destination -Force
            $entry.Published = $true
        }
    }
    catch {
        $published = @($entries | Where-Object { $_.Published })
        [array]::Reverse($published)
        foreach ($entry in $published) {
            if ($entry.Existed -and (Test-Path -LiteralPath $entry.Backup -PathType Leaf)) {
                Move-Item -LiteralPath $entry.Backup -Destination $entry.Destination -Force
            }
            else {
                Remove-Item -LiteralPath $entry.Destination -Force -ErrorAction SilentlyContinue
            }
        }
        throw
    }
    finally {
        foreach ($entry in $entries) {
            Remove-Item -LiteralPath $entry.Temp -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $entry.Backup -Force -ErrorAction SilentlyContinue
        }
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
if ($tier -ne "mechanical" -and $tier -ne "mechanical-structural" -and $tier -ne "new-scene-composition") {
    Fail-Authoring "unsupported scene-authoring tier '$tier'" 64
}
$scene = [string]$authoring.scene
Assert-ScenePath -Value $scene -FieldName "scene"
if ($null -eq $authoring.operations -or @($authoring.operations).Count -eq 0) {
    Fail-Authoring "authoring request has no operations" 64
}

$IsNewSceneComposition = $tier -eq "new-scene-composition"
if ($IsNewSceneComposition) {
    if ($CompositionMode -ne "initial" -and $CompositionMode -ne "iterative") {
        Fail-Authoring "new-scene composition requires an authorized composition mode" 83
    }
    Assert-ScenePath -Value $AuthorizedSourceScene -FieldName "authorized source scene"
    if ($AuthorizedSourceScene -eq $scene) {
        Fail-Authoring "source and target scenes must differ" 64
    }
    $RequestSourceScene = [string]$authoring.sourceScene
    if ($CompositionMode -eq "initial") {
        if ($RequestSourceScene -ne $AuthorizedSourceScene) {
            Fail-Authoring "initial request sourceScene does not match host-authorized source scene" 83
        }
    }
    elseif (-not [string]::IsNullOrWhiteSpace($RequestSourceScene)) {
        Fail-Authoring "iterative new-scene requests must not declare sourceScene" 64
    }
}
elseif (-not [string]::IsNullOrWhiteSpace([string]$authoring.sourceScene)) {
    Fail-Authoring "sourceScene is only valid for new-scene-composition" 64
}

$StageProject = Join-Path (Join-Path $StageRoot $UnityVersion) "RPG-Kingdom"
$WorkspaceTargetScene = Join-Path $SourceProjectPath ($scene -replace '/', '\')
$WorkspaceTargetMeta = "$WorkspaceTargetScene.meta"
if ($IsNewSceneComposition) {
    $WorkspaceSourceScene = Join-Path $SourceProjectPath ($AuthorizedSourceScene -replace '/', '\')
    $WorkspaceSourceMeta = "$WorkspaceSourceScene.meta"
    if (-not (Test-Path -LiteralPath $WorkspaceSourceScene -PathType Leaf)) {
        Fail-Authoring "authorized source scene '$AuthorizedSourceScene' does not exist in the source workspace" 82
    }
    if ($CompositionMode -eq "initial") {
        if ((Test-Path -LiteralPath $WorkspaceTargetScene) -or (Test-Path -LiteralPath $WorkspaceTargetMeta)) {
            Fail-Authoring "initial target '$scene' already exists in the source workspace" 83
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $WorkspaceTargetScene -PathType Leaf) -or -not (Test-Path -LiteralPath $WorkspaceTargetMeta -PathType Leaf)) {
            Fail-Authoring "iterative target scene or meta is missing in the source workspace" 82
        }
    }
    $WorkspaceSourceHashBefore = Get-Sha256 $WorkspaceSourceScene
    $WorkspaceSourceMetaHashBefore = Get-Sha256 $WorkspaceSourceMeta
}
else {
    if (-not (Test-Path -LiteralPath $WorkspaceTargetScene -PathType Leaf)) {
        Fail-Authoring "scene authoring may only modify an existing scene; '$scene' does not exist in the source workspace" 82
    }
}

Assert-UnityHostIdle
New-Item -ItemType Directory -Force -Path $StageProject | Out-Null
foreach ($directory in @("Assets", "Packages", "ProjectSettings")) {
    Invoke-ProjectMirror -Source (Join-Path $SourceProjectPath $directory) -Destination (Join-Path $StageProject $directory)
}

$StageScene = Join-Path $StageProject ($scene -replace '/', '\')
$StageSceneMeta = "$StageScene.meta"
if ($IsNewSceneComposition) {
    $StageSourceScene = Join-Path $StageProject ($AuthorizedSourceScene -replace '/', '\')
    $StageSourceMeta = "$StageSourceScene.meta"
    if (-not (Test-Path -LiteralPath $StageSourceScene -PathType Leaf)) {
        Fail-Authoring "staged source scene '$AuthorizedSourceScene' does not exist after mirroring" 82
    }
    $StageSourceHashBefore = Get-Sha256 $StageSourceScene
    $StageSourceMetaHashBefore = Get-Sha256 $StageSourceMeta
    if ($CompositionMode -eq "initial") {
        if ((Test-Path -LiteralPath $StageScene) -or (Test-Path -LiteralPath $StageSceneMeta)) {
            Fail-Authoring "initial staged target '$scene' unexpectedly exists before execution" 83
        }
    }
    elseif (-not (Test-Path -LiteralPath $StageScene -PathType Leaf) -or -not (Test-Path -LiteralPath $StageSceneMeta -PathType Leaf)) {
        Fail-Authoring "iterative staged target scene or meta does not exist after mirroring" 82
    }
}
elseif (-not (Test-Path -LiteralPath $StageScene -PathType Leaf)) {
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
if (-not (Test-Path -LiteralPath $StageScene -PathType Leaf)) {
    Fail-Authoring "executor reported success but the staged target scene is missing" 92
}

$changedAssets = @($result.changedAssets | ForEach-Object { [string]$_ })
$generatedNavigationAssets = @($result.generatedNavigationAssets | ForEach-Object { [string]$_ })
if ($generatedNavigationAssets.Count -ne @($generatedNavigationAssets | Select-Object -Unique).Count) {
    Fail-Authoring "executor generated-navigation-assets evidence contains duplicate paths" 92
}
$generatedCopyBackAssets = @()
foreach ($generatedAsset in $generatedNavigationAssets) {
    Assert-GeneratedAssetPath -Value $generatedAsset
    $generatedMeta = "$generatedAsset.meta"
    Assert-GeneratedAssetPath -Value $generatedMeta
    $generatedCopyBackAssets += @($generatedAsset, $generatedMeta)
}

if ($IsNewSceneComposition) {
    if ((Get-Sha256 $StageSourceScene) -ne $StageSourceHashBefore -or (Get-Sha256 $StageSourceMeta) -ne $StageSourceMetaHashBefore) {
        Fail-Authoring "source scene or metadata changed in the Unity stage" 92
    }
    if ((Get-Sha256 $WorkspaceSourceScene) -ne $WorkspaceSourceHashBefore -or (Get-Sha256 $WorkspaceSourceMeta) -ne $WorkspaceSourceMetaHashBefore) {
        Fail-Authoring "source scene or metadata changed in the source workspace during authoring" 92
    }

    $baseExpected = if ($CompositionMode -eq "initial") { @($scene, "$scene.meta") } else { @($scene) }
    $expected = @($baseExpected + $generatedCopyBackAssets | Sort-Object)
    $actual = @($changedAssets | Sort-Object)
    if ($actual.Count -ne $expected.Count -or (Compare-Object -ReferenceObject $expected -DifferenceObject $actual).Count -ne 0) {
        Fail-Authoring "new-scene executor changed-assets evidence must exactly match the target scene assets plus executor-attested generated navigation assets" 92
    }

    if ($CompositionMode -eq "initial") {
        if ([string]$result.sourceScene -ne $AuthorizedSourceScene -or $result.sourceUnchanged -ne $true) {
            Fail-Authoring "executor did not attest the authorized source scene remained unchanged" 92
        }
        if (-not (Test-Path -LiteralPath $StageSceneMeta -PathType Leaf)) {
            Fail-Authoring "executor reported initial success but the staged target meta is missing" 92
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $WorkspaceTargetMeta -PathType Leaf)) {
            Fail-Authoring "iterative target metadata disappeared before copy-back" 92
        }
    }

    $copyBackAssets = @($baseExpected + $generatedCopyBackAssets)
    Publish-AssetsAtomically -AssetPaths $copyBackAssets
    $result | Add-Member -NotePropertyName copiedBackAssets -NotePropertyValue $copyBackAssets -Force
    $result | Add-Member -NotePropertyName compositionMode -NotePropertyValue $CompositionMode -Force
    $result | Add-Member -NotePropertyName sourceHashBefore -NotePropertyValue $StageSourceHashBefore -Force
    $result | Add-Member -NotePropertyName sourceHashAfter -NotePropertyValue (Get-Sha256 $StageSourceScene) -Force
}
else {
    if ($generatedNavigationAssets.Count -ne 0) {
        Fail-Authoring "generated navigation assets are only supported for new-scene composition" 92
    }
    if ($changedAssets.Count -ne 1 -or $changedAssets[0] -ne $scene) {
        Fail-Authoring "executor changed-assets evidence does not exactly match the one authorized scene '$scene'" 92
    }
    Publish-AssetsAtomically -AssetPaths @($scene)
}

$result | Add-Member -NotePropertyName tier -NotePropertyValue $tier -Force
$finalJson = $result | ConvertTo-Json -Depth 100 -Compress
[System.IO.File]::WriteAllText($SourceResult, $finalJson, $utf8NoBom)
$finalJson | Write-Output
exit 0
