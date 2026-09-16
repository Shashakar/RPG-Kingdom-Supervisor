#!/usr/bin/env python3
"""Trusted recent finished-task projection for the Supervisor dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
from typing import Any, Callable

import supervisor_telemetry

PROTOCOL_VERSION = 1
DEFAULT_REPO = "Shashakar/RPG-Kingdom"
DEFAULT_LIMIT = 12
DEFAULT_SCAN_LIMIT = 40
DISQUALIFYING_LABELS = {"symphony:halted", "symphony:human-attention"}

GhRunner = Callable[[list[str]], dict[str, Any]]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_gh(arguments: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["gh", *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "code": None}
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "code": proc.returncode,
    }


def gh_json(runner: GhRunner, arguments: list[str]) -> Any:
    result = runner(arguments)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("stderr") or result.get("stdout") or "gh command failed").strip())
    try:
        return json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid gh JSON: {exc}") from exc


def label_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str) and item:
            names.append(item)
    return names


def recent_worker_map(workers: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for worker in workers:
        issue = worker.get("issue")
        if isinstance(issue, int) and issue not in result:
            result[issue] = worker
    return result


def collect(
    *,
    repo: str | None = None,
    runner: GhRunner = run_gh,
    recent_workers: list[dict[str, Any]] | None = None,
    limit: int = DEFAULT_LIMIT,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> dict[str, Any]:
    """Return recently completed Symphony issues using GitHub close reason as authority.

    A dead worker is never sufficient evidence. Closed issues must be `state_reason=completed`
    and must show Symphony participation through either a current lifecycle label or retained
    worker telemetry. Halted/human-attention labels remain disqualifying even if an issue was
    subsequently closed, so the dashboard never silently rewrites an unresolved stop as success.
    """
    repo = repo or os.environ.get("RPGK_REPO", DEFAULT_REPO)
    recent_workers = recent_workers if recent_workers is not None else supervisor_telemetry.recent_workers(100)
    workers_by_issue = recent_worker_map(recent_workers)
    safe_limit = max(1, min(int(limit), 50))
    safe_scan = max(safe_limit, min(int(scan_limit), 100))

    try:
        raw = gh_json(
            runner,
            ["api", f"repos/{repo}/issues?state=closed&sort=updated&direction=desc&per_page={safe_scan}"],
        )
    except RuntimeError as exc:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "generatedAt": iso_now(),
            "available": False,
            "repo": repo,
            "errors": [str(exc)],
            "items": [],
        }

    items: list[dict[str, Any]] = []
    for issue in raw if isinstance(raw, list) else []:
        if not isinstance(issue, dict) or issue.get("pull_request"):
            continue
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        if str(issue.get("state_reason") or "").lower() != "completed":
            continue

        labels = {name.lower() for name in label_names(issue.get("labels"))}
        worker = workers_by_issue.get(number)
        participated = any(name.startswith("symphony:") for name in labels) or worker is not None
        if not participated or labels.intersection(DISQUALIFYING_LABELS):
            continue

        completed_at = issue.get("closed_at") or issue.get("updated_at")
        items.append(supervisor_telemetry.sanitize({
            "issue": number,
            "identifier": f"GH-{number}",
            "title": issue.get("title"),
            "url": issue.get("html_url"),
            "status": "done",
            "statusLabel": "Done",
            "completedAt": completed_at,
            "latestWorkerOutcome": worker.get("outcome") if worker else None,
            "workerRole": worker.get("role") if worker else None,
            "model": worker.get("model") if worker else None,
            "effort": worker.get("effort") if worker else None,
            "route": worker.get("route") if worker else None,
        }))

    items.sort(key=lambda item: parse_time(item.get("completedAt")), reverse=True)
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "generatedAt": iso_now(),
        "available": True,
        "repo": repo,
        "errors": [],
        "items": items[:safe_limit],
    }
