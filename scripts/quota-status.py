#!/usr/bin/env python3
"""Render current global quota separately from historical worker quota evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402


def age_seconds(value: Any) -> float | None:
    parsed = telemetry.parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def summarize(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    age = age_seconds(snapshot.get("observedAt"))
    result["ageSeconds"] = age
    threshold = int(snapshot.get("staleAfterSeconds") or 600)
    if snapshot.get("status") == "available":
        result["freshness"] = "stale" if age is not None and age > threshold else "fresh"
    elif snapshot.get("observedAt"):
        result["freshness"] = "unavailable"
    else:
        result["freshness"] = "not-sampled"
    previous = snapshot.get("lastSuccessful")
    if isinstance(previous, dict):
        previous = dict(previous)
        previous["ageSeconds"] = age_seconds(previous.get("observedAt"))
        result["lastSuccessful"] = previous
    return telemetry.sanitize(result)


def render_text(snapshot: dict[str, Any]) -> str:
    data = summarize(snapshot)
    rate = data.get("rateLimits") or {}
    primary = rate.get("primary") or {}
    weekly = rate.get("secondary") or {}
    lines = ["", "Current global Codex quota"]
    lines.append(f"  Status:      {data.get('freshness')}")
    if data.get("reason"):
        lines.append(f"  Reason:      {data.get('reason')}")
    if data.get("observedAt"):
        lines.append(f"  Observed:    {data.get('observedAt')}")
        lines.append(f"  Age:         {round(float(data.get('ageSeconds') or 0))}s")
    if primary.get("remainingPercent") is not None:
        lines.append(f"  5h remaining:{primary.get('remainingPercent')}%")
    if weekly.get("remainingPercent") is not None:
        lines.append(f"  Weekly:      {weekly.get('remainingPercent')}%")
    if primary.get("resetsAtIso"):
        lines.append(f"  5h reset:    {primary.get('resetsAtIso')}")
    previous = data.get("lastSuccessful") or {}
    previous_rate = previous.get("rateLimits") or {}
    previous_primary = previous_rate.get("primary") or {}
    previous_weekly = previous_rate.get("secondary") or {}
    if previous:
        lines.append(
            "  Last good:   "
            f"{round(float(previous.get('ageSeconds') or 0))}s ago · "
            f"{previous_primary.get('remainingPercent', '-')}% / "
            f"{previous_weekly.get('remainingPercent', '-')}%"
        )
    lines.append("  Note:        current global quota is not worker historical quotaBefore/quotaAfter evidence")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show current authoritative Codex quota")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    value = summarize(telemetry.current_quota())
    if args.json:
        print(json.dumps(value, sort_keys=True))
    else:
        print(render_text(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
