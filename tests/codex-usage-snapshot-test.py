#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "codex-usage-snapshot.py"

FAKE_SERVER = r'''#!/usr/bin/env python3
import json, sys
for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except Exception:
        continue
    request_id = message.get("id")
    method = message.get("method")
    if request_id == 1 and method == "initialize":
        print(json.dumps({"id": 1, "result": {"serverInfo": {"name": "fixture"}}}), flush=True)
    elif request_id == 2 and method == "account/rateLimits/read":
        print(json.dumps({
            "id": 2,
            "result": {
                "accountId": "acct-fixture",
                "ordinaryUsageAllowed": True,
                "rateLimits": {
                    "limitId": "codex",
                    "planType": "plus",
                    "rateLimitReachedType": None,
                    "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1800000000},
                    "secondary": {"usedPercent": 18, "windowDurationMins": 10080, "resetsAt": 1800500000},
                    "credits": {"hasCredits": True, "unlimited": False, "balance": "12.50"}
                },
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitId": "codex",
                        "planType": "plus",
                        "rateLimitReachedType": None,
                        "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1800000000},
                        "secondary": {"usedPercent": 18, "windowDurationMins": 10080, "resetsAt": 1800500000},
                        "credits": {"hasCredits": True, "unlimited": False, "balance": "12.50"}
                    }
                }
            }
        }), flush=True)
'''


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-codex-usage-test-") as temp:
        root = Path(temp)
        fake = root / "fake-app-server.py"
        fake.write_text(FAKE_SERVER, encoding="utf-8")
        state = root / "state"
        env = os.environ.copy()
        env["RPGK_SUPERVISOR_STATE_ROOT"] = str(state)
        env["RPGK_CODEX_APP_SERVER_COMMAND"] = f"{sys.executable} {fake}"
        env["RPGK_QUOTA_STALE_AFTER_SECONDS"] = "600"
        env["SYMPHONY_GITHUB_TOKEN"] = "must-not-leak"

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--write", "--strict"],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"snapshot failed: stdout={result.stdout!r} stderr={result.stderr!r}")

        payload = json.loads((state / "usage" / "current.json").read_text(encoding="utf-8"))
        assert payload["status"] == "available"
        assert payload["source"] == "codex-app-server:account/rateLimits/read"
        assert payload["accountId"] == "acct-fixture"
        assert payload["staleAfterSeconds"] == 600
        assert payload["rateLimits"]["primary"]["usedPercent"] == 25
        assert payload["rateLimits"]["primary"]["remainingPercent"] == 75
        assert payload["rateLimits"]["secondary"]["remainingPercent"] == 82
        assert payload["rateLimits"]["primary"]["windowDurationMins"] == 300
        assert payload["rateLimits"]["secondary"]["windowDurationMins"] == 10080
        assert payload["rateLimits"]["primary"]["resetsAtIso"]
        assert payload["credits"]["balance"] == "12.50"
        assert "must-not-leak" not in json.dumps(payload)

        last_success_path = state / "usage" / "last_success.json"
        last_success = json.loads(last_success_path.read_text(encoding="utf-8"))
        assert last_success["status"] == "available"
        assert last_success["rateLimits"]["primary"]["remainingPercent"] == 75

        # A later probe failure remains the latest state for continuation safety, but it carries
        # the last known good authoritative sample for operator display instead of replacing it.
        env["RPGK_CODEX_APP_SERVER_COMMAND"] = f"{sys.executable} -c 'raise SystemExit(7)'"
        failed = subprocess.run(
            [sys.executable, str(SCRIPT), "--write", "--timeout-seconds", "1"],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert failed.returncode == 0
        latest = json.loads((state / "usage" / "current.json").read_text(encoding="utf-8"))
        assert latest["status"] == "unavailable"
        assert latest["reason"]
        assert latest["lastSuccessful"]["status"] == "available"
        assert latest["lastSuccessful"]["rateLimits"]["primary"]["remainingPercent"] == 75
        preserved = json.loads(last_success_path.read_text(encoding="utf-8"))
        assert preserved == last_success

        history = (state / "usage" / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(history) == 2
        event_text = (state / "telemetry" / "events.jsonl").read_text(encoding="utf-8")
        assert "quota_snapshot" in event_text
        assert "must-not-leak" not in event_text

    print("codex-usage-snapshot-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
