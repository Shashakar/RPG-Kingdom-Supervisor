#!/usr/bin/env python3
"""Trusted controller lock keeper for isolated worker sessions.

Acquires both existing concurrency locks and holds them until the child has
exited. The untrusted process never inherits lock file descriptors.
For use only by trusted controller code; NOT an escalation endpoint.
"""
from __future__ import annotations
import argparse
import json
import fcntl
import os
from pathlib import Path
import re
import signal
import time
import subprocess
import sys
from unity_v2_admission_gate import admit

ISSUE = re.compile(r"^GH-[0-9]+$")
SLOT = {"implementation": "mutation", "repair": "mutation",
        "report-only": "mutation", "review": "review"}

def paths(state_root: Path, role: str, issue: str) -> tuple[Path, Path]:
    if role not in SLOT or not ISSUE.fullmatch(issue):
        raise ValueError("unsupported role or issue")
    root=state_root.resolve(strict=True)
    return root / "locks" / ("codex-" + SLOT[role] + ".lock"), root / "locks" / "issues" / (issue + ".lock")

def wait_for_child(child: subprocess.Popen, grace_seconds: float = 3.0) -> int:
    """Forward termination to the child process group and reap it before unlocking.

    The worker process group must contain all child processes. Detached
    descendants require separate cgroup supervision in the production service.
    """
    previous = {}
    termination = {"signal": None}

    def forward(signum, _frame):
        termination["signal"] = signum
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, forward)
    try:
        deadline = None
        while True:
            try:
                result = child.wait(timeout=0.1)
                return result
            except subprocess.TimeoutExpired:
                pass
            if termination["signal"] is not None:
                if deadline is None:
                    deadline = time.monotonic() + grace_seconds
                elif time.monotonic() >= deadline:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def registrar_command(client_script: str, socket_path: str, action: str,
                      *, issue: str, workspace: str, pid: int,
                      run_id: str, start_ticks: str | None = None) -> list[str]:
    args = [sys.executable, client_script, "--socket", socket_path, action]
    if action == "register":
        args += ["--issue", issue, "--workspace", workspace,
                 "--pid", str(pid), "--run-id", run_id]
    elif action == "revoke" and start_ticks is not None:
        args += ["--pid", str(pid), "--run-id", run_id,
                 "--process-start-ticks", start_ticks]
    else:
        raise ValueError("unsupported registration operation")
    return args


def registered_worker_session(child: subprocess.Popen, *, client_script: str,
                              socket_path: str, issue: str,
                              workspace: str, run_id: str, gate_write_fd: int | None = None) -> int:
    """Prototype only: register a sibling child and revoke on every exit path.

    This MUST be paired with a start gate before production, because Popen
    starts the worker before the registrar acknowledges the lease.
    """
    registration = None
    try:
        command = registrar_command(client_script, socket_path, "register",
                                    issue=issue, workspace=workspace,
                                    pid=child.pid, run_id=run_id)
        result = subprocess.run(command, capture_output=True, text=True,
                                check=True, timeout=5, close_fds=True)
        response = json.loads(result.stdout)
        record = response.get("registration") or {}
        if response.get("ok") is not True or record.get("pid") != child.pid or record.get("runId") != run_id:
            raise PermissionError("registration identity not confirmed")
        ticks = record.get("processStartTicks")
        if not isinstance(ticks, str) or not ticks.isdigit():
            raise PermissionError("missing immutable start time")
        if gate_write_fd is not None:
            ticks = admit(gate_write_fd, response, child.pid, run_id)
            os.close(gate_write_fd)
            gate_write_fd = None
        registration = ticks
        return wait_for_child(child)
    finally:
        if gate_write_fd is not None:
            os.close(gate_write_fd)
        if registration is None:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        else:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            command = registrar_command(client_script, socket_path, "revoke",
                                        issue=issue, workspace=workspace,
                                        pid=child.pid, run_id=run_id,
                                        start_ticks=registration)
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=5, close_fds=True)
            if result.returncode != 0:
                raise PermissionError("worker lease revocation failed")


def start_gated_session(command: list[str], *, client_script: str,
                        socket_path: str, issue: str, workspace: str,
                        run_id: str) -> int:
    """Staging-only worker launch; command must come from trusted controller."""
    read_fd, write_fd = os.pipe()
    try:
        child = subprocess.Popen([*command, "--gate-fd", str(read_fd)],
                                 pass_fds=(read_fd,), start_new_session=True)
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return registered_worker_session(child, client_script=client_script,
                                     socket_path=socket_path, issue=issue,
                                     workspace=workspace, run_id=run_id,
                                     gate_write_fd=write_fd)


def run(state_root: Path, role: str, issue: str, command: list[str]) -> int:
    if not command or any(not part for part in command):
        raise ValueError("missing command")
    slot, issue_file=paths(state_root,role,issue)
    if not slot.parent.is_dir() or not issue_file.parent.is_dir():
        raise FileNotFoundError("trusted lock directories absent")
    # Compatible paths and acquisition order, without FD inheritance.
    with slot.open("a+b") as slot_handle:
        fcntl.flock(slot_handle,fcntl.LOCK_EX)
        with issue_file.open("a+b") as issue_handle:
            fcntl.flock(issue_handle,fcntl.LOCK_EX)
            try:
                child=subprocess.Popen(command, close_fds=True, start_new_session=True)
                return wait_for_child(child)
            finally:
                fcntl.flock(issue_handle,fcntl.LOCK_UN)
                fcntl.flock(slot_handle,fcntl.LOCK_UN)

def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--state-root",required=True)
    parser.add_argument("--role",required=True,choices=list(SLOT))
    parser.add_argument("--issue",required=True)
    parser.add_argument("command",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    # This CLI is staging-only: arbitrary commands must never be passed
    # across a worker-reachable privilege boundary. No sudo policy!
    if not args.command or args.command[0] != "--":
        raise ValueError("missing command separator")
    return run(Path(args.state_root),args.role,args.issue,args.command[1:])

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (ValueError,OSError) as exc:
        print(f"Codex lock keeper failed: {type(exc).__name__}",file=sys.stderr)
        raise SystemExit(75)
