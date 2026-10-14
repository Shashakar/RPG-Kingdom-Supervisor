"""Durable, fail-closed request reservation shared by the two Unity brokers.

A request ID is reserved *before* the host starts. A crash leaves the reservation
in place, intentionally requiring operator reconciliation rather than replaying
potentially non-idempotent Unity mutations. This is at-most-once start admission,
not proof that an external editor operation completed exactly once.
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,220}$")
_ISSUE = re.compile(r"^GH-[0-9]+$")
_OPERATIONS = frozenset({"editmode", "playmode", "capture", "inspect", "author"})


def reserve_execution(state_root: Path, workspace: Path, operation: str, request_id: str) -> bool:
    """Atomically reserve an operation once. False means the ID was already seen.

    The database is host trust state; production service permissions must
    prevent workspace processes from editing or replacing its files.
    """
    if not _ISSUE.fullmatch(workspace.name) or not workspace.is_absolute():
        raise ValueError("invalid workspace for durable Unity reservation")
    if operation not in _OPERATIONS or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid operation or request ID")
    if request_id.startswith("GH-"):
        issue_prefix = re.match(r"^(GH-[0-9]+)-", request_id)
        if issue_prefix is None or issue_prefix.group(1) != workspace.name:
            raise ValueError("request ID names a different issue")
    db = state_root / "unity-execution-journal.sqlite3"
    with sqlite3.connect(db, timeout=5, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reservations ("
                "workspace TEXT NOT NULL, operation TEXT NOT NULL, "
                "request_id TEXT NOT NULL, reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY (workspace, operation, request_id))"
            )
            inserted = conn.execute(
                "INSERT OR IGNORE INTO reservations (workspace, operation, request_id) VALUES (?, ?, ?)",
                (str(workspace.resolve()), operation, request_id),
            ).rowcount
            conn.execute("COMMIT")
            return inserted == 1
        except BaseException:
            conn.execute("ROLLBACK")
            raise
