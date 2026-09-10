#!/usr/bin/env python3
"""Host-owned broker for the RPG Kingdom Unity runner.

The broker is launched by run-symphony.sh in the operator's normal WSL shell.
Workers communicate through typed request files inside their own GH-N workspace;
only this host process invokes the direct WSL -> Windows Unity adapter.
"""

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
ALLOWED_OPERATIONS = {"health", "editmode", "playmode"}
ISSUE_WORKSPACE = re.compile(r"^GH-(\d+)$")
STOP_REQUESTED = False


@dataclass
class RequestSpec:
    request_path: Path
    request_id: str
    operation: str
    test_filter: str
    workspace: Path


@dataclass
class ActiveOperation:
    spec: RequestSpec
    response_path: Path
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
        "summary": None,
        "details": details,
        "completedAt": utc_now(),
    }


def response_for_error(request_id: str, operation: str, code: int, message: str) -> dict[str, Any]:
    return response_for_status(request_id, operation, "failed", code, message)


def broker_paths(request_path: Path) -> tuple[Path, Path]:
    request_id = request_path.stem
    broker_dir = request_path.parent.parent
    return (
        broker_dir / "acks" / f"{request_id}.json",
        broker_dir / "responses" / f"{request_id}.json",
    )


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


def write_response(request_path: Path, response: dict[str, Any]) -> None:
    _, response_path = broker_paths(request_path)
    atomic_json(response_path, response)
    try:
        request_path.unlink()
    except FileNotFoundError:
        pass


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


def prepare_request(
    request_path: Path,
    workspace_root: Path,
    state_root: Path,
) -> RequestSpec | dict[str, Any]:
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
        return response_for_error(
            request_id,
            operation,
            64,
            f"RPG Kingdom Unity broker: invalid request: {exc}",
        )

    try:
        workspace = request_path.parents[4].resolve()
    except IndexError:
        return response_for_error(
            request_id,
            operation,
            81,
            "RPG Kingdom Unity broker: request is not inside a GH workspace",
        )

    expected_request_dir = workspace / "Logs" / "SymphonyUnity" / ".broker" / "requests"
    if request_path.parent.resolve() != expected_request_dir.resolve():
        return response_for_error(
            request_id,
            operation,
            81,
            "RPG Kingdom Unity broker: invalid request location",
        )
    if workspace.parent != workspace_root or ISSUE_WORKSPACE.fullmatch(workspace.name) is None:
        return response_for_error(
            request_id,
            operation,
            81,
            f"RPG Kingdom Unity broker: workspace '{workspace}' is outside '{workspace_root}'",
        )

    if operation != "health":
        lock_error = validate_lock(state_root, workspace)
        if lock_error:
            return response_for_error(
                request_id,
                operation,
                82,
                f"RPG Kingdom Unity broker: {lock_error}",
            )

    return RequestSpec(
        request_path=request_path,
        request_id=request_id,
        operation=operation,
        test_filter=test_filter,
        workspace=workspace,
    )


def start_operation(
    spec: RequestSpec,
    state_root: Path,
    host_runner: Path,
) -> ActiveOperation:
    command = ["bash", str(host_runner), spec.operation, "--project", str(spec.workspace)]
    if spec.test_filter:
        command.extend(["--filter", spec.test_filter])

    environment = os.environ.copy()
    environment["RPGK_SUPERVISOR_STATE_ROOT"] = str(state_root)

    stdout_handle = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    stderr_handle = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=spec.workspace,
            env=environment,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise

    _, response_path = broker_paths(spec.request_path)
    return ActiveOperation(
        spec=spec,
        response_path=response_path,
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
    exit_code = active.process.returncode if active.process.returncode is not None else 86
    if exit_code_override is not None:
        exit_code = exit_code_override

    summary = parse_json_line(stdout)
    status = "completed" if exit_code == 0 else "failed"
    if status_override is not None:
        status = status_override
    elif active.spec.operation in {"editmode", "playmode"} and isinstance(summary, dict):
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
        "requestId": active.spec.request_id,
        "operation": active.spec.operation,
        "status": status,
        "exitCode": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "summary": summary,
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
    return {
        "requestId": active.spec.request_id,
        "workspace": str(active.spec.workspace),
        "issue": active.spec.workspace.name,
        "operation": active.spec.operation,
        "testFilter": active.spec.test_filter,
        "childPid": active.process.pid,
        "startedAt": active.started_at,
        "elapsedSeconds": int(max(0.0, time.monotonic() - active.started_monotonic)),
    }


def last_result_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "requestId": response.get("requestId"),
        "operation": response.get("operation"),
        "status": response.get("status"),
        "exitCode": response.get("exitCode"),
        "summary": response.get("summary"),
        "completedAt": response.get("completedAt"),
    }


