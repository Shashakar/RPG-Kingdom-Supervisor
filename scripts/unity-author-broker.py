#!/usr/bin/env python3
"""Host-owned broker for explicitly authorized Unity scene authoring."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from typing import Any

PROTOCOL_VERSION = 1
SUPPORTED_TIERS = frozenset({"mechanical", "mechanical-structural", "existing-scene-composition", "new-scene-composition"})
ISSUE_WORKSPACE = re.compile(r"^GH-(\d+)$")
STOP_REQUESTED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def parse_json_line(output: str) -> dict[str, Any] | None:
    for raw in reversed(output.splitlines()):
        text = raw.lstrip("\ufeff").strip()
        if not text.startswith("{"):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def response(request_id: str, status: str, code: int, stdout: str = "", stderr: str = "", result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": "author",
        "status": status,
        "exitCode": code,
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "completedAt": utc_now(),
    }


def request_paths(request_path: Path) -> tuple[Path, Path]:
    broker = request_path.parent.parent
    request_id = request_path.stem
    return broker / "acks" / f"{request_id}.json", broker / "responses" / f"{request_id}.json"


def write_response(request_path: Path, payload: dict[str, Any]) -> None:
    _, path = request_paths(request_path)
    atomic_json(path, payload)
    try:
        request_path.unlink()
    except FileNotFoundError:
        pass


def validate_shared_lock(state_root: Path, workspace: Path) -> str | None:
    match = ISSUE_WORKSPACE.fullmatch(workspace.name)
    if match is None:
        return "workspace is not a GH issue workspace"
    expected = f"GH-{match.group(1)}"
    lock = state_root / "locks" / "unity-editor.lock"
    try:
        owner = (lock / "owner").read_text(encoding="utf-8").strip()
        recorded = Path((lock / "workspace").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return f"unity-editor is not locked for {expected}"
    if owner != expected:
        return f"unity-editor belongs to '{owner}', not '{expected}'"
    if recorded != workspace:
        return f"unity-editor lock workspace is '{recorded}', not '{workspace}'"
    return None


def validate_authorization(state_root: Path, workspace: Path, requested_tier: str, requested_scene: str) -> str | None:
    match = ISSUE_WORKSPACE.fullmatch(workspace.name)
    if match is None:
        return "workspace is not a GH issue workspace"
    expected = f"GH-{match.group(1)}"
    marker = state_root / "authoring" / f"{expected}.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"{expected} has no current scene-authoring authorization"
    if payload.get("protocolVersion") != PROTOCOL_VERSION:
        return "scene-authoring authorization uses an unsupported protocol version"
    if payload.get("issue") != expected:
        return "scene-authoring authorization belongs to a different issue"
    authorized_tier = payload.get("tier")
    if authorized_tier not in SUPPORTED_TIERS:
        return f"scene-authoring authorization has unsupported tier '{authorized_tier}'"
    if authorized_tier != requested_tier:
        return f"scene-authoring authorization tier '{authorized_tier}' does not permit requested tier '{requested_tier}'"
    if requested_tier == "existing-scene-composition" and payload.get("scene") != requested_scene:
        return f"scene-authoring authorization does not permit requested scene '{requested_scene}'"
    try:
        authorized_workspace = Path(str(payload.get("workspace", ""))).resolve()
    except OSError:
        return "scene-authoring authorization workspace is invalid"
    if authorized_workspace != workspace:
        return "scene-authoring authorization belongs to a different workspace"
    return None


def validate_request(request_path: Path, workspace_root: Path, state_root: Path) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    request_id = request_path.stem
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request must be an object")
        if payload.get("protocolVersion") != PROTOCOL_VERSION or payload.get("operation") != "author":
            raise ValueError("unsupported request protocol/operation")
        authoring = payload.get("authoring")
        if not isinstance(authoring, dict):
            raise ValueError("authoring payload is required")
        if authoring.get("protocolVersion") != PROTOCOL_VERSION:
            raise ValueError("unsupported authoring protocol version")
        requested_tier = authoring.get("tier")
        if requested_tier not in SUPPORTED_TIERS:
            raise ValueError(f"unsupported scene-authoring tier '{requested_tier}'")
        scene = authoring.get("scene")
        source_scene = authoring.get("sourceScene")
        operations = authoring.get("operations")
        if not isinstance(scene, str) or not scene.startswith("Assets/") or not scene.endswith(".unity") or ".." in scene or "\\\\" in scene:
            raise ValueError("scene must be a project-relative Assets/*.unity path")
        if source_scene is not None and source_scene != "":
            if not isinstance(source_scene, str) or not source_scene.startswith("Assets/") or not source_scene.endswith(".unity") or ".." in source_scene or "\\\\" in source_scene:
                raise ValueError("sourceScene must be a project-relative Assets/*.unity path")
            if source_scene == scene:
                raise ValueError("sourceScene and scene must differ")
        if requested_tier != "new-scene-composition" and source_scene not in (None, ""):
            raise ValueError("sourceScene is only valid for new-scene-composition")
        if not isinstance(operations, list) or not operations:
            raise ValueError("one or more authoring operations are required")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, None, response(request_id, "rejected", 64, stderr=f"RPG Kingdom Unity authoring broker: invalid request: {exc}\n")

    try:
        workspace = request_path.parents[4].resolve()
    except IndexError:
        return None, None, response(request_id, "rejected", 81, stderr="RPG Kingdom Unity authoring broker: invalid request location\n")
    expected_dir = workspace / "Logs" / "SymphonyUnity" / ".author-broker" / "requests"
    if request_path.parent.resolve() != expected_dir.resolve() or workspace.parent != workspace_root or ISSUE_WORKSPACE.fullmatch(workspace.name) is None:
        return None, None, response(request_id, "rejected", 81, stderr="RPG Kingdom Unity authoring broker: request is outside the configured GH workspace root\n")

    error = validate_shared_lock(state_root, workspace)
    if error:
        return None, None, response(request_id, "rejected", 82, stderr=f"RPG Kingdom Unity authoring broker: {error}\n")
    error = validate_authorization(state_root, workspace, requested_tier, scene)
    if error:
        return None, None, response(request_id, "rejected", 83, stderr=f"RPG Kingdom Unity authoring broker: {error}\n")
    return workspace, payload, None


def status_payload(pid: int, workspace_root: Path, state: str, active: dict[str, Any] | None = None, last_result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "pid": pid,
        "state": state,
        "workspaceRoot": str(workspace_root),
        "activeRequest": active,
        "lastResult": last_result,
        "updatedAt": utc_now(),
    }


def terminate_group(process: subprocess.Popen[str], grace: float) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--host-runner", required=True)
    parser.add_argument("--command-timeout-seconds", type=float, default=900)
    parser.add_argument("--kill-grace-seconds", type=float, default=5)
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    host_runner = Path(args.host_runner).expanduser().resolve()
    broker_root = state_root / "unity-author-broker"
    status_path = broker_root / "status.json"
    pid_path = broker_root / "pid"
    broker_root.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    last_result: dict[str, Any] | None = None

    def stop_handler(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    atomic_json(status_path, status_payload(os.getpid(), workspace_root, "ready"))

    try:
        while not STOP_REQUESTED:
            requests = sorted(workspace_root.glob("GH-*/Logs/SymphonyUnity/.author-broker/requests/*.json"), key=lambda path: path.stat().st_mtime)
            if not requests:
                time.sleep(args.poll_seconds)
                continue
            request_path = requests[0]
            workspace, payload, rejection = validate_request(request_path, workspace_root, state_root)
            if rejection is not None:
                last_result = rejection
                write_response(request_path, rejection)
                atomic_json(status_path, status_payload(os.getpid(), workspace_root, "ready", last_result=last_result))
                continue
            assert workspace is not None and payload is not None
            request_id = request_path.stem
            ack_path, _ = request_paths(request_path)
            atomic_json(ack_path, {"protocolVersion": 1, "requestId": request_id, "pid": os.getpid(), "acceptedAt": utc_now()})
            authoring = payload["authoring"]
            active = {
                "requestId": request_id,
                "issue": workspace.name,
                "workspace": str(workspace),
                "tier": authoring["tier"],
                "scene": authoring["scene"],
                "sourceScene": authoring.get("sourceScene"),
                "startedAt": utc_now(),
            }
            atomic_json(status_path, status_payload(os.getpid(), workspace_root, "running", active=active, last_result=last_result))

            process = subprocess.Popen(
                ["bash", str(host_runner), "--project", str(workspace), "--request", str(request_path)],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=args.command_timeout_seconds)
                code = int(process.returncode or 0)
                result = parse_json_line(stdout)
                status = "completed" if code == 0 and isinstance(result, dict) and result.get("success") is True else "failed"
                if code == 0 and status != "completed":
                    code = 86
                    stderr += "RPG Kingdom Unity authoring broker: host returned success without a successful structured result.\n"
                completed = response(request_id, status, code, stdout, stderr, result)
            except subprocess.TimeoutExpired:
                terminate_group(process, args.kill_grace_seconds)
                stdout, stderr = process.communicate()
                completed = response(request_id, "TimedOut", 85, stdout, stderr + "RPG Kingdom Unity authoring broker: host authoring operation timed out.\n")
            last_result = completed
            atomic_json(status_path, status_payload(os.getpid(), workspace_root, "ready", last_result=last_result))
            write_response(request_path, completed)
    finally:
        atomic_json(status_path, status_payload(os.getpid(), workspace_root, "stopped", last_result=last_result))
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
