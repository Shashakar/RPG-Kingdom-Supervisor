#!/usr/bin/env python3
"""C5f WSL broker dispatch drain gate, NOT a Windows stage freeze.

The gate serializes cooperating *WSL* broker dispatch with host drain
activation. It does not prevent same-user Editor C# or other Windows writers,
and does not authenticate the human operator. Never authorize promotion with it.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat

ISSUE = re.compile(r"GH-[1-9][0-9]*\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
GATE_DIR = "unity-stage-drain"
GATE_FILE = "state.json"
STATE_KEYS = frozenset({
    "schemaVersion", "phase", "issue", "workspace", "workspaceRevision",
    "stagePath", "createdAt", "operationId",
})


class DrainDenied(Exception):
    pass


def _safe_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise DrainDenied("stage drain directory missing or linked")
    if path.stat().st_mode & 0o077:
        raise DrainDenied("stage drain directory permissions too broad")


def _safe_regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise DrainDenied("stage drain state/lock is linked, nonregular or accessible to group/other")


def directory(state_root: Path) -> Path:
    if state_root.is_symlink() or not state_root.is_dir():
        raise DrainDenied("host state root missing or linked")
    root = state_root / GATE_DIR
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o700)
    _safe_directory(root)
    return root


@contextmanager
def locked(state_root: Path):
    root = directory(state_root)
    lock = root / "gate.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _safe_regular(lock)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield root
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_locked(root: Path) -> dict | None:
    path = root / GATE_FILE
    if not path.exists() and not path.is_symlink():
        return None
    _safe_regular(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DrainDenied("invalid stage drain state") from exc
    if (not isinstance(data, dict) or set(data) != STATE_KEYS
            or data["schemaVersion"] != 1 or data["phase"] != "draining"
            or not isinstance(data["issue"], str) or not ISSUE.fullmatch(data["issue"])
            or not isinstance(data["workspace"], str) or not data["workspace"]
            or Path(data["workspace"]).name != data["issue"]
            or not isinstance(data["workspaceRevision"], str)
            or not REVISION.fullmatch(data["workspaceRevision"])
            or not isinstance(data["stagePath"], str) or not data["stagePath"]
            or not isinstance(data["createdAt"], str) or not data["createdAt"]
            or not isinstance(data["operationId"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", data["operationId"])):
        raise DrainDenied("invalid or ambiguous stage drain record")
    return data


def _sync_dir(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_locked(root: Path, state: dict) -> None:
    if read_locked(root) is not None:
        raise DrainDenied("stage drain is already active")
    # No snapshot/permission claims: durable only on the local WSL filesystem.
    name = root / (".state-" + secrets.token_hex(12))
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / GATE_FILE)
        _sync_dir(root)
    finally:
        name.unlink(missing_ok=True)


def cancel_locked(root: Path, operation_id: str) -> None:
    current = read_locked(root)
    if current is None or current["operationId"] != operation_id:
        raise DrainDenied("stage drain record changed or missing")
    (root / GATE_FILE).unlink()
    _sync_dir(root)


def status(state_root: Path) -> str:
    """Never mistake malformed or inaccessible gate state for open."""
    try:
        with locked(state_root) as root:
            return "draining" if read_locked(root) is not None else "ready"
    except (DrainDenied, OSError):
        return "blocked"
