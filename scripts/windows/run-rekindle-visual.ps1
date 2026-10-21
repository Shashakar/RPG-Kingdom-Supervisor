# Rekindle-specific, opt-in, host-owned visual evidence runner.
# No production scene or licensed asset bytes are copied back to the source repo.
param(
    [Parameter(Mandatory=$true)][string]$SourceProjectPath,
    [Parameter(Mandatory=$true)][string]$AssetProjectPath,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true)][string]$RunId,
    [Parameter(Mandatory=$true)][string]$UnityVersion,
    [Parameter(Mandatory=$true)][string]$LicensedRoots,
    [string]$UnityPath = "",
    [string]$StageRoot = ""
)
$ErrorActionPreference = "Stop"

function Fail([string]$message, [int]$code = 80) {
    [Console]::Error.WriteLine("Rekindle visual runner: $message")
    exit $code
}
function Mirror([string]$source, [string]$destination) {
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { Fail "Missing staging source: $source" 82 }
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    & robocopy.exe $source $destination /MIR /FFT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "Unable to stage $source (robocopy $LASTEXITCODE)" 84 }
}

if ($RunId -notmatch '^GH-[1-9][0-9]*-visual-[a-f0-9]{12}$') { Fail "Invalid run id" 64 }
if ($UnityVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+[abfp][0-9]+$') { Fail "Invalid Unity version" 64 }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { Fail "Visual manifest missing" 64 }
if (-not (Test-Path -LiteralPath $AssetProjectPath -PathType Container)) { Fail "Local imported asset project is missing" 82 }
if (-not (Get-Command robocopy.exe -ErrorAction SilentlyContinue)) { Fail "robocopy.exe missing" 83 }
if (-not [Environment]::GetFolderPath("LocalApplicationData")) { Fail "LocalApplicationData unavailable" 85 }
if ([string]::IsNullOrEmpty($UnityPath)) { $UnityPath = Join-Path ${env:ProgramFiles} "Unity\Hub\Editor\$UnityVersion\Editor\Unity.exe" }
if (-not (Test-Path -LiteralPath $UnityPath -PathType Leaf)) { Fail "Unity Editor not installed at configured version/path" 81 }
if (@(Get-Process -Name Unity -ErrorAction SilentlyContinue).Count -gt 0) { Fail "Unity Editor is already running; exclusive host ownership required" 89 }

$roots = @($LicensedRoots.Split(';') | Where-Object { $_ })
if ($roots.Count -lt 1 -or $roots.Count -gt 12) { Fail "1-12 fixed approved local asset roots required" 64 }
foreach ($root in $roots) {
    if ($root -notmatch '^[A-Za-z0-9 _.-]{1,80}$' -or
        $root -in @('.', '..', 'Rekindle', 'Settings', 'ThirdParty', '__SupervisorVisualTrial')) {
        Fail "Unsafe local asset root: $root" 64
    }
}
if ([string]::IsNullOrEmpty($StageRoot)) {
    $StageRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "RPGKingdomSupervisor\RekindleVisualStages"
}
$stage = Join-Path (Join-Path $StageRoot $UnityVersion) "Rekindle-Claude"
if ([IO.Path]::GetFullPath($stage).TrimEnd('\') -eq [IO.Path]::GetFullPath($AssetProjectPath).TrimEnd('\')) { Fail "Stage must differ from user's Unity project" 64 }
if ([IO.Path]::GetFullPath($stage).TrimEnd('\') -eq [IO.Path]::GetFullPath($SourceProjectPath).TrimEnd('\')) { Fail "Stage must differ from issue workspace" 64 }
foreach ($required in @("Assets", "Packages", "ProjectSettings")) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceProjectPath $required) -PathType Container)) {
        Fail "Issue workspace lacks $required" 82
    }
}

New-Item -ItemType Directory -Force -Path $stage | Out-Null
foreach ($directory in @("Assets", "Packages", "ProjectSettings")) {
    Mirror (Join-Path $SourceProjectPath $directory) (Join-Path $stage $directory)
}
foreach ($root in $roots) {
    $external = Join-Path (Join-Path $AssetProjectPath "Assets") $root
    if (-not (Test-Path -LiteralPath $external -PathType Container)) { Fail "Approved imported asset root is missing: $root" 82 }
    $dest = Join-Path (Join-Path $stage "Assets") $root
    Mirror $external $dest
}
$helperDir = Join-Path $stage "Assets\__SupervisorVisualTrial\Editor"
New-Item -ItemType Directory -Force -Path $helperDir | Out-Null
$helper = Join-Path $helperDir "SupervisorRekindleVisualTrial.cs"
$source = @'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

public static class SupervisorRekindleVisualTrial
{
    [Serializable] sealed class Root { public int protocolVersion; public int issue; public Experiment[] experiments; }
    [Serializable] sealed class Experiment { public string kind; public Entry[] objects; public Shot[] shots; public CameraSpec camera; }
    [Serializable] sealed class Entry { public string id, role, asset; public float[] position; public float yaw, scale; }
    [Serializable] sealed class Shot { public string id, clip, clipName; public float seconds; }
    [Serializable] sealed class CameraSpec { public float[] position, lookAt; public float fov; }
    [Serializable] sealed class Report { public bool success; public int issue; public string[] captures; public string[] sourceAssets; public string editorVersion; public string error; }

    static string Arg(string[] args, string key)
    {
        for (var i = 0; i + 1 < args.Length; i++)
            if (args[i] == key) return args[i + 1];
        throw new InvalidOperationException("Missing fixed runner argument " + key);
    }
    static Vector3 Point(float[] p) => new Vector3(p[0], p[1], p[2]);
    static void SaveReport(string output, Report report) =>
        File.WriteAllText(Path.Combine(output, "summary.json"), JsonUtility.ToJson(report, true));

    public static void Execute()
    {
        string output = null;
        var report = new Report { success = false };
        try
        {
            var args = Environment.GetCommandLineArgs();
            var manifest = Arg(args, "-rpgkVisualManifest");
            output = Arg(args, "-rpgkVisualOutput");
            Directory.CreateDirectory(output);
            var request = JsonUtility.FromJson<Root>(File.ReadAllText(manifest));
            if (request == null || request.protocolVersion != 1 || request.experiments == null ||
                request.experiments.Length < 1 || request.experiments.Length > 2)
                throw new InvalidOperationException("Invalid host-validated visual request");
            report.issue = request.issue;
            report.editorVersion = Application.unityVersion;
            var saved = new List<string>();
            var assets = new HashSet<string>(StringComparer.Ordinal);
            for (int e = 0; e < request.experiments.Length; e++)
            {
                var experiment = request.experiments[e];
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
                floor.name = "Preview ground (stage only)";
                floor.transform.localScale = new Vector3(8, 1, 8);
                var sunlight = new GameObject("Preview lighting").AddComponent<Light>();
                sunlight.type = LightType.Directional;
                sunlight.intensity = 1.25f;
                sunlight.transform.rotation = Quaternion.Euler(48f, -35f, 0f);
                RenderSettings.ambientLight = new Color(0.58f, 0.60f, 0.64f);
                var cameraObj = new GameObject("Preview camera");
                var camera = cameraObj.AddComponent<Camera>();
                camera.transform.position = Point(experiment.camera.position);
                camera.transform.LookAt(Point(experiment.camera.lookAt));
                camera.fieldOfView = experiment.camera.fov;
                camera.clearFlags = CameraClearFlags.Skybox;

                var instances = new List<GameObject>();
                foreach (var entry in experiment.objects)
                {
                    var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(entry.asset);
                    if (prefab == null) throw new InvalidOperationException("Missing imported model/prefab: " + entry.asset);
                    assets.Add(entry.asset);
                    var instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                    if (instance == null) instance = UnityEngine.Object.Instantiate(prefab);
                    instance.name = entry.id;
                    instance.transform.position = Point(entry.position);
                    instance.transform.rotation = Quaternion.Euler(0f, entry.yaw, 0f);
                    instance.transform.localScale = Vector3.one * entry.scale;
                    instances.Add(instance);
                }
                // The generated scene is saved ONLY in the separate Windows stage.
                var localScene = "Assets/__SupervisorVisualTrial/" + request.issue + "-" + experiment.kind + ".unity";
                EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), localScene);
                foreach (var shot in experiment.shots)
                {
                    if (!string.IsNullOrEmpty(shot.clip))
                    {
                        assets.Add(shot.clip);
                        var clip = AssetDatabase.LoadAllAssetsAtPath(shot.clip)
                            .OfType<AnimationClip>()
                            .FirstOrDefault(x => (string.IsNullOrEmpty(shot.clipName) || x.name == shot.clipName)
                                              && !x.name.StartsWith("__preview__", StringComparison.Ordinal));
                        if (clip == null) throw new InvalidOperationException("Missing animation clip: " + shot.clip + "#" + shot.clipName);
                        AnimationMode.StartAnimationMode();
                        foreach (var obj in instances.Where(x => experiment.objects[instances.IndexOf(x)].role == "character"))
                            AnimationMode.SampleAnimationClip(obj, clip, shot.seconds);
                    }
                    try
                    {
                        var filename = experiment.kind + "-" + shot.id + ".png";
                        Capture(camera, Path.Combine(output, filename));
                        saved.Add(filename);
                    }
                    finally
                    {
                        if (AnimationMode.InAnimationMode()) AnimationMode.StopAnimationMode();
                    }
                }
            }
            report.captures = saved.ToArray();
            report.sourceAssets = assets.OrderBy(x => x, StringComparer.Ordinal).ToArray();
            report.success = true;
            SaveReport(output, report);
            EditorApplication.Exit(0);
        }
        catch (Exception ex)
        {
            report.error = ex.ToString();
            Debug.LogError("Supervisor Rekindle visual trial failed: " + ex);
            if (!string.IsNullOrEmpty(output))
            {
                try { Directory.CreateDirectory(output); SaveReport(output, report); }
                catch { }
            }
            EditorApplication.Exit(2);
        }
    }

    static void Capture(Camera camera, string filename)
    {
        const int width = 1280, height = 720;
        var rt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
        var before = camera.targetTexture;
        var previousActive = RenderTexture.active;
        try
        {
            camera.targetTexture = rt;
            var request = new RenderPipeline.StandardRequest { destination = rt };
            if (RenderPipeline.SupportsRenderRequest(camera, request))
                RenderPipeline.SubmitRenderRequest(camera, request);
            else
                camera.Render();
            RenderTexture.active = rt;
            var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
            try
            {
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                texture.Apply();
                File.WriteAllBytes(filename, texture.EncodeToPNG());
            }
            finally { UnityEngine.Object.DestroyImmediate(texture); }
        }
        finally
        {
            camera.targetTexture = before;
            RenderTexture.active = previousActive;
            rt.Release();
            UnityEngine.Object.DestroyImmediate(rt);
        }
    }
}
'@
[IO.File]::WriteAllText($helper, $source)
$stageOutput = Join-Path $stage ".symphony-results\$RunId"
New-Item -ItemType Directory -Force -Path $stageOutput | Out-Null
$stageManifest = Join-Path $stageOutput "request.json"
Copy-Item -LiteralPath $ManifestPath -Destination $stageManifest -Force
$log = Join-Path $stageOutput "Editor.log"
$arguments = @(
    "-batchmode", "-quit", "-projectPath", $stage,
    "-executeMethod", "SupervisorRekindleVisualTrial.Execute",
    "-logFile", $log,
    "-rpgkVisualManifest", $stageManifest,
    "-rpgkVisualOutput", $stageOutput
)
# Explicit quoting is required for Windows paths containing spaces. Each
# argument is an individual host-provided field; the model supplies no commands.
$quoted = @($arguments | ForEach-Object {
    if ($_ -match '[\s"]') {
        '"' + $_.Replace('"', '\\"') + '"'
    } else { $_ }
})
$process = Start-Process -FilePath $UnityPath -ArgumentList ($quoted -join ' ') -PassThru -NoNewWindow
if (-not $process.WaitForExit(1800000)) {
    try { Stop-Process -Id $process.Id -Force -ErrorAction Stop } catch { }
    Fail "Unity preview exceeded the 30-minute maximum; inspect the process before rearming" 91
}
$summary = Join-Path $stageOutput "summary.json"
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $summary -PathType Leaf)) {
    if (Test-Path -LiteralPath $log) {
        [Console]::Error.WriteLine(((Get-Content -LiteralPath $log -Tail 30) -join "\n"))
    }
    Fail "Unity visual preview failed (exit $($process.ExitCode))" 87
}
$result = Get-Content -LiteralPath $summary -Raw | ConvertFrom-Json
if (-not $result.success) { Fail "Unity visual preview reported failure: $($result.error)" 87 }
New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null
foreach ($file in @($result.captures)) {
    if ($file -notmatch '^(character-comparison|environment-cohesion)-[A-Za-z][A-Za-z0-9_-]{0,47}\.png$') {
        Fail "Unity returned unexpected screenshot filename" 91
    }
    $src = Join-Path $stageOutput $file
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { Fail "Missing Unity screenshot: $file" 91 }
    Copy-Item -LiteralPath $src -Destination (Join-Path $OutputPath $file) -Force
}
Copy-Item -LiteralPath $summary -Destination (Join-Path $OutputPath "summary.json") -Force
Write-Output "Rekindle visual evidence: $OutputPath"
