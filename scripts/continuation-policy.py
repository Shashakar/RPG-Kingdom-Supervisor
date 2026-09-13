#!/usr/bin/env python3
"""Host-side, model-free continuation gate for Symphony worker turns.

The hard Symphony max-turn count remains a safety ceiling. Normal continuation is earned
turn-by-turn from authoritative quota, observed account spend, and host-observable progress.
Route/model selection influences minimum reserve thresholds, but does not impose a smaller
fixed automatic-turn cap.
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
REPORT_ONLY_LABEL = "completion:report-only"


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


def is_report_only(labels: list[str]) -> bool:
    return REPORT_ONLY_LABEL in set(labels)


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


def quota_thresholds(route: str) -> tuple[float, float]:
    expensive = route in {"terra", "sol", "astra"}
    primary_default = 35.0 if expensive else 20.0
    weekly_default = 10.0
    return (
        float_env("RPGK_CONTINUATION_MIN_PRIMARY_PERCENT", primary_default),
        float_env("RPGK_CONTINUATION_MIN_WEEKLY_PERCENT", weekly_default),
    )


def spend_thresholds() -> dict[str, float]:
    return {
        "maxTurnPrimaryPercent": float_env("RPGK_CONTINUATION_MAX_TURN_PRIMARY_SPEND_PERCENT", 15.0),
        "maxTurnWeeklyPercent": float_env("RPGK_CONTINUATION_MAX_TURN_WEEKLY_SPEND_PERCENT", 4.0),
        "maxLifetimePrimaryPercent": float_env("RPGK_CONTINUATION_MAX_LIFETIME_PRIMARY_SPEND_PERCENT", 30.0),
        "maxLifetimeWeeklyPercent": float_env("RPGK_CONTINUATION_MAX_LIFETIME_WEEKLY_SPEND_PERCENT", 8.0),
        "maxTurnFreshTokens": float(int_env("RPGK_CONTINUATION_MAX_TURN_FRESH_TOKENS", 750_000)),
        "maxLifetimeFreshTokens": float(int_env("RPGK_CONTINUATION_MAX_LIFETIME_FRESH_TOKENS", 2_000_000)),
    }


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


def usage_snapshot(workspace: Path, issue: str, worker: dict[str, Any] | None = None) -> dict[str, Any]:
    worker = worker or active_worker(workspace, issue)
    if not worker:
        return {"status": "unavailable", "reason": "active worker telemetry record not found"}
    return telemetry.find_rollout_usage(workspace, worker.get("startedAt"))


def token_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if current.get("status") != "available":
        return {"status": "unavailable", "reason": current.get("reason")}
    prior_usage = (previous or {}).get("usage") or {}
    keys = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningTokens", "totalTokens")
    if prior_usage.get("status") != "available":
        return {
            "status": "available",
            **{key: max(0, int(current.get(key) or 0)) for key in keys},
            "basis": "worker-start",
        }
    return {
        "status": "available",
        **{key: max(0, int(current.get(key) or 0) - int(prior_usage.get(key) or 0)) for key in keys},
        "basis": "previous-turn",
    }


def fresh_tokens(usage: dict[str, Any]) -> int | None:
    if usage.get("status") != "available":
        return None
    input_tokens = int(usage.get("inputTokens") or 0)
    cached_tokens = int(usage.get("cachedInputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    return max(0, input_tokens - cached_tokens) + max(0, output_tokens)


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


def _remaining_percent(quota: dict[str, Any] | None, key: str) -> float | None:
    if not quota or quota.get("status") != "available":
        return None
    value = (((quota.get("rateLimits") or {}).get(key) or {}).get("remainingPercent"))
    return float(value) if isinstance(value, (int, float)) else None


def _reset_value(quota: dict[str, Any] | None, key: str) -> str | None:
    if not quota or quota.get("status") != "available":
        return None
    value = (((quota.get("rateLimits") or {}).get(key) or {}).get("resetsAtIso"))
    return str(value) if value else None


def quota_spend(before: dict[str, Any] | None, after: dict[str, Any], key: str) -> float | None:
    before_remaining = _remaining_percent(before, key)
    after_remaining = _remaining_percent(after, key)
    if before_remaining is None or after_remaining is None:
        return None
    before_reset = _reset_value(before, key)
    after_reset = _reset_value(after, key)
    if before_reset and after_reset and before_reset != after_reset:
        return None
    return max(0.0, before_remaining - after_remaining)


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
        "weeklyReset": (rate.get("secondary") or {}).get("resetsAtIso"),
    }
    if float(primary) < min_primary:
        return False, f"primary quota {primary}% is below continuation threshold {min_primary}%", detail
    if float(weekly) < min_weekly:
        return False, f"weekly quota {weekly}% is below continuation threshold {min_weekly}%", detail
    return True, "quota healthy for automatic continuation", detail


def budget_decision(
    quota: dict[str, Any],
    worker: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    usage: dict[str, Any],
    usage_delta: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    thresholds = spend_thresholds()
    start_quota = (worker or {}).get("quotaBefore") if worker else None
    previous_quota = (previous or {}).get("quota") or start_quota
    turn_primary = quota_spend(previous_quota, quota, "primary")
    turn_weekly = quota_spend(previous_quota, quota, "secondary")
    lifetime_primary = quota_spend(start_quota, quota, "primary")
    lifetime_weekly = quota_spend(start_quota, quota, "secondary")
    turn_fresh = fresh_tokens(usage_delta)
    lifetime_fresh = fresh_tokens(usage)

    details: dict[str, Any] = {
        "mode": "dynamic",
        "accountQuotaIsGlobal": True,
        "turnPrimarySpendPercent": turn_primary,
        "turnWeeklySpendPercent": turn_weekly,
        "lifetimePrimarySpendPercent": lifetime_primary,
        "lifetimeWeeklySpendPercent": lifetime_weekly,
        "turnFreshTokens": turn_fresh,
        "lifetimeFreshTokens": lifetime_fresh,
        **thresholds,
    }

    failures: list[str] = []
    if turn_primary is not None and turn_primary > thresholds["maxTurnPrimaryPercent"]:
        failures.append(
            f"turn primary spend {turn_primary:g}pp exceeds {thresholds['maxTurnPrimaryPercent']:g}pp"
        )
    if turn_weekly is not None and turn_weekly > thresholds["maxTurnWeeklyPercent"]:
        failures.append(
            f"turn weekly spend {turn_weekly:g}pp exceeds {thresholds['maxTurnWeeklyPercent']:g}pp"
        )
    if lifetime_primary is not None and lifetime_primary > thresholds["maxLifetimePrimaryPercent"]:
        failures.append(
            f"lifetime primary spend {lifetime_primary:g}pp exceeds {thresholds['maxLifetimePrimaryPercent']:g}pp"
        )
    if lifetime_weekly is not None and lifetime_weekly > thresholds["maxLifetimeWeeklyPercent"]:
        failures.append(
            f"lifetime weekly spend {lifetime_weekly:g}pp exceeds {thresholds['maxLifetimeWeeklyPercent']:g}pp"
        )

    quota_cost_available = any(
        value is not None for value in (turn_primary, turn_weekly, lifetime_primary, lifetime_weekly)
    )
    details["quotaCostAvailable"] = quota_cost_available
    details["tokenFallbackUsed"] = not quota_cost_available
    if not quota_cost_available:
        if turn_fresh is not None and turn_fresh > thresholds["maxTurnFreshTokens"]:
            failures.append(
                f"turn fresh-token fallback {turn_fresh} exceeds {int(thresholds['maxTurnFreshTokens'])}"
            )
        if lifetime_fresh is not None and lifetime_fresh > thresholds["maxLifetimeFreshTokens"]:
            failures.append(
                f"lifetime fresh-token fallback {lifetime_fresh} exceeds {int(thresholds['maxLifetimeFreshTokens'])}"
            )

    if failures:
        return False, "dynamic continuation budget exceeded: " + ", ".join(failures), details
    if quota_cost_available:
        return True, "dynamic continuation spend remains within budget", details
    if turn_fresh is not None or lifetime_fresh is not None:
        return True, "quota spend delta unavailable; fresh-token fallback remains within budget", details
    return True, "spend deltas unavailable; current authoritative quota reserve remains the fail-safe budget", details


def progress_decision(
    current: dict[str, Any],
    unity: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    *,
    report_only: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    if report_only:
        details: dict[str, Any] = {"reportOnly": True, "workspaceDirty": bool(current.get("dirty"))}
        if current.get("dirty"):
            return (
                False,
                "report-only task changed the source/test workspace; report-only completion requires source-clean state",
                details,
            )

        if previous is None:
            details.update({"workspaceChanged": False, "unityChanged": bool(unity), "consecutiveInvisibleTurns": 0})
            if unity:
                return True, "report-only first turn produced Unity evidence and remains source-clean", details
            return (
                True,
                "report-only task remains source-clean; analysis and report synthesis may be host-invisible",
                details,
            )

        previous_workspace = previous.get("workspace") or {}
        previous_unity = previous.get("unity")
        workspace_changed = current.get("fingerprint") != previous_workspace.get("fingerprint")
        unity_changed = bool(unity and unity.get("runId") != (previous_unity or {}).get("runId"))
        details.update({"workspaceChanged": workspace_changed, "unityChanged": unity_changed, "consecutiveInvisibleTurns": 0})

        if workspace_changed:
            return (
                False,
                "report-only workspace HEAD/status changed; report-only completion requires a stable source-clean workspace",
                details,
            )
        if unity_changed:
            return True, "report-only task produced new Unity evidence", details
        return (
            True,
            "report-only task remains source-clean; artifact analysis and report synthesis may be host-invisible",
            details,
        )

    if previous is None:
        details = {
            "reportOnly": False,
            "workspaceChanged": bool(current.get("dirty")),
            "unityChanged": bool(unity),
            "analysisGraceUsed": False,
            "consecutiveInvisibleTurns": 0,
        }
        if current.get("dirty") or unity:
            return True, "first turn produced source or Unity evidence", details
        details["analysisGraceUsed"] = True
        details["consecutiveInvisibleTurns"] = 1
        return True, "first implementation turn may be analysis-only; granting one bounded analysis grace", details

    previous_workspace = previous.get("workspace") or {}
    previous_unity = previous.get("unity")
    workspace_changed = current.get("fingerprint") != previous_workspace.get("fingerprint")
    unity_changed = bool(unity and unity.get("runId") != (previous_unity or {}).get("runId"))
    previous_progress = previous.get("progress") or {}
    prior_invisible = int(previous_progress.get("consecutiveInvisibleTurns") or 0)
    details = {
        "reportOnly": False,
        "workspaceChanged": workspace_changed,
        "unityChanged": unity_changed,
        "analysisGraceUsed": False,
        "consecutiveInvisibleTurns": 0,
    }

    if unity_changed and not workspace_changed and previous_unity:
        same_filter = unity.get("testFilter") == previous_unity.get("testFilter")
        same_platform = unity.get("testPlatform") == previous_unity.get("testPlatform")
        focused = bool(unity.get("testFilter"))
        current_failed = int(unity.get("failed") or 0) > 0
        prior_failed = int(previous_unity.get("failed") or 0) > 0
        if focused and same_filter and same_platform and current_failed and prior_failed:
            return False, "same focused Unity validation failed again without a source diff change", details

    if not workspace_changed and not unity_changed:
        if prior_invisible < 1:
            details["analysisGraceUsed"] = True
            details["consecutiveInvisibleTurns"] = prior_invisible + 1
            return True, "no host-observable progress; granting one bounded analysis-only continuation", details
        details["consecutiveInvisibleTurns"] = prior_invisible + 1
        return False, "second consecutive host-invisible turn without source or Unity progress", details

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
    report_only = is_report_only(labels)
    quota = refresh_quota(workspace)
    workspace_state = workspace_snapshot(workspace)
    unity = latest_unity(workspace)
    worker = active_worker(workspace, issue)
    usage = usage_snapshot(workspace, issue, worker)
    usage_delta = token_delta(usage, previous)

    allowed = True
    reasons: list[str] = []
    hard_limit_reached = turn >= max_turns
    if hard_limit_reached:
        allowed = False
        reasons.append(f"hard turn ceiling reached ({max_turns} total turns)")
    else:
        reasons.append(f"hard turn ceiling not reached ({turn}/{max_turns})")

    quota_allowed, quota_reason, quota_details = quota_decision(quota, route)
    if not quota_allowed:
        allowed = False
    reasons.append(quota_reason)

    budget_allowed, budget_reason, budget_details = budget_decision(
        quota,
        worker,
        previous,
        usage,
        usage_delta,
    )
    if not budget_allowed:
        allowed = False
    reasons.append(budget_reason)

    progress_allowed, progress_reason, progress_details = progress_decision(
        workspace_state,
        unity,
        previous,
        report_only=report_only,
    )
    if not progress_allowed:
        allowed = False
    reasons.append(progress_reason)

    decision = "continue" if allowed else "stop"
    record = {
        "protocolVersion": 2,
        "observedAt": iso_now(),
        "issue": issue,
        "turn": turn,
        "hardMaxTurns": max_turns,
        "route": route,
        "completionMode": "report-only" if report_only else "implementation",
        "continuationBudgetMode": "dynamic",
        # Compatibility field retained for existing dashboard/detail consumers. It now reflects
        # the workflow hard ceiling rather than a smaller route-specific automatic cap.
        "automaticTurnLimit": max_turns,
        "decision": decision,
        "reason": "; ".join(reasons),
        "quota": quota,
        "quotaDecision": quota_details,
        "dynamicBudget": budget_details,
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
        completionMode=record["completionMode"],
        continuationBudgetMode="dynamic",
        decision=decision,
        reason=record["reason"],
        quota=quota_details,
        dynamicBudget=budget_details,
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
