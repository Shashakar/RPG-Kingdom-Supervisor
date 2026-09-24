#!/usr/bin/env python3
"""Deterministic policy for operator-approved autonomous work."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

HUMAN = {"symphony:human-review", "symphony:human-attention"}
RECOVERABLE = {"continuation_policy", "usage_limit_exceeded", "resource_unavailable", "preflight_recoverable"}


def in_window(now: datetime, start: str, end: str) -> bool:
    start_time = time.fromisoformat(start)
    end_time = time.fromisoformat(end)
    current = now.timetz().replace(tzinfo=None)
    if start_time == end_time:
        return True
    if start_time < end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


def evaluate(config: dict, state: dict, now: datetime) -> dict:
    window = config.get("autonomous_window", {})
    if not window.get("enabled", False):
        return {"state": "disabled", "dispatch": False}
    local = now.astimezone(ZoneInfo(window["timezone"]))
    if not in_window(local, window["start"], window["end"]):
        return {"state": "waiting_for_window", "dispatch": False}
    if state.get("active_worker"):
        return {"state": "running", "dispatch": False}

    completed = {int(value) for value in state.get("completed", [])}
    explicit_blocked = {int(value) for value in state.get("blocked", [])}
    human_blocked: list[int] = []
    dependency_blocked: list[int] = []

    for item in config.get("work_plan", []):
        issue = int(item["issue"])
        if not item.get("enabled", True) or issue in completed or issue in explicit_blocked:
            continue
        dependencies = {int(value) for value in item.get("after", [])}
        if not dependencies.issubset(completed):
            dependency_blocked.append(issue)
            continue

        lifecycle = state.get("issues", {}).get(str(issue), {})
        labels = {str(value).lower() for value in lifecycle.get("labels", [])}
        halt = lifecycle.get("halt_kind")
        if labels & HUMAN or lifecycle.get("manual_action_required") or (halt and halt not in RECOVERABLE):
            human_blocked.append(issue)
            continue

        quota = state.get("quota", {})
        floors = config.get("quota", {})
        if quota.get("status") != "available":
            return {"state": "waiting_for_quota", "dispatch": False, "issue": issue, "reason": "authoritative quota unavailable", "blocked": human_blocked}
        primary_floor = max(floors.get("min_primary_to_start_turn", 0), floors.get("reserve_primary", 0))
        if quota.get("primary_remaining", 0) < primary_floor or quota.get("weekly_remaining", 0) < floors.get("min_weekly_to_start_turn", 0):
            return {"state": "waiting_for_quota", "dispatch": False, "issue": issue, "blocked": human_blocked}
        return {"state": "eligible", "dispatch": True, "issue": issue, "action": item.get("action", "implement"), "blocked": human_blocked}

    if human_blocked:
        return {"state": "human_gate", "dispatch": False, "blocked": human_blocked}
    return {"state": "complete_or_blocked", "dispatch": False, "dependencyBlocked": dependency_blocked}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now().astimezone()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    print(json.dumps(evaluate(config, state, now), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
