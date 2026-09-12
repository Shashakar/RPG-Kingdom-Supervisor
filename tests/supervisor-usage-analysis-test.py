#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import supervisor_usage_analysis as analysis  # noqa: E402


def worker(run_id, issue, role, model, effort, outcome, tokens=None, primary_delta=None, risk=None, duration=60):
    labels = [f"risk:{risk}"] if risk else []
    usage = {"status":"unavailable"}
    if tokens is not None:
        usage = {"status":"available","totalTokens":tokens,"inputTokens":tokens-10,"outputTokens":10,"cachedInputTokens":0,"reasoningTokens":0}
    quota = {"status":"unavailable"}
    if primary_delta is not None:
        quota = {"status":"available","primary":{"remainingPercentagePointDelta":primary_delta},"secondary":None}
    return {
        "runId": run_id,
        "issue": issue,
        "identifier": f"GH-{issue}",
        "role": role,
        "model": model,
        "effort": effort,
        "outcome": outcome,
        "durationSeconds": duration,
        "startedAt": f"2026-09-12T0{issue % 9}:00:00+00:00",
        "endedAt": f"2026-09-12T0{issue % 9}:01:00+00:00",
        "tokenUsage": usage,
        "quotaDelta": quota,
        "lifecycleLabels": labels,
    }

workers = [
    worker("a", 101, "implementation", "gpt-5.6-luna", "medium", "agent-review", 1000, -2, "normal", 80),
    worker("b", 102, "implementation", "gpt-5.6-terra", "medium", "halted", 4000, -8, "investigative", 240),
    worker("c", 101, "repair", "gpt-5.6-luna", "low", "human-review", 500, -1, "normal", 40),
    worker("d", 103, "review", "gpt-5.6-terra", "medium", "approved", None, None, None, 50),
]

result = analysis.analyze(workers)
assert result["workerCount"] == 4
assert result["coverage"]["tokenTelemetry"] == {"available": 3, "total": 4}
assert result["coverage"]["primaryQuotaDelta"] == {"available": 3, "total": 4}
assert result["coverage"]["riskClass"] == {"available": 3, "total": 4}

roles = {row["key"]: row for row in result["byRole"]}
assert roles["implementation"]["workers"] == 2
assert roles["implementation"]["medianTokens"] == 2500
assert roles["implementation"]["medianPrimaryQuotaCostPoints"] == 5
assert roles["repair"]["totalTokens"] == 500

outcomes = {row["key"]: row for row in result["byOutcome"]}
assert outcomes["successful_handoff"]["workers"] == 3
assert outcomes["halted"]["workers"] == 1

risks = {row["key"]: row for row in result["byRisk"]}
assert risks["normal"]["workers"] == 2
assert risks["investigative"]["workers"] == 1
assert risks["unavailable"]["workers"] == 1

assert result["expensiveWorkers"][0]["runId"] == "b"
assert result["expensiveWorkers"][0]["primaryQuotaCostPoints"] == 8

issues = {row["issue"]: row for row in result["issueRollups"]}
assert issues[101]["lifetimes"] == 2
assert issues[101]["continuations"] == 1
assert issues[101]["totalTokens"] == 1500
assert issues[101]["primaryQuotaCostPoints"] == 3
assert result["timingComparison"]["status"] == "unavailable"
assert "wall-clock" in result["timingComparison"]["reason"]

print("supervisor-usage-analysis-test: PASS")
