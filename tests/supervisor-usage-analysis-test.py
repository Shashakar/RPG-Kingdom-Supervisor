#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import supervisor_usage_analysis as analysis  # noqa: E402


def worker(run_id, issue, role, model, effort, outcome, tokens=None, primary_delta=None, risk=None, duration=60, capabilities=None):
    labels = [f"risk:{risk}"] if risk else []
    usage = {"status":"unavailable"}
    if tokens is not None:
        usage = {"status":"available","totalTokens":tokens,"inputTokens":tokens-10,"outputTokens":10,"cachedInputTokens":0,"reasoningTokens":0}
    quota = {"status":"unavailable"}
    if primary_delta is not None:
        quota = {"status":"available","primary":{"remainingPercentagePointDelta":primary_delta},"secondary":None}
    minute = {"implementation": 0, "repair": 2, "review": 4, "report-only": 6}.get(role, 0)
    return {
        "runId": run_id,
        "issue": issue,
        "identifier": f"GH-{issue}",
        "role": role,
        "model": model,
        "effort": effort,
        "outcome": outcome,
        "durationSeconds": duration,
        "startedAt": f"2026-09-12T0{issue % 9}:{minute:02d}:00+00:00",
        "endedAt": f"2026-09-12T0{issue % 9}:{minute + 1:02d}:00+00:00",
        "tokenUsage": usage,
        "quotaDelta": quota,
        "lifecycleLabels": labels,
        "capabilities": capabilities,
    }


deep_capabilities = {
    "selectedSkills": ["rpgk-investigate-bug"],
    "mcp": [
        {"name":"graphify","mcpServer":"rpgk_graphify","enabled":True},
        {"name":"context7","mcpServer":"rpgk_context7","enabled":True},
    ],
}
workers = [
    worker("a", 101, "implementation", "gpt-5.6-luna", "medium", "agent-review", 1000, -2, "normal", 80),
    worker("b", 102, "implementation", "gpt-5.6-terra", "medium", "halted", 4000, -8, "investigative", 240, deep_capabilities),
    worker("c", 101, "repair", "gpt-5.6-luna", "low", "human-review", 500, -1, "normal", 40),
    worker("d", 103, "review", "gpt-5.6-terra", "medium", "approved", None, None, None, 50),
]

events = [
    {
        "eventType":"worker_turn_completed", "workerRunId":"b", "turn":1,
        "mcpUsage": {
            "status":"available", "totalCalls":2,
            "byCapability": {
                "graphify":{"enabled":True,"used":True,"calls":2,"tools":["dependency_path","find_references"]},
                "context7":{"enabled":True,"used":False,"calls":0,"tools":[]},
            },
        },
    },
]

result = analysis.analyze(workers, events=events)
assert result["workerCount"] == 4
assert result["coverage"]["tokenTelemetry"] == {"available": 3, "total": 4}
assert result["coverage"]["primaryQuotaDelta"] == {"available": 3, "total": 4}
assert result["coverage"]["riskClass"] == {"available": 3, "total": 4}
assert result["coverage"]["actualMcpUse"] == {"available": 1, "total": 4}

roles = {row["key"]: row for row in result["byRole"]}
assert roles["implementation"]["workers"] == 2
assert roles["implementation"]["medianTokens"] == 2500
assert roles["implementation"]["medianPrimaryQuotaCostPoints"] == 5
assert roles["implementation"]["mcpCallSamples"] == 1
assert roles["repair"]["totalTokens"] == 500

outcomes = {row["key"]: row for row in result["byOutcome"]}
assert outcomes["successful_handoff"]["workers"] == 3
assert outcomes["halted"]["workers"] == 1

risks = {row["key"]: row for row in result["byRisk"]}
assert risks["normal"]["workers"] == 2
assert risks["investigative"]["workers"] == 1
assert risks["unavailable"]["workers"] == 1

skills = {row["key"]: row for row in result["bySkillBundle"]}
assert skills["rpgk-investigate-bug"]["workers"] == 1
assert skills["none"]["workers"] == 3

graphify = {row["key"]: row for row in result["byCapability"]["graphify"]}
assert graphify["selected_used"]["workers"] == 1
assert graphify["selected_used"]["medianMcpCalls"] == 2
assert graphify["not_selected"]["workers"] == 3
context7 = {row["key"]: row for row in result["byCapability"]["context7"]}
assert context7["selected_unused"]["workers"] == 1
assert context7["not_selected"]["workers"] == 3

assert result["expensiveWorkers"][0]["runId"] == "b"
assert result["expensiveWorkers"][0]["primaryQuotaCostPoints"] == 8
assert result["expensiveWorkers"][0]["mcpCalls"] == 2

issues = {row["issue"]: row for row in result["issueRollups"]}
assert issues[101]["lifetimes"] == 2
assert issues[101]["continuations"] == 1
assert issues[101]["totalTokens"] == 1500
assert issues[101]["primaryQuotaCostPoints"] == 3
assert issues[102]["mcpCalls"] == 2
assert result["timingComparison"]["status"] == "unavailable"
assert "wall-clock" in result["timingComparison"]["reason"]

print("supervisor-usage-analysis-test: PASS")
