"""Linux /proc identity checks for a host-owned Unity admission issuer.

An authenticated local transport should obtain peer_pid from SO_PEERCRED. Never
accept peer_pid, worker PID or process ancestry as user-supplied JSON fields.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def read_ppid(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    """Read PPid from Linux status; do not mistake a missing process for PID 0."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        status = (proc_root / str(pid) / "status").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            try:
                parent = int(line.split(":", 1)[1].strip())
                return parent if parent > 0 else None
            except ValueError:
                return None
    return None


def is_worker_descendant(peer_pid: int, worker_pid: int, *,
                         get_parent: Callable[[int], int | None] = read_ppid,
                         max_depth: int = 64) -> bool:
    """Return true only for an exact worker PID or an observed descendant.

    This is one host-provenance check, not a complete authorization decision:
    caller UID, lease freshness, workspace lock and issue scope must also match.
    """
    if type(peer_pid) is not int or type(worker_pid) is not int:
        return False
    if peer_pid <= 1 or worker_pid <= 1 or max_depth <= 0:
        return False
    seen: set[int] = set()
    current = peer_pid
    for _ in range(max_depth):
        if current == worker_pid:
            return True
        if current <= 1 or current in seen:
            return False
        seen.add(current)
        parent = get_parent(current)
        if type(parent) is not int or parent <= 0:
            return False
        current = parent
    return False
