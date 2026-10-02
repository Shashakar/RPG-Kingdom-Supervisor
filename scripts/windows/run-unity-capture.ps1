param(
    [Parameter(Mandatory = $true)][string]$SourceProjectPath,
    [Parameter(Mandatory = $true)][string]$UnityVersion,
    [string]$UnityPath = "",
    [string]$StageRoot = "",
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$ScenePath,
    [string]$CameraPath = "",
    [string]$ViewName = "",
    [string]$CameraPosition = "",
    [string]$CameraRotation = "",
    [string]$LookAtPath = "",
    [ValidateRange(1.0, 179.0)][double]$FieldOfView = 60.0,
    [switch]$ReuseStageLibrary,
    [ValidateRange(320, 4096)][int]$Width = 1920,
    [ValidateRange(180, 4096)][int]$Height = 1080,
    [string]$RequestId = "",
    [string]$ProgressPath = "",
    [string]$CancelPath = ""
)

$ErrorActionPreference = "Stop"
$script:ProgressSequence = 0

function Fail-Runner {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine("RPG Kingdom Unity capture: $Message")
    exit $Code
}

function Write-ProgressState {
    param([string]$Phase, [int]$UnityPid = 0, [long]$EditorLogBytes = -1, [bool]$SummaryPresent = $false)
    if ([string]::IsNullOrWhiteSpace($ProgressPath)) { return }
    if ([string]::IsNullOrWhiteSpace($RequestId)) { Fail-Runner "ProgressPath requires RequestId" 86 }

    $script:ProgressSequence += 1
    $directory = Split-Path -Parent $ProgressPath
    if (-not [string]::IsNullOrWhiteSpace($directory)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    $payload = [ordered]@{
        protocolVersion = 1
        requestId = $RequestId
        sequence = $script:ProgressSequence
        phase = $Phase
        observedAt = (Get-Date).ToUniversalTime().ToString("o")
        unityPid = if ($UnityPid -gt 0) { $UnityPid } else { $null }
        editorLogBytes = if ($EditorLogBytes -ge 0) { $EditorLogBytes } else { $null }
        resultsBytes = $null
        summaryPresent = $SummaryPresent
    }
    $json = $payload | ConvertTo-Json -Compress
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $temp = "$ProgressPath.tmp.$PID"
    [System.IO.File]::WriteAllText($temp, $json, $utf8)
    Move-Item -LiteralPath $temp -Destination $ProgressPath -Force
}

function Read-CancelRequest {
    if ([string]::IsNullOrWhiteSpace($CancelPath) -or -not (Test-Path -LiteralPath $CancelPath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $CancelPath -Raw | ConvertFrom-Json } catch { return $null }
}

function Assert-UnityHostIdle {
    $processes = @(Get-Process -Name "Unity" -ErrorAction SilentlyContinue)
    if ($processes.Count -eq 0) { return }
    $ids = ($processes | Sort-Object Id | ForEach-Object { $_.Id }) -join ", "
    Fail-Runner "Unity Editor host is busy (Unity.exe PID(s): $ids)." 89
}

function Invoke-ProjectMirror {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /MIR /FFT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail-Runner "robocopy failed for '$Source' -> '$Destination' with exit code $LASTEXITCODE" 84 }
}

if ([string]::IsNullOrWhiteSpace($UnityPath)) {
    $UnityPath = Join-Path ${env:ProgramFiles} "Unity\Hub\Editor\$UnityVersion\Editor\Unity.exe"
}
if ([string]::IsNullOrWhiteSpace($StageRoot)) {
    if ([string]::IsNullOrWhiteSpace(${env:LOCALAPPDATA})) { Fail-Runner "LOCALAPPDATA is unavailable" 80 }
    $StageRoot = Join-Path ${env:LOCALAPPDATA} "RPGKingdomSupervisor\UnityStages"
}
if ($ScenePath -notmatch '^Assets/.+\.unity$' -or $ScenePath.Contains("..")) { Fail-Runner "ScenePath must be a normalized Assets/*.unity path" 64 }

$sourceScene = Join-Path $SourceProjectPath ($ScenePath -replace '/', '\')
if (-not (Test-Path -LiteralPath $sourceScene -PathType Leaf)) { Fail-Runner "scene '$ScenePath' does not exist in the source workspace" 82 }

$StageProject = Join-Path (Join-Path $StageRoot $UnityVersion) "RPG-Kingdom"
if (-not (Test-Path -LiteralPath $UnityPath -PathType Leaf)) { Fail-Runner "Unity $UnityVersion was not found at '$UnityPath'" 81 }
if (-not (Get-Command robocopy.exe -ErrorAction SilentlyContinue)) { Fail-Runner "robocopy.exe is unavailable" 83 }

foreach ($required in @("Assets", "Packages", "ProjectSettings")) {
    $path = Join-Path $SourceProjectPath $required
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { Fail-Runner "source project is missing '$required' at '$path'" 82 }
}

Assert-UnityHostIdle
New-Item -ItemType Directory -Force -Path $StageProject | Out-Null
$stageLibrary = Join-Path $StageProject "Library"
if (-not $ReuseStageLibrary -and (Test-Path -LiteralPath $stageLibrary -PathType Container)) {
    Write-ProgressState -Phase "cleaning_capture_library"
    Remove-Item -LiteralPath $stageLibrary -Recurse -Force
}
Write-ProgressState -Phase "staging"
foreach ($directory in @("Assets", "Packages", "ProjectSettings")) {
    Invoke-ProjectMirror -Source (Join-Path $SourceProjectPath $directory) -Destination (Join-Path $StageProject $directory)
    Write-ProgressState -Phase ("staging_" + $directory.ToLowerInvariant())
}

$StageOutput = Join-Path (Join-Path $StageProject ".symphony-results") $RunId
$SourceOutput = Join-Path (Join-Path $SourceProjectPath "Logs\SymphonyUnity") $RunId
New-Item -ItemType Directory -Force -Path $StageOutput | Out-Null
New-Item -ItemType Directory -Force -Path $SourceOutput | Out-Null
$PngPath = Join-Path $StageOutput "scene.png"
$ManifestPath = Join-Path $StageOutput "manifest.json"
$DiagnosticsPath = Join-Path $StageOutput "visual-diagnostics.json"
$ShaderLogPath = Join-Path $StageOutput "shader-log.txt"
$LogPath = Join-Path $StageOutput "Editor.log"
$SourceLogPath = Join-Path $SourceOutput "Editor.log"

$HelperEditor = Join-Path $StageProject "Assets\__SupervisorVisualCapture\Editor"
$HelperScript = Join-Path $HelperEditor "SupervisorVisualCapture.cs"
New-Item -ItemType Directory -Force -Path $HelperEditor | Out-Null

$helperSource = @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

public static class SupervisorVisualCapture
{
    [Serializable]
    private sealed class CaptureManifest
    {
        public string scene;
        public string cameraPath;
        public string viewName;
        public Vector3 cameraPosition;
        public Vector3 cameraEulerAngles;
        public float fieldOfView;
        public int width;
        public int height;
        public string image;
        public string diagnostics;
        public string shaderLog;
        public string capturedAtUtc;
        public string renderPipeline;
        public string renderMethod;
    }

    [Serializable]
    private sealed class MaterialDiagnostic
    {
        public string materialName;
        public string materialAssetPath;
        public string shaderName;
        public string shaderAssetPath;
        public bool shaderMissing;
        public bool shaderSupported;
    }

    [Serializable]
    private sealed class RendererDiagnostic
    {
        public string hierarchyPath;
        public string rendererType;
        public bool enabled;
        public bool activeInHierarchy;
        public bool inCameraFrustum;
        public string prefabSource;
        public MaterialDiagnostic[] materials;
    }

    [Serializable]
    private sealed class CaptureDiagnostics
    {
        public string scene;
        public string cameraPath;
        public string renderPipeline;
        public string renderMethod;
        public string graphicsDeviceType;
        public string graphicsDeviceName;
        public string graphicsDeviceVersion;
        public int graphicsMemorySizeMb;
        public bool batchMode;
        public int rendererCount;
        public int frustumRendererCount;
        public int materialCount;
        public int missingShaderCount;
        public int unsupportedShaderCount;
        public int frustumMaterialCount;
        public int frustumMissingShaderCount;
        public int frustumUnsupportedShaderCount;
        public RendererDiagnostic[] renderers;
    }

    public static void Capture()
    {
        try
        {
            var args = Environment.GetCommandLineArgs();
            string scenePath = GetArg(args, "-rpgkScene");
            string outputPath = GetArg(args, "-rpgkOutput");
            string manifestPath = GetArg(args, "-rpgkManifest");
            string diagnosticsPath = GetArg(args, "-rpgkDiagnostics");
            string requestedCameraPath = GetArg(args, "-rpgkCamera", "");
            string viewName = GetArg(args, "-rpgkViewName", "");
            string requestedPosition = GetArg(args, "-rpgkCameraPosition", "");
            string requestedRotation = GetArg(args, "-rpgkCameraRotation", "");
            string lookAtPath = GetArg(args, "-rpgkLookAt", "");
            float fieldOfView = float.Parse(GetArg(args, "-rpgkFieldOfView", "60"), System.Globalization.CultureInfo.InvariantCulture);
            int width = int.Parse(GetArg(args, "-rpgkWidth"));
            int height = int.Parse(GetArg(args, "-rpgkHeight"));

            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);

            var scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
            Shader.WarmupAllShaders();
            Camera camera = ResolveCamera(scene, requestedCameraPath);
            if (camera == null) throw new InvalidOperationException("No eligible camera was found in the requested scene.");
            ApplyRequestedCameraPose(scene, camera, requestedPosition, requestedRotation, lookAtPath, fieldOfView);

            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            Directory.CreateDirectory(Path.GetDirectoryName(manifestPath));
            Directory.CreateDirectory(Path.GetDirectoryName(diagnosticsPath));

            var previousTarget = camera.targetTexture;
            var previousActive = RenderTexture.active;
            var rt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
            var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
            string renderMethod = "Camera.Render";

            try
            {
                rt.Create();
                var pipeline = GraphicsSettings.currentRenderPipeline;
                if (pipeline != null)
                {
                    var request = new RenderPipeline.StandardRequest { destination = rt };
                    if (RenderPipeline.SupportsRenderRequest(camera, request))
                    {
                        RenderPipeline.SubmitRenderRequest(camera, request);
                        renderMethod = "RenderPipeline.StandardRequest";
                    }
                    else
                    {
                        camera.targetTexture = rt;
                        camera.Render();
                        renderMethod = "Camera.Render fallback (SRP render request unsupported)";
                    }
                }
                else
                {
                    camera.targetTexture = rt;
                    camera.Render();
                }

                RenderTexture.active = rt;
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
                texture.Apply(false, false);
                File.WriteAllBytes(outputPath, texture.EncodeToPNG());
            }
            finally
            {
                camera.targetTexture = previousTarget;
                RenderTexture.active = previousActive;
                UnityEngine.Object.DestroyImmediate(texture);
                rt.Release();
                UnityEngine.Object.DestroyImmediate(rt);
            }

            var diagnostics = BuildDiagnostics(scene, camera, renderMethod);
            File.WriteAllText(diagnosticsPath, JsonUtility.ToJson(diagnostics, true));

            var manifest = new CaptureManifest
            {
                scene = scenePath,
                cameraPath = HierarchyPath(camera.gameObject),
                viewName = viewName,
                cameraPosition = camera.transform.position,
                cameraEulerAngles = camera.transform.eulerAngles,
                fieldOfView = camera.fieldOfView,
                width = width,
                height = height,
                image = Path.GetFileName(outputPath),
                diagnostics = Path.GetFileName(diagnosticsPath),
                shaderLog = "shader-log.txt",
                capturedAtUtc = DateTime.UtcNow.ToString("o"),
                renderPipeline = CurrentPipelineName(),
                renderMethod = renderMethod
            };
            File.WriteAllText(manifestPath, JsonUtility.ToJson(manifest, true));
            Debug.Log("Supervisor visual capture wrote " + outputPath + " using " + renderMethod);
            Debug.Log(
                "Supervisor visual diagnostics: renderers=" + diagnostics.rendererCount +
                " frustum=" + diagnostics.frustumRendererCount +
                " materials=" + diagnostics.materialCount +
                " missingShaders=" + diagnostics.missingShaderCount +
                " unsupportedShaders=" + diagnostics.unsupportedShaderCount +
                " frustumMissingShaders=" + diagnostics.frustumMissingShaderCount +
                " frustumUnsupportedShaders=" + diagnostics.frustumUnsupportedShaderCount);
            EditorApplication.Exit(0);
        }
        catch (Exception ex)
        {
            Debug.LogError("Supervisor visual capture failed: " + ex);
            EditorApplication.Exit(1);
        }
    }

    private static CaptureDiagnostics BuildDiagnostics(Scene scene, Camera camera, string renderMethod)
    {
        var planes = GeometryUtility.CalculateFrustumPlanes(camera);
        var rendererItems = new List<RendererDiagnostic>();
        int materialCount = 0;
        int missingShaderCount = 0;
        int unsupportedShaderCount = 0;
        int frustumRendererCount = 0;
        int frustumMaterialCount = 0;
        int frustumMissingShaderCount = 0;
        int frustumUnsupportedShaderCount = 0;

        foreach (var renderer in Resources.FindObjectsOfTypeAll<Renderer>()
                     .Where(value => value != null && value.gameObject.scene == scene)
                     .OrderBy(value => HierarchyPath(value.gameObject), StringComparer.Ordinal))
        {
            bool inFrustum = renderer.enabled &&
                             renderer.gameObject.activeInHierarchy &&
                             GeometryUtility.TestPlanesAABB(planes, renderer.bounds);
            if (inFrustum) frustumRendererCount++;

            var materials = renderer.sharedMaterials ?? Array.Empty<Material>();
            var materialItems = new List<MaterialDiagnostic>();
            foreach (var material in materials)
            {
                materialCount++;
                Shader shader = material != null ? material.shader : null;
                bool missing = shader == null;
                bool supported = shader != null && shader.isSupported;
                if (missing) missingShaderCount++;
                else if (!supported) unsupportedShaderCount++;
                if (inFrustum)
                {
                    frustumMaterialCount++;
                    if (missing) frustumMissingShaderCount++;
                    else if (!supported) frustumUnsupportedShaderCount++;
                }

                materialItems.Add(new MaterialDiagnostic
                {
                    materialName = material != null ? material.name : "<missing material>",
                    materialAssetPath = material != null ? AssetDatabase.GetAssetPath(material) : "",
                    shaderName = shader != null ? shader.name : "<missing shader>",
                    shaderAssetPath = shader != null ? AssetDatabase.GetAssetPath(shader) : "",
                    shaderMissing = missing,
                    shaderSupported = supported
                });
            }

            rendererItems.Add(new RendererDiagnostic
            {
                hierarchyPath = HierarchyPath(renderer.gameObject),
                rendererType = renderer.GetType().FullName,
                enabled = renderer.enabled,
                activeInHierarchy = renderer.gameObject.activeInHierarchy,
                inCameraFrustum = inFrustum,
                prefabSource = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(renderer.gameObject) ?? "",
                materials = materialItems.ToArray()
            });
        }

        return new CaptureDiagnostics
        {
            scene = scene.path,
            cameraPath = HierarchyPath(camera.gameObject),
            renderPipeline = CurrentPipelineName(),
            renderMethod = renderMethod,
            graphicsDeviceType = SystemInfo.graphicsDeviceType.ToString(),
            graphicsDeviceName = SystemInfo.graphicsDeviceName,
            graphicsDeviceVersion = SystemInfo.graphicsDeviceVersion,
            graphicsMemorySizeMb = SystemInfo.graphicsMemorySize,
            batchMode = Application.isBatchMode,
            rendererCount = rendererItems.Count,
            frustumRendererCount = frustumRendererCount,
            materialCount = materialCount,
            missingShaderCount = missingShaderCount,
            unsupportedShaderCount = unsupportedShaderCount,
            frustumMaterialCount = frustumMaterialCount,
            frustumMissingShaderCount = frustumMissingShaderCount,
            frustumUnsupportedShaderCount = frustumUnsupportedShaderCount,
            renderers = rendererItems.ToArray()
        };
    }

    private static string CurrentPipelineName()
    {
        return GraphicsSettings.currentRenderPipeline != null
            ? GraphicsSettings.currentRenderPipeline.GetType().FullName
            : "BuiltIn";
    }

    private static void ApplyRequestedCameraPose(
        Scene scene,
        Camera camera,
        string requestedPosition,
        string requestedRotation,
        string lookAtPath,
        float fieldOfView)
    {
        if (!string.IsNullOrWhiteSpace(requestedPosition))
            camera.transform.position = ParseVector3(requestedPosition, "camera position");

        if (!string.IsNullOrWhiteSpace(requestedRotation) && !string.IsNullOrWhiteSpace(lookAtPath))
            throw new InvalidOperationException("Specify camera rotation or look-at path, not both.");

        if (!string.IsNullOrWhiteSpace(requestedRotation))
            camera.transform.eulerAngles = ParseVector3(requestedRotation, "camera rotation");

        if (!string.IsNullOrWhiteSpace(lookAtPath))
        {
            GameObject target = FindByHierarchyPath(scene, lookAtPath);
            if (target == null)
                throw new InvalidOperationException("Look-at path was not found: " + lookAtPath);
            camera.transform.LookAt(target.transform.position);
        }

        if (fieldOfView <= 1f || fieldOfView >= 179f)
            throw new InvalidOperationException("Field of view must be between 1 and 179 degrees.");
        camera.fieldOfView = fieldOfView;
    }

    private static Vector3 ParseVector3(string value, string label)
    {
        string[] parts = value.Split(',');
        if (parts.Length != 3)
            throw new InvalidOperationException("Invalid " + label + ": expected x,y,z.");
        return new Vector3(
            float.Parse(parts[0], System.Globalization.CultureInfo.InvariantCulture),
            float.Parse(parts[1], System.Globalization.CultureInfo.InvariantCulture),
            float.Parse(parts[2], System.Globalization.CultureInfo.InvariantCulture));
    }

    private static Camera ResolveCamera(Scene scene, string requestedPath)
    {
        if (!string.IsNullOrWhiteSpace(requestedPath))
        {
            GameObject target = FindByHierarchyPath(scene, requestedPath);
            if (target == null) throw new InvalidOperationException("Camera path was not found: " + requestedPath);
            var exact = target.GetComponent<Camera>();
            if (exact == null) throw new InvalidOperationException("Requested object does not contain a Camera: " + requestedPath);
            return exact;
        }

        var main = Camera.main;
        if (main != null && main.gameObject.scene == scene) return main;

        foreach (var camera in Resources.FindObjectsOfTypeAll<Camera>())
            if (camera.gameObject.scene == scene && camera.enabled && camera.gameObject.activeInHierarchy) return camera;
        return null;
    }

    private static GameObject FindByHierarchyPath(Scene scene, string path)
    {
        string[] parts = path.Split(new[] {'/'}, StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 0) return null;
        Transform current = null;
        foreach (var root in scene.GetRootGameObjects())
        {
            if (root.name == parts[0])
            {
                if (current != null) throw new InvalidOperationException("Root hierarchy path is ambiguous: " + path);
                current = root.transform;
            }
        }

        for (int i = 1; current != null && i < parts.Length; i++)
        {
            Transform next = null;
            for (int childIndex = 0; childIndex < current.childCount; childIndex++)
            {
                Transform child = current.GetChild(childIndex);
                if (child.name == parts[i])
                {
                    if (next != null) throw new InvalidOperationException("Hierarchy path is ambiguous: " + path);
                    next = child;
                }
            }
            current = next;
        }
        return current != null ? current.gameObject : null;
    }

    private static string HierarchyPath(GameObject gameObject)
    {
        var names = new List<string>();
        Transform current = gameObject.transform;
        while (current != null)
        {
            names.Add(current.name);
            current = current.parent;
        }
        names.Reverse();
        return string.Join("/", names);
    }

    private static string GetArg(string[] args, string name, string fallback = null)
    {
        for (int i = 0; i < args.Length - 1; i++)
            if (string.Equals(args[i], name, StringComparison.Ordinal)) return args[i + 1];
        if (fallback != null) return fallback;
        throw new InvalidOperationException("Missing command-line argument: " + name);
    }
}
'@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($HelperScript, $helperSource, $utf8NoBom)

$unityArgs = @(
    "-batchmode", "-accept-apiupdate",
    "-projectPath", $StageProject,
    "-executeMethod", "SupervisorVisualCapture.Capture",
    "-logFile", $LogPath,
    "-rpgkScene", $ScenePath,
    "-rpgkOutput", $PngPath,
    "-rpgkManifest", $ManifestPath,
    "-rpgkDiagnostics", $DiagnosticsPath,
    "-rpgkViewName", $ViewName,
    "-rpgkCameraPosition", $CameraPosition,
    "-rpgkCameraRotation", $CameraRotation,
    "-rpgkLookAt", $LookAtPath,
    "-rpgkFieldOfView", $FieldOfView.ToString([System.Globalization.CultureInfo]::InvariantCulture),
    "-rpgkWidth", $Width,
    "-rpgkHeight", $Height
)
if (-not [string]::IsNullOrWhiteSpace($CameraPath)) { $unityArgs += @("-rpgkCamera", $CameraPath) }

Write-ProgressState -Phase "unity_startup"
$unityProcess = Start-Process -FilePath $UnityPath -ArgumentList $unityArgs -PassThru
Write-ProgressState -Phase "capture_running" -UnityPid $unityProcess.Id

$cancelled = $false
$lastLogLength = -1L
while (-not $unityProcess.HasExited) {
    $cancel = Read-CancelRequest
    if ($null -ne $cancel -and [string]$cancel.requestId -eq $RequestId) {
        try {
            Stop-Process -Id $unityProcess.Id -Force -ErrorAction Stop
            $unityProcess.WaitForExit()
            $cancelled = $true
            Write-ProgressState -Phase "recovery_cancelled" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength
            break
        } catch {
            Write-ProgressState -Phase "recovery_cancel_failed" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength
        }
    }

    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        try {
            $length = [long](Get-Item -LiteralPath $LogPath).Length
            if ($length -ne $lastLogLength) {
                $lastLogLength = $length
                Write-ProgressState -Phase "capture_running" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength
            }
        } catch {}
    }

    Start-Sleep -Seconds 2
    $unityProcess.Refresh()
}

