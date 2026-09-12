#!/usr/bin/env python3
"""Render current global quota separately from historical worker quota evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import quota_state  # type: ignore  # noqa: E402


def render_text(snapshot: dict[str, Any]) -> str:
    data = quota_state.summarize(snapshot)
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
    value = quota_state.current()
    if args.json:
        print(json.dumps(value, sort_keys=True))
    else:
        print(render_text(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
