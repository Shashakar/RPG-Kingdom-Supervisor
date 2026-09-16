#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finished_tasks  # noqa: E402


CLOSED = [
    {
        "number": 201,
        "title": "Completed through Symphony",
        "state": "closed",
        "state_reason": "completed",
        "html_url": "https://github.com/Shashakar/RPG-Kingdom/issues/201",
        "closed_at": "2026-09-16T06:00:00Z",
        "updated_at": "2026-09-16T06:00:00Z",
        "labels": [{"name": "symphony:human-review"}, {"name": "risk:normal"}],
    },
    {
        "number": 202,
        "title": "Completed with retained worker evidence",
        "state": "closed",
        "state_reason": "completed",
        "html_url": "https://github.com/Shashakar/RPG-Kingdom/issues/202",
        "closed_at": "2026-09-16T06:05:00Z",
        "updated_at": "2026-09-16T06:05:00Z",
        "labels": [{"name": "risk:architecture"}],
    },
    {
        "number": 203,
        "title": "Closed but still halted",
        "state": "closed",
        "state_reason": "completed",
        "html_url": "https://github.com/Shashakar/RPG-Kingdom/issues/203",
        "closed_at": "2026-09-16T06:10:00Z",
        "updated_at": "2026-09-16T06:10:00Z",
        "labels": [{"name": "symphony:halted"}],
    },
    {
        "number": 204,
        "title": "Not planned",
        "state": "closed",
        "state_reason": "not_planned",
        "html_url": "https://github.com/Shashakar/RPG-Kingdom/issues/204",
        "closed_at": "2026-09-16T06:15:00Z",
        "updated_at": "2026-09-16T06:15:00Z",
        "labels": [{"name": "symphony:human-review"}],
    },
    {
        "number": 205,
        "title": "Pull request shape",
        "state": "closed",
        "state_reason": "completed",
        "html_url": "https://github.com/Shashakar/RPG-Kingdom/pull/205",
        "closed_at": "2026-09-16T06:20:00Z",
        "updated_at": "2026-09-16T06:20:00Z",
        "labels": [{"name": "symphony:human-review"}],
        "pull_request": {"url": "https://api.github.com/repos/Shashakar/RPG-Kingdom/pulls/205"},
    },
]


def fake_runner(args: list[str]) -> dict[str, object]:
    assert args[0] == "api"
    assert "issues?state=closed" in args[1]
    return {"ok": True, "stdout": json.dumps(CLOSED), "stderr": "", "code": 0}


RECENT = [
    {
        "issue": 202,
        "identifier": "GH-202",
        "role": "implementation",
        "outcome": "human-review",
        "model": "gpt-5.6-sol",
        "effort": "high",
        "route": "sol",
    },
    {
        "issue": 201,
        "identifier": "GH-201",
        "role": "implementation",
        "outcome": "agent-review",
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "route": "luna",
    },
]

result = finished_tasks.collect(
    repo="Shashakar/RPG-Kingdom",
    runner=fake_runner,
    recent_workers=RECENT,
    limit=12,
)

assert result["available"] is True
assert [item["issue"] for item in result["items"]] == [202, 201]
assert all(item["status"] == "done" and item["statusLabel"] == "Done" for item in result["items"])

item_202 = result["items"][0]
assert item_202["latestWorkerOutcome"] == "human-review"
assert item_202["model"] == "gpt-5.6-sol"
assert item_202["effort"] == "high"
assert item_202["route"] == "sol"

assert 203 not in {item["issue"] for item in result["items"]}, "halted must never be called finished"
assert 204 not in {item["issue"] for item in result["items"]}, "not_planned must not be called finished"
assert 205 not in {item["issue"] for item in result["items"]}, "pull requests are not finished task rows"

failure = finished_tasks.collect(
    runner=lambda _args: {"ok": False, "stdout": "", "stderr": "offline", "code": 1},
    recent_workers=[],
)
assert failure["available"] is False
assert failure["items"] == []
assert failure["errors"] == ["offline"]

print("finished-tasks-test: PASS")
