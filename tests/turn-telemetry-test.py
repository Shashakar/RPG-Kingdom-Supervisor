#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "turn-telemetry.py"
spec = importlib.util.spec_from_file_location("turn_telemetry", SCRIPT)
assert spec and spec.loader
turns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(turns)

quota_80 = {
    "status": "available", "accountId": "acct", "observedAt": "2026-09-13T01:00:00+00:00",
    "rateLimits": {"primary": {"remainingPercent": 80}, "secondary": {"remainingPercent": 90}},
}
quota_76 = {
    "status": "available", "accountId": "acct", "observedAt": "2026-09-13T01:02:00+00:00",
    "rateLimits": {"primary": {"remainingPercent": 76}, "secondary": {"remainingPercent": 89}},
}
quota_74 = {
    "status": "available", "accountId": "acct", "observedAt": "2026-09-13T01:03:00+00:00",
    "rateLimits": {"primary": {"remainingPercent": 74}, "secondary": {"remainingPercent": 88}},
}
quota_70 = {
    "status": "available", "accountId": "acct", "observedAt": "2026-09-13T01:05:00+00:00",
    "rateLimits": {"primary": {"remainingPercent": 70}, "secondary": {"remainingPercent": 87}},
}

usage_1 = {
    "status": "available", "inputTokens": 1000, "cachedInputTokens": 900,
    "outputTokens": 80, "reasoningTokens": 20, "totalTokens": 1080,
}
usage_2 = {
    "status": "available", "inputTokens": 1160, "cachedInputTokens": 1040,
    "outputTokens": 90, "reasoningTokens": 25, "totalTokens": 1250,
}
workspace_0 = {"head": "a", "fingerprint": "w0", "dirty": False, "gitStatus": ""}
workspace_1 = {"head": "a", "fingerprint": "w1", "dirty": True, "gitStatus": " M file"}
workspace_2 = {"head": "a", "fingerprint": "w2", "dirty": True, "gitStatus": " M file\n M test"}
unity_1 = {"runId": "unity-1", "result": "failed", "testFilter": "Focused", "failed": 1}
unity_2 = {"runId": "unity-2", "result": "failed", "testFilter": "Focused", "failed": 1}

# Only turn-start snapshots should call the turn telemetry probe for continuation-policy exits.
# The finish path must reuse the policy's already-fresh ending sample instead of probing quota again.
snapshots = iter([
    {
        "observedAt": "2026-09-13T01:00:00+00:00", "quota": quota_80,
        "usage": {"status": "unavailable", "reason": "no rollout yet"},
        "workspace": workspace_0, "unity": None,
    },
    {
        "observedAt": "2026-09-13T01:03:00+00:00", "quota": quota_74,
        "usage": usage_1, "workspace": workspace_1, "unity": unity_1,
    },
])

policy_end_1 = {
    "observedAt": "2026-09-13T01:02:00+00:00", "quota": quota_76,
    "usage": usage_1, "workspace": workspace_1, "unity": unity_1,
    "reason": "quota healthy; host-observable progress detected",
}
policy_end_2 = {
    "observedAt": "2026-09-13T01:05:00+00:00", "quota": quota_70,
    "usage": usage_2, "workspace": workspace_2, "unity": unity_2,
    "reason": "route terra automatic turn limit reached (2 total turns)",
}

events: list[dict] = []
turns._snapshot = lambda workspace, issue: next(snapshots)
turns._worker_run_id = lambda workspace, issue: "GH-108-implementation-test"
turns._ensure_local_excludes = lambda workspace: None
turns.telemetry.append_event = lambda event_type, **fields: events.append({"eventType": event_type, **fields})

with tempfile.TemporaryDirectory() as temp:
    workspace = Path(temp)
    assert turns.start_turn(workspace, "GH-108", 1, 4, ["risk:investigative"]) == 0
    assert turns.finish_turn(workspace, "GH-108", 1, "continue", json.dumps(policy_end_1)) == 0
    first = json.loads((workspace / turns.HISTORY_NAME).read_text(encoding="utf-8").splitlines()[0])
    assert first["automaticTurnLimit"] == 2
    assert first["tokenDelta"]["totalTokens"] == 1080
    assert first["tokenDelta"]["cachedInputTokens"] == 900
    assert first["tokenDelta"]["basis"].startswith("first-turn cumulative")
    assert first["quotaDelta"]["primary"]["remainingPercentagePointDelta"] == -4
    assert first["progress"]["workspaceChanged"] is True
    assert first["progress"]["unityChanged"] is True
    assert first["continuationPolicy"]["quota"] == quota_76

    assert turns.start_turn(workspace, "GH-108", 2, 4, ["risk:investigative"]) == 0
    assert turns.finish_turn(workspace, "GH-108", 2, "continuation-budget-stop", json.dumps(policy_end_2)) == 0
    lines = (workspace / turns.HISTORY_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second["decision"] == "continuation-budget-stop"
    assert second["tokenDelta"]["totalTokens"] == 170
    assert second["tokenDelta"]["cachedInputTokens"] == 140
    assert second["tokenDelta"]["inputTokens"] == 160
    assert second["quotaDelta"]["primary"]["remainingPercentagePointDelta"] == -4
    assert "automatic turn limit reached" in second["reason"]
    assert second["unityAfter"]["runId"] == "unity-2"

assert [event["eventType"] for event in events] == [
    "worker_turn_started", "worker_turn_completed", "worker_turn_started", "worker_turn_completed",
]
assert events[-1]["workerRunId"] == "GH-108-implementation-test"

print("turn-telemetry-test: PASS")
