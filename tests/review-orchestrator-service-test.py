#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from urllib import error

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "review_orchestrator_service", ROOT / "scripts/review-orchestrator-service.py"
)
assert SPEC and SPEC.loader
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


def configure_status(temp: Path) -> None:
    service.STATE_ROOT = temp
    service.STATUS_DIR = temp / "review-orchestrator"
    service.STATUS_PATH = service.STATUS_DIR / "status.json"
    service.POLL_SECONDS = 0.01
    service.BACKOFF_INITIAL_SECONDS = 0.01
    service.BACKOFF_MAX_SECONDS = 0.02


def status() -> dict:
    return json.loads(service.STATUS_PATH.read_text(encoding="utf-8"))


def main() -> int:
    # Transient DNS/network failure does not terminate the service; the next poll succeeds.
    with tempfile.TemporaryDirectory() as raw:
        configure_status(Path(raw))
        calls = 0
        sleeps: list[float] = []

        def poll() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise error.URLError(OSError(-3, "Temporary failure in name resolution"))

        original_once = service.review.once
        service.review.once = poll
        try:
            assert service.run_loop(max_cycles=1, sleep_fn=sleeps.append) == 0
        finally:
            service.review.once = original_once

        result = status()
        assert calls == 2
        assert result["state"] == "ready"
        assert result["lastSuccessfulPoll"]
        assert result["lastError"] is None
        assert sleeps and sleeps[0] == 0.01

    # 5xx/rate-limit shaped failures are retryable.
    for code in (408, 429, 500, 502, 503, 504):
        assert service.classify_failure(RuntimeError(f"GitHub GET /issues failed: {code} fixture")) == "transient"

    # Authentication/configuration failures are permanent and must not be treated like transient DNS.
    for code in (401, 403):
        assert service.classify_failure(RuntimeError(f"GitHub GET /issues failed: {code} fixture")) == "permanent"
    assert service.classify_failure(RuntimeError("SYMPHONY_GITHUB_TOKEN is required")) == "permanent"

    # Unknown programming failures are visible as degraded/retryable rather than killing the sidecar.
    assert service.classify_failure(ValueError("fixture programming failure")) == "unexpected"
    with tempfile.TemporaryDirectory() as raw:
        configure_status(Path(raw))
        calls = 0

        def flaky() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("fixture programming failure")

        original_once = service.review.once
        service.review.once = flaky
        try:
            assert service.run_loop(max_cycles=1, sleep_fn=lambda _: None) == 0
        finally:
            service.review.once = original_once
        assert calls == 2
        assert status()["state"] == "ready"

    # Status writes are durable JSON and contain no credential/token payload.
    with tempfile.TemporaryDirectory() as raw:
        configure_status(Path(raw))
        service.write_status("degraded", lastError="fixture", lastErrorKind="transient")
        text = service.STATUS_PATH.read_text(encoding="utf-8")
        parsed = json.loads(text)
        assert parsed["protocolVersion"] == 1
        assert parsed["state"] == "degraded"
        assert "Authorization" not in text
        assert "SYMPHONY_GITHUB_TOKEN" not in text

    print("review-orchestrator-service-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
