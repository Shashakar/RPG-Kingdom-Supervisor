#!/usr/bin/env python3
"""Host broker transport for fixed read-only Windows isolated task mailbox.

No worker-selected task, Windows path, shell, C# or arbitrary command.
The Windows sandbox agent (separately registered by an administrator) owns
the Unity Editor and executes the hardcoded get_scene_hierarchy operation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("unity_cli_ro_isolated_dependency",
                                               BASE / "unity-cli-readonly.py")
assert SPEC and SPEC.loader
ro = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ro
SPEC.loader.exec_module(ro)

ENABLED = "RPGK_UNITY_ISOLATED_READONLY_ENABLED"
SID = re.compile(r"^S-1-5-21-(?:[0-9]+-){3}[0-9]+$")
HOST = re.compile(r"^isolated-[0-9a-f]{32}$")
SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"


def deny(message: str) -> None:
    raise ro.Denied(message)


def host_dir_windows(raw: str) -> Path:
    # Only the dedicated ProgramData subfolder can be selected by the operator.
    if not isinstance(raw, str):
        deny("host path missing")
    parts = raw.replace("/", "\\").split("\\")
    if (len(parts) != 5 or parts[:4] !=
            ["C:", "ProgramData", "RPGKingdomSupervisor", "IsolatedUnity"]
            or not HOST.fullmatch(parts[4])):
        deny("isolated host must be a canonical C:\\ProgramData\\RPGKingdomSupervisor\\IsolatedUnity\\isolated-* directory")
    result = subprocess.run(["wslpath", "-u", raw], capture_output=True,
                            text=True, timeout=10, check=False)
    if result.returncode:
        deny("Windows isolated host path cannot be mapped")
    folder = Path(result.stdout.strip())
    if folder.is_symlink() or not folder.is_dir():
        deny("isolated host directory is missing or linked")
    folder = folder.resolve(strict=True)
    if folder.name != parts[4]:
        deny("isolated host path changed through links")
    return folder


def trusted_config(host: Path) -> dict:
    try:
        cfg = json.loads((host / "config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("missing Windows host config") from exc
    if (cfg.get("schemaVersion") != 1 or cfg.get("enabled") is not True
            or cfg.get("mode") != "isolated-readonly"
            or cfg.get("hostId") != host.name
            or not SID.fullmatch(str(cfg.get("SandboxSid", "")))
            or cfg.get("AllowedScene") != SCENE):
        deny("invalid or disabled isolated Windows host configuration")
    if (not str(cfg.get("StagePath", "")).endswith("\\project")
            or not str(cfg.get("QueueRoot", "")).endswith("\\queue")):
        deny("Windows host configuration has unexpected internal paths")
    queue = host / "queue"
    if queue.is_symlink() or not queue.is_dir():
        deny("invalid Windows host mailbox")
    return cfg


def health(host: Path, cfg: dict) -> int:
    try:
        proof = json.loads((host / "queue/probes.json").read_text(encoding="utf-8-sig"))
        editor = json.loads((host / "queue/editor.json").read_text(encoding="utf-8-sig"))
        ready = json.loads((host / "queue/ready.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("Windows agent has no valid denial-probe/Editor receipts") from exc
    if (proof.get("status") != "denial-probes-passed"
            or proof.get("sandboxSid") != cfg["SandboxSid"]
            or proof.get("readProbeCount", 0) < 2
            or proof.get("writeProbeCount", 0) < 1
            or proof.get("readyForArbitraryEval") is not False
            or proof.get("readyForCopyback") is not False
            or proof.get("configAndAgentWriteDenied") is not True
            or proof.get("operatorRequestWriteDenied") is not True
            or proof.get("stageWriteConfirmed") is not True
            or ready.get("status") != "scene-opened"
            or ready.get("sandboxSid") != cfg["SandboxSid"]
            or ready.get("scenePath") != SCENE
            or ready.get("unityPid") != editor.get("unityPid")
            or ready.get("readyForArbitraryEval") is not False
            or ready.get("readyForCopyback") is not False
            or editor.get("status") != "launched"
            or editor.get("sandboxSid") != cfg["SandboxSid"]
            or editor.get("stagePath") != cfg["StagePath"]
            or not isinstance(editor.get("unityPid"), int)
            or editor["unityPid"] <= 0):
        deny("sandbox negative-access/Editor launch report is not acceptable")
    return editor["unityPid"]


def grant_path(state: Path, issue: str) -> Path:
    if not ro.ISSUE.fullmatch(issue):
        deny("invalid issue")
    return state / "unity-isolated-grants" / (issue + ".json")


def operator_grant(workspace: Path, revision: str, state: Path, host_windows: str) -> dict:
    host = host_dir_windows(host_windows)
    cfg = trusted_config(host)
    pid = health(host, cfg)
    record = {
        "schemaVersion": 1, "enabled": True, "mode": "isolated-scene-summary",
        "issue": workspace.name, "workspace": str(workspace),
        "workspaceRevision": revision, "scenePath": SCENE,
        "hostWindows": host_windows, "hostId": host.name,
        "sandboxSid": cfg["SandboxSid"], "unityPid": pid,
    }
    ro.write_grant(grant_path(state, workspace.name), record)
    return {"success": True, "status": "granted-readonly", "issue": workspace.name,
            "scenePath": SCENE, "readyForArbitraryEval": False, "copyback": False}


def run(workspace: Path, revision: str, state: Path) -> dict:
    try:
        record = json.loads(grant_path(state, workspace.name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("no explicit operator grant for isolated Windows host") from exc
    if (record.get("schemaVersion") != 1 or record.get("enabled") is not True
            or record.get("mode") != "isolated-scene-summary"
            or record.get("issue") != workspace.name
            or record.get("workspace") != str(workspace)
            or record.get("workspaceRevision") != revision
            or record.get("scenePath") != SCENE):
        deny("operator grant no longer binds this issue/revision/scene")
    host = host_dir_windows(record.get("hostWindows"))
    cfg = trusted_config(host)
    if (record.get("hostId") != host.name
            or record.get("sandboxSid") != cfg["SandboxSid"]
            or record.get("unityPid") != health(host, cfg)):
        deny("sandbox identity/Editor PID changed since operator grant")

    # Small, random, one-shot request. The sandbox never accepts code/URLs.
    request_id = "rpgk-" + secrets.token_hex(16)
    inbox = host / "queue/requests"
    outbox = host / "queue/responses"
    for folder in (inbox, outbox):
        if folder.is_symlink() or not folder.is_dir():
            deny("isolated mailbox missing or linked")
    request = inbox / (request_id + ".json")
    response = outbox / (request_id + ".json")
    temp = inbox / ("." + request_id + ".tmp")
    try:
        temp.write_text(json.dumps({
            "schemaVersion": 1, "requestId": request_id,
            "operation": "get_scene_hierarchy", "scenePath": SCENE,
        }, separators=(",", ":")), encoding="utf-8")
        temp.rename(request)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if response.is_file():
                if response.stat().st_size > 16384:
                    deny("Windows mailbox summary is too large")
                result = json.loads(response.read_text(encoding="utf-8-sig"))
                if (not isinstance(result, dict)
                        or result.get("schemaVersion") != 1
                        or result.get("requestId") != request_id
                        or result.get("operation") != "get_scene_hierarchy"
                        or result.get("success") is not True
                        or result.get("scenePath") != SCENE
                        or result.get("stageOnly") is not True
                        or result.get("copyback") is not False
                        or not isinstance(result.get("gameObjectCount"), int)
                        or result["gameObjectCount"] < 1
                        or result["gameObjectCount"] > 200000):
                    deny("Windows agent failed, rejected, or returned invalid summary")
                return {
                    "success": True, "operation": "isolated-scene-summary",
                    "scenePath": SCENE, "gameObjectCount": result["gameObjectCount"],
                    "rootCount": result.get("rootCount"),
                    "terrainPaths": result.get("terrainPaths", [])[:64],
                    "stageOnly": True, "copyback": False,
                    "readyForArbitraryEval": False,
                }
            time.sleep(0.2)
        deny("Windows sandbox did not respond within deadline")
    finally:
        # Only remove our own nonce-named mailbox files. Sandbox never deletes inbox.
        for file in (temp, request, response):
            try:
                file.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("grant", "revoke", "run"))
    p.add_argument("--workspace", required=True)
    p.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    p.add_argument("--host-dir-windows", default="")
    p.add_argument("--scene", default=SCENE)
    args = p.parse_args()
    try:
        if os.environ.get(ENABLED) != "1":
            deny("isolated read-only Windows task bridge disabled by default")
        ws, revision = ro.workspace_info(args.workspace)
        state = Path(args.state_root).expanduser().resolve()
        if args.scene != SCENE:
            deny("unapproved scene target")
        if args.action == "revoke":
            grant_path(state, ws.name).unlink(missing_ok=True)
            result = {"success": True, "status": "revoked", "issue": ws.name}
        elif args.action == "grant":
            if not args.host_dir_windows:
                deny("grant requires trusted operator-selected host")
            result = operator_grant(ws, revision, state, args.host_dir_windows)
        else:
            if args.host_dir_windows:
                deny("worker cannot select Windows host")
            result = run(ws, revision, state)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (ro.Denied, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Isolated Windows Unity bridge: blocked: " + str(exc), file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
