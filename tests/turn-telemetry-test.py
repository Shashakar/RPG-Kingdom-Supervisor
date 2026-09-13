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

capabilities = {
    "mcp": [
        {"name": "graphify", "mcpServer": "rpgk_graphify", "enabled": True},
        {"name": "context7", "mcpServer": "rpgk_context7", "enabled": True},
    ]
}

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
    "continuationBudgetMode": "dynamic",
    "dynamicBudget": {
        "turnPrimarySpendPercent": 4,
        "turnWeeklySpendPercent": 1,
        "lifetimePrimarySpendPercent": 4,
        "lifetimeWeeklySpendPercent": 1,
    },
    "reason": "hard turn ceiling not reached (1/4); quota healthy; dynamic continuation spend remains within budget; host-observable progress detected",
}
policy_end_2 = {
    "observedAt": "2026-09-13T01:05:00+00:00", "quota": quota_70,
    "usage": usage_2, "workspace": workspace_2, "unity": unity_2,
    "continuationBudgetMode": "dynamic",
    "dynamicBudget": {
        "turnPrimarySpendPercent": 4,
        "turnWeeklySpendPercent": 1,
        "lifetimePrimarySpendPercent": 10,
        "lifetimeWeeklySpendPercent": 3,
    },
    "reason": "hard turn ceiling not reached (2/4); dynamic continuation budget exceeded: turn primary spend 16pp exceeds 15pp",
}

events: list[dict] = []
turns._snapshot = lambda workspace, issue: next(snapshots)
turns._worker_run_id = lambda workspace, issue: "GH-108-implementation-test"
turns._worker_capabilities = lambda workspace, issue: capabilities
turns._ensure_local_excludes = lambda workspace: None
turns.telemetry.append_event = lambda event_type, **fields: events.append({"eventType": event_type, **fields})

with tempfile.TemporaryDirectory() as temp:
    workspace = Path(temp)
    assert turns.start_turn(workspace, "GH-108", 1, 4, ["risk:investigative"]) == 0
    assert turns.finish_turn(workspace, "GH-108", 1, "continue", json.dumps(policy_end_1)) == 0
    first = json.loads((workspace / turns.HISTORY_NAME).read_text(encoding="utf-8").splitlines()[0])
    # Dynamic continuation no longer gives Terra a smaller route cap. Turn telemetry retains this
    # compatibility field, but it now reflects the workflow hard ceiling.
    assert first["automaticTurnLimit"] == 4
    assert first["hardMaxTurns"] == 4
    assert first["tokenDelta"]["totalTokens"] == 1080
    assert first["tokenDelta"]["cachedInputTokens"] == 900
    assert first["tokenDelta"]["basis"].startswith("first-turn cumulative")
    assert first["quotaDelta"]["primary"]["remainingPercentagePointDelta"] == -4
    assert first["progress"]["workspaceChanged"] is True
    assert first["progress"]["unityChanged"] is True
    assert first["continuationPolicy"]["quota"] == quota_76
    assert first["continuationPolicy"]["continuationBudgetMode"] == "dynamic"
    assert first["continuationPolicy"]["dynamicBudget"]["turnPrimarySpendPercent"] == 4
    assert first["mcpUsage"]["status"] == "unavailable"

    assert turns.start_turn(workspace, "GH-108", 2, 4, ["risk:investigative"]) == 0
    assert turns.finish_turn(workspace, "GH-108", 2, "continuation-budget-stop", json.dumps(policy_end_2)) == 0
    lines = (workspace / turns.HISTORY_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second["decision"] == "continuation-budget-stop"
    assert second["automaticTurnLimit"] == 4
    assert second["tokenDelta"]["totalTokens"] == 170
    assert second["tokenDelta"]["cachedInputTokens"] == 140
    assert second["tokenDelta"]["inputTokens"] == 160
    assert second["quotaDelta"]["primary"]["remainingPercentagePointDelta"] == -4
    assert "dynamic continuation budget exceeded" in second["reason"]
    assert "automatic turn limit reached" not in second["reason"]
    assert second["unityAfter"]["runId"] == "unity-2"

    # Rollout MCP records are deduplicated by call id. A begin/end pair counts once, and
    # enabled-but-unused Context7 remains distinguishable from Graphify actually being used.
    rollout = workspace / "rollout-mcp.jsonl"
    rollout.write_text("\n".join(json.dumps(item) for item in [
        {"payload": {"type": "mcp_tool_call_begin", "call_id": "call-g1", "invocation": {"server": "rpgk_graphify", "tool": "find_references"}}},
        {"payload": {"type": "mcp_tool_call_end", "call_id": "call-g1", "invocation": {"server": "rpgk_graphify", "tool": "find_references"}}},
        {"payload": {"type": "mcp_tool_call", "id": "call-g2", "server": "rpgk_graphify", "tool": "dependency_path", "status": "completed"}},
        {"payload": {"type": "function_call", "call_id": "call-c1", "name": "mcp__rpgk_context7__query-docs"}},
    ]) + "\n", encoding="utf-8")
    mcp_after = turns._rollout_mcp_snapshot({"status": "available", "source": str(rollout)})
    assert mcp_after["callCount"] == 3
    mcp_delta = turns._mcp_delta({"status": "unavailable"}, mcp_after, 1, capabilities)
    assert mcp_delta["status"] == "available"
    assert mcp_delta["totalCalls"] == 3
    assert mcp_delta["byCapability"]["graphify"]["calls"] == 2
    assert mcp_delta["byCapability"]["graphify"]["used"] is True
    assert mcp_delta["byCapability"]["context7"]["calls"] == 1
    assert mcp_delta["byCapability"]["context7"]["tools"] == ["query-docs"]

    before = {"status": "available", "calls": [mcp_after["calls"][0]]}
    second_delta = turns._mcp_delta(before, mcp_after, 2, capabilities)
    assert second_delta["totalCalls"] == 2

assert [event["eventType"] for event in events] == [
    "worker_turn_started", "worker_turn_completed", "worker_turn_started", "worker_turn_completed",
]
assert events[-1]["workerRunId"] == "GH-108-implementation-test"

print("turn-telemetry-test: PASS")