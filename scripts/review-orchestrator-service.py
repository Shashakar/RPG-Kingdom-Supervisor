#!/usr/bin/env python3
"""Durable host service wrapper for the review orchestrator poll loop."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error

SUPERVISOR_ROOT = Path(
    os.environ.get("RPGK_SUPERVISOR_ROOT", str(Path.home() / "src/RPG-Kingdom-Supervisor"))
).expanduser()
STATE_ROOT = Path(
    os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor"))
).expanduser()
STATUS_DIR = STATE_ROOT / "review-orchestrator"
STATUS_PATH = STATUS_DIR / "status.json"
POLL_SECONDS = float(os.environ.get("RPGK_REVIEW_POLL_SECONDS", "15"))
BACKOFF_INITIAL_SECONDS = float(os.environ.get("RPGK_REVIEW_BACKOFF_INITIAL_SECONDS", "5"))
BACKOFF_MAX_SECONDS = float(os.environ.get("RPGK_REVIEW_BACKOFF_MAX_SECONDS", "60"))
PERMANENT_BLOCK_SLEEP_SECONDS = float(os.environ.get("RPGK_REVIEW_PERMANENT_BLOCK_SLEEP_SECONDS", "3600"))

SPEC = importlib.util.spec_from_file_location(
    "review_orchestrator_core", SUPERVISOR_ROOT / "scripts/review-orchestrator.py"
)
if not SPEC or not SPEC.loader:
    raise RuntimeError("unable to load review-orchestrator.py")
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def read_status() -> dict[str, Any]:
    try:
        raw = STATUS_PATH.read_text(encoding="utf-8")
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_status(state: str, **fields: Any) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    payload = read_status()
    payload.update(
        {
            "protocolVersion": 1,
            "pid": os.getpid(),
            "state": state,
            "updatedAt": iso(),
        }
    )
    payload.update(fields)
    temp = STATUS_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(STATUS_PATH)


def classify_failure(exc: BaseException) -> str:
    if isinstance(exc, (error.URLError, TimeoutError, ConnectionError)):
        return "transient"
    if isinstance(exc, OSError):
        return "transient"

    text = str(exc)
    permanent_markers = (
        " failed: 401 ",
        " failed: 403 ",
        "SYMPHONY_GITHUB_TOKEN is required",
    )
    if any(marker in text for marker in permanent_markers):
        return "permanent"

    transient_markers = (
        " failed: 408 ",
        " failed: 429 ",
        " failed: 500 ",
        " failed: 502 ",
        " failed: 503 ",
        " failed: 504 ",
        "Temporary failure in name resolution",
        "timed out",
        "Connection reset",
        "Remote end closed connection",
    )
    if any(marker in text for marker in transient_markers):
        return "transient"
    return "unexpected"


def poll_once() -> None:
    attempted = iso()
    write_status("polling", lastPollAttempt=attempted, nextRetryAt=None)
    review.once()
    write_status(
        "ready",
        lastSuccessfulPoll=iso(),
        lastError=None,
        lastErrorKind=None,
        nextRetryAt=None,
    )


def run_loop(
    *,
    max_cycles: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    backoff = max(0.1, BACKOFF_INITIAL_SECONDS)
    cycles = 0
    write_status(
        "starting",
        startedAt=iso(),
        lastError=None,
        lastErrorKind=None,
        nextRetryAt=None,
    )
    print(f"RPG Kingdom review orchestrator service: polling every {POLL_SECONDS}s", flush=True)

    while True:
        try:
            poll_once()
            backoff = max(0.1, BACKOFF_INITIAL_SECONDS)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return 0
            sleep_fn(POLL_SECONDS)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

            kind = classify_failure(exc)
            message = f"{type(exc).__name__}: {exc}"
            if kind == "permanent":
                write_status(
                    "blocked",
                    lastError=message,
                    lastErrorKind=kind,
                    nextRetryAt=None,
                )
                print(
                    f"review-orchestrator-service: permanent GitHub/configuration failure; service blocked until restart: {message}",
                    file=sys.stderr,
                    flush=True,
                )
                # Stay alive so the parent Supervisor cannot mistake a dead child for a healthy idle
                # system. A corrected credential/configuration requires an explicit Supervisor restart.
                while True:
                    sleep_fn(PERMANENT_BLOCK_SLEEP_SECONDS)

            wait_seconds = min(max(0.1, backoff), max(0.1, BACKOFF_MAX_SECONDS))
            next_retry = utc_now() + timedelta(seconds=wait_seconds)
            write_status(
                "degraded",
                lastError=message,
                lastErrorKind=kind,
                nextRetryAt=iso(next_retry),
            )
            print(
                f"review-orchestrator-service: {kind} poll failure; retrying in {wait_seconds:.1f}s: {message}",
                file=sys.stderr,
                flush=True,
            )
            sleep_fn(wait_seconds)
            backoff = min(max(0.1, BACKOFF_MAX_SECONDS), wait_seconds * 2)


def main() -> int:
    if not getattr(review, "TOKEN", ""):
        write_status(
            "blocked",
            startedAt=iso(),
            lastError="SYMPHONY_GITHUB_TOKEN is required",
            lastErrorKind="permanent",
            nextRetryAt=None,
        )
        print("review-orchestrator-service: SYMPHONY_GITHUB_TOKEN is required", file=sys.stderr)
        return 64
    return run_loop()


if __name__ == "__main__":
    raise SystemExit(main())
