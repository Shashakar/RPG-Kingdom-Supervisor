#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import runpy
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE_TEST = ROOT / "tests" / "continuation-policy-base-test.py"
SCRIPT = ROOT / "scripts" / "continuation-policy.py"

# First prove the preserved policy contract still passes exactly as before.
os.environ["RPGK_CONTINUATION_FOLLOW_THROUGH"] = "0"
runpy.run_path(str(BASE_TEST), run_name="__main__")

# Then exercise the new runtime-only follow-through behavior.
os.environ["RPGK_CONTINUATION_FOLLOW_THROUGH"] = "1"
spec = importlib.util.spec_from_file_location("continuation_policy_follow_through", SCRIPT)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def quota(primary: float, weekly: float) -> dict:
    return {
        "status": "available",
        "observedAt": policy.iso_now(),
        "rateLimits": {
            "primary": {"remainingPercent": primary, "resetsAtIso": "primary-reset"},
            "secondary": {"remainingPercent": weekly, "resetsAtIso": "weekly-reset"},
        },
    }


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

# A productive 18pp turn no longer halts solely because it crossed the ordinary 15pp threshold.
with tempfile.TemporaryDirectory() as temp:
    workspace = Path(temp)
    policy._ACTIVE_WORKSPACE = workspace
    policy._ACTIVE_LABELS = {"risk:investigative"}
    policy.workspace_snapshot = lambda _: {
        "fingerprint": "changed",
        "dirty": True,
        "head": "b",
        "gitStatus": " M gameplay.cs",
    }
    policy.latest_unity = lambda _: {"runId": "run-2", "failed": 1}
    allowed, reason, detail = policy._runtime_budget_decision(
        quota(70, 85),
        {"quotaBefore": quota(90, 90)},
        {"quota": quota(88, 89), "workspace": {"fingerprint": "old"}, "unity": {"runId": "run-1"}},
        usage,
        usage_delta,
    )
    assert allowed, reason
    assert detail["productiveOverride"] is True
    assert detail["turnPrimarySpendPercent"] == 18

# Lifetime cost remains a hard stop even when the latest turn produced progress.
with tempfile.TemporaryDirectory() as temp:
    workspace = Path(temp)
    policy._ACTIVE_WORKSPACE = workspace
    policy._ACTIVE_LABELS = {"risk:investigative"}
    policy.workspace_snapshot = lambda _: {
        "fingerprint": "changed-again",
        "dirty": True,
        "head": "c",
        "gitStatus": " M gameplay.cs",
    }
    policy.latest_unity = lambda _: {"runId": "run-3", "failed": 1}
    allowed, reason, detail = policy._runtime_budget_decision(
        quota(59, 81),
        {"quotaBefore": quota(90, 90)},
        {"quota": quota(64, 82), "workspace": {"fingerprint": "old"}, "unity": {"runId": "run-2"}},
        usage,
        usage_delta,
    )
    assert not allowed
    assert detail["lifetimePrimarySpendPercent"] == 31

# Implementation work gets two host-invisible follow-through turns after demonstrated progress.
policy._ACTIVE_LABELS = {"risk:investigative"}
previous = {
    "workspace": {"fingerprint": "same", "dirty": True},
    "unity": {"runId": "run-1", "failed": 1},
    "progress": {"consecutiveInvisibleTurns": 1},
}
allowed, reason, detail = policy._runtime_progress_decision(
    {"fingerprint": "same", "dirty": True},
    {"runId": "run-1", "failed": 1},
    previous,
)
assert allowed, reason
assert detail["consecutiveInvisibleTurns"] == 2
assert "follow-through analysis window" in reason

previous["progress"] = {"consecutiveInvisibleTurns": 2}
allowed, reason, detail = policy._runtime_progress_decision(
    {"fingerprint": "same", "dirty": True},
    {"runId": "run-1", "failed": 1},
    previous,
)
assert not allowed
assert detail["consecutiveInvisibleTurns"] == 3

# One repeated focused failure is treated as new downstream-defect evidence; a second repeat stops.
previous_focus = {
    "workspace": {"fingerprint": "same", "dirty": True},
    "unity": {"runId": "focused-1", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    "progress": {"consecutiveInvisibleTurns": 0, "repeatedFocusedFailureTurns": 0},
}
allowed, reason, detail = policy._runtime_progress_decision(
    {"fingerprint": "same", "dirty": True},
    {"runId": "focused-2", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    previous_focus,
)
assert allowed, reason
assert detail["repeatedFocusedFailureTurns"] == 1
assert "downstream-defect follow-through" in reason

previous_focus["unity"] = {"runId": "focused-2", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1}
previous_focus["progress"] = {"consecutiveInvisibleTurns": 0, "repeatedFocusedFailureTurns": 1}
allowed, reason, detail = policy._runtime_progress_decision(
    {"fingerprint": "same", "dirty": True},
    {"runId": "focused-3", "testPlatform": "PlayMode", "testFilter": "Focused", "failed": 1},
    previous_focus,
)
assert not allowed
assert detail["repeatedFocusedFailureTurns"] == 2

print("continuation-policy-follow-through-test: PASS")
