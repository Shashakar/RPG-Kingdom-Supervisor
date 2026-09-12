#!/usr/bin/env python3
"""Host-side, model-free continuation gate for Symphony worker turns.

The hard Symphony max-turn count remains a safety ceiling. This policy decides whether a
normal completed turn should automatically spend another turn. It uses only host-observable
state: route labels, authoritative Codex rate limits, source-workspace progress, Unity run
progress, and cumulative rollout token telemetry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402

STATE_NAME = ".symphony-continuation-state.json"
STOP_NAME = ".symphony-continuation-stop.json"
RUNTIME_PREFIXES = (".symphony-", "Logs/SymphonyUnity/")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_labels(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item for item in value.splitlines() if item.strip()]
    if not isinstance(parsed, list):
        return []
    return [str(item).strip().lower() for item in parsed if str(item).strip()]


def route_class(labels: list[str]) -> str:
    label_set = set(labels)
    for model in ("astra", "sol", "terra", "luna"):
        if f"model:{model}" in label_set:
            return model
    if "risk:end-to-end" in label_set:
        return "astra"
    if "risk:architecture" in label_set:
        return "sol"
    if "risk:investigative" in label_set:
        return "terra"
    return "luna"


def int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def auto_turn_limit(route: str) -> int:
    defaults = {"luna": 4, "terra": 2, "sol": 2, "astra": 1}
    return max(1, int_env(f"RPGK_AUTO_TURN_LIMIT_{route.upper()}", defaults[route]))


def quota_thresholds(route: str) -> tuple[float, float]:
    expensive = route in {"terra", "sol", "astra"}
    primary_default = 35.0 if expensive else 20.0
    weekly_default = 10.0
    return (
        float_env("RPGK_CONTINUATION_MIN_PRIMARY_PERCENT", primary_default),
        float_env("RPGK_CONTINUATION_MIN_WEEKLY_PERCENT", weekly_default),
    )


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def ensure_local_excludes(workspace: Path) -> None:
    probe = run(["git", "rev-parse", "--git-path", "info/exclude"], workspace)
    if probe.returncode != 0 or not probe.stdout.strip():
        return
    path = Path(probe.stdout.strip())
    if not path.is_absolute():
        path = workspace / path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = set(existing.splitlines())
    additions = [f"/{STATE_NAME}", f"/{STOP_NAME}"]
    with path.open("a", encoding="utf-8") as handle:
        for addition in additions:
            if addition not in lines:
                handle.write(addition + "\n")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    telemetry.atomic_json(path, value)


def read_json(path: Path) -> dict[str, Any]:
    return telemetry.read_json(path)


def filtered_git_status(workspace: Path) -> str:
    result = run(["git", "status", "--porcelain", "--untracked-files=normal"], workspace)
    if result.returncode != 0:
        return ""
    lines: list[str] = []
    for raw in result.stdout.splitlines():
        path_text = raw[3:] if len(raw) > 3 else raw
        if any(path_text.startswith(prefix) for prefix in RUNTIME_PREFIXES):
            continue
        lines.append(raw)
    return "\n".join(lines)


def workspace_snapshot(workspace: Path) -> dict[str, Any]:
    head_result = run(["git", "rev-parse", "HEAD"], workspace)
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    status = filtered_git_status(workspace)
    payload = {"head": head, "gitStatus": status}
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload["dirty"] = bool(status.strip())
    return payload


def latest_unity(workspace: Path) -> dict[str, Any] | None:
    summaries = sorted(
        workspace.glob("Logs/SymphonyUnity/*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        return None
    value = read_json(summaries[0])
    if not value:
        return None
    return {
        "runId": value.get("runId"),
        "result": value.get("result"),
        "testPlatform": value.get("testPlatform"),
        "testFilter": value.get("testFilter"),
        "total": value.get("total"),
        "passed": value.get("passed"),
        "failed": value.get("failed"),
    }


def active_worker(workspace: Path, issue: str) -> dict[str, Any] | None:
    active_dir = telemetry.state_root() / "workers" / "active"
    for path in active_dir.glob("*.json"):
        value = read_json(path)
        if value.get("identifier") == issue and Path(str(value.get("workspace") or "")).resolve() == workspace:
            return value
    return None


def usage_snapshot(workspace: Path, issue: str) -> dict[str, Any]:
    worker = active_worker(workspace, issue)
    if not worker:
        return {"status": "unavailable", "reason": "active worker telemetry record not found"}
    return telemetry.find_rollout_usage(workspace, worker.get("startedAt"))


def token_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if current.get("status") != "available":
        return {"status": "unavailable", "reason": current.get("reason")}
    prior_usage = (previous or {}).get("usage") or {}
    if prior_usage.get("status") != "available":
        return {"status": "unavailable", "reason": "no prior cumulative token sample"}
    keys = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningTokens", "totalTokens")
    return {
        "status": "available",
        **{key: max(0, int(current.get(key) or 0) - int(prior_usage.get(key) or 0)) for key in keys},
    }


def refresh_quota(workspace: Path) -> dict[str, Any]:
    command = [sys.executable, str(SCRIPT_DIR / "codex-usage-snapshot.py"), "--write", "--quiet"]
    result = run(command, workspace)
    quota = telemetry.current_quota()
    if result.returncode != 0 and quota.get("status") != "available":
        return {
            "status": "unavailable",
            "reason": result.stderr.strip() or result.stdout.strip() or quota.get("reason") or "quota refresh failed",
        }
    return quota


def quota_age_seconds(quota: dict[str, Any]) -> float | None:
    observed = telemetry.parse_time(quota.get("observedAt"))
    if observed is None:
        return None
    return max(0.0, (utc_now() - observed).total_seconds())


def quota_decision(quota: dict[str, Any], route: str) -> tuple[bool, str, dict[str, Any]]:
    if quota.get("status") != "available":
        return False, f"quota unavailable: {quota.get('reason') or 'unknown reason'}", {}
    age = quota_age_seconds(quota)
    max_age = float_env("RPGK_CONTINUATION_QUOTA_MAX_AGE_SECONDS", 180.0)
    if age is None or age > max_age:
        return False, f"quota sample stale ({age if age is not None else 'unknown'}s)", {"ageSeconds": age}
    rate = quota.get("rateLimits") or {}
    primary = (rate.get("primary") or {}).get("remainingPercent")
    weekly = (rate.get("secondary") or {}).get("remainingPercent")
    min_primary, min_weekly = quota_thresholds(route)
    if not isinstance(primary, (int, float)) or not isinstance(weekly, (int, float)):
        return False, "quota sample is missing primary or weekly remaining percentage", {}
    detail = {
        "ageSeconds": age,
        "primaryRemainingPercent": primary,
        "weeklyRemainingPercent": weekly,
        "minimumPrimaryPercent": min_primary,
        "minimumWeeklyPercent": min_weekly,
        "primaryReset": (rate.get("primary") or {}).get("resetsAtIso"),
    }
    if float(primary) < min_primary:
        return False, f"primary quota {primary}% is below continuation threshold {min_primary}%", detail
    if float(weekly) < min_weekly:
        return False, f"weekly quota {weekly}% is below continuation threshold {min_weekly}%", detail
    return True, "quota healthy for automatic continuation", detail


def progress_decision(current: dict[str, Any], unity: dict[str, Any] | None, previous: dict[str, Any] | None) -> tuple[bool, str, dict[str, Any]]:
    if previous is None:
        if current.get("dirty") or unity:
            return True, "first turn produced source or Unity evidence", {}
        return False, "first turn produced no source diff and no Unity evidence", {}

    previous_workspace = previous.get("workspace") or {}
    previous_unity = previous.get("unity")
    workspace_changed = current.get("fingerprint") != previous_workspace.get("fingerprint")
    unity_changed = bool(unity and unity.get("runId") != (previous_unity or {}).get("runId"))
    details = {"workspaceChanged": workspace_changed, "unityChanged": unity_changed}

    if not workspace_changed and not unity_changed:
        return False, "no host-observable source or Unity progress since the prior turn", details

    if unity_changed and not workspace_changed and previous_unity:
        same_filter = unity.get("testFilter") == previous_unity.get("testFilter")
        current_failed = int(unity.get("failed") or 0) > 0
        prior_failed = int(previous_unity.get("failed") or 0) > 0
        if same_filter and current_failed and prior_failed:
            return False, "same focused Unity validation failed again without a source diff change", details

    return True, "host-observable progress detected", details


def state_paths(workspace: Path) -> tuple[Path, Path]:
    return workspace / STATE_NAME, workspace / STOP_NAME


def reset(workspace: Path) -> int:
    ensure_local_excludes(workspace)
    state_path, stop_path = state_paths(workspace)
    for path in (state_path, stop_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return 0


def evaluate(workspace: Path, issue: str, turn: int, max_turns: int, labels: list[str]) -> int:
    ensure_local_excludes(workspace)
    state_path, stop_path = state_paths(workspace)
    previous = read_json(state_path) or None
    route = route_class(labels)
    quota = refresh_quota(workspace)
    workspace_state = workspace_snapshot(workspace)
    unity = latest_unity(workspace)
    usage = usage_snapshot(workspace, issue)
    usage_delta = token_delta(usage, previous)

    allowed = True
    reasons: list[str] = []
    limit = min(max_turns, auto_turn_limit(route))
    if turn >= limit:
        allowed = False
        reasons.append(f"route {route} automatic turn limit reached ({limit} total turns)")

    quota_allowed, quota_reason, quota_details = quota_decision(quota, route)
    if not quota_allowed:
        allowed = False
    reasons.append(quota_reason)

    progress_allowed, progress_reason, progress_details = progress_decision(workspace_state, unity, previous)
    if not progress_allowed:
        allowed = False
    reasons.append(progress_reason)

    decision = "continue" if allowed else "stop"
    record = {
        "protocolVersion": 1,
        "observedAt": iso_now(),
        "issue": issue,
        "turn": turn,
        "hardMaxTurns": max_turns,
        "route": route,
        "automaticTurnLimit": limit,
        "decision": decision,
        "reason": "; ".join(reasons),
        "quota": quota,
        "quotaDecision": quota_details,
        "workspace": workspace_state,
        "unity": unity,
        "usage": usage,
        "turnUsageDelta": usage_delta,
        "progress": progress_details,
    }
    atomic_json(state_path, record)
    telemetry.append_event(
        "continuation_decision",
        issue=issue,
        turn=turn,
        hardMaxTurns=max_turns,
        route=route,
        decision=decision,
        reason=record["reason"],
        quota=quota_details,
        progress=progress_details,
        turnUsageDelta=usage_delta,
    )

    if allowed:
        try:
            stop_path.unlink()
        except FileNotFoundError:
            pass
        print(json.dumps(record, separators=(",", ":")))
        return 0

    atomic_json(stop_path, record)
    print(json.dumps(record, separators=(",", ":")))
    return 20


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate whether Symphony should automatically spend another Codex turn")
    sub = parser.add_subparsers(dest="command", required=True)
    reset_parser = sub.add_parser("reset")
    reset_parser.add_argument("--workspace", required=True)
    eval_parser = sub.add_parser("evaluate")
    eval_parser.add_argument("--workspace", required=True)
    eval_parser.add_argument("--issue", required=True)
    eval_parser.add_argument("--turn", required=True, type=int)
    eval_parser.add_argument("--max-turns", required=True, type=int)
    eval_parser.add_argument("--labels-json", default="[]")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"continuation policy: workspace does not exist: {workspace}", file=sys.stderr)
        return 70
    if args.command == "reset":
        return reset(workspace)
    return evaluate(workspace, args.issue, args.turn, args.max_turns, parse_labels(args.labels_json))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"continuation policy error: {exc}", file=sys.stderr)
        raise SystemExit(70)
