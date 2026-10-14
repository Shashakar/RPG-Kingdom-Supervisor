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
