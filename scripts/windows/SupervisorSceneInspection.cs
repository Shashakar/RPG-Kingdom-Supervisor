// Trusted host-owned code copied only into Supervisor's disposable Unity staging project.
// Never copy this Editor script into the source game's Assets directory.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class SupervisorSceneInspection
{
    [Serializable] private sealed class Report
    {
        public int protocolVersion = 1;
        public string scene;
        public string capturedAtUtc;
        public int objectCount;
        public bool truncated;
        public List<Node> hierarchy = new List<Node>();
        public List<TerrainInfo> terrains = new List<TerrainInfo>();
        public List<ColliderInfo> colliders = new List<ColliderInfo>();
        public List<NavInfo> navigation = new List<NavInfo>();
    }

    [Serializable] private sealed class Node
    {
        public string path;
        public bool active;
        public Vector3 worldPosition;
        public Vector3 worldEuler;
        public Vector3 worldScale;
        public string[] componentTypes;
        public bool hasRenderer;
        public Bounds rendererBounds;
    }

    [Serializable] private sealed class TerrainInfo
    {
        public string path;
        public string terrainDataAssetPath;
        public bool active;
        public Vector3 position;
        public Vector3 size;
        public Bounds worldBounds;
        public int heightmapResolution;
        public int alphamapResolution;
        public int terrainLayerCount;
        public int treeInstanceCount;
        public int detailPrototypeCount;
        public bool hasMatchingTerrainCollider;
        public float minHeightMetres;
        public float maxHeightMetres;
        public float[] heightSamples9x9Metres;
    }

    [Serializable] private sealed class ColliderInfo
    {
        public string path;
        public string type;
        public bool enabled;
        public bool trigger;
        public Bounds bounds;
    }

    [Serializable] private sealed class NavInfo
    {
        public string path;
        public string componentType;
        public bool enabled;
        public string dataAssetPath;
    }

    public static void Inspect()
    {
        string[] args = Environment.GetCommandLineArgs();
        var path = Arg(args, "-rpgkScene");
        var output = Arg(args, "-rpgkInspection");
        if (!path.StartsWith("Assets/", StringComparison.Ordinal) || !path.EndsWith(".unity", StringComparison.Ordinal)
            || path.Contains("..") || path.Contains("\\"))
            throw new InvalidOperationException("Inspection requires a normalized Assets/*.unity scene path.");
        if (AssetDatabase.LoadAssetAtPath<SceneAsset>(path) == null)
            throw new InvalidOperationException("Inspection scene does not exist: " + path);

        var scene = EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
        if (!scene.IsValid() || !scene.isLoaded) throw new InvalidOperationException("Inspection scene not loaded.");
        var report = new Report { scene = path, capturedAtUtc = DateTime.UtcNow.ToString("o") };
        const int limit = 12000;
        foreach (var root in scene.GetRootGameObjects())
            foreach (var transform in root.GetComponentsInChildren<Transform>(true))
            {
                report.objectCount++;
                if (report.objectCount > limit) { report.truncated = true; continue; }
                var go = transform.gameObject;
                var components = go.GetComponents<Component>();
                var renderer = go.GetComponent<Renderer>();
                report.hierarchy.Add(new Node
                {
                    path = PathFor(transform), active = go.activeInHierarchy,
                    worldPosition = transform.position, worldEuler = transform.eulerAngles,
                    worldScale = transform.lossyScale,
                    componentTypes = components.Select(c => c == null ? "(missing script)" : c.GetType().FullName).ToArray(),
                    hasRenderer = renderer != null,
                    rendererBounds = renderer != null ? renderer.bounds : default(Bounds)
                });
                foreach (var collider in components.OfType<Collider>())
                    report.colliders.Add(new ColliderInfo
                    {
                        path = PathFor(transform), type = collider.GetType().FullName,
                        enabled = collider.enabled, trigger = collider.isTrigger, bounds = collider.bounds
                    });
                foreach (var nav in components.Where(c => c != null && c.GetType().FullName == "Unity.AI.Navigation.NavMeshSurface"))
                {
                    var serialized = new SerializedObject(nav);
                    var data = serialized.FindProperty("m_NavMeshData");
                    var asset = data != null ? data.objectReferenceValue : null;
                    report.navigation.Add(new NavInfo
                    {
                        path = PathFor(transform), componentType = nav.GetType().FullName,
                        enabled = nav is Behaviour && ((Behaviour)nav).enabled,
                        dataAssetPath = asset != null ? AssetDatabase.GetAssetPath(asset) : ""
                    });
                }
                var terrain = go.GetComponent<Terrain>();
                if (terrain == null) continue;
                var dataTerrain = terrain.terrainData;
                var colliderTerrain = go.GetComponent<TerrainCollider>();
                var info = new TerrainInfo
                {
                    path = PathFor(transform), position = transform.position,
                    active = terrain.enabled && go.activeInHierarchy,
                    terrainDataAssetPath = dataTerrain != null ? AssetDatabase.GetAssetPath(dataTerrain) : "",
                    hasMatchingTerrainCollider = dataTerrain != null && colliderTerrain != null
                        && colliderTerrain.enabled && colliderTerrain.terrainData == dataTerrain
                };
                if (dataTerrain != null)
                {
                    info.size = dataTerrain.size;
                    info.worldBounds = new Bounds(transform.position + dataTerrain.size * 0.5f, dataTerrain.size);
                    info.heightmapResolution = dataTerrain.heightmapResolution;
                    info.alphamapResolution = dataTerrain.alphamapResolution;
                    info.terrainLayerCount = dataTerrain.terrainLayers != null ? dataTerrain.terrainLayers.Length : 0;
                    info.treeInstanceCount = dataTerrain.treeInstanceCount;
                    info.detailPrototypeCount = dataTerrain.detailPrototypes != null ? dataTerrain.detailPrototypes.Length : 0;
                    int resolution = dataTerrain.heightmapResolution;
                    float[,] heights = dataTerrain.GetHeights(0, 0, resolution, resolution);
                    float min = float.MaxValue, max = float.MinValue;
                    for (int z = 0; z < resolution; z++)
                        for (int x = 0; x < resolution; x++)
                        {
                            float h = heights[z, x] * dataTerrain.size.y;
                            if (h < min) min = h;
                            if (h > max) max = h;
                        }
                    info.minHeightMetres = min;
                    info.maxHeightMetres = max;
                    info.heightSamples9x9Metres = new float[81];
                    for (int z = 0; z < 9; z++)
                        for (int x = 0; x < 9; x++)
                            info.heightSamples9x9Metres[z * 9 + x] =
                                heights[Mathf.RoundToInt(z * (resolution - 1) / 8f),
                                        Mathf.RoundToInt(x * (resolution - 1) / 8f)] * dataTerrain.size.y;
                }
                report.terrains.Add(info);
            }

        Directory.CreateDirectory(Path.GetDirectoryName(output));
        File.WriteAllText(output, JsonUtility.ToJson(report, true));
        Debug.Log("SupervisorSceneInspection: inspected " + report.objectCount +
                  " objects, " + report.terrains.Count + " terrains and " + report.navigation.Count + " navigation surfaces.");
    }

    private static string PathFor(Transform current)
    {
        var parts = new List<string>();
        while (current != null) { parts.Add(current.name); current = current.parent; }
        parts.Reverse();
        return string.Join("/", parts);
    }

    private static string Arg(string[] args, string name)
    {
        for (int i = 0; i < args.Length - 1; i++)
            if (args[i] == name) return args[i + 1];
        throw new InvalidOperationException("Missing inspection argument: " + name);
    }
}
