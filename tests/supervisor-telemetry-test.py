#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "supervisor_telemetry.py"
spec = importlib.util.spec_from_file_location("rpgk_supervisor_telemetry", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load supervisor_telemetry.py")
telemetry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(telemetry)


def quota(primary_remaining: int, secondary_remaining: int) -> dict:
    return {
        "protocolVersion": 1,
        "status": "available",
        "observedAt": telemetry.iso_now(),
        "accountId": "acct-test",
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 100 - primary_remaining, "remainingPercent": primary_remaining},
            "secondary": {"usedPercent": 100 - secondary_remaining, "remainingPercent": secondary_remaining},
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-supervisor-telemetry-") as temp:
        root = Path(temp)
        state = root / "state"
        codex = root / "codex"
        workspace = root / "GH-321"
        workspace.mkdir()
        os.environ["RPGK_SUPERVISOR_STATE_ROOT"] = str(state)
        os.environ["CODEX_HOME"] = str(codex)
        os.environ["SYMPHONY_GITHUB_TOKEN"] = "super-secret-fixture-token"

        telemetry.write_service_status("symphony", "running", os.getpid())
        telemetry.atomic_json(
            telemetry.service_status_path("review"),
            {
                "protocolVersion": 1,
                "state": "degraded",
                "pid": os.getpid(),
                "lastError": "temporary DNS failure",
                "updatedAt": telemetry.iso_now(),
            },
        )
        telemetry.atomic_json(
            telemetry.service_status_path("unity"),
            {
                "protocolVersion": 1,
                "state": "running",
                "pid": os.getpid(),
                "activeRequest": {"issue": "GH-321", "operation": "playmode"},
                "updatedAt": telemetry.iso_now(),
            },
        )
        telemetry.atomic_json(
            telemetry.service_status_path("git"),
            {"protocolVersion": 1, "state": "ready", "pid": 99999999, "updatedAt": telemetry.iso_now()},
        )

        assert telemetry.normalize_service("symphony")["health"] == "healthy"
        assert telemetry.normalize_service("review")["health"] == "degraded"
        assert telemetry.normalize_service("unity")["health"] == "busy"
        assert telemetry.normalize_service("git")["health"] == "stopped"

        usage_path = state / "usage" / "current.json"
        telemetry.atomic_json(usage_path, quota(80, 70))
        started = telemetry.worker_start("implementation", "gpt-5.6-luna", "medium", "luna", workspace)
        assert started["identifier"] == "GH-321"
        assert started["quotaBefore"]["rateLimits"]["primary"]["remainingPercent"] == 80

        rollout = codex / "sessions" / "2026" / "09" / "12" / "rollout-fixture.jsonl"
        rollout.parent.mkdir(parents=True)
        records = [
            {"payload": {"type": "session_meta", "id": "thread-fixture"}},
            {"payload": {"type": "turn_context", "cwd": str(workspace)}},
            {
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 900,
                            "cached_input_tokens": 700,
                            "output_tokens": 100,
                            "reasoning_output_tokens": 40,
                            "total_tokens": 1000,
                        }
                    },
                }
            },
        ]
        rollout.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")

        telemetry.atomic_json(usage_path, quota(74, 69))
        completed = telemetry.worker_end("implementation", workspace, outcome="agent-review")
        assert completed["tokenUsage"]["status"] == "available"
        assert completed["tokenUsage"]["totalTokens"] == 1000
        assert completed["tokenUsage"]["cachedInputTokens"] == 700
        assert completed["quotaDelta"]["primary"]["remainingPercentagePointDelta"] == -6
        assert completed["quotaDelta"]["secondary"]["remainingPercentagePointDelta"] == -1
        assert completed["outcome"] == "agent-review"
        assert not (state / "workers" / "active" / "implementation.json").exists()
        assert (state / "workers" / "history" / f"{completed['runId']}.json").is_file()

        sanitized = telemetry.sanitize(
            {
                "authorization": "Bearer abcdefghijklmnop",
                "inputTokens": 123,
                "note": "prefix super-secret-fixture-token suffix",
            }
        )
        assert sanitized["authorization"] == "[REDACTED]"
        assert sanitized["inputTokens"] == 123
        assert "super-secret-fixture-token" not in sanitized["note"]

        operations = telemetry.collect_operations()
        serialized = json.dumps(operations)
        assert "super-secret-fixture-token" not in serialized
        assert operations["services"]["unity"]["health"] == "busy"
        assert operations["recentWorkers"][0]["runId"] == completed["runId"]

    print("supervisor-telemetry-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
