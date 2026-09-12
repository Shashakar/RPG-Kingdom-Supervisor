#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "scripts" / "supervisor_dashboard.py"
text = DASHBOARD.read_text(encoding="utf-8")

assert 'parsed.path == "/api/operations"' in text
assert "supervisor_telemetry.collect_operations()" in text
assert 'parsed.path == "/api/lifecycle"' in text
assert "supervisor_activity.collect()" in text
assert "Work lifecycle queues" in text
assert "Recent activity" in text
assert "Recent Codex worker lifetimes" in text
assert "Codex quota" in text
assert "Active Codex slots" in text
for service in ("symphony", "review", "unity", "git"):
    assert service in text
for queue in ("Implementing", "Agent Review", "Rework", "Human Review", "Human Attention", "Halted / Quota"):
    assert queue in text

# The dashboard is a read-only renderer. Lifecycle/process mutation belongs to existing host
# adapters and must never be introduced as a dashboard convenience action.
for forbidden in (
    "git push",
    "git merge",
    "gh issue edit",
    "gh pr merge",
    "kill(",
    "os.kill",
    "subprocess.run",
    "subprocess.Popen",
):
    assert forbidden not in text, forbidden

assert 'ThreadingHTTPServer(("127.0.0.1", port)' in text
print("operations-dashboard-policy-test: PASS")
