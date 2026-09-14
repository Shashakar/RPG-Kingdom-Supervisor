#!/usr/bin/env python3
"""Progress-aware runtime wrapper for the Supervisor continuation policy.

The original policy engine is preserved in ``continuation-policy-base.py``. This wrapper keeps its
public API and safety ceilings, while allowing implementation workers to follow newly exposed
in-scope defects when they are still making concrete progress.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

BASE_PATH = Path(__file__).with_name("continuation-policy-base.py")
_spec = importlib.util.spec_from_file_location("_rpgk_continuation_policy_base", BASE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"unable to load continuation policy base: {BASE_PATH}")
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

# Preserve the existing module API for callers/tests/telemetry consumers.
for _name, _value in vars(_base).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

_base_budget_decision = _base.budget_decision
_base_progress_decision = _base.progress_decision
_base_evaluate = _base.evaluate
_ACTIVE_ROUTE = "luna"
_ACTIVE_LABELS: set[str] = set()
_ACTIVE_WORKSPACE: Path | None = None


def _follow_through_enabled() -> bool:
    value = os.environ.get("RPGK_CONTINUATION_FOLLOW_THROUGH", "1").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    # Report-only work has different semantics and is intentionally excluded. Otherwise the
    # behavior is the default for implementation work; authority/scope boundaries remain enforced
    # by the worker/tooling layers rather than by starving an in-scope debugging chain of turns.
    return REPORT_ONLY_LABEL not in _ACTIVE_LABELS


def _observable_progress(
    current: dict[str, Any],
    unity: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> bool:
    if previous is None:
        return bool(current.get("dirty") or unity)
    previous_workspace = previous.get("workspace") or {}
    previous_unity = previous.get("unity") or {}
    workspace_changed = current.get("fingerprint") != previous_workspace.get("fingerprint")
    unity_changed = bool(unity and unity.get("runId") != previous_unity.get("runId"))
    return workspace_changed or unity_changed


def _runtime_budget_decision(
    quota: dict[str, Any],
    worker: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    usage: dict[str, Any],
    usage_delta: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    allowed, reason, details = _base_budget_decision(quota, worker, previous, usage, usage_delta)
    details = dict(details)
    details["productiveOverride"] = False
    if allowed or not _follow_through_enabled() or _ACTIVE_WORKSPACE is None:
        return allowed, reason, details

    current = workspace_snapshot(_ACTIVE_WORKSPACE)
    unity = latest_unity(_ACTIVE_WORKSPACE)
    if not _observable_progress(current, unity, previous):
        return False, reason, details

    # Lifetime budgets remain hard. A productive turn may exceed the ordinary per-turn budget,
    # but only within a second bounded ceiling. This handles expensive debugging turns that
    # produce a new source diff or Unity result without turning progress into an unlimited spend.
    max_productive_primary = float_env("RPGK_CONTINUATION_PRODUCTIVE_MAX_TURN_PRIMARY_SPEND_PERCENT", 25.0)
    max_productive_weekly = float_env("RPGK_CONTINUATION_PRODUCTIVE_MAX_TURN_WEEKLY_SPEND_PERCENT", 6.0)
    max_productive_fresh = float(int_env("RPGK_CONTINUATION_PRODUCTIVE_MAX_TURN_FRESH_TOKENS", 1_250_000))

    lifetime_primary = details.get("lifetimePrimarySpendPercent")
    lifetime_weekly = details.get("lifetimeWeeklySpendPercent")
    turn_primary = details.get("turnPrimarySpendPercent")
    turn_weekly = details.get("turnWeeklySpendPercent")
    turn_fresh = details.get("turnFreshTokens")
    lifetime_fresh = details.get("lifetimeFreshTokens")

    if lifetime_primary is not None and lifetime_primary > details["maxLifetimePrimaryPercent"]:
        return False, reason, details
    if lifetime_weekly is not None and lifetime_weekly > details["maxLifetimeWeeklyPercent"]:
        return False, reason, details
    if details.get("quotaCostAvailable"):
        if turn_primary is not None and turn_primary > max_productive_primary:
            return False, reason, details
        if turn_weekly is not None and turn_weekly > max_productive_weekly:
            return False, reason, details
    else:
        if lifetime_fresh is not None and lifetime_fresh > details["maxLifetimeFreshTokens"]:
            return False, reason, details
        if turn_fresh is not None and turn_fresh > max_productive_fresh:
            return False, reason, details

    details.update(
        {
            "productiveOverride": True,
            "productiveMaxTurnPrimaryPercent": max_productive_primary,
            "productiveMaxTurnWeeklyPercent": max_productive_weekly,
            "productiveMaxTurnFreshTokens": max_productive_fresh,
        }
    )
    return (
        True,
        "productive turn exceeded the standard per-turn budget but remains within the bounded progress-aware budget",
        details,
    )


def _runtime_progress_decision(
    current: dict[str, Any],
    unity: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    *,
    report_only: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    allowed, reason, details = _base_progress_decision(
        current,
        unity,
        previous,
        report_only=report_only,
    )
    details = dict(details)
    details.setdefault("followThroughMode", _follow_through_enabled())
    details.setdefault("repeatedFocusedFailureTurns", 0)
    if allowed or report_only or not _follow_through_enabled():
        return allowed, reason, details

    previous_progress = (previous or {}).get("progress") or {}

    # Newly exposed failures often require analysis before the next source mutation. Give
    # implementation work two consecutive host-invisible turns by default, then stop normally.
    if "consecutive host-invisible turn" in reason:
        limit = int_env("RPGK_CONTINUATION_FOLLOW_THROUGH_INVISIBLE_TURNS", 2)
        invisible = int(details.get("consecutiveInvisibleTurns") or 0)
        if invisible <= max(1, limit):
            details["analysisGraceUsed"] = True
            details["followThroughAnalysisLimit"] = max(1, limit)
            return (
                True,
                f"follow-through analysis window {invisible}/{max(1, limit)} after prior implementation progress",
                details,
            )

    # A new run of the same focused test can reveal that the failure moved deeper even when the
    # coarse summary still reports one failed test. Permit one such rerun before requiring new
    # source/validation movement.
    if "same focused Unity validation failed again" in reason:
        limit = int_env("RPGK_CONTINUATION_FOLLOW_THROUGH_REPEATED_FOCUSED_FAILURES", 1)
        count = int(previous_progress.get("repeatedFocusedFailureTurns") or 0) + 1
        details["repeatedFocusedFailureTurns"] = count
        details["repeatedFocusedFailureLimit"] = max(0, limit)
        if count <= max(0, limit):
            return (
                True,
                f"new focused Unity evidence remains red; granting bounded downstream-defect follow-through {count}/{limit}",
                details,
            )

    return False, reason, details


def evaluate(workspace: Path, issue: str, turn: int, max_turns: int, labels: list[str]) -> int:
    global _ACTIVE_ROUTE, _ACTIVE_LABELS, _ACTIVE_WORKSPACE
    _ACTIVE_ROUTE = route_class(labels)
    _ACTIVE_LABELS = set(labels)
    _ACTIVE_WORKSPACE = workspace

    # Tests and callers historically monkeypatch dependencies on this public module. Mirror those
    # hooks into the preserved base module before delegating.
    dependency_names = (
        "refresh_quota",
        "workspace_snapshot",
        "latest_unity",
        "usage_snapshot",
        "active_worker",
        "ensure_local_excludes",
    )
    saved_dependencies = {name: getattr(_base, name) for name in dependency_names}
    saved_budget = _base.budget_decision
    saved_progress = _base.progress_decision
    try:
        for name in dependency_names:
            setattr(_base, name, globals()[name])
        _base.budget_decision = _runtime_budget_decision
        _base.progress_decision = _runtime_progress_decision
        return _base_evaluate(workspace, issue, turn, max_turns, labels)
    finally:
        for name, value in saved_dependencies.items():
            setattr(_base, name, value)
        _base.budget_decision = saved_budget
        _base.progress_decision = saved_progress
        _ACTIVE_WORKSPACE = None
        _ACTIVE_LABELS = set()
        _ACTIVE_ROUTE = "luna"


# Make the preserved CLI use the enhanced runtime evaluate function.
_base.evaluate = evaluate


if __name__ == "__main__":
    try:
        raise SystemExit(_base.main())
    except Exception as exc:
        print(f"continuation policy error: {exc}", file=sys.stderr)
        raise SystemExit(70)
