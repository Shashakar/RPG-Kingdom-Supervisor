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

import supervisor_activity as activity  # noqa: E402

NOW = datetime.now(timezone.utc)

def iso(offset_seconds: int) -> str:
    return (NOW + timedelta(seconds=offset_seconds)).isoformat()


def marker(**values: object) -> str:
    return "<!-- rpgk-review-state\n" + json.dumps(values, separators=(",", ":")) + "\n-->"


ISSUES = {
    106: {
        "number": 106,
        "title": "Repair inventory regression",
        "state": "OPEN",
        "url": "https://github.com/Shashakar/RPG-Kingdom/issues/106",
        "updatedAt": iso(-100),
        "labels": [
            {"name": "symphony:ready"},
            {"name": "symphony:agent-review"},
            {"name": "symphony:rework"},
            {"name": "repair-route:terra"},
        ],
        "comments": [{
            "id": "c106",
            "createdAt": iso(-90),
            "url": "https://example.invalid/c106",
            "body": marker(
                state="rework", reviewCycle=1, repairAttempts=1, maxRepairAttempts=2,
                lastVerdict="changes_required", lastSummary="Fix the regression",
                reason="none", prNumber=107, prHeadSha="abc123def456",
                routingRecommendation="terra", updatedAt=iso(-90),
            ),
        }],
    },
    107: {
        "number": 107, "title": "Ready for integration", "state": "OPEN",
        "url": "https://github.com/Shashakar/RPG-Kingdom/issues/107",
        "updatedAt": iso(-80), "labels": [{"name": "symphony:human-review"}],
        "comments": [{
            "id": "c107", "createdAt": iso(-75), "url": "https://example.invalid/c107",
            "body": marker(
                state="human_review", reviewCycle=2, repairAttempts=1, maxRepairAttempts=2,
                lastVerdict="approved", lastSummary="Looks good", reason="none",
                prNumber=108, prHeadSha="def456abc123", routingRecommendation="unchanged",
                updatedAt=iso(-75),
            ),
        }],
    },
    108: {
        "number": 108, "title": "Needs product decision", "state": "OPEN",
        "url": "https://github.com/Shashakar/RPG-Kingdom/issues/108",
        "updatedAt": iso(-70), "labels": [{"name": "symphony:human-attention"}],
        "comments": [{
            "id": "c108", "createdAt": iso(-65), "url": "https://example.invalid/c108",
            "body": marker(
                state="human_attention", reviewCycle=1, repairAttempts=0, maxRepairAttempts=2,
                lastVerdict="blocked_or_ambiguous", lastSummary="Scope decision required",
                reason="scope_change_required", prNumber=109, prHeadSha="987654321abc",
                routingRecommendation="unchanged", updatedAt=iso(-65),
            ),
        }],
    },
    109: {
        "number": 109, "title": "Quota stopped worker", "state": "OPEN",
        "url": "https://github.com/Shashakar/RPG-Kingdom/issues/109",
        "updatedAt": iso(-60), "labels": [{"name": "symphony:halted"}, {"name": "risk:normal"}],
        "comments": [{
            "id": "c109", "createdAt": iso(-55), "url": "https://example.invalid/c109",
            "body": "Symphony stopped this worker lifetime because Codex reported `usage_limit_exceeded`.",
        }],
    },
    110: {
        "number": 110, "title": "Active implementation", "state": "OPEN",
        "url": "https://github.com/Shashakar/RPG-Kingdom/issues/110",
        "updatedAt": iso(-50), "labels": [{"name": "symphony:ready"}, {"name": "risk:architecture"}],
        "comments": [],
    },
}

EVENTS = {
    number: [{
        "id": number * 10,
        "event": "labeled",
        "created_at": iso(-120 + number - 106),
        "label": {"name": next(
            item["name"] for item in issue["labels"]
            if item["name"] in activity.LIFECYCLE_LABELS
            and item["name"] not in {"symphony:ready", "symphony:agent-review"}
        ) if number in {106, 107, 108, 109} else "symphony:ready"},
    }]
    for number, issue in ISSUES.items()
}

# Rework legitimately overlaps ready/agent-review during handoff. Add those events too; precedence
# must still make the current queue Rework.
EVENTS[106] = [
    {"id": 1061, "event": "labeled", "created_at": iso(-150), "label": {"name": "symphony:agent-review"}},
    {"id": 1062, "event": "labeled", "created_at": iso(-140), "label": {"name": "symphony:ready"}},
    {"id": 1063, "event": "labeled", "created_at": iso(-130), "label": {"name": "symphony:rework"}},
]


def fake_runner(args: list[str]) -> dict[str, object]:
    if args[:2] == ["issue", "list"]:
        payload = [{key: value for key, value in issue.items() if key != "comments"} for issue in ISSUES.values()]
        return {"ok": True, "stdout": json.dumps(payload), "stderr": "", "code": 0}
    if args[:2] == ["issue", "view"]:
        number = int(args[2])
        return {"ok": True, "stdout": json.dumps(ISSUES[number]), "stderr": "", "code": 0}
    if args and args[0] == "api":
        number = int(args[1].split("/issues/", 1)[1].split("/", 1)[0])
        return {"ok": True, "stdout": json.dumps(EVENTS[number]), "stderr": "", "code": 0}
    return {"ok": False, "stdout": "", "stderr": f"unexpected args: {args}", "code": 1}


ACTIVE = [{
    "issue": 110, "identifier": "GH-110", "alive": True, "role": "implementation",
    "runId": "run-110", "model": "gpt-5.6-terra", "effort": "medium", "route": "terra",
}]
RECENT = [{"issue": 106, "identifier": "GH-106", "role": "repair", "outcome": "agent-review"}]

