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

for name in (
    "RPGK_CONTINUATION_MAX_TURN_PRIMARY_SPEND_PERCENT",
    "RPGK_CONTINUATION_MAX_TURN_WEEKLY_SPEND_PERCENT",
    "RPGK_CONTINUATION_MAX_LIFETIME_PRIMARY_SPEND_PERCENT",
    "RPGK_CONTINUATION_MAX_LIFETIME_WEEKLY_SPEND_PERCENT",
    "RPGK_CONTINUATION_MAX_TURN_FRESH_TOKENS",
    "RPGK_CONTINUATION_MAX_LIFETIME_FRESH_TOKENS",
):
    os.environ.pop(name, None)

assert policy.route_class(["risk:mechanical"]) == "luna"
assert policy.route_class(["risk:investigative"]) == "terra"
assert policy.route_class(["risk:architecture"]) == "sol"
assert policy.route_class(["risk:end-to-end"]) == "astra"
assert policy.route_class(["risk:investigative", "model:luna"]) == "luna"
assert policy.is_report_only(["completion:report-only"])
assert not policy.is_report_only(["risk:mechanical"])


def quota(primary: float, weekly: float, observed: str | None = None) -> dict:
    return {
        "status": "available",
        "observedAt": observed or policy.iso_now(),
        "rateLimits": {
            "primary": {"remainingPercent": primary, "resetsAtIso": "primary-reset"},
            "secondary": {"remainingPercent": weekly, "resetsAtIso": "weekly-reset"},
        },
    }


healthy_quota = quota(80, 90)
allowed, reason, detail = policy.quota_decision(healthy_quota, "terra")
assert allowed, reason
assert detail["minimumPrimaryPercent"] == 35.0

low_quota = quota(20, 90)
allowed, reason, _ = policy.quota_decision(low_quota, "terra")
assert not allowed and "below continuation threshold" in reason

allowed, reason, _ = policy.quota_decision({"status": "unavailable", "reason": "probe failed"}, "terra")
assert not allowed and "probe failed" in reason

# Spend is derived from authoritative remaining-percentage movement; token counts are not mapped
# to percentage points.
worker = {"quotaBefore": quota(90, 90)}
previous = {"quota": quota(87, 89)}
usage = {
    "status": "available",
    "inputTokens": 1_500_000,
    "cachedInputTokens": 1_350_000,
    "outputTokens": 20_000,
    "reasoningTokens": 5_000,
    "totalTokens": 1_520_000,
}
usage_delta = {
    "status": "available",
    "inputTokens": 700_000,
    "cachedInputTokens": 640_000,
    "outputTokens": 8_000,
    "reasoningTokens": 2_000,
    "totalTokens": 708_000,
}
allowed, reason, detail = policy.budget_decision(quota(83, 88), worker, previous, usage, usage_delta)
assert allowed, reason
assert detail["turnPrimarySpendPercent"] == 4
assert detail["lifetimePrimarySpendPercent"] == 7
assert detail["tokenFallbackUsed"] is False

# A pathological single turn is stopped even when reserve remains above the basic quota floor.
allowed, reason, _ = policy.budget_decision(
    quota(70, 85),
    {"quotaBefore": quota(90, 90)},
    {"quota": quota(88, 89)},
    usage,
    usage_delta,
)
assert not allowed and "turn primary spend 18pp" in reason

# Lifetime cost can stop a worker even when the latest turn itself is reasonable.
allowed, reason, _ = policy.budget_decision(
    quota(59, 81),
    {"quotaBefore": quota(90, 90)},
    {"quota": quota(64, 82)},
    usage,
    usage_delta,
)
assert not allowed and "lifetime primary spend 31pp" in reason

# When quota movement cannot be attributed (for example a reset boundary), fresh-token telemetry is
# a fallback safety budget, not a synthetic quota estimate.
reset_quota = quota(80, 90)
reset_quota["rateLimits"]["primary"]["resetsAtIso"] = "new-reset"
reset_quota["rateLimits"]["secondary"]["resetsAtIso"] = "new-weekly-reset"
allowed, reason, detail = policy.budget_decision(
    reset_quota,
    {"quotaBefore": quota(90, 90)},
    {"quota": quota(85, 89)},
    usage,
    usage_delta,
)
assert allowed, reason
assert detail["tokenFallbackUsed"] is True
assert "fresh-token fallback" in reason

