#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "continuation-policy.py"
spec = importlib.util.spec_from_file_location("continuation_policy", SCRIPT)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

assert policy.route_class(["risk:mechanical"]) == "luna"
assert policy.route_class(["risk:investigative"]) == "terra"
assert policy.route_class(["risk:architecture"]) == "sol"
assert policy.route_class(["risk:end-to-end"]) == "astra"
assert policy.route_class(["risk:investigative", "model:luna"]) == "luna"

healthy_quota = {
    "status": "available",
    "observedAt": policy.iso_now(),
    "rateLimits": {
        "primary": {"remainingPercent": 80, "resetsAtIso": "later"},
        "secondary": {"remainingPercent": 90},
    },
}
allowed, reason, detail = policy.quota_decision(healthy_quota, "terra")
assert allowed, reason
assert detail["minimumPrimaryPercent"] == 35.0

low_quota = json.loads(json.dumps(healthy_quota))
low_quota["rateLimits"]["primary"]["remainingPercent"] = 20
allowed, reason, _ = policy.quota_decision(low_quota, "terra")
assert not allowed and "below continuation threshold" in reason

allowed, reason, _ = policy.quota_decision({"status": "unavailable", "reason": "probe failed"}, "terra")
assert not allowed and "probe failed" in reason

first_workspace = {"fingerprint": "a", "dirty": True}
allowed, reason, _ = policy.progress_decision(first_workspace, None, None)
assert allowed and "source or Unity evidence" in reason

allowed, reason, _ = policy.progress_decision({"fingerprint": "a", "dirty": False}, None, None)
assert not allowed and "no source diff" in reason

previous = {
    "workspace": {"fingerprint": "a", "dirty": True},
    "unity": {"runId": "run-1", "testFilter": "Focused", "failed": 1},
}
allowed, reason, _ = policy.progress_decision(
    {"fingerprint": "a", "dirty": True},
    {"runId": "run-2", "testFilter": "Focused", "failed": 1},
    previous,
)
assert not allowed and "failed again" in reason

# Exercise the integrated decision path without starting Codex/App Server or touching the real
# Supervisor state. GH-108 should regress to at most two automatic Terra turns by default.
real_refresh_quota = policy.refresh_quota
real_workspace_snapshot = policy.workspace_snapshot
real_latest_unity = policy.latest_unity
real_usage_snapshot = policy.usage_snapshot
real_append_event = policy.telemetry.append_event
real_ensure_local_excludes = policy.ensure_local_excludes
try:
    policy.refresh_quota = lambda workspace: healthy_quota
    policy.latest_unity = lambda workspace: None
    policy.usage_snapshot = lambda workspace, issue: {
        "status": "available",
        "inputTokens": 1000,
        "cachedInputTokens": 900,
        "outputTokens": 50,
        "reasoningTokens": 20,
        "totalTokens": 1050,
    }
    policy.telemetry.append_event = lambda *args, **kwargs: None
    policy.ensure_local_excludes = lambda workspace: None

    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp)
        snapshots = iter(
            [
                {"fingerprint": "turn-1", "dirty": True, "head": "a", "gitStatus": " M one"},
                {"fingerprint": "turn-2", "dirty": True, "head": "a", "gitStatus": " M two"},
            ]
        )
        policy.workspace_snapshot = lambda workspace: next(snapshots)

        result = policy.evaluate(workspace, "GH-108", 1, 4, ["risk:investigative"])
        assert result == 0
        first = json.loads((workspace / policy.STATE_NAME).read_text(encoding="utf-8"))
        assert first["decision"] == "continue"
        assert first["automaticTurnLimit"] == 2

        result = policy.evaluate(workspace, "GH-108", 2, 4, ["risk:investigative"])
        assert result == 20
        stopped = json.loads((workspace / policy.STOP_NAME).read_text(encoding="utf-8"))
        assert stopped["decision"] == "stop"
        assert "automatic turn limit reached" in stopped["reason"]

    # Cheap Luna work may still use the hard four-turn ceiling when it is making progress.
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp)
        policy.workspace_snapshot = lambda workspace: {
            "fingerprint": "luna-progress",
            "dirty": True,
            "head": "b",
            "gitStatus": " M file",
        }
        result = policy.evaluate(workspace, "GH-200", 1, 4, ["risk:normal"])
        assert result == 0
        state = json.loads((workspace / policy.STATE_NAME).read_text(encoding="utf-8"))
        assert state["automaticTurnLimit"] == 4

    # Missing authoritative quota is a transparent fail-safe stop, not unlimited capacity.
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp)
        policy.refresh_quota = lambda workspace: {"status": "unavailable", "reason": "no sample"}
        policy.workspace_snapshot = lambda workspace: {
            "fingerprint": "progress",
            "dirty": True,
            "head": "c",
            "gitStatus": " M file",
        }
        result = policy.evaluate(workspace, "GH-201", 1, 4, ["risk:normal"])
        assert result == 20
        stopped = json.loads((workspace / policy.STOP_NAME).read_text(encoding="utf-8"))
        assert "quota unavailable" in stopped["reason"]
finally:
    policy.refresh_quota = real_refresh_quota
    policy.workspace_snapshot = real_workspace_snapshot
    policy.latest_unity = real_latest_unity
    policy.usage_snapshot = real_usage_snapshot
    policy.telemetry.append_event = real_append_event
    policy.ensure_local_excludes = real_ensure_local_excludes

print("continuation-policy-test: PASS")