lifecycle = activity.collect_lifecycle(
    repo="Shashakar/RPG-Kingdom",
    runner=fake_runner,
    active_workers=ACTIVE,
    recent_workers=RECENT,
)
assert lifecycle["available"] is True
assert [item["issue"] for item in lifecycle["queues"]["rework"]] == [106]
assert not lifecycle["queues"]["agent_review"], "rework must win over the transient agent-review label"
assert not [item for item in lifecycle["queues"]["implementing"] if item["issue"] == 106], "rework must win over ready"

rework = lifecycle["queues"]["rework"][0]
assert rework["route"]["route"] == "terra"
assert rework["route"]["model"] == "gpt-5.6-terra"
assert rework["reviewCycle"] == 1
assert rework["repairAttempts"] == 1 and rework["maxRepairAttempts"] == 2
assert rework["prNumber"] == 107 and rework["headSha"] == "abc123def456"
assert rework["stateSince"] == EVENTS[106][-1]["created_at"]
assert rework["stateAgeSeconds"] is not None and rework["stateAgeSeconds"] >= 0

human_review = lifecycle["queues"]["human_review"][0]
assert human_review["humanActionRequired"] is True
assert human_review["latestVerdict"] == "approved"
assert lifecycle["queues"]["human_attention"][0]["haltReason"] == "scope_change_required"

halted = lifecycle["queues"]["halted"][0]
assert halted["quotaBlocked"] is True
assert halted["haltReason"] == "usage_limit_exceeded"

implementation = lifecycle["queues"]["implementing"][0]
assert implementation["issue"] == 110 and implementation["active"] is True
# Runtime truth wins for an active worker; labels describe what should route, not what is already running.
assert implementation["route"]["source"] == "active-worker"
assert implementation["route"]["route"] == "terra"

# Routing projection follows the same precedence as routing-policy.sh.
assert activity.route_from_labels(["symphony:ready", "risk:architecture"])["route"] == "sol"
assert activity.route_from_labels(["symphony:rework", "risk:architecture", "repair-route:luna"])["route"] == "luna"
assert activity.route_from_labels(["symphony:ready", "model:terra", "risk:architecture", "effort:high"])["route"] == "terra"
assert activity.route_from_labels(["symphony:ready", "model:terra", "model:sol"])["status"] == "invalid"

with tempfile.TemporaryDirectory() as temp_dir:
    workspace_root = Path(temp_dir)
    response_dir = workspace_root / "GH-106" / "Logs" / "SymphonyGit" / ".broker" / "responses"
    response_dir.mkdir(parents=True)
    (response_dir / "git-1.json").write_text(json.dumps({
        "protocolVersion": 1,
        "requestId": "git-1",
        "operation": "handoff",
        "status": "completed",
        "exitCode": 0,
        "completedAt": iso(-20),
        "result": {
            "prNumber": 107,
            "prUrl": "https://github.com/Shashakar/RPG-Kingdom/pull/107",
            "commitSha": "feedfacecafebeef",
        },
    }), encoding="utf-8")

    unity_runs = [{
        "requestId": "unity-1", "issue": "GH-106", "operation": "playmode",
        "testFilter": "InventoryTests", "startedAt": iso(-45), "completedAt": iso(-35),
        "finalStatus": "failed", "status": "completed",
        "diagnosis": {"category": "compile", "message": "not all code paths return a value", "code": "CS0161"},
    }]
    telemetry_events = [
        {
            "eventType": "worker_started", "observedAt": iso(-60), "issue": 106, "identifier": "GH-106",
            "runId": "repair-106", "role": "repair", "route": "terra", "model": "gpt-5.6-terra",
            "effort": "medium", "startedAt": iso(-60),
        },
        {
            "eventType": "worker_completed", "observedAt": iso(-10), "issue": 106, "identifier": "GH-106",
            "runId": "repair-106", "role": "repair", "route": "terra", "model": "gpt-5.6-terra",
            "effort": "medium", "startedAt": iso(-60), "endedAt": iso(-10), "outcome": "agent-review",
            "tokenUsage": {"status": "available", "totalTokens": 1234},
        },
    ]

    original_active = activity.supervisor_telemetry.active_workers
    original_recent = activity.supervisor_telemetry.recent_workers
    original_events = activity.supervisor_telemetry.recent_events
    original_runs = activity.unity_run_history.collect_runs
    try:
        activity.supervisor_telemetry.active_workers = lambda: ACTIVE
        activity.supervisor_telemetry.recent_workers = lambda limit=20: RECENT
        activity.supervisor_telemetry.recent_events = lambda limit=50: telemetry_events
        activity.unity_run_history.collect_runs = lambda **kwargs: unity_runs
        combined = activity.collect(
            repo="Shashakar/RPG-Kingdom",
            runner=fake_runner,
            workspace_root=workspace_root,
            use_cache=False,
        )
    finally:
        activity.supervisor_telemetry.active_workers = original_active
        activity.supervisor_telemetry.recent_workers = original_recent
        activity.supervisor_telemetry.recent_events = original_events
        activity.unity_run_history.collect_runs = original_runs

    categories = {item["category"] for item in combined["activity"]}
    assert {"lifecycle", "review", "worker", "unity", "git"}.issubset(categories), categories
    compile_events = [item for item in combined["activity"] if item.get("unityRequestId") == "unity-1"]
    assert compile_events and "CS0161" in compile_events[0]["title"]
    completed = next(item for item in combined["activity"] if item.get("id") == "telemetry:worker_completed:repair-106")
    assert completed["unityRequestIds"] == ["unity-1"]
    handoff = next(item for item in combined["activity"] if item.get("id") == "git:GH-106:git-1")
    assert handoff["prNumber"] == 107 and handoff["headSha"] == "feedfacecafebeef"

print("supervisor-activity-test: PASS")
