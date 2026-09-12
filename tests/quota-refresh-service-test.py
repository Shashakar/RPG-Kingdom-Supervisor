#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "scripts" / "quota-refresh-service.py"

FAKE_SERVER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
counter_path = pathlib.Path(os.environ["RPGK_TEST_QUOTA_COUNTER"])
try:
    count = int(counter_path.read_text()) + 1
except Exception:
    count = 1
counter_path.write_text(str(count))
used = count * 10
for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except Exception:
        continue
    method = message.get("method")
    if method in {"thread/start", "turn/start"}:
        pathlib.Path(os.environ["RPGK_TEST_MODEL_MARKER"]).write_text(method)
        raise SystemExit(91)
    if method == "initialize" and message.get("id") == 1:
        print(json.dumps({"id": 1, "result": {"serverInfo": {"name": "quota-fixture"}}}), flush=True)
    elif method == "account/rateLimits/read" and message.get("id") == 2:
        print(json.dumps({
            "id": 2,
            "result": {
                "accountId": "acct-fixture",
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 1800000000},
                    "secondary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 1800500000}
                }
            }
        }), flush=True)
'''


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-quota-refresh-") as temp:
        root = Path(temp)
        state = root / "state"
        fake = root / "fake-app-server.py"
        fake.write_text(FAKE_SERVER, encoding="utf-8")
        counter = root / "counter"
        marker = root / "model-called"
        env = os.environ.copy()
        env["RPGK_SUPERVISOR_STATE_ROOT"] = str(state)
        env["RPGK_CODEX_APP_SERVER_COMMAND"] = f"{sys.executable} {fake}"
        env["RPGK_TEST_QUOTA_COUNTER"] = str(counter)
        env["RPGK_TEST_MODEL_MARKER"] = str(marker)
        env["RPGK_QUOTA_STALE_AFTER_SECONDS"] = "600"
        env["SYMPHONY_GITHUB_TOKEN"] = "host-secret-must-not-leak"

        for expected_remaining in (90, 80):
            proc = subprocess.run(
                [sys.executable, str(SERVICE), "--once", "--timeout-seconds", "2"],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert proc.returncode == 0, (proc.stdout, proc.stderr)
            payload = json.loads((state / "usage" / "current.json").read_text(encoding="utf-8"))
            assert payload["status"] == "available"
            assert payload["rateLimits"]["primary"]["remainingPercent"] == expected_remaining

        status = json.loads((state / "usage" / "refresher-status.json").read_text(encoding="utf-8"))
        assert status["lastSnapshotStatus"] == "available"
        assert status["lastCompletedAt"]
        assert status["staleAfterSeconds"] == 600
        assert counter.read_text() == "2"
        assert not marker.exists(), "quota refresh unexpectedly started a model thread/turn"
        serialized = json.dumps(status) + (state / "usage" / "current.json").read_text(encoding="utf-8")
        assert "host-secret-must-not-leak" not in serialized

    print("quota-refresh-service-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
