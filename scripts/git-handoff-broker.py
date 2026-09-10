#!/usr/bin/env python3
"""Host-owned broker for bounded RPG Kingdom Git metadata/network handoff."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
ALLOWED_OPERATIONS = {"health", "prepare", "handoff"}
ISSUE_WORKSPACE = re.compile(r"^GH-(\d+)$")
STOP_REQUESTED = False


@dataclass
class RequestSpec:
    request_path: Path
    request_id: str
    operation: str
    workspace: Path


@dataclass
class ActiveOperation:
    spec: RequestSpec
    process: subprocess.Popen[Any]
    stdout_handle: Any
    stderr_handle: Any
    started_monotonic: float
    started_at: str


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


def broker_paths(request_path: Path) -> tuple[Path, Path]:
    request_id = request_path.stem
    broker_dir = request_path.parent.parent
    return broker_dir / "acks" / f"{request_id}.json", broker_dir / "responses" / f"{request_id}.json"


def acknowledge_request(request_path: Path, pid: int) -> None:
    ack_path, _ = broker_paths(request_path)
    atomic_json(
        ack_path,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_path.stem,
            "pid": pid,
            "acceptedAt": utc_now(),
        },
    )


def response_for_status(
    request_id: str,
    operation: str,
    status: str,
    code: int,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": operation,
        "status": status,
        "exitCode": code,
        "stdout": "",
        "stderr": message + "\n",
        "result": None,
        "details": details,
        "completedAt": utc_now(),
    }


def write_response(request_path: Path, response: dict[str, Any]) -> None:
    _, response_path = broker_paths(request_path)
    atomic_json(response_path, response)
    try:
        request_path.unlink()
    except FileNotFoundError:
        pass


def prepare_request(request_path: Path, workspace_root: Path) -> RequestSpec | dict[str, Any]:
    request_id = request_path.stem
    operation = "unknown"
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        request_id = str(request.get("requestId", request_id))
        operation = str(request.get("operation", ""))
        if request.get("protocolVersion") != PROTOCOL_VERSION:
            raise ValueError("unsupported broker protocol version")
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"unsupported Git handoff operation '{operation}'")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return response_for_status(request_id, operation, "InvalidRequest", 64, f"RPG Kingdom Git broker: invalid request: {exc}")

    try:
        workspace = request_path.parents[4].resolve()
    except IndexError:
        return response_for_status(request_id, operation, "InvalidWorkspace", 81, "RPG Kingdom Git broker: request is not inside a GH workspace")

    expected = workspace / "Logs" / "SymphonyGit" / ".broker" / "requests"
    if request_path.parent.resolve() != expected.resolve():
        return response_for_status(request_id, operation, "InvalidWorkspace", 81, "RPG Kingdom Git broker: invalid request location")
    if workspace.parent != workspace_root or ISSUE_WORKSPACE.fullmatch(workspace.name) is None:
        return response_for_status(
            request_id,
            operation,
            "InvalidWorkspace",
            81,
            f"RPG Kingdom Git broker: workspace '{workspace}' is outside '{workspace_root}'",
        )

    return RequestSpec(request_path=request_path, request_id=request_id, operation=operation, workspace=workspace)


def start_operation(spec: RequestSpec, workspace_root: Path, state_root: Path, host_runner: Path) -> ActiveOperation:
    command = [
        sys.executable,
        str(host_runner),
        "--request",
        str(spec.request_path),
        "--workspace",
        str(spec.workspace),
        "--workspace-root",
        str(workspace_root),
        "--state-root",
        str(state_root),
    ]
    stdout_handle = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    stderr_handle = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=spec.workspace,
            env=os.environ.copy(),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise

    return ActiveOperation(
        spec=spec,
        process=process,
        stdout_handle=stdout_handle,
        stderr_handle=stderr_handle,
        started_monotonic=time.monotonic(),
        started_at=utc_now(),
    )


def read_output(handle: Any) -> str:
    handle.flush()
    handle.seek(0)
    return str(handle.read())


def complete_operation(
    active: ActiveOperation,
    *,
    status_override: str | None = None,
    exit_code_override: int | None = None,
    extra_stderr: str = "",
) -> dict[str, Any]:
    stdout = read_output(active.stdout_handle)
    stderr = read_output(active.stderr_handle) + extra_stderr
    result = parse_json_line(stdout)
    exit_code = active.process.returncode if active.process.returncode is not None else 86
    if exit_code_override is not None:
        exit_code = exit_code_override
    status = "completed" if exit_code == 0 else "failed"
    if isinstance(result, dict):
        status = str(result.get("status", status))
        try:
            exit_code = int(result.get("exitCode", exit_code))
        except (TypeError, ValueError):
            exit_code = 86
    if status_override is not None:
        status = status_override

    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": active.spec.request_id,
        "operation": active.spec.operation,
        "status": status,
        "exitCode": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "details": None,
        "completedAt": utc_now(),
    }


def close_operation(active: ActiveOperation) -> None:
    active.stdout_handle.close()
    active.stderr_handle.close()


def terminate_process_group(process: subprocess.Popen[Any], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(grace_seconds, 0.0)
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=max(grace_seconds, 0.5))
    except subprocess.TimeoutExpired:
        pass


def active_snapshot(active: ActiveOperation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "requestId": active.spec.request_id,
        "workspace": str(active.spec.workspace),
        "issue": active.spec.workspace.name,
        "operation": active.spec.operation,
        "childPid": active.process.pid,
        "startedAt": active.started_at,
        "elapsedSeconds": int(max(0.0, time.monotonic() - active.started_monotonic)),
    }
    try:
        request_payload = json.loads(active.spec.request_path.read_text(encoding="utf-8"))
        if isinstance(request_payload, dict):
            payload["branch"] = request_payload.get("branch")
    except (OSError, json.JSONDecodeError):
        pass
    return payload


def last_result_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    return {
        "requestId": response.get("requestId"),
        "operation": response.get("operation"),
        "status": response.get("status"),
        "exitCode": response.get("exitCode"),
        "branch": result.get("branch") if isinstance(result, dict) else None,
        "commitSha": result.get("commitSha") if isinstance(result, dict) else None,
        "prNumber": result.get("prNumber") if isinstance(result, dict) else None,
        "prUrl": result.get("prUrl") if isinstance(result, dict) else None,
        "completedAt": response.get("completedAt"),
    }


def fail_stale_requests(workspace_root: Path) -> int:
    count = 0
    for request_path in sorted(workspace_root.glob("GH-*/Logs/SymphonyGit/.broker/requests/*.json"), key=str):
        request_id = request_path.stem
        operation = "unknown"
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                request_id = str(payload.get("requestId", request_id))
                operation = str(payload.get("operation", operation))
        except (OSError, json.JSONDecodeError):
            pass
        write_response(
            request_path,
            response_for_status(
                request_id,
                operation,
                "StaleRequest",
                89,
                "RPG Kingdom Git broker: request predates this broker process and will not be replayed",
            ),
        )
        count += 1
    return count


def install_signal_handlers() -> None:
    def stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def main() -> int:
    parser = argparse.ArgumentParser(description="RPG Kingdom host-owned Git handoff broker")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--host-runner", required=True)
    parser.add_argument("--poll-ms", type=int, default=200)
    parser.add_argument("--command-timeout-seconds", type=int, default=300)
    parser.add_argument("--kill-grace-seconds", type=float, default=5.0)
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    host_runner = Path(args.host_runner).expanduser().resolve()
    broker_root = state_root / "git-broker"
    broker_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)

    if not host_runner.is_file():
        print(f"RPG Kingdom Git broker: host runner does not exist: {host_runner}", file=sys.stderr)
        return 66

    lock_handle = (broker_root / "broker.lock").open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("RPG Kingdom Git broker: another broker already owns the host lock", file=sys.stderr)
        return 73

    install_signal_handlers()
    pid = os.getpid()
    (broker_root / "pid").write_text(f"{pid}\n", encoding="utf-8")
    status_path = broker_root / "status.json"
    last_result: dict[str, Any] | None = None
    active: ActiveOperation | None = None
    last_status_write = 0.0

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

    stale_count = fail_stale_requests(workspace_root)
    write_status("ready")
    if stale_count:
        print(f"RPG Kingdom Git broker: rejected {stale_count} stale request(s)", file=sys.stderr)

    try:
        while not STOP_REQUESTED:
            now = time.monotonic()

            if active is not None:
                if active.process.poll() is not None:
                    response = complete_operation(active)
                    write_response(active.spec.request_path, response)
                    last_result = last_result_snapshot(response)
                    close_operation(active)
                    active = None
                    write_status("ready")
                    last_status_write = now
                elif now - active.started_monotonic >= max(args.command_timeout_seconds, 1):
                    terminate_process_group(active.process, args.kill_grace_seconds)
                    response = complete_operation(
                        active,
                        status_override="TimedOut",
                        exit_code_override=124,
                        extra_stderr="RPG Kingdom Git broker: host handoff exceeded the configured timeout; process group was terminated.\n",
                    )
                    write_response(active.spec.request_path, response)
                    last_result = last_result_snapshot(response)
                    close_operation(active)
                    active = None
                    write_status("ready")
                    last_status_write = now
                elif now - last_status_write >= 1.0:
                    write_status("running", active_snapshot(active))
                    last_status_write = now

            request_paths = sorted(workspace_root.glob("GH-*/Logs/SymphonyGit/.broker/requests/*.json"), key=str)
            for request_path in request_paths:
                if active is not None and request_path == active.spec.request_path:
                    continue

                prepared = prepare_request(request_path, workspace_root)
                if isinstance(prepared, dict):
                    write_response(request_path, prepared)
                    continue

                acknowledge_request(request_path, pid)
                if active is not None:
                    snapshot = active_snapshot(active)
                    write_response(
                        request_path,
                        response_for_status(
                            prepared.request_id,
                            prepared.operation,
                            "HostBusy",
                            87,
                            f"RPG Kingdom Git broker: host is busy with {snapshot['issue']} {snapshot['operation']}",
                            details={"activeRequest": snapshot},
                        ),
                    )
                    continue

                try:
                    active = start_operation(prepared, workspace_root, state_root, host_runner)
                except Exception as exc:
                    write_response(
                        request_path,
                        response_for_status(
                            prepared.request_id,
                            prepared.operation,
                            "failed",
                            86,
                            f"RPG Kingdom Git broker: failed to start host handoff: {exc}",
                        ),
                    )
                    active = None
                    continue
                write_status("running", active_snapshot(active))
                last_status_write = now

            time.sleep(max(args.poll_ms, 25) / 1000.0)
    finally:
        if active is not None:
            terminate_process_group(active.process, args.kill_grace_seconds)
            response = complete_operation(
                active,
                status_override="BrokerStopped",
                exit_code_override=90,
                extra_stderr="RPG Kingdom Git broker: broker stopped while host handoff was active; process group was terminated.\n",
            )
            write_response(active.spec.request_path, response)
            last_result = last_result_snapshot(response)
            close_operation(active)
        write_status("stopped")
        try:
            (broker_root / "pid").unlink()
        except FileNotFoundError:
            pass
        lock_handle.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
