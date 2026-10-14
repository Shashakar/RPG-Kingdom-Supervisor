"""Protected worker-lease registry and process identity validation.

Registration callers MUST run under a dedicated trusted orchestration UID.
Codex workers must run under a distinct UID and cannot access this socket or
the directory holding leases. Kernel SO_PEERCRED is the only caller identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

ISSUE = re.compile(r"^GH-[0-9]+$")
RUN_ID = re.compile(r"^GH-[0-9]+-[A-Za-z0-9_-]{1,160}$")


def start_ticks(pid: int, proc: Path = Path("/proc")) -> str:
    if not isinstance(pid, int) or pid <= 1:
        raise ValueError("invalid process PID")
    raw = (proc / str(pid) / "stat").read_text()
    fields = raw.rsplit(") ", 1)[1].split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise ValueError("invalid process start ticks")
    return fields[19]


def process_uid(pid: int, proc: Path = Path("/proc")) -> int:
    for line in (proc / str(pid) / "status").read_text().splitlines():
        if line.startswith("Uid:"):
            uids = [int(item) for item in line.split()[1:5]]
            if len(uids) != 4 or len(set(uids)) != 1:
                raise PermissionError("worker real/effective/saved/filesystem UIDs differ")
            return uids[0]
    raise ValueError("missing process real UID")


def protected_directory(path: Path, owner_uid: int) -> None:
    mode = path.lstat()
    if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != owner_uid or stat.S_IMODE(mode.st_mode) & 0o077:
        raise PermissionError("registration directory must be trusted-owned mode 0700 or tighter")


def validate_process(pid: int, *, worker_uid: int, proc: Path = Path("/proc")) -> str:
    if worker_uid == os.getuid():
        raise PermissionError("registrar and Codex worker must run under different UIDs")
    first = start_ticks(pid, proc)
    if process_uid(pid, proc) != worker_uid or start_ticks(pid, proc) != first:
        raise PermissionError("worker UID/start identity mismatch")
    return first


def register(state_root: Path, workspace_root: Path, *, issue: str, workspace: str,
             pid: int, worker_uid: int, run_id: str) -> dict:
    if not ISSUE.fullmatch(issue) or not RUN_ID.fullmatch(run_id) or not run_id.startswith(issue + "-"):
        raise ValueError("invalid issue or run ID")
    root = workspace_root.resolve(strict=True)
    resolved = Path(workspace).resolve(strict=True)
    if resolved.parent != root or resolved.name != issue:
        raise PermissionError("workspace outside issue root")
    directory = state_root / "workers" / "active"
    protected_directory(directory, os.getuid())
    identity = validate_process(pid, worker_uid=worker_uid)
    record = {"runId": run_id, "workspace": str(resolved), "pid": pid,
              "processStartTicks": identity, "workerUid": worker_uid}
    target = directory / (run_id + ".json")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if validate_process(pid, worker_uid=worker_uid) != identity:
        target.unlink(missing_ok=True)
        raise PermissionError("worker process identity changed during registration")
    return record


def revoke(state_root: Path, *, run_id: str, pid: int, identity: str) -> bool:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    directory = state_root / "workers" / "active"
    protected_directory(directory, os.getuid())
    target = directory / (run_id + ".json")
    try:
        record = json.loads(target.read_text())
    except FileNotFoundError:
        return False
    if record.get("pid") != pid or record.get("processStartTicks") != identity or record.get("runId") != run_id:
        raise PermissionError("revocation does not match registered lifetime")
    target.unlink()
    return True