def fail_stale_requests(workspace_root: Path) -> int:
    count = 0
    for request_path in sorted(
        workspace_root.glob("GH-*/Logs/SymphonyUnity/.broker/requests/*.json"),
        key=str,
    ):
        request_id = request_path.stem
        operation = "unknown"
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            if isinstance(request, dict):
                request_id = str(request.get("requestId", request_id))
                operation = str(request.get("operation", operation))
        except (OSError, json.JSONDecodeError):
            pass

        response = response_for_status(
            request_id,
            operation,
            "StaleRequest",
            89,
            "RPG Kingdom Unity broker: request predates this broker process and will not be replayed",
        )
        write_response(request_path, response)
        count += 1
    return count


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
    parser.add_argument("--command-timeout-seconds", type=int, default=1800)
    parser.add_argument("--kill-grace-seconds", type=float, default=5.0)
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
        print(f"RPG Kingdom Unity broker: rejected {stale_count} stale request(s)", file=sys.stderr)

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
                        extra_stderr=(
                            "RPG Kingdom Unity broker: host operation timed out after "
                            f"{args.command_timeout_seconds}s and its process group was terminated\n"
                        ),
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

            request_paths = sorted(
                workspace_root.glob("GH-*/Logs/SymphonyUnity/.broker/requests/*.json"),
                key=lambda path: (path.stat().st_mtime_ns, str(path)),
            )

            for request_path in request_paths:
                if active is not None and request_path == active.spec.request_path:
                    continue

                acknowledge_request(request_path, pid)
                prepared = prepare_request(request_path, workspace_root, state_root)
                if isinstance(prepared, dict):
                    write_response(request_path, prepared)
                    continue

                if active is not None:
                    busy = response_for_status(
                        prepared.request_id,
                        prepared.operation,
                        "HostBusy",
                        87,
                        (
                            "RPG Kingdom Unity broker: host is busy with "
                            f"{active.spec.request_id} ({active.spec.operation})"
                        ),
                        details={"activeRequest": active_snapshot(active)},
                    )
                    write_response(request_path, busy)
                    continue

                try:
                    active = start_operation(prepared, state_root, host_runner)
                except OSError as exc:
                    failed = response_for_error(
                        prepared.request_id,
                        prepared.operation,
                        70,
                        f"RPG Kingdom Unity broker: failed to start host operation: {exc}",
                    )
                    write_response(request_path, failed)
                    continue

                write_status("running", active_snapshot(active))
                last_status_write = time.monotonic()
                break

            time.sleep(max(args.poll_ms, 25) / 1000.0)
    finally:
        if active is not None:
            terminate_process_group(active.process, args.kill_grace_seconds)
            stopped = complete_operation(
                active,
                status_override="BrokerStopped",
                exit_code_override=90,
                extra_stderr="RPG Kingdom Unity broker: broker stopped while this host operation was active\n",
            )
            write_response(active.spec.request_path, stopped)
            last_result = last_result_snapshot(stopped)
            close_operation(active)

        write_status("stopped")
        try:
            (broker_root / "pid").unlink()
        except FileNotFoundError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
