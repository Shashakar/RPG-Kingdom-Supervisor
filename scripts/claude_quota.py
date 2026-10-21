#!/usr/bin/env python3
"""Claude subscription-quota view and operator-confirmed /usage snapshots.

Claude Code's documented /usage display is interactive; unlike Codex App
Server it has no supported non-interactive account quota source in this
Supervisor. NEVER convert CLI cost/token totals into quota percentages.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any



def state_root() -> Path:
    return Path(os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")
    )).expanduser().resolve()


def snapshot_path() -> Path:
    return state_root() / "usage" / "claude" / "current.json"


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None
    except ValueError:
        return None


def _percent(value: object) -> float | None:
    if (value is None or isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or not 0 <= value <= 100):
        return None
    return float(value)


def _window(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {"remainingPercent", "resetsAtIso"}:
        return None
    remaining = _percent(value["remainingPercent"])
    reset = value["resetsAtIso"]
    if remaining is None or (reset is not None and _time(reset) is None):
        return None
    return {"remainingPercent": remaining, "resetsAtIso": reset}


def current_quota() -> dict[str, Any]:
    path = snapshot_path()
    try:
        if path.is_symlink() or path.stat().st_size > 10_000:
            raise ValueError("untrusted Claude quota file")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "provider": "claude", "status": "unavailable", "source": None,
            "reason": "Claude subscription quota is not available from a supported non-interactive CLI API. Optionally record values from Claude Code /usage.",
        }
    if (not isinstance(raw, dict) or raw.get("provider") != "claude"
            or raw.get("source") != "operator:claude-code-/usage"
            or raw.get("protocolVersion") != 1):
        return {"provider": "claude", "status": "unavailable",
                "reason": "Claude quota record has an unsupported origin/format"}
    rates = raw.get("rateLimits")
    if not isinstance(rates, dict):
        return {"provider": "claude", "status": "unavailable",
                "reason": "Claude quota record has invalid rate-limit windows"}
    observed = _time(raw.get("observedAt"))
    primary = _window(rates.get("primary"))
    secondary = _window(rates.get("secondary"))
    if not observed or not primary or not secondary:
        return {"provider": "claude", "status": "unavailable",
                "reason": "Claude quota record has invalid observed time or window percentages"}
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    if age < -120:
        return {"provider": "claude", "status": "unavailable",
                "reason": "Claude quota record is dated in the future"}
    try:
        threshold = int(os.environ.get("RPGK_CLAUDE_QUOTA_STALE_SECONDS", "600"))
    except ValueError:
        threshold = 600
    threshold = max(60, min(threshold, 86400))
    return {
        "provider": "claude", "status": "stale" if age > threshold else "available",
        "source": "operator:claude-code-/usage", "observedAt": raw["observedAt"],
        "lastSuccessfulObservedAt": raw["observedAt"], "staleAfterSeconds": threshold,
        "reason": "Manual Claude /usage observation; not an automated API snapshot",
        "rateLimits": {"primary": primary, "secondary": secondary},
    }


def recent_pilot_usage() -> dict[str, Any]:
    """One latest CLI result per issue; not a cumulative billing total."""
    root = state_root() / "claude-pilot"
    candidates: list[tuple[float, dict[str, Any]]] = []
    try:
        paths = sorted(root.glob("GH-*/latest.json"))[:250]
    except OSError:
        paths = []
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > 80_000:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("repository") != "Shashakar/Rekindle-Claude"
                    or data.get("issue") != path.parent.name or data.get("status") != "completed"):
                continue
            cost = data.get("reportedCostUsd")
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                cost = None
            candidates.append((path.stat().st_mtime, {
                "issue": data["issue"], "model": data.get("requestedModel"),
                "reportedCostUsd": cost, "status": "completed",
            }))
        except (OSError, ValueError, TypeError):
            continue
    if not candidates:
        return {"status": "unavailable", "reason": "No completed Claude CLI pilot reports in this Supervisor state"}
    newest = max(candidates, key=lambda row: row[0])
    return {"status": "available", "source": "claude-cli:result",
            "latest": newest[1], "observedAt": datetime.fromtimestamp(newest[0], timezone.utc).isoformat(),
            "note": "CLI-reported per-run cost; not subscription remaining quota"}


def record(five_hour: float, weekly: float, five_reset: str | None, week_reset: str | None) -> Path:
    for label, value in (("5-hour", five_hour), ("weekly", weekly)):
        if _percent(value) is None:
            raise ValueError(f"{label} remaining percentage must be 0..100")
    for label, date in (("5-hour reset", five_reset), ("weekly reset", week_reset)):
        if date and _time(date) is None:
            raise ValueError(f"{label} must be timezone-aware ISO 8601")
    path = snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("refusing symlink Claude quota destination")
    observation = {
        "protocolVersion": 1, "provider": "claude",
        "source": "operator:claude-code-/usage",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "rateLimits": {
            "primary": {"remainingPercent": float(five_hour), "resetsAtIso": five_reset},
            "secondary": {"remainingPercent": float(weekly), "resetsAtIso": week_reset},
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".claude-quota-", delete=False) as handle:
        tmp = Path(handle.name)
        json.dump(observation, handle, separators=(",", ":"))
        handle.write("\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    rec = sub.add_parser("record", help="Record exact remaining percentages observed in Claude Code /usage")
    rec.add_argument("--five-hour-remaining", type=float, required=True)
    rec.add_argument("--weekly-remaining", type=float, required=True)
    rec.add_argument("--five-hour-reset", help="Optional timezone-aware ISO 8601 time")
    rec.add_argument("--weekly-reset", help="Optional timezone-aware ISO 8601 time")
    args = parser.parse_args()
    if args.command == "show":
        print(json.dumps(current_quota(), indent=2))
        return 0
    try:
        dest = record(args.five_hour_remaining, args.weekly_remaining,
                      args.five_hour_reset, args.weekly_reset)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Recorded manual Claude /usage snapshot in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
