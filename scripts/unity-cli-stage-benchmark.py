#!/usr/bin/env python3
"""C2 operator-granted, fixed-code, stage-only Unity Editor authoring benchmark.

A worker may request exactly one known disposable test scene through the host
Unity broker. No model-supplied C#, destination path, save of the gameplay scene,
or source copyback. This is NOT a sandbox for arbitrary Editor code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("unity_cli_readonly_benchmark_dependency",
                                               ROOT / "unity-cli-readonly.py")
assert SPEC and SPEC.loader
ro = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ro
SPEC.loader.exec_module(ro)

SCHEMA = 1
SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
DEST = "Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity"
EDITOR_VERSION = "6000.3.10f1"
CLI_VERSION = "1.0.0-beta.8"
PIPELINE_VERSION = "0.8.0-exp.1"
PROTECTED = (
    SCENE,
    SCENE + ".meta",
    "Packages/manifest.json",
    "ProjectSettings/ProjectVersion.txt",
)
ENABLE = "RPGK_UNITY_CLI_BENCHMARK_ENABLED"
MODE = "stage-benchmark"


def fail(reason: str) -> None:
    raise ro.Denied(reason)


def grant_path(state: Path, issue: str) -> Path:
    if not ro.ISSUE.fullmatch(issue):
        fail("invalid GH issue")
    return state / "unity-cli-benchmark-grants" / (issue + ".json")


def sha_file(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(stage: Path) -> dict[str, str | None]:
    result = {}
    for rel in PROTECTED:
        candidate = stage / rel
        if candidate.is_symlink():
            fail("protected source contains a symbolic link")
        result[rel] = sha_file(candidate) if candidate.is_file() else None
    if result[SCENE] is None or result["Packages/manifest.json"] is None:
        fail("protected scene or project manifest missing from staged Editor")
    return result


def stage_target(stage: Path) -> Path:
    root = (stage / "Assets/RPGKingdom/Generated/AgentBenchmarks")
    asset_dir = stage / "Assets/RPGKingdom/Generated"
    if root.is_symlink() or asset_dir.is_symlink():
        fail("symlinks in generated asset destination are forbidden")
    target = stage / DEST
    if target.exists() or target.is_symlink():
        fail("benchmark scene already exists; refusing to overwrite")
    if root.exists() and not root.is_dir():
        fail("benchmark destination is not a directory")
    return target


def cli_versions(exe: str, stage: Path, receipt: dict) -> None:
    if receipt.get("unityVersion") != EDITOR_VERSION:
        fail("Unity Editor version does not match the pinned benchmark")
    check = subprocess.run([exe, "--version"], capture_output=True,
                           text=True, timeout=15, cwd=stage, check=False)
    if check.returncode or check.stdout.strip() != CLI_VERSION:
        fail("Unity CLI version not pinned to tested beta.8")
    listing = ro.cli_json(exe, stage, ["pipeline", "list", "--format", "json"])
    instances = listing.get("data", {}).get("instances", [])
    found = [x for x in instances if isinstance(x, dict)
             and ro.ntpath.normcase(str(x.get("projectPath", ""))) ==
             ro.ntpath.normcase(receipt["stagePath"])
             and x.get("pid") == receipt["unityPid"]]
    if (len(found) != 1 or found[0].get("isRunning") is not True
            or found[0].get("hasPipelinePackage") is not True
            or found[0].get("pipelineVersion") != PIPELINE_VERSION
            or found[0].get("pipelineServer", {}).get("isReachable") is not True):
        fail("owned stage Pipeline is not reachable at the pinned version")


def verify_active_scene(exe: str, stage: Path, receipt: dict) -> dict:
    hierarchy = ro.cli_json(exe, stage, [
        "command", "get_scene_hierarchy", "--project-path",
        receipt["stagePath"], "--format", "json"
    ])
    return ro.compact_scene(hierarchy, SCENE)


# This is a constant compiled into the trusted host adapter, NOT worker input.
# No arbitrary methods, locations, assets or scene object specs are accepted.
# A new additive scene is saved, and the original active PlaytestScene is restored.
FIXED_CSHARP = r"""
var original = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
if (original.path != "Assets/RPGKingdom/Scenes/PlaytestScene.unity" || original.isDirty)
    throw new System.InvalidOperationException("PlaytestScene is not clean and active");
var relative = "Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity";
var absolute = System.IO.Path.Combine(UnityEngine.Application.dataPath, "RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity");
if (System.IO.File.Exists(absolute))
    throw new System.InvalidOperationException("Benchmark scene must not already exist");
System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(absolute));
var scene = UnityEditor.SceneManagement.EditorSceneManager.NewScene(
    UnityEditor.SceneManagement.NewSceneSetup.EmptyScene,
    UnityEditor.SceneManagement.NewSceneMode.Additive);
