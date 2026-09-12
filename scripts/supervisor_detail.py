#!/usr/bin/env python3
"""Worker-lifetime drill-down collector for Supervisor issue #46C."""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
from typing import Any

import supervisor_activity
import supervisor_maintenance
import supervisor_telemetry
import unity_run_history

PROTOCOL_VERSION = 1


def _load_worker(run_id: str) -> tuple[dict[str, Any] | None, str | None]:
    for path in sorted((supervisor_telemetry.state_root() / "workers" / "active").glob("*.json")):
        value = supervisor_telemetry.read_json(path)
        if value.get("runId") == run_id:
            value["alive"] = supervisor_telemetry.process_alive(value.get("pid"))
            return value, "active"
    path = supervisor_telemetry.state_root() / "workers" / "history" / f"{run_id}.json"
    value = supervisor_telemetry.read_json(path)
    return (value, "completed") if value else (None, None)


def _all_issue_workers(issue: int) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path in (supervisor_telemetry.state_root() / "workers" / "history").glob("*.json"):
        value = supervisor_telemetry.read_json(path)
        if value.get("issue") == issue:
            values.append(value)
    for value in supervisor_telemetry.active_workers():
        if value.get("issue") == issue:
            values.append(value)
    values.sort(key=lambda item: supervisor_activity.parse_time(item.get("startedAt")) or supervisor_activity.datetime.min.replace(tzinfo=supervisor_activity.timezone.utc))
    return values


def _lineage(workers: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    ids = [str(item.get("runId")) for item in workers]
    if run_id not in ids:
        return {"previousRunId": None, "nextRunId": None, "sequence": None, "totalIssueLifetimes": len(workers)}
    index = ids.index(run_id)
    return {
        "previousRunId": ids[index - 1] if index > 0 else None,
        "nextRunId": ids[index + 1] if index + 1 < len(ids) else None,
        "sequence": index + 1,
        "totalIssueLifetimes": len(workers),
        "basis": "same-issue chronological worker lifetimes; this is lineage context, not a claim of causal retry linkage",
    }


def _inside_worker(worker: dict[str, Any], value: Any, tolerance_seconds: int = 60) -> bool:
    when = supervisor_activity.parse_time(value)
    started = supervisor_activity.parse_time(worker.get("startedAt"))
    ended = supervisor_activity.parse_time(worker.get("endedAt"))
    if not when or not started:
        return False
    if ended is None:
        return when >= started - timedelta(seconds=tolerance_seconds)
    return started - timedelta(seconds=tolerance_seconds) <= when <= ended + timedelta(seconds=tolerance_seconds)


def _unity_for_worker(worker: dict[str, Any], workspace_root: Path) -> list[dict[str, Any]]:
    identifier = worker.get("identifier")
    runs = unity_run_history.collect_runs(workspace_root=workspace_root, issue=identifier, limit=200)
    return [
        run for run in runs
        if _inside_worker(worker, run.get("startedAt") or run.get("acceptedAt") or run.get("completedAt"))
    ]


def _git_for_worker(worker: dict[str, Any], workspace_root: Path) -> list[dict[str, Any]]:
    return [
        event for event in supervisor_activity._git_handoff_events(workspace_root)
        if event.get("issue") == worker.get("issue") and _inside_worker(worker, event.get("observedAt"))
    ]


def collect(run_id: str, *, workspace_root: Path | None = None, use_cache: bool = True) -> dict[str, Any] | None:
    worker, worker_state = _load_worker(run_id)
    if worker is None:
        return None
    issue = worker.get("issue")
    if not isinstance(issue, int):
        return None
    workspace_root = workspace_root or supervisor_activity._workspace_root()
    activity = supervisor_activity.collect(workspace_root=workspace_root, limit=500, use_cache=use_cache)
    current = next((item for item in activity.get("items", []) if item.get("issue") == issue), None)
    related_activity = [event for event in activity.get("activity", []) if event.get("issue") == issue]
    worker_activity = [
        event for event in related_activity
        if event.get("workerRunId") == run_id
        or event.get("category") in {"lifecycle", "review"} and _inside_worker(worker, event.get("observedAt"), tolerance_seconds=120)
    ]
    unity_runs = _unity_for_worker(worker, workspace_root)
    git_events = _git_for_worker(worker, workspace_root)
    issue_workers = _all_issue_workers(issue)
    review_events = [event for event in related_activity if event.get("category") == "review"]
    artifacts = []
    for run in unity_runs:
        if run.get("artifactPath"):
            artifacts.append({"kind": "unity", "requestId": run.get("requestId"), "path": run.get("artifactPath")})
        for kind, path in (run.get("paths") or {}).items():
            if path:
                artifacts.append({"kind": kind, "requestId": run.get("requestId"), "path": path})
    return supervisor_telemetry.sanitize({
        "protocolVersion": PROTOCOL_VERSION,
        "generatedAt": supervisor_activity.iso_now(),
        "workerState": worker_state,
        "worker": worker,
        "currentQuota": supervisor_telemetry.current_quota(),
        "quotaSemantics": "currentQuota is current global state; worker.quotaBefore/quotaAfter/quotaDelta are historical lifetime evidence",
        "currentLifecycle": current,
        "continuationLineage": _lineage(issue_workers, run_id),
        "activity": worker_activity,
        "reviewHistory": review_events,
        "unityRuns": unity_runs,
        "gitHandoffs": git_events,
        "artifacts": artifacts,
        "maintenance": supervisor_maintenance.status(),
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect one Supervisor worker lifetime drill-down")
    parser.add_argument("run_id")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    payload = collect(args.run_id, use_cache=not args.no_cache)
    if payload is None:
        print(json.dumps({"error": "worker run not found", "runId": args.run_id}))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
