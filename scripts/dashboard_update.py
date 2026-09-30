#!/usr/bin/env python3
"""Read-only update status and narrow host-owned update trigger for the dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(os.environ.get("RPGK_SUPERVISOR_ROOT", Path(__file__).resolve().parents[1]))
STATE_ROOT = Path(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", Path.home() / ".local/state/rpg-kingdom-supervisor"))
STATUS_PATH = STATE_ROOT / "dashboard-update.json"
UPDATE_SERVICE = os.environ.get("RPGK_DASHBOARD_UPDATE_SERVICE", "rpg-kingdom-dashboard-update.service")


class UpdateError(RuntimeError):
    pass


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _short(sha: str | None) -> str | None:
    return sha[:12] if sha else None


def status() -> dict[str, Any]:
    branch = _git("branch", "--show-current").stdout.strip()
    head = _git("rev-parse", "HEAD").stdout.strip()
    dirty = bool(_git("status", "--porcelain").stdout.strip())
    upstream_proc = _git("rev-parse", "--verify", "origin/main", check=False)
    upstream = upstream_proc.stdout.strip() if upstream_proc.returncode == 0 else None
    ahead = behind = None
    if upstream:
        counts = _git("rev-list", "--left-right", "--count", f"{head}...{upstream}").stdout.split()
        if len(counts) == 2:
            ahead, behind = (int(counts[0]), int(counts[1]))
    last: dict[str, Any] | None = None
    try:
        value = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            last = value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {
        "branch": branch,
        "head": head,
        "headShort": _short(head),
        "originMain": upstream,
        "originMainShort": _short(upstream),
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "updateAvailable": bool(behind and behind > 0),
        "lastUpdate": last,
    }


def trigger() -> dict[str, Any]:
    current = status()
    if current["branch"] != "main":
        raise UpdateError(f"dashboard updates require branch main; current branch is {current['branch'] or '(detached)'}")
    if current["dirty"]:
        raise UpdateError("dashboard update refused because the Supervisor checkout has local changes")
    proc = subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "start", UPDATE_SERVICE],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise UpdateError(detail or "failed to start dashboard update service; run scripts/install-dashboard-update.sh once")
    return {"accepted": True, "service": UPDATE_SERVICE, "head": current["head"], "message": "Update started; the dashboard will reconnect after services restart."}
