#!/usr/bin/env python3
"""Host-owned, model-free scheduler for approved autonomous work plans."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("autonomous_plan", HERE / "autonomous-plan.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load autonomous plan policy")
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

DEFAULT_REPO = os.environ.get("RPGK_REPO", "Shashakar/RPG-Kingdom")
STATE_ROOT = Path(os.path.expanduser(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", "~/.local/state/rpg-kingdom-supervisor")))
CONFIG = STATE_ROOT / "autonomous-plan.json"
STATUS = STATE_ROOT / "autonomous-status.json"


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def read(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def gh(arguments: list[str]) -> Any:
    proc = subprocess.run(["gh", *arguments], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    output = proc.stdout.strip()
    return json.loads(output) if output.startswith(("{", "[")) else output


def issue_state(repo: str, number: int) -> dict:
    value = gh(["issue", "view", str(number), "--repo", repo, "--json", "number,state,labels,comments"])
    labels = [item["name"] for item in value.get("labels", [])]
    comments = value.get("comments", [])
    body = str(comments[-1].get("body", "")).lower() if comments else ""
    halt = None
    if "usage_limit_exceeded" in body or "usage quota" in body:
        halt = "usage_limit_exceeded"
    elif "continuation-budget-stop" in body or "continuation policy" in body:
        halt = "continuation_policy"
    elif "resource" in body and "unavailable" in body:
        halt = "resource_unavailable"
    elif "preflight" in body and ("recoverable" in body or "cleared" in body):
        halt = "preflight_recoverable"
    elif any(label.lower() == "symphony:halted" for label in labels):
        halt = "unknown_halt"
    return {
        "labels": labels,
        "closed": value.get("state") == "CLOSED",
        "halt_kind": halt,
        "manual_action_required": "manual_action_required" in body or "manual action required: yes" in body,
    }


def quota_state() -> dict:
    try:
        subprocess.run([sys.executable, str(HERE / "codex-usage-snapshot.py"), "--write", "--quiet"], timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
    quota = read(STATE_ROOT / "usage" / "current.json", {})
    rate = quota.get("rateLimits", {})
    return {
        "primary_remaining": (rate.get("primary") or {}).get("remainingPercent", 0),
        "weekly_remaining": (rate.get("secondary") or {}).get("remainingPercent", 0),
        "status": quota.get("status", "unavailable"),
        "observedAt": quota.get("observedAt"),
    }


def active_issue() -> int | None:
    directory = STATE_ROOT / "workers" / "active"
    if not directory.exists():
        return None
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        issue = value.get("issue")
        if isinstance(issue, int):
            return issue
    return None


def snapshot(config: dict, repo: str) -> dict:
    issues: dict[str, dict] = {}
    completed: list[int] = []
    for item in config.get("work_plan", []):
        number = int(item["issue"])
        try:
            state = issue_state(repo, number)
        except Exception as exc:
            state = {"labels": [], "halt_kind": "state_unavailable", "error": str(exc)}
        issues[str(number)] = state
        if state.get("closed"):
            completed.append(number)
    current = active_issue()
    return {
        "active_worker": current is not None,
        "active_issue": current,
        "completed": completed,
        "issues": issues,
        "quota": quota_state(),
    }


def mutate(repo: str, decision: dict, state: dict) -> str:
    number = int(decision["issue"])
    labels = {value.lower() for value in state["issues"][str(number)].get("labels", [])}
    if labels & plan.HUMAN:
        return "human gate"
    if "symphony:ready" in labels:
        return "already ready"
    if "symphony:halted" in labels:
        subprocess.run([str(HERE / "rearm-issue.sh"), str(number)], check=True, timeout=60)
        return "rearmed"
    gh(["issue", "edit", str(number), "--repo", repo, "--add-label", "symphony:ready"])
    return "armed"


def stop_boundary_reached(config: dict, state: dict) -> bool:
    target = config.get("stop_after_issue")
    if target is None:
        return False
    target = int(target)
    if target in state.get("completed", []):
        return True
    lifecycle = state.get("issues", {}).get(str(target), {})
    labels = {str(value).lower() for value in lifecycle.get("labels", [])}
    halt = lifecycle.get("halt_kind")
    return bool(labels & plan.HUMAN) or bool(lifecycle.get("manual_action_required")) or bool(halt and halt not in plan.RECOVERABLE)


def tick(config: dict, repo: str) -> dict:
    now = datetime.now(timezone.utc)
    previous = read(STATUS, {})
    state = snapshot(config, repo)
    decision = plan.evaluate(config, state, now)
    if config.get("paused") or stop_boundary_reached(config, state):
        decision = {"state": "paused", "dispatch": False, "reason": "operator pause/stop boundary", "stopAfterIssue": config.get("stop_after_issue")}

    result = {
        "protocolVersion": 1,
        "observedAt": now.isoformat(),
        "decision": decision,
        "state": state,
        "repo": repo,
        "window": config.get("autonomous_window", {}),
        "workPlan": config.get("work_plan", []),
        "paused": bool(config.get("paused")),
        "stopAfterIssue": config.get("stop_after_issue"),
    }

    prior_state = (previous.get("decision") or {}).get("state")
    if decision.get("state") == "human_gate" or (decision.get("state") == "waiting_for_window" and prior_state not in {"waiting_for_window", "disabled", "not_configured"}):
        result["summary"] = {
            "generatedAt": now.isoformat(),
            "reason": decision.get("state"),
            "completedIssues": state.get("completed", []),
            "blockedIssues": decision.get("blocked", []),
            "dependencyBlocked": decision.get("dependencyBlocked", []),
            "quota": state.get("quota", {}),
            "nextAction": "human review required" if decision.get("state") == "human_gate" else "window closed; no new dispatches",
        }

    if decision.get("dispatch"):
        try:
            result["actionResult"] = mutate(repo, decision, state)
        except Exception as exc:
            result["decision"] = {"state": "human_gate", "dispatch": False, "issue": decision.get("issue")}
            result["error"] = str(exc)

    atomic_write(STATUS, result)
    return result


def update_control(action: str, issue: int | None = None) -> dict:
    config = read(CONFIG, {})
    window = config.setdefault("autonomous_window", {})
    if action == "enable":
        window["enabled"] = True
    elif action == "disable":
        window["enabled"] = False
    elif action == "pause":
        config["paused"] = True
    elif action == "resume":
        config["paused"] = False
        config.pop("stop_after_issue", None)
    elif action == "stop-after-issue":
        target = active_issue() or (read(STATUS, {}).get("decision") or {}).get("issue")
        if target is None:
            raise ValueError("no current issue to stop after")
        config["stop_after_issue"] = int(target)
    elif action in {"plan-enable", "plan-disable", "move-up", "move-down"}:
        items = config.setdefault("work_plan", [])
        index = next((idx for idx, item in enumerate(items) if int(item.get("issue", -1)) == issue), None)
        if index is None:
            raise ValueError("issue is not in work plan")
        if action == "plan-enable":
            items[index]["enabled"] = True
        elif action == "plan-disable":
            items[index]["enabled"] = False
        elif action == "move-up" and index > 0:
            items[index - 1], items[index] = items[index], items[index - 1]
        elif action == "move-down" and index < len(items) - 1:
            items[index + 1], items[index] = items[index], items[index + 1]
    else:
        raise ValueError(action)
    atomic_write(CONFIG, config)
    return config


def default_config() -> dict:
    return {
        "autonomous_window": {"enabled": False, "timezone": "America/Denver", "start": "22:00", "end": "06:00"},
        "quota": {"min_primary_to_start_turn": 15, "min_weekly_to_start_turn": 5, "reserve_primary": 10},
        "work_plan": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="cmd", required=True)
    run = commands.add_parser("run")
    run.add_argument("--interval", type=int, default=60)
    run.add_argument("--repo", default=DEFAULT_REPO)
    once = commands.add_parser("tick")
    once.add_argument("--repo", default=DEFAULT_REPO)
    configure = commands.add_parser("configure")
    configure.add_argument("--from-file", required=True)
    control = commands.add_parser("control")
    control.add_argument("action", choices=["enable", "disable", "pause", "resume", "stop-after-issue", "plan-enable", "plan-disable", "move-up", "move-down"])
    control.add_argument("--issue", type=int)
    commands.add_parser("status")
    args = parser.parse_args()

    if args.cmd == "configure":
        value = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        atomic_write(CONFIG, value)
        print(json.dumps(value, indent=2))
        return 0
    if args.cmd == "control":
        print(json.dumps(update_control(args.action, args.issue), indent=2))
        return 0
    if args.cmd == "status":
        print(json.dumps(read(STATUS, {"decision": {"state": "not_started", "dispatch": False}}), indent=2))
        return 0

    if not CONFIG.exists():
        atomic_write(CONFIG, default_config())
    if args.cmd == "tick":
        print(json.dumps(tick(read(CONFIG, {}), args.repo), indent=2))
        return 0

    while True:
        try:
            tick(read(CONFIG, {}), args.repo)
        except Exception as exc:
            atomic_write(STATUS, {
                "protocolVersion": 1,
                "observedAt": datetime.now(timezone.utc).isoformat(),
                "decision": {"state": "human_gate", "dispatch": False},
                "error": str(exc),
            })
        time.sleep(max(15, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
