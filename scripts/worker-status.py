#!/usr/bin/env python3
"""Persist a bounded task-level status from an existing Codex worker turn.

This is deliberately workspace-local Supervisor state. It is not a completion
signal and it never mutates GitHub lifecycle state. The after-run guard may use
a fresh record to explain an unfinished worker lifetime without spending an
additional model turn just to ask why work stopped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from typing import Any

STATUS_NAME = ".symphony-worker-status.json"
ATTEMPT_MARKER = ".symphony-attempt-complete"
ISSUE_RE = re.compile(r"^GH-(\d+)$")
STATES = {"incomplete", "blocked"}
CLASSIFICATIONS = {
    "validation_failed",
    "manual_action_required",
    "handoff_incomplete",
    "worker_error",
    "scope_mismatch",
    "unknown",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def attempt_boundary(workspace: Path) -> str:
    marker = workspace / ATTEMPT_MARKER
    try:
        return str(marker.stat().st_mtime_ns)
    except FileNotFoundError:
        return "none"


def issue_number(workspace: Path) -> int:
    match = ISSUE_RE.fullmatch(workspace.name)
    if match is None:
        raise RuntimeError(f"workspace '{workspace}' is not GH-<issue>")
    return int(match.group(1))


def ensure_excluded(workspace: Path) -> None:
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return
    import subprocess

    probe = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        return
    path = Path(probe.stdout.strip())
    if not path.is_absolute():
        path = workspace / path
    path.parent.mkdir(parents=True, exist_ok=True)
    pattern = f"/{STATUS_NAME}"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if pattern not in existing.splitlines():
        with path.open("a", encoding="utf-8") as handle:
            handle.write(pattern + "\n")


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
        Path(temp_name).replace(path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def write_status(
    workspace: Path,
    *,
    state: str,
    classification: str,
    summary: str,
    remaining: list[str],
    blocked_by: str | None,
    manual_action_required: bool,
    recommended_next_action: str | None,
    validation_runs: list[str],
) -> dict[str, Any]:
    if state not in STATES:
        raise ValueError(f"unsupported state: {state}")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"unsupported classification: {classification}")
    if not summary.strip():
        raise ValueError("summary must be non-empty")
    ensure_excluded(workspace)
    payload = {
        "protocolVersion": 1,
        "issue": issue_number(workspace),
        "attemptBoundary": attempt_boundary(workspace),
        "updatedAt": iso_now(),
        "state": state,
        "classification": classification,
        "summary": summary.strip(),
        "remainingAcceptanceCriteria": [item.strip() for item in remaining if item.strip()][:20],
        "blockedBy": blocked_by.strip() if blocked_by and blocked_by.strip() else None,
        "manualActionRequired": bool(manual_action_required),
        "recommendedNextAction": recommended_next_action.strip() if recommended_next_action and recommended_next_action.strip() else None,
        "validationRunIds": [item.strip() for item in validation_runs if item.strip()][:20],
    }
    atomic_write(workspace / STATUS_NAME, payload)
    return payload


def read_fresh_status(workspace: Path, issue: int, expected_boundary: str) -> tuple[dict[str, Any] | None, str | None]:
    path = workspace / STATUS_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no worker status was recorded"
    except (OSError, json.JSONDecodeError):
        return None, "worker status could not be read"
    if not isinstance(value, dict):
        return None, "worker status was not an object"
    if value.get("issue") != issue:
        return None, "worker status belongs to a different issue"
    if str(value.get("attemptBoundary")) != str(expected_boundary):
        return None, "worker status belongs to a prior worker lifetime"
    if value.get("state") not in STATES or value.get("classification") not in CLASSIFICATIONS:
        return None, "worker status has an unsupported state/classification"
    if not str(value.get("summary") or "").strip():
        return None, "worker status summary is empty"
    return value, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist task-level status for an unfinished RPG Kingdom worker")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--state", choices=sorted(STATES), required=True)
    parser.add_argument("--classification", choices=sorted(CLASSIFICATIONS), required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--remaining", action="append", default=[])
    parser.add_argument("--blocked-by")
    parser.add_argument("--manual-action-required", action="store_true")
    parser.add_argument("--recommended-next-action")
    parser.add_argument("--validation-run", action="append", default=[])
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    payload = write_status(
        workspace,
        state=args.state,
        classification=args.classification,
        summary=args.summary,
        remaining=args.remaining,
        blocked_by=args.blocked_by,
        manual_action_required=args.manual_action_required,
        recommended_next_action=args.recommended_next_action,
        validation_runs=args.validation_run,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
