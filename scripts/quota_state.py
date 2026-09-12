#!/usr/bin/env python3
"""Derived freshness semantics for authoritative Codex quota snapshots.

The latest persisted snapshot remains authoritative for gating decisions. This module only adds
age/freshness metadata for operator presentation and diagnostics; it never converts token counts
into quota and never substitutes a historical successful sample for a failed latest probe.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import supervisor_telemetry as telemetry

DEFAULT_STALE_AFTER_SECONDS = 600


def age_seconds(value: Any) -> float | None:
    parsed = telemetry.parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def summarize(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    source = snapshot or {}
    result = dict(source)
    age = age_seconds(source.get("observedAt"))
    result["ageSeconds"] = age
    try:
        threshold = max(60, int(source.get("staleAfterSeconds") or DEFAULT_STALE_AFTER_SECONDS))
    except (TypeError, ValueError):
        threshold = DEFAULT_STALE_AFTER_SECONDS
    result["staleAfterSeconds"] = threshold

    if source.get("status") == "available" and source.get("observedAt"):
        result["freshness"] = "stale" if age is not None and age > threshold else "fresh"
    elif source.get("observedAt"):
        result["freshness"] = "unavailable"
    else:
        result["freshness"] = "not-sampled"

    previous = source.get("lastSuccessful")
    if isinstance(previous, dict):
        previous_copy = dict(previous)
        previous_copy["ageSeconds"] = age_seconds(previous.get("observedAt"))
        result["lastSuccessful"] = previous_copy

    return telemetry.sanitize(result)


def current() -> dict[str, Any]:
    return summarize(telemetry.current_quota())