if (-not $cancelled) { $unityProcess.WaitForExit() }
$unityExitCode = $unityProcess.ExitCode
if (Test-Path -LiteralPath $LogPath -PathType Leaf) { Copy-Item -LiteralPath $LogPath -Destination $SourceLogPath -Force }

if ($cancelled) { Fail-Runner "request-owned Unity process was cancelled" 91 }
if ($unityExitCode -ne 0) { Fail-Runner "Unity exited with code $unityExitCode during scene capture. Inspect '$SourceLogPath'." 87 }
if (-not (Test-Path -LiteralPath $PngPath -PathType Leaf)) { Fail-Runner "Unity completed without producing scene.png" 87 }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { Fail-Runner "Unity completed without producing manifest.json" 87 }
if (-not (Test-Path -LiteralPath $DiagnosticsPath -PathType Leaf)) { Fail-Runner "Unity completed without producing visual-diagnostics.json" 87 }

$shaderLines = @()
if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
    $shaderLines = @(Select-String -LiteralPath $LogPath -Pattern '(?i)(shader error|failed to compile shader|no supported subshader|shader.*not supported|shader.*unsupported)' |
        Select-Object -First 200 |
        ForEach-Object { $_.Line })
}
[System.IO.File]::WriteAllLines($ShaderLogPath, [string[]]$shaderLines, $utf8NoBom)