# Observable implementation progress continues normally.
first_workspace = {"fingerprint": "a", "dirty": True}
allowed, reason, detail = policy.progress_decision(first_workspace, None, None)
assert allowed and "source or Unity evidence" in reason
assert detail["consecutiveInvisibleTurns"] == 0

# A first analysis-only turn is allowed once; two consecutive invisible turns are not.
allowed, reason, detail = policy.progress_decision({"fingerprint": "clean", "dirty": False}, None, None)
assert allowed and "analysis-only" in reason
assert detail["consecutiveInvisibleTurns"] == 1

invisible_previous = {
    "workspace": {"fingerprint": "clean", "dirty": False},
    "unity": None,
    "progress": detail,
}
allowed, reason, detail2 = policy.progress_decision(
    {"fingerprint": "clean", "dirty": False}, None, invisible_previous
)
assert not allowed and "second consecutive" in reason
assert detail2["consecutiveInvisibleTurns"] == 2

# A productive turn resets the invisible-turn grace.
allowed, reason, detail = policy.progress_decision(
    {"fingerprint": "changed", "dirty": True},
    None,
    invisible_previous,
)
assert allowed and "progress detected" in reason
assert detail["consecutiveInvisibleTurns"] == 0

# Report-only tasks are intentionally source-clean. Analysis/report synthesis is not necessarily
# host-observable, so a clean workspace remains valid progress until the hard ceiling/budget stops it.
allowed, reason, detail = policy.progress_decision(
    {"fingerprint": "clean", "dirty": False},
    None,
    None,
    report_only=True,
)
assert allowed and "host-invisible" in reason
assert detail["reportOnly"] is True

allowed, reason, _ = policy.progress_decision(
    {"fingerprint": "dirty", "dirty": True},
    None,
    None,
    report_only=True,
)
assert not allowed and "requires source-clean state" in reason

