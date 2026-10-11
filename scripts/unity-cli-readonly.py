#!/usr/bin/env python3
"""Operator-granted, read-only Unity CLI scene summary for the Supervisor host broker.

No generic command, C# evaluation, editor writes, or copyback are exposed.
This is a capability gate, NOT an OS isolation boundary for untrusted C#.
"""
from __future__ import annotations

import argparse
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SCHEMA = 1
ISSUE = re.compile(r"GH-[1-9][0-9]*\Z")
SESSION = re.compile(r"pilot-[0-9a-f]{32}\Z")
MAX_OUTPUT = 16 * 1024 * 1024
TIMEOUT = 90


class Denied(Exception):
    pass


def normalized_windows(path: str) -> str:
    if not isinstance(path, str) or not re.match(r"^[A-Za-z]:\\", path):
        raise Denied("stage must be an absolute Windows drive path")
    norm = ntpath.normpath(path)
    if norm != path.rstrip("\\") or ".." in norm.split("\\"):
        raise Denied("noncanonical stage path")
    parts = norm.replace("/", "\\").split("\\")
    if len(parts) < 7 or parts[-3:] != ["RPGKingdomSupervisor", "UnityCliPilots", parts[-1]]:
        raise Denied("stage is not under the dedicated UnityCliPilots root")
    if not SESSION.fullmatch(parts[-1]):
        raise Denied("invalid stage identifier")
    if any(part.lower() in {"temp", "tmp"} for part in parts[2:-3]):
        raise Denied("unexpected stage root")
    return norm


