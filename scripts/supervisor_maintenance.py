#!/usr/bin/env python3
"""Bounded local telemetry retention and restart reconciliation for Supervisor #46C."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import supervisor_telemetry

DEFAULT_RETENTION_DAYS = 30
PROTOCOL_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("RPGK_TELEMETRY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def state_root() -> Path:
    return supervisor_telemetry.state_root()


def _timestamp(record: dict[str, Any]) -> datetime | None:
    for key in ("endedAt", "completedAt", "observedAt", "startedAt"):
        parsed = parse_time(record.get(key))
        if parsed:
            return parsed
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    result: list[dict[str, Any]] = []
    for raw in lines:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _rewrite_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(supervisor_telemetry.sanitize(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _completed_record_from_stale(active: dict[str, Any]) -> dict[str, Any]:
    started = parse_time(active.get("startedAt"))
    completed = {
        **active,
        "endedAt": iso_now(),
        "durationSeconds": max(0.0, (utc_now() - started).total_seconds()) if started else None,
        "outcome": "stale-process",
        "reconciliation": {
            "reason": "active worker PID was not alive during Supervisor startup maintenance",
            "reconciledAt": iso_now(),
        },
        "tokenUsage": {"status": "unavailable", "reason": "worker ended outside the normal completion boundary"},
        "quotaAfter": supervisor_telemetry.current_quota(),
    }
    completed["quotaDelta"] = supervisor_telemetry.quota_delta(active.get("quotaBefore") or {}, completed["quotaAfter"])
    return supervisor_telemetry.sanitize(completed)


def reconcile_stale_workers(*, apply: bool) -> list[dict[str, Any]]:
    active_dir = state_root() / "workers" / "active"
    history_dir = state_root() / "workers" / "history"
    reconciled: list[dict[str, Any]] = []
    for path in sorted(active_dir.glob("*.json")):
        active = supervisor_telemetry.read_json(path)
        if not active or supervisor_telemetry.process_alive(active.get("pid")):
            continue
        completed = _completed_record_from_stale(active)
        reconciled.append(completed)
        if not apply:
            continue
        run_id = str(completed.get("runId") or path.stem)
        history_path = history_dir / f"{run_id}.json"
        if not history_path.exists():
            supervisor_telemetry.atomic_json(history_path, completed)
            supervisor_telemetry.append_jsonl(state_root() / "workers" / "history.jsonl", completed)
            supervisor_telemetry.append_event("worker_reconciled", **completed)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return reconciled


def prune_history(*, apply: bool) -> dict[str, int]:
    cutoff = utc_now() - timedelta(days=retention_days())
    root = state_root()
    counts = {"workerFiles": 0, "workerRows": 0, "events": 0, "quotaSnapshots": 0}

    history_dir = root / "workers" / "history"
    for path in history_dir.glob("*.json"):
        record = supervisor_telemetry.read_json(path)
        when = _timestamp(record)
        if when and when < cutoff:
            counts["workerFiles"] += 1
            if apply:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    for relative, key in (
        (Path("workers/history.jsonl"), "workerRows"),
        (Path("telemetry/events.jsonl"), "events"),
        (Path("usage/snapshots.jsonl"), "quotaSnapshots"),
    ):
        path = root / relative
        records = _read_jsonl(path)
        kept: list[dict[str, Any]] = []
        removed = 0
        for record in records:
            when = _timestamp(record)
            if when and when < cutoff:
                removed += 1
            else:
                kept.append(record)
        counts[key] = removed
        if apply and removed:
            _rewrite_jsonl(path, kept)
    return counts


def maintain(*, apply: bool) -> dict[str, Any]:
    stale = reconcile_stale_workers(apply=apply)
    pruned = prune_history(apply=apply)
    result = supervisor_telemetry.sanitize({
        "protocolVersion": PROTOCOL_VERSION,
        "mode": "apply" if apply else "dry-run",
        "observedAt": iso_now(),
        "retentionDays": retention_days(),
        "staleWorkers": [
            {"runId": item.get("runId"), "identifier": item.get("identifier"), "role": item.get("role"), "pid": item.get("pid")}
            for item in stale
        ],
        "pruned": pruned,
    })
    if apply:
        supervisor_telemetry.atomic_json(state_root() / "maintenance" / "status.json", result)
        supervisor_telemetry.append_event("maintenance_completed", **result)
    return result


def status() -> dict[str, Any]:
    value = supervisor_telemetry.read_json(state_root() / "maintenance" / "status.json")
    if value:
        return supervisor_telemetry.sanitize(value)
    return {
        "status": "unavailable",
        "reason": "maintenance has not run yet",
        "retentionDays": retention_days(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervisor local telemetry maintenance")
    parser.add_argument("--apply", action="store_true", help="apply stale-worker reconciliation and retention pruning")
    parser.add_argument("--status", action="store_true", help="print last applied maintenance status")
    args = parser.parse_args()
    payload = status() if args.status else maintain(apply=args.apply)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
