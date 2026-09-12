#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "scripts" / "supervisor_dashboard.py"
TEMPLATE = ROOT / "scripts" / "supervisor_dashboard.html"

python_text = DASHBOARD.read_text(encoding="utf-8")
assert TEMPLATE.is_file(), "dashboard template is missing"
html = TEMPLATE.read_text(encoding="utf-8")
combined = python_text + "\n" + html

# API-backed observability from #33/#46 remains reachable.
for route, collector in (
    ('parsed.path == "/api/operations"', "supervisor_telemetry.collect_operations()"),
    ('parsed.path == "/api/maintenance"', "supervisor_maintenance.status()"),
    ('parsed.path == "/api/lifecycle"', "supervisor_activity.collect()"),
    ('parsed.path == "/api/usage-analysis"', "supervisor_usage_analysis.analyze("),
):
    assert route in python_text
    assert collector in python_text
assert 'r"/api/worker/([^/]+)"' in python_text
assert "supervisor_detail.collect(run_id)" in python_text
assert 'r"/api/issue/(\\d+)"' in python_text
assert 'parsed.path == "/api/unity/runs"' in python_text
assert 'r"/api/unity/run/([^/]+)"' in python_text

# Split template is an explicit deployment dependency and must be referenced directly.
assert 'PAGE_PATH = SCRIPT_DIR / "supervisor_dashboard.html"' in python_text
assert 'PAGE_PATH.read_text(encoding="utf-8")' in python_text

# Quota UX distinguishes fresh, stale, unavailable, and not-yet-sampled state without inferring
# quota from token telemetry. Worker detail keeps global current quota explicitly separate from
# historical quotaBefore/quotaAfter fields stored on the worker.
for text in (
    "Quota <strong>stale · last",
    "Quota <strong>not sampled yet</strong>",
    "Quota <strong>unavailable</strong>",
    "latest refresh failed:",
    "5h reset",
    "currentGlobalQuota",
    "supervisor_telemetry.current_quota()",
):
    assert text in python_text, text
assert "tokenUsage" not in python_text.split("QUOTA_FRESHNESS_SCRIPT", 1)[1].split('"""', 1)[0]

# Initial overview answers operator questions before deeper diagnostics.
for text in (
    "System overview",
    "Overall health",
    "Active workers",
    "Human action",
    "5h quota remaining",
    "Active work",
    "No active workers. Supervisor is idle.",
    "No human action required",
):
    assert text in html, text
for service in ("symphony", "review", "unity", "git"):
    assert service in html

# Work/activity keeps all lifecycle queues and issue drill-down is a direct path.
for queue in (
    "Implementing",
    "Agent Review",
    "Rework",
    "Human Review",
    "Human Attention",
    "Halted / Quota",
    "Report Complete",
):
    assert queue in html
assert "Recent activity" in html
assert "Recent Codex worker lifetimes" in html
assert "function openIssue(identifier)" in html
assert "onclick=\"openIssue(" in html

# Usage and Unity remain available without dominating the initial overview.
assert "Comparative Codex usage" in html
assert "Grouped usage tables" in html
assert "Expensive workers and continuation cost" in html
assert "<details" in html
for grouping in ("By role", "By model", "By effort", "By risk", "Successful handoff vs halted/rework"):
    assert grouping in html
assert "Highest-cost recent lifetimes" in html
assert "Issue continuation cost" in html
assert "Unity run history" in html
assert "Issue Unity history" in html
assert "openRun(" in html

# Readability/accessibility presentation contract.
assert 'aria-label="Dashboard sections"' in html
assert ':focus-visible' in html
assert "font-family:Inter,ui-sans-serif,system-ui" in html
assert ".mono,code,pre,.metric-value,.pill,.timeline-time" in html
assert "Operator attention" in html
assert "Pruning:" in html

# The dashboard is a read-only renderer. Lifecycle/process mutation belongs to host
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
    "supervisor_maintenance.maintain(",
):
    assert forbidden not in combined, forbidden

assert 'ThreadingHTTPServer(("127.0.0.1", port)' in python_text
print("operations-dashboard-policy-test: PASS")
