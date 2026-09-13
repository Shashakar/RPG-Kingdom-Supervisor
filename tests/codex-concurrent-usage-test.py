#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import supervisor_usage_analysis as analysis  # noqa: E402


def worker(run_id: str, issue: int, role: str, start: str, end: str) -> dict:
    return {
        "runId": run_id,
        "issue": issue,
        "identifier": f"GH-{issue}",
        "role": role,
        "model": "gpt-5.6-luna" if role != "review" else "gpt-5.6-terra",
        "effort": "medium",
        "outcome": "agent-review" if role != "review" else "approved",
        "durationSeconds": 600,
        "startedAt": start,
        "endedAt": end,
        "tokenUsage": {
            "status": "available",
            "totalTokens": 1000,
            "inputTokens": 900,
            "cachedInputTokens": 700,
            "outputTokens": 100,
            "reasoningTokens": 20,
        },
        "quotaDelta": {
            "status": "available",
            "primary": {"remainingPercentagePointDelta": -5},
            "secondary": None,
        },
        "lifecycleLabels": ["risk:normal"],
    }


implementation = worker(
    "impl-overlap", 201, "implementation",
    "2026-09-12T12:00:00+00:00", "2026-09-12T12:10:00+00:00",
)
review = worker(
    "review-overlap", 202, "review",
    "2026-09-12T12:02:00+00:00", "2026-09-12T12:08:00+00:00",
)

result = analysis.analyze([implementation, review], events=[])
assert result["coverage"]["tokenTelemetry"] == {"available": 2, "total": 2}
assert result["coverage"]["primaryQuotaDelta"] == {"available": 0, "total": 2}
assert result["coverage"]["quotaAttributionBlockedByOverlap"] == {"workers": 2, "total": 2}
assert result["overall"]["overlappingWorkers"] == 2
for row in result["expensiveWorkers"]:
    assert row["primaryQuotaCostPoints"] is None
    assert row["quotaAttribution"] == "unavailable-overlap"
    assert len(row["overlappingRunIds"]) == 1

# Non-overlapping workers keep authoritative before/after quota attribution.
review["startedAt"] = "2026-09-12T12:11:00+00:00"
review["endedAt"] = "2026-09-12T12:16:00+00:00"
exclusive = analysis.analyze([implementation, review], events=[])
assert exclusive["coverage"]["primaryQuotaDelta"] == {"available": 2, "total": 2}
assert exclusive["coverage"]["quotaAttributionBlockedByOverlap"] == {"workers": 0, "total": 2}

print("codex-concurrent-usage-test: PASS")
