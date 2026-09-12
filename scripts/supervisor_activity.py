#!/usr/bin/env python3
"""Read-only lifecycle queues and cross-system activity for Supervisor issue #46B."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Iterable

import review_state
import supervisor_telemetry
import unity_run_history

PROTOCOL_VERSION = 1
DEFAULT_REPO = "Shashakar/RPG-Kingdom"
DEFAULT_WORKSPACE_ROOT = "~/code/rpg-kingdom-symphony-workspaces"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_ACTIVITY_LIMIT = 80
DEFAULT_CACHE_SECONDS = 10.0

LIFECYCLE_LABELS = {
    "symphony:ready": "implementing",
    "symphony:agent-review": "agent_review",
    "symphony:rework": "rework",
    "symphony:human-review": "human_review",
    "symphony:human-attention": "human_attention",
    "symphony:halted": "halted",
    "symphony:report-complete": "report_complete",
}
LIFECYCLE_PRECEDENCE = (
    "symphony:human-attention",
    "symphony:human-review",
    "symphony:rework",
    "symphony:agent-review",
    "symphony:halted",
    "symphony:report-complete",
    "symphony:ready",
)
QUEUE_ORDER = (
    "implementing",
    "agent_review",
    "rework",
    "human_review",
    "human_attention",
    "halted",
    "report_complete",
)
ROUTE_PREFIXES = ("risk:", "model:", "effort:", "repair-route:")
MODEL_NAMES = {
    "luna": "gpt-5.6-luna",
    "terra": "gpt-5.6-terra",
    "sol": "gpt-5.6-sol",
    "astra": "gpt-6-astra",
}
ROLE_NAMES = {
    "implementation": "Implementation",
    "repair": "Repair",
    "review": "Automated review",
    "report-only": "Report-only worker",
}
LABEL_EVENT_TITLES = {
    "symphony:ready": "Queued for implementation",
    "symphony:rearm": "Continuation approved",
    "symphony:agent-review": "Moved to Agent Review",
    "symphony:rework": "Moved to Rework",
    "symphony:human-review": "Moved to Human Review",
    "symphony:human-attention": "Human attention required",
    "symphony:halted": "Worker halted",
    "symphony:report-complete": "Report-only work complete",
}
ISSUE_RE = re.compile(r"^GH-(\d+)$")
_CACHE: dict[str, Any] = {"key": None, "expires": 0.0, "payload": None}

GhRunner = Callable[[list[str]], dict[str, Any]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


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


def run_gh(arguments: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["gh", *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "code": None}
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "code": proc.returncode,
    }


def gh_json(runner: GhRunner, arguments: list[str]) -> Any:
    result = runner(arguments)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("stderr") or result.get("stdout") or "gh command failed").strip())
    try:
        return json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid gh JSON: {exc}") from exc


def label_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str) and item:
            names.append(item)
    return names


def lifecycle_label(labels: Iterable[str]) -> str | None:
    lowered = {item.lower() for item in labels}
    return next((name for name in LIFECYCLE_PRECEDENCE if name in lowered), None)


def queue_for_labels(labels: Iterable[str]) -> tuple[str | None, str | None]:
    label = lifecycle_label(labels)
    return (LIFECYCLE_LABELS.get(label), label) if label else (None, None)


def _selected_label(labels: set[str], prefix: str) -> list[str]:
    return sorted(item for item in labels if item.startswith(prefix))


def route_from_labels(labels: Iterable[str]) -> dict[str, Any]:
    lowered = {item.lower() for item in labels}
    model_labels = _selected_label(lowered, "model:")
    risk_labels = _selected_label(lowered, "risk:")
    effort_labels = _selected_label(lowered, "effort:")
    repair_labels = _selected_label(lowered, "repair-route:")
    conflicts = []
    if len(model_labels) > 1:
        conflicts.append("conflicting model labels")
    if len(risk_labels) > 1:
        conflicts.append("conflicting risk labels")
    if len(effort_labels) > 1:
        conflicts.append("conflicting effort labels")
    if len(repair_labels) > 1:
        conflicts.append("conflicting repair route labels")
    if repair_labels and "symphony:rework" not in lowered:
        conflicts.append("repair route label outside rework")
    if conflicts:
        return {"status": "invalid", "reason": "; ".join(conflicts), "model": None, "effort": None, "route": None}

    route = None
    default_effort = None
    if model_labels:
        route = model_labels[0].split(":", 1)[1]
        default_effort = {"luna": "low", "terra": "medium", "sol": "high", "astra": "medium"}[route]
    elif repair_labels and "symphony:rework" in lowered:
        route = repair_labels[0].split(":", 1)[1]
        default_effort = {"luna": "low", "terra": "medium", "sol": "high", "astra": "medium"}[route]
    elif "risk:end-to-end" in lowered:
        route, default_effort = "astra", "medium"
    elif "risk:architecture" in lowered:
        route, default_effort = "sol", "high"
    elif "risk:investigative" in lowered:
        route, default_effort = "terra", "medium"
    elif "risk:mechanical" in lowered:
        route, default_effort = "luna", "low"
    else:
        route, default_effort = "luna", "medium"

    effort = effort_labels[0].split(":", 1)[1] if effort_labels else default_effort
    return {
        "status": "available",
        "source": "labels",
        "model": MODEL_NAMES[route],
        "effort": effort,
        "route": route,
    }


def _worker_route(worker: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    if not worker:
        return fallback
    if not worker.get("model") and not worker.get("effort"):
        return fallback
    return {
        "status": "available",
        "source": "active-worker",
        "model": worker.get("model"),
        "effort": worker.get("effort"),
        "route": worker.get("route"),
    }


def _active_worker_map(workers: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for worker in workers:
        issue = worker.get("issue")
        if not isinstance(issue, int):
            continue
        prior = result.get(issue)
        if prior is None or (worker.get("alive") and not prior.get("alive")):
            result[issue] = worker
    return result


def _recent_worker_map(workers: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for worker in workers:
        issue = worker.get("issue")
        if isinstance(issue, int) and issue not in result:
            result[issue] = worker
    return result


def _latest_review_state(comments: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    markers: list[dict[str, Any]] = []
    if not isinstance(comments, list):
        return {}, markers
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        state = review_state.parse_state(str(comment.get("body") or ""))
        if state is None:
            continue
        markers.append({
            "state": state,
            "createdAt": comment.get("createdAt") or state.get("updatedAt"),
            "url": comment.get("url"),
            "id": comment.get("id"),
        })
    return (markers[-1]["state"] if markers else {}), markers


def _halt_reason(comments: Any, review: dict[str, Any]) -> str | None:
    if review.get("reason") not in (None, "", "none"):
        return str(review["reason"])
    if not isinstance(comments, list):
        return None
    for comment in reversed(comments):
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body") or "")
        lower = body.lower()
        if "usage_limit_exceeded" in lower or "codex usage quota was exhausted" in lower:
            return "usage_limit_exceeded"
        if "turn budget" in lower or "symphony:ready was still present" in lower or "symphony:ready` was still present" in lower:
            return "worker_lifetime_halted"
    return None


def _issue_events(repo: str, number: int, runner: GhRunner) -> list[dict[str, Any]]:
    try:
        raw = gh_json(runner, ["api", f"repos/{repo}/issues/{number}/events?per_page=100"])
    except RuntimeError:
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _state_since(events: list[dict[str, Any]], current_label: str | None, fallback: Any) -> str | None:
    if current_label:
        for event in reversed(events):
            if event.get("event") != "labeled":
                continue
            label = event.get("label")
            name = label.get("name") if isinstance(label, dict) else None
            if str(name or "").lower() == current_label:
                return event.get("created_at") or event.get("createdAt") or fallback
    return str(fallback) if fallback else None


def _seconds_in_state(value: Any) -> float | None:
    parsed = parse_time(value)
    return max(0.0, (utc_now() - parsed).total_seconds()) if parsed else None


def _lifecycle_event_title(label: str) -> str:
    return LABEL_EVENT_TITLES.get(label, label)


def _github_activity(
    issue: dict[str, Any],
    github_events: list[dict[str, Any]],
    review_markers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    number = int(issue["issue"])
    identifier = issue["identifier"]
    result: list[dict[str, Any]] = []
    for event in github_events:
        kind = str(event.get("event") or "")
        if kind != "labeled":
            continue
        label = event.get("label")
        name = str(label.get("name") if isinstance(label, dict) else "").lower()
        if name not in LABEL_EVENT_TITLES:
            continue
        observed = event.get("created_at") or event.get("createdAt")
        result.append({
            "id": f"github-label:{number}:{event.get('id') or observed}:{name}",
            "observedAt": observed,
            "category": "lifecycle",
            "kind": name,
            "issue": number,
            "identifier": identifier,
            "issueUrl": issue.get("url"),
            "prNumber": issue.get("prNumber"),
            "prUrl": issue.get("prUrl"),
            "title": _lifecycle_event_title(name),
            "summary": issue.get("haltReason") if name == "symphony:halted" else None,
        })
    for marker in review_markers:
        state = marker.get("state") if isinstance(marker.get("state"), dict) else {}
        cycle = state.get("reviewCycle")
        verdict = state.get("lastVerdict")
        if not cycle or not verdict:
            continue
        observed = marker.get("createdAt") or state.get("updatedAt")
        result.append({
            "id": f"review-state:{number}:{marker.get('id') or observed}:{cycle}:{verdict}",
            "observedAt": observed,
            "category": "review",
            "kind": "review_verdict",
            "issue": number,
            "identifier": identifier,
            "issueUrl": issue.get("url"),
            "prNumber": state.get("prNumber") or issue.get("prNumber"),
            "prUrl": issue.get("prUrl"),
            "title": f"Automated review cycle {cycle}: {str(verdict).replace('_', ' ')}",
            "summary": state.get("lastSummary") or state.get("reason"),
            "headSha": state.get("prHeadSha"),
            "reviewCycle": cycle,
            "repairAttempts": state.get("repairAttempts"),
        })
    return result


def _safe_lifecycle_labels(labels: Iterable[str]) -> list[str]:
    return sorted(
        item for item in labels
        if item.lower() in LIFECYCLE_LABELS
        or item.lower() == "symphony:rearm"
        or item.lower().startswith(ROUTE_PREFIXES)
    )


def _issue_detail(repo: str, number: int, runner: GhRunner) -> dict[str, Any]:
    try:
        value = gh_json(
            runner,
            [
                "issue", "view", str(number), "--repo", repo,
                "--json", "number,title,state,url,updatedAt,labels,comments",
            ],
        )
        return value if isinstance(value, dict) else {}
    except RuntimeError as exc:
        return {"_error": str(exc)}


def collect_lifecycle(
    *,
    repo: str | None = None,
    runner: GhRunner = run_gh,
    active_workers: list[dict[str, Any]] | None = None,
    recent_workers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    repo = repo or os.environ.get("RPGK_REPO", DEFAULT_REPO)
    active_workers = active_workers if active_workers is not None else supervisor_telemetry.active_workers()
    recent_workers = recent_workers if recent_workers is not None else supervisor_telemetry.recent_workers(50)
    active_by_issue = _active_worker_map(active_workers)
    recent_by_issue = _recent_worker_map(recent_workers)
    errors: list[str] = []
    try:
        raw = gh_json(
            runner,
            [
                "issue", "list", "--repo", repo, "--state", "open", "--limit", "100",
                "--json", "number,title,state,url,updatedAt,labels",
            ],
        )
    except RuntimeError as exc:
        return {
            "available": False,
            "repo": repo,
            "generatedAt": iso_now(),
            "errors": [str(exc)],
            "queues": {name: [] for name in QUEUE_ORDER},
            "items": [],
            "activity": [],
        }
    summaries = raw if isinstance(raw, list) else []
    items: list[dict[str, Any]] = []
    github_activity: list[dict[str, Any]] = []

    for summary in summaries:
        if not isinstance(summary, dict) or not isinstance(summary.get("number"), int):
            continue
        summary_labels = label_names(summary.get("labels"))
        queue, current_label = queue_for_labels(summary_labels)
        if queue is None:
            continue
        number = int(summary["number"])
        detail = _issue_detail(repo, number, runner)
        if detail.get("_error"):
            errors.append(f"GH-{number}: {detail['_error']}")
            detail = summary
        labels = label_names(detail.get("labels")) or summary_labels
        queue, current_label = queue_for_labels(labels)
        if queue is None:
            continue
        comments = detail.get("comments") or []
        review, review_markers = _latest_review_state(comments)
        events = _issue_events(repo, number, runner)
        state_since = _state_since(events, current_label, review.get("updatedAt") or detail.get("updatedAt") or summary.get("updatedAt"))
        active = active_by_issue.get(number)
        recent = recent_by_issue.get(number)
        route = _worker_route(active, route_from_labels(labels))
        pr_number = review.get("prNumber")
        pr_url = f"https://github.com/{repo}/pull/{pr_number}" if pr_number else None
        halt_reason = _halt_reason(comments, review) if queue == "halted" else review.get("reason")
        item = {
            "issue": number,
            "identifier": f"GH-{number}",
            "title": detail.get("title") or summary.get("title"),
            "url": detail.get("url") or summary.get("url"),
            "lifecycleState": queue,
            "lifecycleLabel": current_label,
            "labels": _safe_lifecycle_labels(labels),
            "stateSince": state_since,
            "stateAgeSeconds": _seconds_in_state(state_since),
            "active": bool(active and active.get("alive")),
            "workerRole": active.get("role") if active else None,
            "workerRunId": active.get("runId") if active else None,
            "route": route,
            "reviewCycle": review.get("reviewCycle"),
            "repairAttempts": review.get("repairAttempts"),
            "maxRepairAttempts": review.get("maxRepairAttempts"),
            "latestVerdict": review.get("lastVerdict"),
            "latestSummary": review.get("lastSummary"),
            "haltReason": halt_reason,
            "quotaBlocked": halt_reason == "usage_limit_exceeded",
            "prNumber": pr_number,
            "prUrl": pr_url,
            "headSha": review.get("prHeadSha"),
            "latestWorkerOutcome": recent.get("outcome") if recent else None,
            "humanActionRequired": queue in {"human_review", "human_attention", "halted", "report_complete"},
        }
        items.append(item)
        github_activity.extend(_github_activity(item, events, review_markers))

    items.sort(key=lambda item: (QUEUE_ORDER.index(item["lifecycleState"]), item["issue"]))
    queues = {name: [item for item in items if item["lifecycleState"] == name] for name in QUEUE_ORDER}
    return {
        "available": True,
        "repo": repo,
        "generatedAt": iso_now(),
        "errors": errors,
        "queues": queues,
        "items": items,
        "activity": github_activity,
    }


def _workspace_root() -> Path:
    return Path(os.path.expanduser(os.environ.get("RPGK_WORKSPACE_ROOT") or os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT))


def _retention_cutoff() -> datetime:
    try:
        days = int(os.environ.get("RPGK_ACTIVITY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    except ValueError:
        days = DEFAULT_RETENTION_DAYS
    return utc_now() - timedelta(days=max(days, 1))


def _git_handoff_events(workspace_root: Path) -> list[dict[str, Any]]:
    cutoff = _retention_cutoff()
    result: list[dict[str, Any]] = []
    if not workspace_root.is_dir():
        return result
    for path in workspace_root.glob("GH-*/Logs/SymphonyGit/.broker/responses/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        observed = payload.get("completedAt")
        parsed = parse_time(observed)
        if parsed and parsed < cutoff:
            continue
        try:
            workspace = path.parents[4]
        except IndexError:
            continue
        match = ISSUE_RE.fullmatch(workspace.name)
        if match is None:
            continue
        number = int(match.group(1))
        operation = str(payload.get("operation") or "unknown")
        status = str(payload.get("status") or "unknown")
        nested = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        pr_number = nested.get("prNumber")
        commit_sha = nested.get("commitSha")
        if operation == "handoff":
            title = "Git handoff completed" if status in {"completed", "passed", "success"} else "Git handoff failed"
        elif operation == "prepare":
            title = "Git workspace prepared" if status in {"completed", "passed", "success"} else "Git prepare failed"
        else:
            title = f"Git {operation} {status}"
        summary_parts = []
        if pr_number:
            summary_parts.append(f"PR #{pr_number}")
        if commit_sha:
            summary_parts.append(str(commit_sha)[:12])
        if not summary_parts and payload.get("stderr"):
            summary_parts.append(str(payload.get("stderr")).strip().splitlines()[0][:300])
        result.append({
            "id": f"git:{workspace.name}:{payload.get('requestId') or path.stem}",
            "observedAt": observed,
            "category": "git",
            "kind": operation,
            "issue": number,
            "identifier": workspace.name,
            "title": title,
            "summary": " · ".join(summary_parts) or status,
            "prNumber": pr_number,
            "prUrl": nested.get("prUrl"),
            "headSha": commit_sha,
            "gitRequestId": payload.get("requestId"),
            "status": status,
        })
    return result


def _unity_activity(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for run in runs:
        issue = str(run.get("issue") or "")
        match = ISSUE_RE.fullmatch(issue)
        number = int(match.group(1)) if match else None
        operation = str(run.get("operation") or "Unity")
        status = str(run.get("finalStatus") or run.get("status") or "unknown")
        diagnosis = run.get("diagnosis") if isinstance(run.get("diagnosis"), dict) else {}
        observed = run.get("startedAt") if status == "running" else run.get("completedAt") or run.get("startedAt")
        label = operation[:1].upper() + operation[1:]
        if status == "running":
            title = f"{label} validation started"
            summary = diagnosis.get("message")
        elif status == "passed":
            title = f"{label} validation passed"
            summary = run.get("testFilter") or "all tests"
        else:
            category = str(diagnosis.get("category") or status).replace("_", " ")
            code = diagnosis.get("code")
            title = f"{label} validation {category}"
            if code:
                title += f" — {code}"
            summary = diagnosis.get("message")
        result.append({
            "id": f"unity:{run.get('requestId')}",
            "observedAt": observed,
            "category": "unity",
            "kind": status,
            "issue": number,
            "identifier": issue or None,
            "title": title,
            "summary": summary,
            "unityRequestId": run.get("requestId"),
            "status": status,
        })
    return result


def _related_unity_requests(event: dict[str, Any], runs: list[dict[str, Any]]) -> list[str]:
    issue = event.get("identifier")
    started = parse_time(event.get("startedAt"))
    ended = parse_time(event.get("endedAt") or event.get("observedAt"))
    if not issue or not started or not ended:
        return []
    matches: list[str] = []
    for run in runs:
        if run.get("issue") != issue:
            continue
        when = parse_time(run.get("startedAt") or run.get("acceptedAt") or run.get("completedAt"))
        request_id = run.get("requestId")
        if when and request_id and started - timedelta(seconds=60) <= when <= ended + timedelta(seconds=60):
            matches.append(str(request_id))
    return matches[:8]


def _telemetry_activity(events: list[dict[str, Any]], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("eventType") or "")
        if event_type not in {"worker_started", "worker_completed"}:
            continue
        role = str(event.get("role") or "worker")
        role_name = ROLE_NAMES.get(role, role.replace("-", " ").title())
        model = str(event.get("route") or event.get("model") or "unknown")
        effort = str(event.get("effort") or "unknown")
        identifier = event.get("identifier")
        if event_type == "worker_started":
            title = f"{role_name} started — {model}/{effort}"
            summary = None
        else:
            outcome = str(event.get("outcome") or "unknown").replace("_", " ")
            title = f"{role_name} completed — {outcome}"
            token = event.get("tokenUsage") if isinstance(event.get("tokenUsage"), dict) else {}
            total = token.get("totalTokens") if token.get("status") == "available" else None
            summary = f"{total} tokens" if total is not None else None
        activity = {
            "id": f"telemetry:{event_type}:{event.get('runId') or event.get('observedAt')}",
            "observedAt": event.get("observedAt") or event.get("endedAt") or event.get("startedAt"),
            "category": "worker",
            "kind": event_type,
            "issue": event.get("issue"),
            "identifier": identifier,
            "title": title,
            "summary": summary,
            "workerRunId": event.get("runId"),
            "role": role,
            "startedAt": event.get("startedAt"),
            "endedAt": event.get("endedAt"),
        }
        if event_type == "worker_completed":
            activity["unityRequestIds"] = _related_unity_requests(activity, runs)
        result.append(activity)
    return result


def _dedupe_sort_activity(events: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("id") or "")
        if not event_id or not event.get("observedAt"):
            continue
        deduped[event_id] = supervisor_telemetry.sanitize(event)
    ordered = list(deduped.values())
    ordered.sort(key=lambda item: parse_time(item.get("observedAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return ordered[: max(1, min(limit, 500))]


def collect(
    *,
    repo: str | None = None,
    runner: GhRunner = run_gh,
    workspace_root: Path | None = None,
    limit: int = DEFAULT_ACTIVITY_LIMIT,
    use_cache: bool = True,
) -> dict[str, Any]:
    repo = repo or os.environ.get("RPGK_REPO", DEFAULT_REPO)
    workspace_root = workspace_root or _workspace_root()
    cache_key = f"{repo}|{workspace_root}|{limit}"
    now = time.monotonic()
    if use_cache and _CACHE.get("key") == cache_key and now < float(_CACHE.get("expires") or 0) and _CACHE.get("payload"):
        return deepcopy(_CACHE["payload"])

    active = supervisor_telemetry.active_workers()
    recent = supervisor_telemetry.recent_workers(50)
    lifecycle = collect_lifecycle(repo=repo, runner=runner, active_workers=active, recent_workers=recent)
    runs = unity_run_history.collect_runs(workspace_root=workspace_root, limit=50)
    telemetry_events = supervisor_telemetry.recent_events(150)
    activity = _dedupe_sort_activity(
        [
            *lifecycle.get("activity", []),
            *_telemetry_activity(telemetry_events, runs),
            *_unity_activity(runs),
            *_git_handoff_events(workspace_root),
        ],
        limit,
    )
    payload = supervisor_telemetry.sanitize({
        "protocolVersion": PROTOCOL_VERSION,
        "generatedAt": iso_now(),
        "available": lifecycle.get("available", False),
        "repo": repo,
        "errors": lifecycle.get("errors", []),
        "queues": lifecycle.get("queues", {name: [] for name in QUEUE_ORDER}),
        "items": lifecycle.get("items", []),
        "activity": activity,
    })
    if use_cache:
        try:
            cache_seconds = float(os.environ.get("RPGK_ACTIVITY_CACHE_SECONDS", DEFAULT_CACHE_SECONDS))
        except ValueError:
            cache_seconds = DEFAULT_CACHE_SECONDS
        _CACHE.update({"key": cache_key, "expires": now + max(0.0, cache_seconds), "payload": deepcopy(payload)})
    return payload
