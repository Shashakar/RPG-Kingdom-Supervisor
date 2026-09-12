#!/usr/bin/env python3
"""Capture authoritative Codex account rate-limit state through App Server.

This does not start a model turn. It opens a short-lived local App Server connection, calls
`account/rateLimits/read`, normalizes only fields returned by Codex, then exits. Missing fields
remain missing/unavailable; quota is never estimated from token counts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import time
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402


def write_message(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("Codex App Server stdin is unavailable")
    proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def read_response(proc: subprocess.Popen[str], request_id: int, timeout_seconds: float) -> dict[str, Any]:
    if proc.stdout is None:
        raise RuntimeError("Codex App Server stdout is unavailable")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        readable, _, _ = select.select([proc.stdout], [], [], min(1.0, max(0.0, deadline - time.monotonic())))
        if not readable:
            if proc.poll() is not None:
                break
            continue
        line = proc.stdout.readline()
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            raise RuntimeError(f"App Server request {request_id} failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"App Server response {request_id} has no object result")
        return result
    stderr = ""
    if proc.poll() is not None and proc.stderr is not None:
        stderr = proc.stderr.read().strip()
    raise RuntimeError("timed out waiting for Codex App Server" + (f": {stderr[:500]}" if stderr else ""))


def iso_epoch(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def normalize_window(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    used = value.get("usedPercent")
    result: dict[str, Any] = {}
    if isinstance(used, (int, float)):
        result["usedPercent"] = used
        result["remainingPercent"] = 100 - used
    duration = value.get("windowDurationMins")
    if isinstance(duration, (int, float)):
        result["windowDurationMins"] = duration
    reset = value.get("resetsAt")
    if isinstance(reset, (int, float)):
        result["resetsAt"] = reset
        result["resetsAtIso"] = iso_epoch(reset)
    return result or None


def select_rate_limit_bucket(result: dict[str, Any]) -> dict[str, Any] | None:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        codex = by_id.get("codex")
        if isinstance(codex, dict):
            return codex
        if len(by_id) == 1:
            only = next(iter(by_id.values()))
            if isinstance(only, dict):
                return only
    direct = result.get("rateLimits")
    return direct if isinstance(direct, dict) else None


def normalize_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    bucket = select_rate_limit_bucket(result)
    observed = telemetry.iso_now()
    if bucket is None:
        return {
            "protocolVersion": telemetry.PROTOCOL_VERSION,
            "status": "unavailable",
            "observedAt": observed,
            "reason": "Codex returned no rate-limit bucket",
        }

    normalized: dict[str, Any] = {
        "protocolVersion": telemetry.PROTOCOL_VERSION,
        "status": "available",
        "observedAt": observed,
        "source": "codex-app-server:account/rateLimits/read",
        "accountId": result.get("accountId"),
        "ordinaryUsageAllowed": result.get("ordinaryUsageAllowed"),
        "rateLimits": {
            "limitId": bucket.get("limitId"),
            "planType": bucket.get("planType"),
            "rateLimitReachedType": bucket.get("rateLimitReachedType"),
            "primary": normalize_window(bucket.get("primary")),
            "secondary": normalize_window(bucket.get("secondary")),
        },
    }
    credits = bucket.get("credits")
    if isinstance(credits, dict):
        normalized["credits"] = {
            "hasCredits": credits.get("hasCredits"),
            "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance"),
        }
    reset_credits = result.get("rateLimitResetCredits")
    if isinstance(reset_credits, dict):
        normalized["rateLimitResetCredits"] = {
            key: reset_credits.get(key)
            for key in ("availableCount", "nextExpiringAt")
            if key in reset_credits
        }
    return telemetry.sanitize(normalized)


def app_server_snapshot(timeout_seconds: float) -> dict[str, Any]:
    command_text = os.environ.get("RPGK_CODEX_APP_SERVER_COMMAND", "codex app-server --stdio")
    command = shlex.split(command_text)
    if not command:
        raise RuntimeError("RPGK_CODEX_APP_SERVER_COMMAND is empty")
    env = os.environ.copy()
    for name in ("SYMPHONY_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY"):
        env.pop(name, None)
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    try:
        write_message(
            proc,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "capabilities": {"experimentalApi": True},
                    "clientInfo": {
                        "name": "rpgk-supervisor-usage-snapshot",
                        "title": "RPG Kingdom Supervisor Usage Snapshot",
                        "version": "1.0.0",
                    },
                },
            },
        )
        read_response(proc, 1, timeout_seconds)
        write_message(proc, {"method": "initialized", "params": {}})
        write_message(proc, {"method": "account/rateLimits/read", "id": 2, "params": {}})
        result = read_response(proc, 2, timeout_seconds)
        return normalize_snapshot(result)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def persist(snapshot: dict[str, Any]) -> None:
    root = telemetry.state_root() / "usage"
    telemetry.atomic_json(root / "current.json", snapshot)
    telemetry.append_jsonl(root / "snapshots.jsonl", snapshot)
    telemetry.append_event(
        "quota_snapshot",
        status=snapshot.get("status"),
        rateLimits=snapshot.get("rateLimits"),
        ordinaryUsageAllowed=snapshot.get("ordinaryUsageAllowed"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Codex rate-limit state without starting a model turn")
    parser.add_argument("--write", action="store_true", help="persist under Supervisor state")
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("RPGK_USAGE_SNAPSHOT_TIMEOUT_SECONDS", "15")))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--strict", action="store_true", help="return nonzero when the snapshot is unavailable")
    args = parser.parse_args()

    try:
        snapshot = app_server_snapshot(max(1.0, args.timeout_seconds))
    except Exception as exc:
        snapshot = {
            "protocolVersion": telemetry.PROTOCOL_VERSION,
            "status": "unavailable",
            "observedAt": telemetry.iso_now(),
            "reason": str(exc),
        }
    snapshot = telemetry.sanitize(snapshot)
    if args.write:
        persist(snapshot)
    if not args.quiet:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    if args.strict and snapshot.get("status") != "available":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
