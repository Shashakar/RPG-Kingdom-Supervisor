#!/usr/bin/env python3
"""Read-only Unity broker run history and diagnostics.

History is reconstructed from durable workspace-local broker metadata and Unity
artifacts. The broker itself remains the execution authority; this module never
runs Unity or mutates workspaces.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET

DEFAULT_WORKSPACE_ROOT = "~/code/rpg-kingdom-symphony-workspaces"
DEFAULT_STATE_ROOT = "~/.local/state/rpg-kingdom-supervisor"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_LIMIT = 100
MAX_TEXT_BYTES = 2_000_000

COMPILER_ERROR = re.compile(
    r"(?P<path>[^\r\n()]+\.(?:cs|asmdef|asmref))\((?P<line>\d+),(?P<column>\d+)\):\s*error\s+(?P<code>[A-Z]+\d+):\s*(?P<message>.+)",
    re.IGNORECASE,
)
ARTIFACT_LINE = re.compile(r"RPG Kingdom Unity runner: artifacts -> (?P<path>.+)")
STARTUP_TERMS = (
    ("licens", "Unity licensing failure"),
    ("activation", "Unity activation failure"),
    ("failed to load project", "Unity project-load failure"),
    ("project could not be opened", "Unity project-load failure"),
    ("aborting batchmode", "Unity startup/batchmode failure"),
    ("fatal error", "Unity fatal runtime failure"),
    ("crash!!!", "Unity crash"),
)
LICENSE_RECOVERY_TERMS = (
    "successfully updated license",
    "license successfully updated",
    "license update successful",
)
INFRA_STATUSES = {
    "HostBusy": ("host_busy", "Unity host busy", True),
    "TimedOut": ("timeout", "Unity host timeout", True),
    "StaleRequest": ("stale_request", "Stale broker request", True),
    "BrokerStopped": ("broker_stopped", "Unity broker stopped", True),
}
INFRA_EXIT_CODES = {70, 80, 81, 82, 83, 84, 85, 86, 89, 90}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_text(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def response_artifact_path(response: dict[str, Any], workspace: Path) -> Path | None:
    # The PowerShell summary records SourceOutput in Windows/UNC syntax. The host
    # shell always prints the corresponding WSL artifact path after a test run, so
    # prefer that local path when reconstructing history.
    stdout = str(response.get("stdout") or "")
    found = None
    for match in ARTIFACT_LINE.finditer(stdout):
        found = match.group("path").strip()
    if found:
        path = Path(found).expanduser()
        return path if path.is_absolute() else (workspace / path).resolve()

    summary = response.get("summary")
    if isinstance(summary, dict):
        run_id = summary.get("runId")
        if isinstance(run_id, str) and run_id.strip():
            inferred = workspace / "Logs" / "SymphonyUnity" / run_id.strip()
            if inferred.exists():
                return inferred.resolve()
        raw = summary.get("artifactPath")
        if isinstance(raw, str) and raw.strip():
            path = Path(raw.strip()).expanduser()
            if path.is_absolute():
                return path
    return None


def failed_tests_from_xml(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    failures: list[dict[str, str]] = []
    for case in root.iter("test-case"):
        result = (case.attrib.get("result") or "").lower()
        if result not in {"failed", "failure"}:
            continue
        failure = case.find("failure")
        message = ""
        stack = ""
        if failure is not None:
            message = (failure.findtext("message") or "").strip()
            stack = (failure.findtext("stack-trace") or "").strip()
        failures.append(
            {
                "name": case.attrib.get("fullname") or case.attrib.get("name") or "unknown test",
                "message": message,
                "stackTrace": stack,
            }
        )
    return failures[:50]


def compiler_diagnostic(editor_log: str) -> dict[str, Any] | None:
    match = COMPILER_ERROR.search(editor_log)
    if not match:
        return None
    source = match.group("path").strip()
    message = match.group("message").strip()
    return {
        "category": "compile",
        "title": "Script compilation",
        "message": message,
        "sourcePath": source,
        "line": int(match.group("line")),
        "column": int(match.group("column")),
        "code": match.group("code").upper(),
        "retryable": False,
        "testsStarted": False,
    }


def diagnose_run(
    response: dict[str, Any],
    summary: dict[str, Any] | None,
    editor_log: str,
    failed_tests: list[dict[str, str]],
    results_exist: bool,
) -> dict[str, Any]:
    status = str(response.get("status") or "unknown")
    if status in INFRA_STATUSES:
        category, title, retryable = INFRA_STATUSES[status]
        return {
            "category": category,
            "title": title,
            "message": str(response.get("stderr") or title).strip(),
            "retryable": retryable,
            "testsStarted": False,
        }

    compiled = compiler_diagnostic(editor_log)
    if compiled:
        return compiled

    exit_code = response.get("exitCode")
    if isinstance(exit_code, int) and exit_code in INFRA_EXIT_CODES:
        return {
            "category": "infrastructure",
            "title": "Unity host/infrastructure failure",
            "message": str(response.get("stderr") or f"Unity host operation failed with exit code {exit_code}.").strip(),
            "retryable": exit_code in {70, 80, 84, 85, 86, 89, 90},
            "testsStarted": False,
        }

    if failed_tests:
        first = failed_tests[0]
        extra = len(failed_tests) - 1
        title = f"{len(failed_tests)} Unity test{'s' if len(failed_tests) != 1 else ''} failed"
        message = first.get("message") or first.get("name") or "Unity test failure"
        if extra:
            message = f"{message} (+{extra} more failing test{'s' if extra != 1 else ''})"
        return {
            "category": "test_failure",
            "title": title,
            "message": message,
            "retryable": False,
            "testsStarted": True,
        }

    if isinstance(summary, dict):
        try:
            failed_count = int(summary.get("failed") or 0)
        except (TypeError, ValueError):
            failed_count = 0
        if failed_count > 0:
            return {
                "category": "test_failure",
                "title": f"{failed_count} Unity test{'s' if failed_count != 1 else ''} failed",
                "message": "The summary reports failed tests; detailed names/assertions were unavailable from results.xml.",
                "retryable": False,
                "testsStarted": True,
            }

    lower_log = editor_log.lower()
    licensing_recovered = any(term in lower_log for term in LICENSE_RECOVERY_TERMS)
    for term, title in STARTUP_TERMS:
        if term in {"licens", "activation"} and licensing_recovered:
            continue
        if term in lower_log:
            excerpt = next((line.strip() for line in editor_log.splitlines() if term in line.lower()), title)
            return {
                "category": "startup",
                "title": title,
                "message": excerpt[:1000],
                "retryable": term in {"licens", "activation"},
                "testsStarted": results_exist,
            }

    if status == "NoTestsMatched" or (isinstance(summary, dict) and "total" in summary and int(summary.get("total") or 0) == 0):
        return {
            "category": "no_tests",
            "title": "No tests matched",
            "message": "Unity completed, but zero tests matched the requested filter.",
            "retryable": False,
            "testsStarted": True,
        }

    exit_code = response.get("exitCode")
    failed = status not in {"completed", "passed", "success"} or (isinstance(exit_code, int) and exit_code != 0)
    if failed and not results_exist:
        stderr = str(response.get("stderr") or "").strip()
        return {
            "category": "unity_runner",
            "title": "Unity failed before test results were produced",
            "message": stderr or "No results.xml was produced; inspect Editor.log and broker response metadata.",
            "retryable": False,
            "testsStarted": False,
        }

    if failed:
        return {
            "category": "unity_failure",
            "title": "Unity run failed",
            "message": str(response.get("stderr") or "Unity returned a failing result.").strip(),
            "retryable": False,
            "testsStarted": results_exist,
        }

    return {
        "category": "passed",
        "title": "Passed",
        "message": "Unity validation completed successfully.",
        "retryable": False,
        "testsStarted": results_exist,
    }


def run_from_response(response_path: Path, workspace: Path) -> dict[str, Any] | None:
    response = read_json(response_path)
    if response is None:
        return None
    request_id = str(response.get("requestId") or response_path.stem)
    broker_dir = response_path.parent.parent
    request_path = broker_dir / "history" / "requests" / f"{request_id}.json"
    request = read_json(request_path) or {}
    ack = read_json(broker_dir / "acks" / f"{request_id}.json") or {}

    artifact = response_artifact_path(response, workspace)
    summary = response.get("summary") if isinstance(response.get("summary"), dict) else None
    if artifact is not None and summary is None:
        summary = read_json(artifact / "summary.json")
    results_path = artifact / "results.xml" if artifact else None
    editor_path = artifact / "Editor.log" if artifact else None
    failed_tests = failed_tests_from_xml(results_path) if results_path else []
    editor_log = read_text(editor_path) if editor_path else ""
    diagnosis = diagnose_run(
        response,
        summary,
        editor_log,
        failed_tests,
        bool(results_path and results_path.is_file()),
    )

    started_at = response.get("startedAt") or ack.get("acceptedAt") or request.get("requestedAt")
    completed_at = response.get("completedAt")
    started_dt = parse_time(started_at)
    completed_dt = parse_time(completed_at)
    duration = None
    if started_dt and completed_dt:
        duration = max(0.0, (completed_dt - started_dt).total_seconds())

    operation = str(request.get("operation") or response.get("operation") or "unknown")
    test_filter = request.get("testFilter")
    if test_filter in (None, "") and isinstance(summary, dict):
        test_filter = summary.get("testFilter")

    result = str((summary or {}).get("result") or response.get("status") or "unknown")
    if diagnosis.get("category") == "passed":
        final_status = "passed"
    elif str(response.get("status")) == "TimedOut":
        final_status = "timed_out"
    elif str(response.get("status")) == "HostBusy":
        final_status = "rejected_busy"
    else:
        final_status = "failed"

    excerpts: list[str] = []
    if diagnosis.get("category") == "compile":
        needle = str(diagnosis.get("code") or "")
        excerpts = [line.strip() for line in editor_log.splitlines() if needle and needle in line][:8]
    elif diagnosis.get("category") == "startup":
        title_words = str(diagnosis.get("title") or "").lower().split()
        excerpts = [
            line.strip() for line in editor_log.splitlines()
            if any(word in line.lower() for word in title_words if len(word) > 4)
        ][:8]

    return {
        "requestId": request_id,
        "issue": workspace.name,
        "workspace": str(workspace),
        "operation": operation,
        "testFilter": str(test_filter or ""),
        "requestedAt": request.get("requestedAt"),
        "acceptedAt": ack.get("acceptedAt"),
        "startedAt": started_at,
        "completedAt": completed_at,
        "durationSeconds": duration,
        "status": response.get("status"),
        "finalStatus": final_status,
        "result": result,
        "exitCode": response.get("exitCode"),
        "artifactPath": str(artifact) if artifact else None,
        "paths": {
            "editorLog": str(editor_path) if editor_path and editor_path.is_file() else None,
            "resultsXml": str(results_path) if results_path and results_path.is_file() else None,
            "summaryJson": str(artifact / "summary.json") if artifact and (artifact / "summary.json").is_file() else None,
            "brokerRequest": str(request_path) if request_path.is_file() else None,
            "brokerResponse": str(response_path),
        },
        "summary": summary,
        "failedTests": failed_tests,
        "diagnosis": diagnosis,
        "errorExcerpts": excerpts,
        "broker": {"request": request or None, "ack": ack or None, "response": response},
    }


def active_run(state_root: Path) -> dict[str, Any] | None:
    status = read_json(state_root / "unity-broker" / "status.json")
    if not status or status.get("state") != "running" or not isinstance(status.get("activeRequest"), dict):
        return None
    active = status["activeRequest"]
    return {
        "requestId": active.get("requestId"),
        "issue": active.get("issue"),
        "workspace": active.get("workspace"),
        "operation": active.get("operation"),
        "testFilter": active.get("testFilter") or "",
        "acceptedAt": active.get("startedAt"),
        "startedAt": active.get("startedAt"),
        "completedAt": None,
        "durationSeconds": active.get("elapsedSeconds"),
        "status": "running",
        "finalStatus": "running",
        "result": "running",
        "exitCode": None,
        "artifactPath": None,
        "paths": {},
        "summary": None,
        "failedTests": [],
        "diagnosis": {
            "category": "running",
            "title": "Running",
            "message": "Unity host operation is currently active.",
            "retryable": False,
            "testsStarted": None,
        },
        "errorExcerpts": [],
        "broker": {"status": status},
    }


def iter_runs(workspace_root: Path) -> Iterable[dict[str, Any]]:
    for response_path in workspace_root.glob("GH-*/Logs/SymphonyUnity/.broker/responses/*.json"):
        workspace = response_path.parents[4]
        run = run_from_response(response_path, workspace)
        if run is not None:
            yield run


def collect_runs(
    *,
    workspace_root: Path | None = None,
    state_root: Path | None = None,
    issue: str | None = None,
    operation: str | None = None,
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    retention_days: int | None = None,
    include_active: bool = True,
) -> list[dict[str, Any]]:
    workspace_root = workspace_root or Path(os.path.expanduser(os.environ.get("RPGK_WORKSPACE_ROOT") or os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT))
    state_root = state_root or Path(os.path.expanduser(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", DEFAULT_STATE_ROOT)))
    if retention_days is None:
        try:
            retention_days = int(os.environ.get("RPGK_UNITY_HISTORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
        except ValueError:
            retention_days = DEFAULT_RETENTION_DAYS
    cutoff = utc_now() - timedelta(days=max(retention_days, 1))
    runs = list(iter_runs(workspace_root)) if workspace_root.is_dir() else []
    if include_active:
        active = active_run(state_root)
        if active:
            runs.append(active)

    def keep(run: dict[str, Any]) -> bool:
        if issue and run.get("issue") != issue:
            return False
        if operation and run.get("operation") != operation:
            return False
        if status and run.get("finalStatus") != status and run.get("status") != status:
            return False
        if run.get("finalStatus") == "running":
            return True
        completed = parse_time(run.get("completedAt"))
        return completed is None or completed >= cutoff

    filtered = [run for run in runs if keep(run)]
    filtered.sort(key=lambda run: parse_time(run.get("completedAt") or run.get("startedAt") or run.get("acceptedAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return filtered[: max(1, min(limit, 500))]


def find_run(request_id: str, **kwargs: Any) -> dict[str, Any] | None:
    for run in collect_runs(limit=500, **kwargs):
        if run.get("requestId") == request_id:
            return run
    return None
