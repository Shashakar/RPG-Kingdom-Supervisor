#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import supervisor_maintenance  # noqa: E402
import supervisor_telemetry  # noqa: E402

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    os.environ["RPGK_SUPERVISOR_STATE_ROOT"] = str(root)
    os.environ["RPGK_TELEMETRY_RETENTION_DAYS"] = "30"
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    active = root / "workers" / "active" / "implementation.json"
    active.parent.mkdir(parents=True)
    active.write_text(json.dumps({
        "protocolVersion": 1,
        "runId": "GH-46-implementation-dead",
        "issue": 46,
        "identifier": "GH-46",
        "role": "implementation",
        "pid": 99999999,
        "startedAt": recent,
        "quotaBefore": {"status": "unavailable"},
    }), encoding="utf-8")

    history_dir = root / "workers" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "old.json").write_text(json.dumps({"runId":"old","endedAt":old}), encoding="utf-8")
    (history_dir / "recent.json").write_text(json.dumps({"runId":"recent","endedAt":recent}), encoding="utf-8")
    (root / "workers" / "history.jsonl").write_text(
        json.dumps({"runId":"old","endedAt":old}) + "\n" + json.dumps({"runId":"recent","endedAt":recent}) + "\n",
        encoding="utf-8",
    )
    events = root / "telemetry" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"eventType":"old","observedAt":old}) + "\n" + json.dumps({"eventType":"recent","observedAt":recent}) + "\n", encoding="utf-8")
    snapshots = root / "usage" / "snapshots.jsonl"
    snapshots.parent.mkdir(parents=True)
    snapshots.write_text(json.dumps({"observedAt":old}) + "\n" + json.dumps({"observedAt":recent}) + "\n", encoding="utf-8")

    preview = supervisor_maintenance.maintain(apply=False)
    assert len(preview["staleWorkers"]) == 1
    assert preview["pruned"] == {"workerFiles":1,"workerRows":1,"events":1,"quotaSnapshots":1}
    assert active.exists()

    applied = supervisor_maintenance.maintain(apply=True)
    assert applied["mode"] == "apply"
    assert not active.exists()
    reconciled = history_dir / "GH-46-implementation-dead.json"
    assert reconciled.exists()
    value = json.loads(reconciled.read_text(encoding="utf-8"))
    assert value["outcome"] == "stale-process"
    assert value["reconciliation"]["reason"]
    assert not (history_dir / "old.json").exists()
    assert (history_dir / "recent.json").exists()
    assert len(supervisor_maintenance._read_jsonl(root / "workers" / "history.jsonl")) == 2  # recent + reconciled
    assert len(supervisor_maintenance._read_jsonl(events)) >= 2  # recent + reconciliation/maintenance events
    assert len(supervisor_maintenance._read_jsonl(snapshots)) == 1
    status = supervisor_maintenance.status()
    assert status["retentionDays"] == 30

print("supervisor-maintenance-test: PASS")
