"""Identity proof for an isolated local Codex worker.

The keeper PID is never accepted as the Codex PID. A trusted controller must
resolve the child PID, matching kernel start ticks, and pinned worker UID.
"""
from __future__ import annotations
import os
from pathlib import Path
import stat

def proc_identity(pid: int, proc: Path = Path("/proc")) -> dict:
    if type(pid) is not int or pid < 2:
        raise ValueError("invalid worker PID")
    raw=(proc/str(pid)/"stat").read_text()
    tail=raw.rsplit(") ",1)[1].split()
    if len(tail)<20 or not tail[19].isdigit():
        raise ValueError("invalid worker start time")
    uid_line=next((line for line in (proc/str(pid)/"status").read_text().splitlines()
                   if line.startswith("Uid:")),None)
    if uid_line is None:
        raise ValueError("missing worker UID")
    uids=[int(part) for part in uid_line.split()[1:5]]
    if len(uids)!=4 or len(set(uids))!=1:
        raise PermissionError("worker has mixed Unix credentials")
    return {"pid":pid,"startTicks":tail[19],"uid":uids[0]}

def verify_spawned_worker(*, keeper_pid: int, child_pid: int, expected_uid: int,
                          proc: Path = Path("/proc")) -> dict:
    if keeper_pid==child_pid:
        raise PermissionError("keeper PID is not the worker PID")
    first=proc_identity(child_pid,proc)
    if first["uid"] != expected_uid:
        raise PermissionError("worker UID mismatch")
    stat_tail=(proc/str(child_pid)/"stat").read_text().rsplit(") ",1)[1].split()
    parent_pid=int(stat_tail[1])
    if parent_pid != keeper_pid:
        raise PermissionError("worker is not immediate keeper child")
    second=proc_identity(child_pid,proc)
    if second != first:
        raise PermissionError("worker identity changed")
    return first

def parent_pid(pid: int, proc: Path = Path("/proc")) -> int:
    if type(pid) is not int or pid < 2:
        raise ValueError("invalid PID")
    fields=(proc / str(pid) / "stat").read_text().rsplit(") ",1)[1].split()
    if len(fields) < 2:
        raise ValueError("missing parent PID")
    return int(fields[1])


def verify_controller_sibling(*, client_pid: int, child_pid: int, controller_uid: int,
                              worker_uid: int, proc: Path = Path("/proc")) -> dict:
    """The controller's socket client and worker must share a live trusted keeper.

    Use with kernel SO_PEERCRED for client PID/UID, never a JSON-supplied PID
    for the client or the keeper. Recheck kernel process identities at issue.
    """
    client=proc_identity(client_pid,proc)
    if client["uid"] != controller_uid or child_pid == client_pid:
        raise PermissionError("unauthorized registrar client identity")
    keeper=parent_pid(client_pid,proc)
    if keeper < 2 or parent_pid(child_pid,proc) != keeper:
        raise PermissionError("worker is not a sibling of registrar client")
    keeper_identity=proc_identity(keeper,proc)
    if keeper_identity["uid"] != controller_uid:
        raise PermissionError("untrusted keeper")
    worker=proc_identity(child_pid,proc)
    if worker["uid"] != worker_uid:
        raise PermissionError("wrong worker identity")
    if parent_pid(client_pid,proc) != keeper or parent_pid(child_pid,proc) != keeper:
        raise PermissionError("process ancestry changed")
    if proc_identity(keeper,proc) != keeper_identity or proc_identity(child_pid,proc) != worker:
        raise PermissionError("process identity changed")
    return worker