previous_focus = {
    "workspace": {"fingerprint": "a", "dirty": True},
    "unity": {"runId": "run-1", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    "progress": {"consecutiveInvisibleTurns": 0},
}
allowed, reason, _ = policy.progress_decision(
    {"fingerprint": "a", "dirty": True},
    {"runId": "run-2", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    previous_focus,
)
assert not allowed and "failed again" in reason

# Unfiltered EditMode and PlayMode suites are different validation work even though both filters
# are empty. Do not collapse them into the repeated-focused-failure stop condition.
previous_full = {
    "workspace": {"fingerprint": "clean", "dirty": False},
    "unity": {"runId": "edit", "testPlatform": "EditMode", "testFilter": None, "failed": 5},
    "progress": {"consecutiveInvisibleTurns": 0},
}
allowed, reason, _ = policy.progress_decision(
    {"fingerprint": "clean", "dirty": False},
    {"runId": "play", "testPlatform": "PlayMode", "testFilter": None, "failed": 7},
    previous_full,
)
assert allowed and "progress detected" in reason

# Report-only diagnostics may intentionally rerun a failing focused test to establish evidence.
allowed, reason, _ = policy.progress_decision(
    {"fingerprint": "clean", "dirty": False},
    {"runId": "run-2", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    {
        "workspace": {"fingerprint": "clean", "dirty": False},
        "unity": {"runId": "run-1", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    },
    report_only=True,
)
assert allowed and "new Unity evidence" in reason

# Exercise the integrated decision path without starting Codex/App Server or touching real state.
real_refresh_quota = policy.refresh_quota
real_workspace_snapshot = policy.workspace_snapshot
real_latest_unity = policy.latest_unity
real_usage_snapshot = policy.usage_snapshot
real_active_worker = policy.active_worker
real_append_event = policy.telemetry.append_event
real_ensure_local_excludes = policy.ensure_local_excludes
try:
    current_quota = [quota(90, 90)]
    policy.refresh_quota = lambda workspace: current_quota[0]
    policy.latest_unity = lambda workspace: None
    policy.usage_snapshot = lambda workspace, issue, worker=None: {
        "status": "available",
        "inputTokens": 1_000,
        "cachedInputTokens": 900,
        "outputTokens": 50,
        "reasoningTokens": 20,
        "totalTokens": 1_050,
    }
    policy.active_worker = lambda workspace, issue: {"quotaBefore": quota(95, 92), "startedAt": policy.iso_now()}
    policy.telemetry.append_event = lambda *args, **kwargs: None
    policy.ensure_local_excludes = lambda workspace: None

    # GH-111 regression: Terra turn 2 is no longer stopped merely because it is Terra. A bounded
    # analysis-only continuation is allowed when quota/spend are healthy, then a second invisible
    # turn stops if it still produces no source/Unity progress.
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp)
        snapshots = iter(
            [
                {"fingerprint": "turn-1", "dirty": True, "head": "a", "gitStatus": " M one"},
                {"fingerprint": "turn-1", "dirty": True, "head": "a", "gitStatus": " M one"},
                {"fingerprint": "turn-1", "dirty": True, "head": "a", "gitStatus": " M one"},
            ]
        )
        policy.workspace_snapshot = lambda workspace: next(snapshots)

        current_quota[0] = quota(92, 91)
        result = policy.evaluate(workspace, "GH-111", 1, 4, ["risk:investigative"])
        assert result == 0
        first = json.loads((workspace / policy.STATE_NAME).read_text(encoding="utf-8"))
        assert first["decision"] == "continue"
        assert first["continuationBudgetMode"] == "dynamic"
        assert first["automaticTurnLimit"] == 4

        current_quota[0] = quota(89, 90)
        result = policy.evaluate(workspace, "GH-111", 2, 4, ["risk:investigative"])
        assert result == 0
        second = json.loads((workspace / policy.STATE_NAME).read_text(encoding="utf-8"))
        assert second["decision"] == "continue"
        assert second["progress"]["analysisGraceUsed"] is True
        assert "hard turn ceiling not reached (2/4)" in second["reason"]
        assert "route terra automatic turn limit" not in second["reason"]

        current_quota[0] = quota(87, 89)
        result = policy.evaluate(workspace, "GH-111", 3, 4, ["risk:investigative"])
        assert result == 20
        stopped = json.loads((workspace / policy.STOP_NAME).read_text(encoding="utf-8"))
        assert "second consecutive host-invisible turn" in stopped["reason"]

    # Productive Terra may continue through turn 3 and only hits the hard ceiling after turn 4.
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp)
        snapshots = iter(
            [
                {"fingerprint": "p1", "dirty": True, "head": "a", "gitStatus": " M one"},
                {"fingerprint": "p2", "dirty": True, "head": "a", "gitStatus": " M two"},
                {"fingerprint": "p3", "dirty": True, "head": "a", "gitStatus": " M three"},
                {"fingerprint": "p4", "dirty": True, "head": "a", "gitStatus": " M four"},
            ]
        )
        policy.workspace_snapshot = lambda workspace: next(snapshots)
        for turn, primary, weekly, expected in (
            (1, 92, 91, 0),
            (2, 89, 90, 0),
            (3, 86, 89, 0),
            (4, 83, 88, 20),
        ):
            current_quota[0] = quota(primary, weekly)
            result = policy.evaluate(workspace, "GH-300", turn, 4, ["risk:investigative"])
            assert result == expected
        stopped = json.loads((workspace / policy.STOP_NAME).read_text(encoding="utf-8"))
        assert "hard turn ceiling reached (4 total turns)" in stopped["reason"]

    # Report-only work remains source-clean and may synthesize across multiple turns.
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp)
        policy.workspace_snapshot = lambda workspace: {
            "fingerprint": "report-clean",
            "dirty": False,
            "head": "main",
            "gitStatus": "",
        }
        current_quota[0] = quota(92, 91)
        assert policy.evaluate(workspace, "GH-110", 1, 4, ["risk:mechanical", "completion:report-only"]) == 0
        current_quota[0] = quota(91, 90)
        assert policy.evaluate(workspace, "GH-110", 2, 4, ["risk:mechanical", "completion:report-only"]) == 0
        state = json.loads((workspace / policy.STATE_NAME).read_text(encoding="utf-8"))
        assert state["completionMode"] == "report-only"
        assert "host-invisible" in state["reason"]

    # Missing authoritative current quota remains a transparent fail-safe stop.
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
    policy.active_worker = real_active_worker
    policy.telemetry.append_event = real_append_event
    policy.ensure_local_excludes = real_ensure_local_excludes

print("continuation-policy-test: PASS")