try {
    var root = new UnityEngine.GameObject("SupervisorCliBenchmarkRoot");
    UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(root, scene);
    var cube = UnityEngine.GameObject.CreatePrimitive(UnityEngine.PrimitiveType.Cube);
    cube.name = "SupervisorCliBenchmarkCube";
    UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(cube, scene);
    cube.transform.SetParent(root.transform, false);
    cube.transform.localPosition = new UnityEngine.Vector3(0f, 0.5f, 0f);
    if (!UnityEditor.SceneManagement.EditorSceneManager.SaveScene(scene, relative, false))
        throw new System.InvalidOperationException("Could not save benchmark scene");
}
finally {
    UnityEngine.SceneManagement.SceneManager.SetActiveScene(original);
    UnityEditor.SceneManagement.EditorSceneManager.CloseScene(scene, true);
}
return relative;
""".strip()


def workspace_clean(workspace: Path) -> None:
    status = subprocess.run(["git", "-C", str(workspace), "status", "--porcelain",
                             "--untracked-files=no"], capture_output=True, text=True,
                            timeout=15, check=False)
    if status.returncode or status.stdout.strip():
        fail("workspace tracked files must be clean at grant time")


def current_grant(state: Path, workspace: Path, revision: str) -> dict:
    try:
        record = json.loads(grant_path(state, workspace.name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("no active stage benchmark grant") from exc
    if (not isinstance(record, dict)
            or record.get("schemaVersion") != SCHEMA
            or record.get("enabled") is not True
            or record.get("mode") != MODE
            or record.get("workspace") != str(workspace)
            or record.get("workspaceRevision") != revision
            or record.get("scenePath") != SCENE
            or record.get("targetScene") != DEST
            or record.get("issue") != workspace.name):
        fail("issue, revision or scene not covered by stage benchmark grant")
    return record


def author(stage: Path, receipt: dict, *, exe: str) -> dict:
    stage_target(stage)
    before = fingerprint(stage)
    cli_versions(exe, stage, receipt)
    summary = verify_active_scene(exe, stage, receipt)
    # Bounded single-purpose code, executed only inside an explicitly granted
    # disposable NTFS stage. Workers cannot supply the code or choose output.
    ro.cli_json(exe, stage, [
        "command", "eval", "--code", FIXED_CSHARP,
        "--project-path", receipt["stagePath"], "--format", "json"
    ])
    after = fingerprint(stage)
    if before != after:
        fail("protected scene/project files changed during benchmark")
    target = stage / DEST
    if not target.is_file() or target.is_symlink():
        fail("Unity reported success but did not create the benchmark scene")
    payload = target.read_text(encoding="utf-8-sig", errors="replace")
    if "SupervisorCliBenchmarkCube" not in payload or "SupervisorCliBenchmarkRoot" not in payload:
        fail("saved scene does not contain expected benchmark objects")
    verify_active_scene(exe, stage, receipt)
    return {
        "success": True,
        "operation": "editor-stage-benchmark",
        "stageOnly": True,
        "sourceScene": SCENE,
        "sourceGameObjectCount": summary["gameObjectCount"],
        "targetScene": DEST,
        "targetSha256": sha_file(target),
        "protectedFilesUnchanged": True,
        "copyback": False,
        "requiresHumanReview": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Host-owned fixed Unity scene benchmark")
    parser.add_argument("action", choices=("grant", "revoke", "run"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    parser.add_argument("--session-windows", default="")
    parser.add_argument("--scene", default=SCENE)
    args = parser.parse_args()
    try:
        workspace, revision = ro.workspace_info(args.workspace)
        state = Path(args.state_root).expanduser().resolve()
        path = grant_path(state, workspace.name)
        if args.action == "revoke":
            path.unlink(missing_ok=True)
            print(json.dumps({"status": "revoked", "issue": workspace.name}))
            return 0
        if os.environ.get(ENABLE) != "1":
            fail("stage authoring benchmark is disabled by default")
        if args.scene != SCENE:
            fail("benchmark operates only on the pinned clean PlaytestScene")
        if args.action == "grant":
            workspace_clean(workspace)
            stage, receipt = ro.stage_info(args.session_windows)
            stage_target(stage)
            baseline = fingerprint(stage)
            grant = {
                "schemaVersion": SCHEMA, "enabled": True, "mode": MODE,
                "issue": workspace.name, "workspace": str(workspace),
                "workspaceRevision": revision, "scenePath": SCENE,
                "targetScene": DEST, "stagePath": receipt["stagePath"],
                "unityPid": receipt["unityPid"], "protectedBaseline": baseline,
            }
            ro.write_grant(path, grant)
            print(json.dumps({"status": "granted", "issue": workspace.name,
                              "stageOnly": True, "targetScene": DEST}))
            return 0
        if args.session_windows:
            fail("worker requests must not select a Windows stage")
        grant = current_grant(state, workspace, revision)
        stage, receipt = ro.stage_info(grant["stagePath"])
        if receipt["unityPid"] != grant["unityPid"]:
            fail("stage Editor PID changed after grant")
        if fingerprint(stage) != grant["protectedBaseline"]:
            fail("staged protected project baseline changed since grant")
        print(json.dumps(author(stage, receipt, exe=ro.cli_path()), separators=(",", ":")))
        return 0
    except (ro.Denied, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Unity CLI stage benchmark: blocked: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
