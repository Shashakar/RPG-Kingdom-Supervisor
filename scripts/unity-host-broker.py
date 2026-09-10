#!/usr/bin/env python3
"""Host-owned broker for the RPG Kingdom Unity runner.

The broker is launched by run-symphony.sh in the operator's normal WSL shell.
Workers communicate through typed request files inside their own GH-N workspace;
only this host process invokes the direct WSL -> Windows Unity adapter.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

PROTOCOL_VERSION = 1
ALLOWED_OPERATIONS = {"health", "editmode", "playmode"}
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
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def parse_json_line(output: str) -> dict[str, Any] | None:
    for raw_line in reversed(output.splitlines()):
        line = raw_line.lstrip("\ufeff").strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def response_for_error(request_id: str, operation: str, code: int, message: str) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": operation,
        "status": "failed",
        "exitCode": code,
        "stdout": "",
        "stderr": message + "\n",
        "summary": None,
        "completedAt": utc_now(),
    }


def validate_lock(state_root: Path, workspace: Path) -> str | None:
    match = ISSUE_WORKSPACE.fullmatch(workspace.name)
    if match is None:
        return f"workspace '{workspace}' is not a GH issue workspace"

    expected_owner = f"GH-{match.group(1)}"
    lock_dir = state_root / "locks" / "unity-editor.lock"
    owner_path = lock_dir / "owner"
    workspace_path = lock_dir / "workspace"
    if not owner_path.is_file():
        return f"unity-editor is not locked for {expected_owner}"

    owner = owner_path.read_text(encoding="utf-8").strip()
    if owner != expected_owner:
        return f"unity-editor belongs to '{owner}', not '{expected_owner}'"

    if workspace_path.is_file():
        recorded = Path(workspace_path.read_text(encoding="utf-8").strip()).resolve()
        if recorded != workspace:
            return f"unity-editor lock workspace is '{recorded}', not '{workspace}'"

    return None


def handle_request(
    request_path: Path,
    workspace_root: Path,
    state_root: Path,
    host_runner: Path,
    command_timeout: int,
) -> dict[str, Any]:
    request_id = request_path.stem
    operation = "unknown"

    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        request_id = str(request.get("requestId", request_id))
        operation = str(request.get("operation", ""))
        test_filter = str(request.get("testFilter", ""))
        if request.get("protocolVersion") != PROTOCOL_VERSION:
            raise ValueError("unsupported broker protocol version")
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"unsupported Unity operation '{operation}'")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return response_for_error(request_id, operation, 64, f"RPG Kingdom Unity broker: invalid request: {exc}")

    try:
        workspace = request_path.parents[4].resolve()
    except IndexError:
        return response_for_error(request_id, operation, 81, "RPG Kingdom Unity broker: request is not inside a GH workspace")

    expected_request_dir = workspace / "Logs" / "SymphonyUnity" / ".broker" / "requests"
    if request_path.parent.resolve() != expected_request_dir.resolve():
        return response_for_error(request_id, operation, 81, "RPG Kingdom Unity broker: invalid request location")
    if workspace.parent != workspace_root or ISSUE_WORKSPACE.fullmatch(workspace.name) is None:
        return response_for_error(request_id, operation, 81, f"RPG Kingdom Unity broker: workspace '{workspace}' is outside '{workspace_root}'")

    if operation != "health":
        lock_error = validate_lock(state_root, workspace)
        if lock_error:
            return response_for_error(request_id, operation, 82, f"RPG Kingdom Unity broker: {lock_error}")

    command = ["bash", str(host_runner), operation, "--project", str(workspace)]
    if test_filter:
        command.extend(["--filter", test_filter])

    environment = os.environ.copy()
    environment["RPGK_SUPERVISOR_STATE_ROOT"] = str(state_root)

    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=command_timeout,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"RPG Kingdom Unity broker: host operation timed out after {command_timeout}s\n"
        exit_code = 124

    summary = parse_json_line(stdout)
    status = "completed" if exit_code == 0 else "failed"
    if operation in {"editmode", "playmode"} and isinstance(summary, dict):
        try:
            total = int(summary.get("total", -1))
        except (TypeError, ValueError):
            total = -1
        if total == 0:
            status = "NoTestsMatched"
            exit_code = 88
            stderr += "RPG Kingdom Unity broker: Unity completed but zero tests matched the requested filter.\n"

    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": operation,
        "status": status,
        "exitCode": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "summary": summary,
        "completedAt": utc_now(),
    }


def install_signal_handlers() -> None:
    def stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def main() -> int:
    parser = argparse.ArgumentParser(description="RPG Kingdom host-owned Unity execution broker")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--host-runner", required=True)
    parser.add_argument("--poll-ms", type=int, default=200)
    parser.add_argument("--command-timeout-seconds", type=int, default=3600)
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    host_runner = Path(args.host_runner).expanduser().resolve()
    broker_root = state_root / "unity-broker"
    broker_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)

    if not host_runner.is_file():
        print(f"RPG Kingdom Unity broker: host runner does not exist: {host_runner}", file=sys.stderr)
        return 66

    lock_handle = (broker_root / "broker.lock").open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("RPG Kingdom Unity broker: another broker already owns the host lock", file=sys.stderr)
        return 73

    install_signal_handlers()
    pid = os.getpid()
    (broker_root / "pid").write_text(f"{pid}\n", encoding="utf-8")
    status_path = broker_root / "status.json"
    last_result: dict[str, Any] | None = None

    def write_status(state: str, active_request: dict[str, Any] | None = None) -> None:
        atomic_json(
            status_path,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "state": state,
                "pid": pid,
                "workspaceRoot": str(workspace_root),
                "activeRequest": active_request,
                "lastResult": last_result,
                "updatedAt": utc_now(),
            },
        )

    write_status("ready")

    try:
        while not STOP_REQUESTED:
            request_paths = sorted(
                workspace_root.glob("GH-*/Logs/SymphonyUnity/.broker/requests/*.json"),
                key=lambda path: (path.stat().st_mtime_ns, str(path)),
            )
            if not request_paths:
                time.sleep(max(args.poll_ms, 25) / 1000.0)
                continue

            request_path = request_paths[0]
            request_id = request_path.stem
            broker_dir = request_path.parent.parent
            ack_path = broker_dir / "acks" / f"{request_id}.json"
            response_path = broker_dir / "responses" / f"{request_id}.json"
            atomic_json(ack_path, {"protocolVersion": PROTOCOL_VERSION, "requestId": request_id, "pid": pid, "acceptedAt": utc_now()})
            write_status("running", {"requestId": request_id, "workspace": str(request_path.parents[4])})

            response = handle_request(request_path, workspace_root, state_root, host_runner, args.command_timeout_seconds)
            atomic_json(response_path, response)
            try:
                request_path.unlink()
            except FileNotFoundError:
                pass

            last_result = {
                "requestId": response.get("requestId"),
                "operation": response.get("operation"),
                "status": response.get("status"),
                "exitCode": response.get("exitCode"),
                "summary": response.get("summary"),
                "completedAt": response.get("completedAt"),
            }
            write_status("ready")
    finally:
        write_status("stopped")
        try:
            (broker_root / "pid").unlink()
        except FileNotFoundError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
