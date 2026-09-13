#!/usr/bin/env python3
"""Comparative worker usage analysis for Supervisor issues #46D and #34."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import statistics
from typing import Any, Callable, Iterable

import supervisor_telemetry

PROTOCOL_VERSION = 1
DEFAULT_LIMIT = 500


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _token_value(worker: dict[str, Any], field: str = "totalTokens") -> int | None:
    usage = worker.get("tokenUsage") if isinstance(worker.get("tokenUsage"), dict) else {}
    value = usage.get(field)
    return int(value) if usage.get("status") == "available" and isinstance(value, (int, float)) else None


def _quota_cost(worker: dict[str, Any], window: str) -> float | None:
    delta = worker.get("quotaDelta") if isinstance(worker.get("quotaDelta"), dict) else {}
    if delta.get("status") != "available":
        return None
    item = delta.get(window) if isinstance(delta.get(window), dict) else {}
    value = _number(item.get("remainingPercentagePointDelta"))
    return -value if value is not None else None


def _risk(worker: dict[str, Any]) -> str:
    labels = worker.get("lifecycleLabels") if isinstance(worker.get("lifecycleLabels"), list) else []
    risks = [str(item).split(":", 1)[1] for item in labels if str(item).lower().startswith("risk:") and ":" in str(item)]
    return risks[0] if len(risks) == 1 else ("conflicting" if len(risks) > 1 else "unavailable")


def _outcome_class(worker: dict[str, Any]) -> str:
    value = str(worker.get("outcome") or "unknown").lower()
    if value in {"agent-review", "human-review", "report-complete", "completed-or-idle", "approved"}:
        return "successful_handoff"
    if value in {"halted", "stale-process", "usage_limit_exceeded"}:
        return "halted"
    if value in {"rework", "changes_requested"}:
        return "rework"
    if value in {"human-attention", "blocked_or_ambiguous"}:
        return "human_attention"
    return "other"


def _median(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if isinstance(value, (int, float))]
    return statistics.median(clean) if clean else None


def _sum(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(clean) if clean else None


def _mcp_call_count(worker: dict[str, Any]) -> int | None:
    summary = worker.get("_capabilityUsageSummary")
    if not isinstance(summary, dict) or not summary.get("telemetryAvailable"):
        return None
    return int(summary.get("totalCalls") or 0)


def _metric_group(name: str, workers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": name,
        "workers": len(workers),
        "issues": len({item.get("issue") for item in workers if item.get("issue") is not None}),
        "tokenSamples": sum(_token_value(item) is not None for item in workers),
        "totalTokens": _sum(_token_value(item) for item in workers),
        "medianTokens": _median(_token_value(item) for item in workers),
        "medianDurationSeconds": _median(item.get("durationSeconds") for item in workers),
        "primaryQuotaSamples": sum(_quota_cost(item, "primary") is not None for item in workers),
        "medianPrimaryQuotaCostPoints": _median(_quota_cost(item, "primary") for item in workers),
        "secondaryQuotaSamples": sum(_quota_cost(item, "secondary") is not None for item in workers),
        "medianSecondaryQuotaCostPoints": _median(_quota_cost(item, "secondary") for item in workers),
        "mcpCallSamples": sum(_mcp_call_count(item) is not None for item in workers),
        "medianMcpCalls": _median(_mcp_call_count(item) for item in workers),
    }


def _group(workers: list[dict[str, Any]], key: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for worker in workers:
        buckets[key(worker)].append(worker)
    return sorted((_metric_group(name, items) for name, items in buckets.items()), key=lambda item: (-item["workers"], item["key"]))


def _selected_mcp(worker: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for item in capabilities.get("mcp") or []:
        if isinstance(item, dict) and item.get("name"):
            result[str(item["name"])] = item
    return result


def _skill_bundle(worker: dict[str, Any]) -> str:
    capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    skills = capabilities.get("selectedSkills") if isinstance(capabilities.get("selectedSkills"), list) else []
    clean = sorted(str(item) for item in skills if str(item))
    return "+".join(clean) if clean else "none"


def _capability_usage(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_run: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("eventType") != "worker_turn_completed":
            continue
        run_id = event.get("workerRunId")
        if not run_id:
            continue
        summary = by_run.setdefault(str(run_id), {"telemetryAvailable": False, "totalCalls": 0, "byCapability": {}})
        mcp = event.get("mcpUsage") if isinstance(event.get("mcpUsage"), dict) else {}
        if mcp.get("status") != "available":
            continue
        summary["telemetryAvailable"] = True
        summary["totalCalls"] += int(mcp.get("totalCalls") or 0)
        by_capability = mcp.get("byCapability") if isinstance(mcp.get("byCapability"), dict) else {}
        for name, usage in by_capability.items():
            if not isinstance(usage, dict):
                continue
            item = summary["byCapability"].setdefault(str(name), {"calls": 0, "tools": set()})
            item["calls"] += int(usage.get("calls") or 0)
            item["tools"].update(str(tool) for tool in usage.get("tools") or [] if str(tool))
    for summary in by_run.values():
        for item in summary["byCapability"].values():
            item["used"] = item["calls"] > 0
            item["tools"] = sorted(item["tools"])
    return by_run


def _capability_bucket(worker: dict[str, Any], name: str) -> str:
    selected = _selected_mcp(worker).get(name)
    if not selected or not selected.get("enabled"):
        return "not_selected"
    summary = worker.get("_capabilityUsageSummary")
    if not isinstance(summary, dict) or not summary.get("telemetryAvailable"):
        return "selected_unknown"
    usage = summary.get("byCapability") if isinstance(summary.get("byCapability"), dict) else {}
    calls = int((usage.get(name) or {}).get("calls") or 0) if isinstance(usage.get(name), dict) else 0
    return "selected_used" if calls > 0 else "selected_unused"


def _capability_names(workers: list[dict[str, Any]]) -> list[str]:
    names = {"graphify", "context7"}
    for worker in workers:
        names.update(_selected_mcp(worker).keys())
    return sorted(names)


def _expensive(workers: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    rows = []
    for worker in workers:
        rows.append({
            "runId": worker.get("runId"),
            "identifier": worker.get("identifier"),
            "issue": worker.get("issue"),
            "role": worker.get("role"),
            "model": worker.get("model"),
            "effort": worker.get("effort"),
            "risk": _risk(worker),
            "skillBundle": _skill_bundle(worker),
            "outcome": worker.get("outcome"),
            "outcomeClass": _outcome_class(worker),
            "durationSeconds": worker.get("durationSeconds"),
            "totalTokens": _token_value(worker),
            "mcpCalls": _mcp_call_count(worker),
            "primaryQuotaCostPoints": _quota_cost(worker, "primary"),
            "secondaryQuotaCostPoints": _quota_cost(worker, "secondary"),
            "startedAt": worker.get("startedAt"),
        })
    rows.sort(key=lambda row: (row["primaryQuotaCostPoints"] if row["primaryQuotaCostPoints"] is not None else -1, row["totalTokens"] if row["totalTokens"] is not None else -1), reverse=True)
    return rows[:limit]


def _issue_rollups(workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for worker in workers:
        if isinstance(worker.get("issue"), int):
            buckets[int(worker["issue"])].append(worker)
    rows = []
    for issue, items in buckets.items():
        ordered = sorted(items, key=lambda item: parse_time(item.get("startedAt")) or datetime.min.replace(tzinfo=timezone.utc))
        rows.append({
            "issue": issue,
            "identifier": f"GH-{issue}",
            "lifetimes": len(items),
            "continuations": max(0, len(items) - 1),
            "roles": sorted({str(item.get("role") or "unknown") for item in items}),
            "totalTokens": _sum(_token_value(item) for item in items),
            "mcpCalls": _sum(_mcp_call_count(item) for item in items),
            "primaryQuotaCostPoints": _sum(_quota_cost(item, "primary") for item in items),
            "totalDurationSeconds": _sum(item.get("durationSeconds") for item in items),
            "finalOutcome": ordered[-1].get("outcome") if ordered else None,
            "runIds": [item.get("runId") for item in ordered],
        })
    rows.sort(key=lambda row: (row["continuations"], row["totalTokens"] or 0), reverse=True)
    return rows


def analyze(
    workers: list[dict[str, Any]] | None = None,
    limit: int = DEFAULT_LIMIT,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workers = workers if workers is not None else supervisor_telemetry.recent_workers(limit)
    workers = [dict(item) for item in workers if isinstance(item, dict)]
    events = events if events is not None else supervisor_telemetry.recent_events(max(2000, limit * 8))
    usage_by_run = _capability_usage([item for item in events if isinstance(item, dict)])
    for worker in workers:
        worker["_capabilityUsageSummary"] = usage_by_run.get(str(worker.get("runId") or ""), {})

    tokens = [_token_value(item) for item in workers]
    primary = [_quota_cost(item, "primary") for item in workers]
    mcp_samples = [_mcp_call_count(item) for item in workers]
    capability_names = _capability_names(workers)
    by_capability = {name: _group(workers, lambda item, n=name: _capability_bucket(item, n)) for name in capability_names}
    payload = {
        "protocolVersion": PROTOCOL_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "workerCount": len(workers),
        "coverage": {
            "tokenTelemetry": {"available": sum(value is not None for value in tokens), "total": len(workers)},
            "primaryQuotaDelta": {"available": sum(value is not None for value in primary), "total": len(workers)},
            "riskClass": {"available": sum(_risk(item) not in {"unavailable", "conflicting"} for item in workers), "total": len(workers)},
            "actualMcpUse": {"available": sum(value is not None for value in mcp_samples), "total": len(workers)},
        },
        "overall": _metric_group("all", workers),
        "byRole": _group(workers, lambda item: str(item.get("role") or "unknown")),
        "byModel": _group(workers, lambda item: str(item.get("model") or "unknown")),
        "byEffort": _group(workers, lambda item: str(item.get("effort") or "unknown")),
        "byRisk": _group(workers, _risk),
        "byOutcome": _group(workers, _outcome_class),
        "bySkillBundle": _group(workers, _skill_bundle),
        "byCapability": by_capability,
        "expensiveWorkers": _expensive(workers),
        "issueRollups": _issue_rollups(workers),
        "timingComparison": {
            "status": "unavailable",
            "reason": "Codex telemetry does not expose authoritative model-reasoning wall-clock time separately from the worker lifetime, so Unity validation wait time cannot be compared to model reasoning time without inventing a metric.",
        },
        "notes": [
            "Quota cost is authoritative percentage-point consumption only when both before/after App Server samples are available.",
            "Token counts and quota percentage points are intentionally reported as separate metrics.",
            "Capability actual-use groups come from attributable per-turn rollout MCP-call telemetry; selected_unknown means the capability was enabled but retained rollout evidence was insufficient to prove use or non-use.",
            "A selected-but-unused capability is not treated as a failure; Context7 is intentionally available only as an on-demand external-documentation surface.",
            "Continuation counts are chronological same-issue lifetime counts; they do not claim causal retry linkage.",
            "Risk class is taken from the worker's persisted lifecycle labels and is unavailable for older records that did not retain it.",
        ],
    }
    return supervisor_telemetry.sanitize(payload)


if __name__ == "__main__":
    import json
    print(json.dumps(analyze(), indent=2, sort_keys=True))