def stage_info(windows_path: str) -> tuple[Path, dict]:
    path = normalized_windows(windows_path)
    convert = subprocess.run(["wslpath", "-u", path], text=True, capture_output=True,
                             timeout=10, check=False)
    if convert.returncode != 0 or not convert.stdout.strip():
        raise Denied("stage path cannot be mapped to WSL")
    mapped = Path(convert.stdout.strip()).resolve(strict=True)
    if mapped.is_symlink() or not mapped.is_dir():
        raise Denied("stage directory invalid")
    try:
        receipt = json.loads((mapped / "session.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise Denied("missing or invalid stage receipt") from exc
    if (receipt.get("schemaVersion") != SCHEMA
            or receipt.get("sessionId") != mapped.name
            or ntpath.normcase(str(receipt.get("stagePath", ""))) != ntpath.normcase(path)
            or not isinstance(receipt.get("unityPid"), int)
            or receipt["unityPid"] <= 0):
        raise Denied("stage receipt binding mismatch")
    return mapped, receipt


def workspace_info(raw: str) -> tuple[Path, str]:
    workspace = Path(raw).expanduser().resolve(strict=True)
    allowed_root = Path(os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT",
                                       str(Path.home() / "code/rpg-kingdom-symphony-workspaces"))).expanduser().resolve()
    if not ISSUE.fullmatch(workspace.name) or workspace.parent != allowed_root:
        raise Denied("not a direct GH-N issue workspace")
    result = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", result.stdout.strip()):
        raise Denied("issue workspace has no valid Git HEAD")
    return workspace, result.stdout.strip()


def scene_path(raw: str) -> str:
    if (not isinstance(raw, str) or not raw.startswith("Assets/")
            or not raw.endswith(".unity") or "\\" in raw
            or any(x in ("", ".", "..") for x in raw.split("/"))):
        raise Denied("scene must be a canonical Assets/*.unity path")
    return raw


def grant_file(state_root: Path, issue: str) -> Path:
    if not ISSUE.fullmatch(issue):
        raise Denied("invalid issue identifier")
    return state_root / "unity-cli-grants" / f"{issue}.json"


def read_grant(path: Path) -> dict:
    try:
        grant = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Denied("no valid operator grant") from exc
    if (not isinstance(grant, dict) or grant.get("schemaVersion") != SCHEMA
            or grant.get("enabled") is not True
            or grant.get("mode") != "scene-summary"):
        raise Denied("operator grant disabled or unsupported")
    return grant


def write_grant(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".grant-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cli_path() -> str:
    exe = os.environ.get("RPGK_UNITY_CLI_EXE") or shutil.which("unity.exe")
    if not exe or not Path(exe).is_file():
        raise Denied("RPGK_UNITY_CLI_EXE must point to the Windows unity.exe exposed in WSL")
    return str(Path(exe).resolve())


def cli_json(exe: str, stage: Path, args: list[str]) -> dict:
    with tempfile.TemporaryFile(mode="w+b") as out, tempfile.TemporaryFile(mode="w+b") as err:
        try:
            process = subprocess.run([exe] + args, stdout=out, stderr=err,
                                     cwd=str(stage), timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired as exc:
            raise Denied("Unity CLI command timed out") from exc
        if process.returncode != 0:
            raise Denied("Unity CLI command returned an error")
        if out.tell() > MAX_OUTPUT:
            raise Denied("Unity CLI output exceeded the read-only size limit")
        out.seek(0)
        try:
            result = json.load(out)
        except (UnicodeError, ValueError) as exc:
            raise Denied("Unity CLI returned malformed JSON") from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        raise Denied("Unity CLI reported an unsuccessful command")
    return result


def verify_pipeline(exe: str, stage: Path, receipt: dict) -> None:
    listing = cli_json(exe, stage, ["pipeline", "list", "--format", "json"])
    instances = listing.get("data", {}).get("instances", [])
    if not isinstance(instances, list):
        raise Denied("invalid Pipeline discovery response")
    match = [x for x in instances if isinstance(x, dict)
             and ntpath.normcase(str(x.get("projectPath", ""))) ==
                 ntpath.normcase(receipt["stagePath"])
             and x.get("pid") == receipt["unityPid"]]
    if len(match) != 1:
        raise Denied("owned Unity Editor process not present in Pipeline discovery")
    instance = match[0]
    if (instance.get("isRunning") is not True
            or instance.get("hasPipelinePackage") is not True
            or instance.get("pipelineServer", {}).get("isReachable") is not True):
        raise Denied("owned Editor Pipeline is unavailable")


def compact_scene(payload: dict, expected: str) -> dict:
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or result.get("scenePath") != expected:
        raise Denied("active Editor scene differs from granted scene")
    if result.get("isDirty") is not False:
        raise Denied("active Editor scene has unsaved changes")
    roots = result.get("roots")
    if not isinstance(roots, list):
        raise Denied("Unity returned no valid scene hierarchy")
    count = 0
    terrain = []
    stack = list(roots)
    while stack:
        obj = stack.pop()
        if not isinstance(obj, dict) or not isinstance(obj.get("children"), list):
            raise Denied("invalid hierarchy node")
        count += 1
        if count > 200000:
            raise Denied("hierarchy node limit exceeded")
        if "Terrain" in obj.get("components", []):
            terrain.append(str(obj.get("hierarchyPath", "")))
        stack.extend(obj["children"])
    return {
        "success": True,
        "operation": "editor-scene-summary",
        "sceneName": result.get("sceneName"),
        "scenePath": expected,
        "sceneDirty": False,
        "rootNames": [str(obj.get("name", "")) for obj in roots][:64],
        "rootCount": len(roots),
        "gameObjectCount": count,
        "terrainPaths": terrain[:64],
        "terrainCount": len(terrain),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Operator-granted Unity scene summary")
    parser.add_argument("action", choices=("grant", "revoke", "inspect"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    parser.add_argument("--session-windows", default="")
    parser.add_argument("--scene", default="")
    args = parser.parse_args()
    try:
        workspace, sha = workspace_info(args.workspace)
        state = Path(args.state_root).expanduser().resolve()
        grant_path = grant_file(state, workspace.name)
        if args.action == "revoke":
            grant_path.unlink(missing_ok=True)
            print(json.dumps({"status": "revoked", "issue": workspace.name}))
            return 0
        if args.action == "grant":
            if os.environ.get("RPGK_UNITY_CLI_READONLY_ENABLED") != "1":
                raise Denied("explicit host enable flag is required")
            mapped, receipt = stage_info(args.session_windows)
            scene = scene_path(args.scene)
            if not (mapped / scene).is_file():
                raise Denied("granted scene does not exist in staged Unity project")
            grant = {
                "schemaVersion": SCHEMA, "enabled": True, "mode": "scene-summary",
                "issue": workspace.name, "workspace": str(workspace),
                "workspaceRevision": sha, "stagePath": receipt["stagePath"],
                "unityPid": receipt["unityPid"], "scenePath": scene,
            }
            write_grant(grant_path, grant)
            print(json.dumps({"status": "granted", "issue": workspace.name,
                              "scenePath": scene, "workspaceRevision": sha}))
            return 0
        if os.environ.get("RPGK_UNITY_CLI_READONLY_ENABLED") != "1":
            raise Denied("read-only Editor lane is disabled by default")
        if args.session_windows:
            raise Denied("worker requests cannot select a session")
        grant = read_grant(grant_path)
        expected = scene_path(args.scene)
        if (grant.get("issue") != workspace.name or grant.get("workspace") != str(workspace)
                or grant.get("workspaceRevision") != sha
                or grant.get("scenePath") != expected):
            raise Denied("issue/workspace/revision/scene not covered by grant")
        mapped, receipt = stage_info(str(grant.get("stagePath", "")))
        if grant.get("unityPid") != receipt["unityPid"]:
            raise Denied("stage Editor PID changed since grant")
        exe = cli_path()
        verify_pipeline(exe, mapped, receipt)
        hierarchy = cli_json(exe, mapped, [
            "command", "get_scene_hierarchy", "--project-path", receipt["stagePath"],
            "--format", "json"
        ])
        print(json.dumps(compact_scene(hierarchy, expected), separators=(",", ":")))
        return 0
    except (Denied, OSError, subprocess.SubprocessError) as exc:
        print(f"Unity CLI read-only: blocked: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
