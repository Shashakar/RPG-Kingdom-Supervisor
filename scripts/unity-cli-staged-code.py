#!/usr/bin/env python3
"""Opt-in, same-Windows-user Unity Editor C# execution in a disposable stage.

Security model: TRUSTED LOCAL CODE. This is NOT a sandbox; C# can access OS
resources available to the operator Windows account. The practical barriers
are explicit host opt-in, issue/revision/PID grant, exclusive Supervisor Unity
lease, script provenance, and NO automatic source copyback.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("unity_cli_readonly_staged_code", ROOT / "unity-cli-readonly.py")
assert spec and spec.loader
ro = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ro
spec.loader.exec_module(ro)

FLAG = "RPGK_UNITY_STAGED_CODE_ENABLED"
SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
MAX_CODE = 65536
SCRIPT = re.compile(r"(?:AgentScripts|Logs/SymphonyUnity/AgentScripts)/[A-Za-z][A-Za-z0-9_-]{0,70}\.cs\Z")
CHECKED = ("Packages/manifest.json", "ProjectSettings/ProjectVersion.txt")
IGNORED_DIRS = (".git", "Library", "Logs", "Temp", "Obj", "Build", "Builds")


def deny(message: str) -> None:
    raise ro.Denied(message)


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def protected(stage: Path) -> dict:
    found = {}
    for rel in CHECKED:
        item = stage / rel
        if item.is_symlink() or (item.exists() and not item.is_file()):
            deny("invalid protected stage path: " + rel)
        found[rel] = hash_file(item) if item.is_file() else None
    if not (stage / SCENE).is_file() or not found["Packages/manifest.json"]:
        deny("missing staged scene/project baseline")
    return found


def candidate(stage: Path) -> dict:
    """Inventory stage Assets only; report changes, never promote."""
    root = stage / "Assets"
    if root.is_symlink() or not root.is_dir():
        deny("invalid staged Assets directory")
    result = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        here = Path(directory)
        for d in dirs:
            if (here / d).is_symlink():
                deny("linked directory in Unity Assets")
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in sorted(names):
            file = here / name
            if file.is_symlink() or not file.is_file():
                deny("nonregular or linked staged asset")
            rel = file.relative_to(stage).as_posix()
            if len(result) >= 250000:
                deny("Unity staged asset count exceeds bound")
            result[rel] = hash_file(file)
    return result


def code_path(workspace: Path, rel: str) -> Path:
    if not SCRIPT.fullmatch(rel):
        deny("script must be AgentScripts/NAME.cs or Logs/SymphonyUnity/AgentScripts/NAME.cs")
    parts = rel.split("/")
    folder = workspace
    for component in parts[:-1]:
        folder = folder / component
        if folder.is_symlink() or not folder.is_dir():
            deny("script parent missing or linked")
    path = folder / parts[-1]
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CODE:
        deny("script missing, linked, or exceeds 64 KiB")
    if path.resolve(strict=True).parent != folder.resolve(strict=True):
        deny("script escaped issue workspace")
    return path


def grant_path(state: Path, issue: str) -> Path:
    if not ro.ISSUE.fullmatch(issue):
        deny("invalid issue identifier")
    return state / "unity-staged-code-grants" / (issue + ".json")


def current_grant(state: Path, workspace: Path, revision: str) -> dict:
    try:
        grant = json.loads(grant_path(state, workspace.name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("no host grant for Unity staged code") from exc
    if (not isinstance(grant, dict)
            or grant.get("schemaVersion") != 1 or grant.get("enabled") is not True
            or grant.get("mode") != "operator-trusted-staged-editor-code"
            or grant.get("issue") != workspace.name
            or grant.get("workspace") != str(workspace)
            or grant.get("workspaceRevision") != revision
            or grant.get("scenePath") != SCENE):
        deny("operator grant does not match issue, workspace, revision, or scene")
    return grant


def windows_unity_processes() -> list[dict]:
    """Read actual Windows Unity processes; no model-supplied PowerShell."""
    ps = os.environ.get("RPGK_POWERSHELL_EXE", "powershell.exe")
    query = (
        "$ErrorActionPreference = 'Stop'; "
        "$items = @(Get-CimInstance Win32_Process -Filter \"Name = 'Unity.exe'\" "
        "| Select-Object ProcessId, ParentProcessId, CommandLine); "
        "ConvertTo-Json -InputObject $items -Compress -Depth 3"
    )
    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ro.Denied("Windows Unity process census unavailable") from exc
    if result.returncode != 0:
        deny("Windows Unity process census failed")
    try:
        entries = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ro.Denied("invalid Windows Unity process census") from exc
    if not isinstance(entries, list) or not entries:
        deny("no Windows Unity processes discovered")
    if any(not isinstance(row, dict) for row in entries):
        deny("invalid Windows Unity process entry")
    return entries


def command_project(command: str) -> str | None:
    if not isinstance(command, str):
        return None
    match = re.search(r'(?:^|\s)"?-projectPath"?\s+(?:"([^"]+)"|(\S+))', command, re.I)
    return ro.normalized_windows((match.group(1) or match.group(2)).replace("/", "\\")) if match else None


def verify_exclusive_persistent_editor(receipt: dict, processes: list[dict]) -> dict:
    """Allow only the granted Editor and its stage-bound asset import children."""
    pid = receipt["unityPid"]
    stage = ro.normalized_windows(receipt["stagePath"])
    main = [row for row in processes if row.get("ProcessId") == pid]
    if len(main) != 1:
        deny("granted Unity Editor PID is missing or ambiguous")
    try:
        main_project = command_project(main[0].get("CommandLine"))
    except ro.Denied:
        deny("granted Unity Editor project path invalid")
    if not main_project or main_project.casefold() != stage.casefold():
        deny("granted Unity Editor command line does not match staged project")

    imports = []
    for row in processes:
        child_pid = row.get("ProcessId")
        if child_pid == pid:
            continue
        cmd = row.get("CommandLine")
        try:
            project = command_project(cmd)
        except ro.Denied:
            project = None
        if (isinstance(child_pid, int) and child_pid > 0
                and row.get("ParentProcessId") == pid
                and project is not None and project.casefold() == stage.casefold()
                and isinstance(cmd, str)
                and re.search(r'(?:^|\s)"?-adb2"?(?:\s|$)', cmd, re.I)
                and re.search(r'(?:^|\s)"?-name"?\s+"?AssetImportWorker[0-9]+"?(?:\s|$)', cmd, re.I)):
            imports.append(child_pid)
            continue
        deny("unrelated Unity.exe process detected (PID: " + str(child_pid) + ")")
    return {"unityPid": pid, "assetImportWorkerPids": sorted(imports),
            "stagePath": receipt["stagePath"]}


def persistent_preflight(workspace: Path, revision: str, state: Path) -> dict:
    grant = current_grant(state, workspace, revision)
    stage, receipt = ro.stage_info(grant.get("stagePath"))
    if receipt["unityPid"] != grant.get("unityPid"):
        deny("granted Unity Editor PID changed")
    if protected(stage) != grant.get("protectedBaseline"):
        deny("protected stage project differs from operator grant")
    # The operator's new Stage B receipt must bind this exact GH-N workspace.
    source = subprocess.run(["wslpath", "-w", str(workspace)], capture_output=True,
                            text=True, timeout=10, check=False)
    if source.returncode or not source.stdout.strip():
        deny("unable to verify Windows path for issue workspace")
    recorded = receipt.get("sourceProjectPath")
    if (not isinstance(recorded, str)
            or ntpath.normcase(ntpath.normpath(recorded)) !=
                ntpath.normcase(ntpath.normpath(source.stdout.strip()))):
        deny("Windows stage was not created from the granted issue workspace")
    verified_processes = verify_exclusive_persistent_editor(
        receipt, windows_unity_processes())
    ro.verify_pipeline(ro.cli_path(), stage, receipt)
    hierarchy = ro.cli_json(ro.cli_path(), stage, [
        "command", "get_scene_hierarchy",
        "--project-path", receipt["stagePath"], "--format", "json"
    ])
    ro.compact_scene(hierarchy, SCENE)
    if protected(stage) != grant.get("protectedBaseline"):
        deny("protected stage project changed during persistent preflight")
    return {"status": "ready", "mode": "operator-granted-persistent-unity-editor",
            "issue": workspace.name, "workspaceRevision": revision,
            **verified_processes, "copyback": False}


def run_code(stage: Path, receipt: dict, code: str, script: str, exe: str, before: dict) -> dict:
    # Verify the *actual* CLI/Pipeline editor PID and active scene.
    ro.verify_pipeline(exe, stage, receipt)
    hierarchy = ro.cli_json(exe, stage, [
        "command", "get_scene_hierarchy",
        "--project-path", receipt["stagePath"], "--format", "json"
    ])
    scene = ro.compact_scene(hierarchy, SCENE)
    if scene["sceneDirty"]:
        deny("the active source scene has unsaved changes")

    started_assets = candidate(stage)
    before_source = protected(stage)
    if before_source != before:
        deny("protected stage project differs from operator grant")

    # Direct WSL -> Windows CLI process with an argv vector, NO shell, no raw
    # file paths or project paths supplied by model worker.
    response = ro.cli_json(exe, stage, [
        "command", "eval", "--code", code,
        "--project-path", receipt["stagePath"], "--format", "json"
    ])

    after_source = protected(stage)
    modified_protected = [k for k in before_source if before_source[k] != after_source[k]]
    after_assets = candidate(stage)
    added = sorted(after_assets.keys() - started_assets.keys())
    removed = sorted(started_assets.keys() - after_assets.keys())
    modified = sorted(k for k in started_assets.keys() & after_assets.keys()
                      if started_assets[k] != after_assets[k])
    # Source scene edits are permitted *inside the disposable stage* and
    # reported like any other asset. Only project configuration is pinned.
    # This permits iterative terrain/scene authoring without regranting.
    return {
        "success": not modified_protected,
        "operation": "editor-staged-code",
        "scriptPath": script,
        "scriptSha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "scenePath": SCENE,
        "sceneGameObjectCountBefore": scene["gameObjectCount"],
        "addedAssets": added[:100],
        "removedAssets": removed[:100],
        "modifiedAssets": modified[:100],
        "addedAssetHashes": {k: after_assets[k] for k in added[:100]},
        "modifiedAssetHashes": {k: after_assets[k] for k in modified[:100]},
        "assetCounts": {"added": len(added), "removed": len(removed), "modified": len(modified)},
        "protectedChanges": modified_protected,
        "requiresReview": True,
        "stageOnly": True,
        "copyback": False,
        "evalReturnedSuccess": response["success"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Host-owned Unity staged code operation")
    parser.add_argument("action", choices=("grant", "revoke", "preflight", "run"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    parser.add_argument("--session-windows", default="")
    parser.add_argument("--script", default="")
    parser.add_argument("--scene", default=SCENE)
    args = parser.parse_args()
    try:
        workspace, revision = ro.workspace_info(args.workspace)
        state = Path(args.state_root).expanduser().resolve()
        target = grant_path(state, workspace.name)
        if args.action == "revoke":
            target.unlink(missing_ok=True)
            print(json.dumps({"status": "revoked", "issue": workspace.name}))
            return 0
        if os.environ.get(FLAG) != "1":
            deny("same-account Unity staged execution is disabled by default")
        if args.scene != SCENE:
            deny("source scene must be the approved PlaytestScene")
        if args.action == "grant":
            if args.script:
                deny("operator grant does not select an individual script")
            if not args.session_windows:
                deny("grant requires operator-selected disposable Windows stage")
            stage, receipt = ro.stage_info(args.session_windows)
            baseline = protected(stage)
            ro.verify_pipeline(ro.cli_path(), stage, receipt)
            data = {
                "schemaVersion": 1, "enabled": True,
                "mode": "operator-trusted-staged-editor-code",
                "issue": workspace.name, "workspace": str(workspace),
                "workspaceRevision": revision, "scenePath": SCENE,
                "stagePath": receipt["stagePath"], "unityPid": receipt["unityPid"],
                "protectedBaseline": baseline,
            }
            ro.write_grant(target, data)
            print(json.dumps({"status": "granted", "issue": workspace.name,
                              "stageOnly": True, "warning": "C# runs with operator Windows identity"}))
            return 0

        if args.session_windows:
            deny("worker cannot supply a Windows stage")
        if args.action == "preflight":
            if args.session_windows or args.script:
                deny("persistent preflight takes no worker-selected stage or script")
            print(json.dumps(persistent_preflight(workspace, revision, state), separators=(",", ":")))
            return 0
        grant = current_grant(state, workspace, revision)
        stage, receipt = ro.stage_info(grant.get("stagePath"))
        if receipt["unityPid"] != grant.get("unityPid"):
            deny("Editor PID changed since grant")
        if protected(stage) != grant.get("protectedBaseline"):
            deny("protected source scene/project changed since grant")
        file = code_path(workspace, args.script)
        code = file.read_text(encoding="utf-8-sig")
        if not code.strip() or len(code.encode("utf-8")) > MAX_CODE:
            deny("script empty or exceeds 64 KiB")
        result = run_code(stage, receipt, code, args.script,
                          ro.cli_path(), grant["protectedBaseline"])
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result["success"] else 82
    except (ro.Denied, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Unity staged code: blocked: " + str(exc), file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
