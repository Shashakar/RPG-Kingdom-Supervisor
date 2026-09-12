#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import quota_state  # noqa: E402

UI = ROOT / "scripts" / "supervisor_quota_ui.js"
DASHBOARD = ROOT / "scripts" / "supervisor_dashboard.py"

now = datetime.now(timezone.utc)

no_sample = quota_state.summarize({"status": "unavailable", "reason": "no authoritative Codex rate-limit snapshot has been recorded"})
assert no_sample["freshness"] == "not-sampled"

fresh = quota_state.summarize({
    "status": "available",
    "observedAt": now.isoformat(),
    "staleAfterSeconds": 600,
    "source": "codex-app-server:account/rateLimits/read",
    "rateLimits": {
        "primary": {"remainingPercent": 73, "resetsAtIso": (now + timedelta(hours=2)).isoformat()},
        "secondary": {"remainingPercent": 91},
    },
})
assert fresh["freshness"] == "fresh"
assert fresh["rateLimits"]["primary"]["remainingPercent"] == 73

stale = quota_state.summarize({
    "status": "available",
    "observedAt": (now - timedelta(minutes=12)).isoformat(),
    "staleAfterSeconds": 600,
    "rateLimits": {"primary": {"remainingPercent": 73}, "secondary": {"remainingPercent": 91}},
})
assert stale["freshness"] == "stale"
assert stale["ageSeconds"] >= 11 * 60

unavailable = quota_state.summarize({
    "status": "unavailable",
    "observedAt": now.isoformat(),
    "staleAfterSeconds": 600,
    "reason": "timed out waiting for Codex App Server",
    "lastSuccessful": {
        "status": "available",
        "observedAt": (now - timedelta(minutes=7)).isoformat(),
        "rateLimits": {"primary": {"remainingPercent": 44}, "secondary": {"remainingPercent": 88}},
    },
})
assert unavailable["freshness"] == "unavailable"
assert unavailable["lastSuccessful"]["rateLimits"]["primary"]["remainingPercent"] == 44
assert unavailable["lastSuccessful"]["ageSeconds"] >= 6 * 60

ui = UI.read_text(encoding="utf-8")
for phrase in ("not sampled", "unavailable", "stale", "5h reset", "last good"):
    assert phrase in ui, phrase
assert "token" not in ui.lower(), "quota presentation must not infer quota from token telemetry"

server = DASHBOARD.read_text(encoding="utf-8")
assert 'QUOTA_UI_PATH = SCRIPT_DIR / "supervisor_quota_ui.js"' in server
assert 'parsed.path == "/supervisor-quota-ui.js"' in server
assert 'payload["quota"] = quota_state.summarize(payload.get("quota"))' in server
assert 'ThreadingHTTPServer(("127.0.0.1", port)' in server
assert "do_POST" not in server

print("quota-presentation-policy-test: PASS")