Copy-Item -LiteralPath $PngPath -Destination (Join-Path $SourceOutput "scene.png") -Force
Copy-Item -LiteralPath $ManifestPath -Destination (Join-Path $SourceOutput "manifest.json") -Force
Copy-Item -LiteralPath $DiagnosticsPath -Destination (Join-Path $SourceOutput "visual-diagnostics.json") -Force
Copy-Item -LiteralPath $ShaderLogPath -Destination (Join-Path $SourceOutput "shader-log.txt") -Force

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$diagnostics = Get-Content -LiteralPath $DiagnosticsPath -Raw | ConvertFrom-Json
$summary = [ordered]@{
    result = "Captured"
    runId = $RunId
    scene = [string]$manifest.scene
    cameraPath = [string]$manifest.cameraPath
    viewName = [string]$manifest.viewName
    cameraPosition = $manifest.cameraPosition
    cameraEulerAngles = $manifest.cameraEulerAngles
    fieldOfView = [double]$manifest.fieldOfView
    width = [int]$manifest.width
    height = [int]$manifest.height
    image = "scene.png"
    diagnostics = "visual-diagnostics.json"
    shaderLog = "shader-log.txt"
    renderMethod = [string]$manifest.renderMethod
    renderPipeline = [string]$manifest.renderPipeline
    graphicsDeviceType = [string]$diagnostics.graphicsDeviceType
    missingShaderCount = [int]$diagnostics.missingShaderCount
    unsupportedShaderCount = [int]$diagnostics.unsupportedShaderCount
    frustumMissingShaderCount = [int]$diagnostics.frustumMissingShaderCount
    frustumUnsupportedShaderCount = [int]$diagnostics.frustumUnsupportedShaderCount
    shaderLogMatchCount = @($shaderLines).Count
    artifactPath = $SourceOutput
    unityVersion = $UnityVersion
}
$summaryJson = $summary | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText((Join-Path $SourceOutput "summary.json"), $summaryJson, $utf8NoBom)
Write-ProgressState -Phase "completed" -UnityPid $unityProcess.Id -EditorLogBytes $lastLogLength -SummaryPresent $true
$summaryJson | Write-Output
exit 0
