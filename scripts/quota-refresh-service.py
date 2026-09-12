#!/usr/bin/env python3
"""Periodically refresh authoritative Codex quota without starting model turns."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402

MIN_INTERVAL_SECONDS = 60
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_STALE_AFTER_SECONDS = 600


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stale_after_seconds() -> int:
    try:
        return max(60, int(os.environ.get("RPGK_QUOTA_STALE_AFTER_SECONDS", str(DEFAULT_STALE_AFTER_SECONDS))))
    except ValueError:
        return DEFAULT_STALE_AFTER_SECONDS


def status_path() -> Path:
    return telemetry.state_root() / "usage" / "refresher-status.json"


def write_status(**fields: Any) -> None:
    prior = telemetry.read_json(status_path())
    payload = {
        "protocolVersion": telemetry.PROTOCOL_VERSION,
        "service": "quota-refresh",
        "pid": os.getpid(),
        "startedAt": prior.get("startedAt") or iso_now(),
        **fields,
    }
    telemetry.atomic_json(status_path(), payload)


def snapshot_command(timeout_seconds: float) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT_DIR / "codex-usage-snapshot.py"),
        "--write",
        "--quiet",
        "--timeout-seconds",
        str(timeout_seconds),
    ]


def refresh(timeout_seconds: float) -> int:
    env = os.environ.copy()
    for name in ("SYMPHONY_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY"):
        env.pop(name, None)
    started = iso_now()
    try:
        proc = subprocess.run(
            snapshot_command(timeout_seconds),
            env=env,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=max(5.0, timeout_seconds + 5.0),
            check=False,
        )
        exit_code = proc.returncode
        stderr = proc.stderr.strip()
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stderr = f"quota snapshot subprocess timed out after {exc.timeout}s"
    latest = telemetry.current_quota()
    write_status(
        state="ready" if exit_code == 0 else "degraded",
        intervalSeconds=None,
        staleAfterSeconds=stale_after_seconds(),
        lastAttemptAt=started,
        lastCompletedAt=iso_now(),
        lastExitCode=exit_code,
        lastSnapshotStatus=latest.get("status"),
        lastError=(stderr[:1000] if stderr else latest.get("reason")),
    )
    return exit_code


def run_loop(interval_seconds: int, timeout_seconds: float, initial_refresh: bool) -> int:
    interval_seconds = max(MIN_INTERVAL_SECONDS, interval_seconds)
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    write_status(state="starting", intervalSeconds=interval_seconds, staleAfterSeconds=stale_after_seconds())

    if initial_refresh and not stopped:
        refresh(timeout_seconds)
        current = telemetry.read_json(status_path())
        write_status(**{**current, "state": current.get("state", "ready"), "intervalSeconds": interval_seconds})

    while not stopped:
        deadline = time.monotonic() + interval_seconds
        while not stopped and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        if stopped:
            break
        refresh(timeout_seconds)
        current = telemetry.read_json(status_path())
        write_status(**{**current, "state": current.get("state", "ready"), "intervalSeconds": interval_seconds})

    write_status(state="stopped", completedAt=iso_now(), intervalSeconds=interval_seconds, staleAfterSeconds=stale_after_seconds())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep authoritative Codex quota telemetry fresh while Supervisor is alive")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.environ.get("RPGK_QUOTA_REFRESH_SECONDS", str(DEFAULT_INTERVAL_SECONDS))),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("RPGK_USAGE_SNAPSHOT_TIMEOUT_SECONDS", "15")),
    )
    parser.add_argument("--no-initial-refresh", action="store_true")
    parser.add_argument("--once", action="store_true", help="refresh once and exit; intended for deterministic tests")
    args = parser.parse_args()
    if args.once:
        return refresh(max(1.0, args.timeout_seconds))
    return run_loop(args.interval_seconds, max(1.0, args.timeout_seconds), not args.no_initial_refresh)


if __name__ == "__main__":
    raise SystemExit(main())
