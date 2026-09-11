#!/usr/bin/env python3
"""Read-only diagnostics for RPG Kingdom Symphony issues and Unity runs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_REPO = "Shashakar/RPG-Kingdom"
ERROR_TERMS = (
    "Read-only file system",
    "FETCH_HEAD",
    "index.lock",
    "UtilBindVsockAnyPort",
    "socket failed",
    "permission denied",
    "Permission denied",
)
COMPILER_ERROR = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^()\r\n]*\.(?:cs|asmdef|asmref))"
    r"\((?P<line>\d+),(?P<column>\d+)\): error "
    r"(?P<code>[A-Z]+\d+): (?P<message>[^\r\n]+)"
)
ARTIFACT_LINE = re.compile(r"artifacts\s*->\s*(?P<path>[^\r\n]+)", re.IGNORECASE)


def run(command: list[str], cwd: Path | None = None, timeout: int = 15) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "code": None, "stdout": "", "stderr": str(exc)}


def workspace_root() -> Path:
    return Path(
        os.path.expanduser(
            os.environ.get(
                "RPGK_WORKSPACE_ROOT", "~/code/rpg-kingdom-symphony-workspaces"
            )
        )
    )


def state_root() -> Path:
    return Path(
        os.path.expanduser(
            os.environ.get(
                "RPGK_SUPERVISOR_STATE_ROOT", "~/.local/state/rpg-kingdom-supervisor"
            )
        )
    )


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_text_tail(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def elapsed_seconds(started_at: Any, completed_at: Any) -> float | None:
    start = parse_timestamp(started_at)
    end = parse_timestamp(completed_at)
    if start is None or end is None:
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def issue_from_github(number: int, repo: str) -> dict[str, Any]:
    result = run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,state,url,updatedAt,labels,comments",
        ]
    )
    if not result["ok"]:
        return {"available": False, "error": result["stderr"] or result["stdout"]}
    try:
        raw = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        return {"available": False, "error": f"invalid gh JSON: {exc}"}
    comments = raw.get("comments") or []
    return {
        "available": True,
        "number": raw.get("number"),
        "title": raw.get("title"),
        "state": raw.get("state"),
        "url": raw.get("url"),
        "updated_at": raw.get("updatedAt"),
        "labels": [item.get("name") for item in raw.get("labels", []) if item.get("name")],
        "latest_comment": comments[-1].get("body", "") if comments else "",
        "comment_count": len(comments),
    }


def workspace_state(identifier: str) -> dict[str, Any]:
    workspace = workspace_root() / identifier
    result: dict[str, Any] = {
        "path": str(workspace),
        "exists": workspace.is_dir(),
        "attempt_complete": (workspace / ".symphony-attempt-complete").exists(),
    }
    if not workspace.is_dir():
        return result
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace)
    head = run(["git", "rev-parse", "HEAD"], cwd=workspace)
    git_status = run(["git", "status", "--short", "--branch"], cwd=workspace)
    result.update(
        {
            "branch": branch["stdout"] if branch["ok"] else None,
            "head": head["stdout"] if head["ok"] else None,
            "git_status": git_status["stdout"] if git_status["ok"] else None,
            "git_error": None if git_status["ok"] else git_status["stderr"],
        }
    )
    summaries = sorted(
        workspace.glob("Logs/SymphonyUnity/*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if summaries:
        latest = summaries[0]
        summary = load_json(latest)
        result["latest_unity"] = summary or {"error": "unable to parse summary.json"}
        result["latest_unity"]["summary_path"] = str(latest)
    else:
        result["latest_unity"] = None
    return result


def unity_lock_state() -> dict[str, Any]:
    lock = state_root() / "locks" / "unity-editor.lock"
    result: dict[str, Any] = {"path": str(lock), "held": lock.is_dir()}
    if lock.is_dir():
        for name in ("owner", "workspace", "acquired-at"):
            path = lock / name
            result[name.replace("-", "_")] = (
                path.read_text(errors="replace").strip() if path.exists() else None
            )
    return result


def symphony_state(identifier: str) -> dict[str, Any]:
    root = Path(
        os.path.expanduser(
            os.environ.get("RPGK_SYMPHONY_ROOT", "~/src/openai-symphony/elixir")
        )
    )
    files = [
        path
        for path in (root / "log").glob("symphony.log*")
        if path.is_file() and not path.name.endswith((".idx", ".siz"))
    ]
    files.sort(key=lambda path: path.stat().st_mtime)
    matches: list[str] = []
    for path in files:
        for line in read_text_tail(path).splitlines():
            if f"issue_identifier={identifier}" in line or f" {identifier} " in line:
                matches.append(line)
    matches = matches[-120:]
    session_id = None
    thread_id = None
    turn = None
    for line in reversed(matches):
        if session_id is None:
            found = re.search(r"session_id=([^\s]+)", line)
            if found:
                session_id = found.group(1)
                thread_id = session_id[:36]
        if turn is None:
            found = re.search(r"turn=(\d+/\d+)", line)
            if found:
                turn = found.group(1)
        if session_id and turn:
            break
    return {
        "root": str(root),
        "lines": matches[-30:],
        "session_id": session_id,
        "thread_id": thread_id,
        "turn": turn,
    }


def walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_strings(child)


def rollout_state(thread_id: str | None) -> dict[str, Any]:
    if not thread_id:
        return {"available": False, "reason": "no Symphony thread id found"}
    sessions = Path(os.path.expanduser("~/.codex/sessions"))
    candidates = list(sessions.glob(f"**/rollout-*{thread_id}*.jsonl"))
    if not candidates:
        return {"available": False, "reason": f"no rollout found for {thread_id}"}
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    path = candidates[0]
    latest_tokens = None
    last_context = None
    failures: list[str] = []
    model = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload")
                if isinstance(payload, dict):
                    ptype = payload.get("type")
                    if ptype == "token_count":
                        latest_tokens = payload.get("info")
                    elif ptype == "turn_context":
                        last_context = payload
                    elif ptype in ("session_meta", "thread_started"):
                        model = payload.get("model") or model
                for text in walk_strings(record):
                    if any(term in text for term in ERROR_TERMS):
                        failures.append(text)
    except OSError as exc:
        return {"available": False, "reason": str(exc), "path": str(path)}
    context_summary: dict[str, Any] = {}
    if isinstance(last_context, dict):
        flattened = "\n".join(walk_strings(last_context))
        context_summary = {
            "mentions_git": ".git" in flattened,
            "mentions_workspace_write": "workspace-write" in flattened
            or "workspaceWrite" in flattened,
            "raw": last_context,
        }
    return {
        "available": True,
        "path": str(path),
        "model": model,
        "tokens": latest_tokens,
        "turn_context": context_summary,
        "failures": failures[-10:],
    }


def artifact_directory(response: dict[str, Any], workspace: Path) -> Path | None:
    summary = response.get("summary")
    if isinstance(summary, dict):
        for key in ("artifactDirectory", "artifactPath", "artifacts"):
            value = summary.get(key)
            if isinstance(value, str) and value:
                path = Path(value)
                return path if path.is_absolute() else workspace / path
    found = ARTIFACT_LINE.search(str(response.get("stdout") or ""))
    if found:
        path = Path(found.group("path").strip().strip("'\""))
        return path if path.is_absolute() else workspace / path
    return None


def test_filter_from_log(editor_log: Path | None) -> str | None:
    if editor_log is None or not editor_log.is_file():
        return None
    lines = read_text_tail(editor_log, 256_000).splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == "-testfilter" and index + 1 < len(lines):
            return lines[index + 1].strip().strip('"')
        found = re.search(r"-testFilter(?:=|\s+)[\"']?([^\"'\r\n]+)", line, re.I)
        if found:
            return found.group(1).strip()
    return None


def compiler_errors(editor_log: Path | None) -> list[dict[str, Any]]:
    if editor_log is None or not editor_log.is_file():
        return []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    for match in COMPILER_ERROR.finditer(read_text_tail(editor_log)):
        item = (
            match.group("path").strip(),
            int(match.group("line")),
            int(match.group("column")),
            match.group("code"),
            match.group("message").strip(),
        )
        if item in seen:
            continue
        seen.add(item)
        errors.append(
            {
                "path": item[0],
                "line": item[1],
                "column": item[2],
                "code": item[3],
                "message": item[4],
            }
        )
    return errors[:20]


def failed_tests(results_xml: Path | None) -> list[dict[str, str]]:
    if results_xml is None or not results_xml.is_file():
        return []
    try:
        root = ET.parse(results_xml).getroot()
    except (ET.ParseError, OSError):
        return []
    failures: list[dict[str, str]] = []
    for case in root.iter("test-case"):
        if str(case.attrib.get("result", "")).lower() not in {"failed", "failure"}:
            continue
        failure = case.find("failure")
        message_node = failure.find("message") if failure is not None else None
        failures.append(
            {
                "name": str(
                    case.attrib.get("fullname")
                    or case.attrib.get("full-name")
                    or case.attrib.get("name")
                    or "unknown test"
                ),
                "message": message_node.text.strip()
                if message_node is not None and message_node.text
                else "",
            }
        )
    return failures[:20]


def failure_diagnosis(
    response: dict[str, Any],
    editor_log: Path | None,
    results_xml: Path | None,
    summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    response_status = str(response.get("status") or "").lower()
    exit_code = response.get("exitCode")
    result = str((summary or {}).get("result") or "").lower()
    compile_failures = compiler_errors(editor_log)
    if compile_failures:
        first = compile_failures[0]
        return {
            "category": "compile",
            "title": "Script compilation",
            "message": f"{first['path']}:{first['line']}:{first['column']} {first['code']}: {first['message']}",
            "retryable": False,
            "testsStarted": bool(results_xml and results_xml.is_file()),
            "compilerErrors": compile_failures,
            "failedTests": [],
        }
    tests = failed_tests(results_xml)
    if tests or result in {"failed", "failure"}:
        first = tests[0] if tests else {"name": "Unity test run", "message": "one or more tests failed"}
        return {
            "category": "test",
            "title": "Test failure",
            "message": f"{first['name']}: {first['message']}".rstrip(": "),
            "retryable": False,
            "testsStarted": True,
            "compilerErrors": [],
            "failedTests": tests,
        }
    stderr = str(response.get("stderr") or "").strip()
    if response_status == "notestsmatched":
        return {
            "category": "test-discovery",
            "title": "No tests matched",
            "message": stderr or "No tests matched the requested filter.",
            "retryable": False,
            "testsStarted": False,
            "compilerErrors": [],
            "failedTests": [],
        }
    if response_status == "timedout" or exit_code == 124:
        return {
            "category": "infrastructure",
            "title": "Unity host timeout",
            "message": stderr or "Unity host operation timed out.",
            "retryable": True,
            "testsStarted": bool(results_xml and results_xml.is_file()),
            "compilerErrors": [],
            "failedTests": [],
        }
    if response_status == "stalerequest" or exit_code in {81, 82, 86, 89} or "Unity broker:" in stderr:
        return {
            "category": "infrastructure",
            "title": "Supervisor/Unity infrastructure failure",
            "message": stderr or f"Unity broker failed with exit code {exit_code}.",
            "retryable": True,
            "testsStarted": bool(results_xml and results_xml.is_file()),
            "compilerErrors": [],
            "failedTests": [],
        }
    if response_status == "failed" or (isinstance(exit_code, int) and exit_code != 0):
        return {
            "category": "unity-startup",
            "title": "Unity startup/runner failure",
            "message": stderr or f"Unity exited with code {exit_code} without a successful test result.",
            "retryable": True,
            "testsStarted": bool(results_xml and results_xml.is_file()),
            "compilerErrors": [],
            "failedTests": [],
        }
    return None


def normalized_status(response: dict[str, Any], summary: dict[str, Any] | None) -> str:
    raw = str(response.get("status") or "").lower()
    result = str((summary or {}).get("result") or "").lower()
    exit_code = response.get("exitCode")
    if raw == "timedout":
        return "timed_out"
    if raw == "notestsmatched":
        return "no_tests"
    if raw == "stalerequest":
        return "stale"
    if result in {"passed", "success", "successful"}:
        return "passed"
    if result in {"failed", "failure"}:
        return "failed"
    if raw == "completed" and exit_code == 0:
        return "passed"
    if raw == "failed" or (isinstance(exit_code, int) and exit_code != 0):
        return "failed"
    return raw or "unknown"


def unity_run_history(identifier: str, limit: int = 20) -> list[dict[str, Any]]:
    workspace = workspace_root() / identifier
    broker_dir = workspace / "Logs" / "SymphonyUnity" / ".broker"
    ack_dir = broker_dir / "acks"
    response_dir = broker_dir / "responses"
    if not workspace.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    for response_path in response_dir.glob("*.json"):
        response = load_json(response_path)
        if response is None:
            continue
        request_id = str(response.get("requestId") or response_path.stem)
        completed_ids.add(request_id)
        ack = load_json(ack_dir / f"{request_id}.json") or {}
        accepted_at = ack.get("acceptedAt")
        completed_at = response.get("completedAt")
        artifacts = artifact_directory(response, workspace)
        editor_log = artifacts / "Editor.log" if artifacts else None
        results_xml = artifacts / "results.xml" if artifacts else None
        summary_path = artifacts / "summary.json" if artifacts else None
        summary = response.get("summary") if isinstance(response.get("summary"), dict) else None
        if summary is None and summary_path is not None:
            summary = load_json(summary_path)
        item: dict[str, Any] = {
            "requestId": request_id,
            "runId": (summary or {}).get("runId"),
            "issue": identifier,
            "workspace": str(workspace),
            "operation": response.get("operation"),
            "testFilter": (summary or {}).get("testFilter") or test_filter_from_log(editor_log),
            "acceptedAt": accepted_at,
            "completedAt": completed_at,
            "durationSeconds": elapsed_seconds(accepted_at, completed_at),
            "status": normalized_status(response, summary),
            "rawStatus": response.get("status"),
            "exitCode": response.get("exitCode"),
            "summary": summary,
            "stderr": str(response.get("stderr") or "").strip(),
            "artifactDirectory": str(artifacts) if artifacts else None,
            "editorLogPath": str(editor_log) if editor_log and editor_log.exists() else None,
            "resultsPath": str(results_xml) if results_xml and results_xml.exists() else None,
            "summaryPath": str(summary_path) if summary_path and summary_path.exists() else None,
        }
        item["diagnosis"] = failure_diagnosis(response, editor_log, results_xml, summary)
        runs.append(item)
    for ack_path in ack_dir.glob("*.json"):
        if ack_path.stem in completed_ids:
            continue
        ack = load_json(ack_path)
        if ack is None:
            continue
        runs.append(
            {
                "requestId": str(ack.get("requestId") or ack_path.stem),
                "runId": None,
                "issue": identifier,
                "workspace": str(workspace),
                "operation": None,
                "testFilter": None,
                "acceptedAt": ack.get("acceptedAt"),
                "completedAt": None,
                "durationSeconds": None,
                "status": "acknowledged",
                "rawStatus": "acknowledged",
                "exitCode": None,
                "summary": None,
                "stderr": "",
                "artifactDirectory": None,
                "editorLogPath": None,
                "resultsPath": None,
                "summaryPath": None,
                "diagnosis": None,
            }
        )
    broker_status = load_json(state_root() / "unity-broker" / "status.json") or {}
    active = broker_status.get("activeRequest")
    if isinstance(active, dict) and active.get("issue") == identifier:
        request_id = str(active.get("requestId") or "")
        active_item = {
            "requestId": request_id,
            "runId": None,
            "issue": identifier,
            "workspace": str(workspace),
            "operation": active.get("operation"),
            "testFilter": active.get("testFilter"),
            "acceptedAt": active.get("startedAt"),
            "completedAt": None,
            "durationSeconds": active.get("elapsedSeconds"),
            "status": "running",
            "rawStatus": "running",
            "exitCode": None,
            "summary": None,
            "stderr": "",
            "artifactDirectory": None,
            "editorLogPath": None,
            "resultsPath": None,
            "summaryPath": None,
            "diagnosis": None,
        }
        existing = next((item for item in runs if item["requestId"] == request_id), None)
        if existing is None:
            runs.append(active_item)
        else:
            existing.update(active_item)
    runs.sort(
        key=lambda item: (
            (parse_timestamp(item.get("completedAt") or item.get("acceptedAt")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            str(item.get("requestId") or ""),
        ),
        reverse=True,
    )
    return runs[: max(1, limit)]


def collect(number: int) -> dict[str, Any]:
    identifier = f"GH-{number}"
    repo = os.environ.get("RPGK_REPO", DEFAULT_REPO)
    symphony = symphony_state(identifier)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "identifier": identifier,
        "github": issue_from_github(number, repo),
        "workspace": workspace_state(identifier),
        "unity_lock": unity_lock_state(),
        "unity_runs": unity_run_history(identifier),
        "symphony": symphony,
        "codex": rollout_state(symphony.get("thread_id")),
    }


def status(value: bool | None) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "UNKNOWN"


def render_text(data: dict[str, Any]) -> str:
    gh = data["github"]
    ws = data["workspace"]
    sx = data["symphony"]
    cx = data["codex"]
    unity = ws.get("latest_unity")
    lines = [f"{data['identifier']} diagnostics", ""]
    if gh.get("available"):
        lines += [
            "GitHub",
            f"  State:       {gh.get('state')}",
            f"  Labels:      {', '.join(gh.get('labels', [])) or '-'}",
            f"  Comments:    {gh.get('comment_count')}",
        ]
    else:
        lines += ["GitHub", f"  Error:       {gh.get('error')}"]
    lines += [
        "",
        "Workspace",
        f"  Exists:      {ws.get('exists')}",
        f"  Branch:      {ws.get('branch') or '-'}",
        f"  Attempt used:{' yes' if ws.get('attempt_complete') else ' no'}",
    ]
    if ws.get("git_status"):
        lines.extend(f"  Git:         {line}" for line in ws["git_status"].splitlines())
    lines += ["", "Symphony", f"  Session:     {sx.get('session_id') or '-'}", f"  Turn:        {sx.get('turn') or '-'}"]
    lines += ["", "Codex"]
    if cx.get("available"):
        lines.append(f"  Rollout:     {cx.get('path')}")
        tokens = cx.get("tokens") or {}
        usage = tokens.get("total_token_usage") if isinstance(tokens, dict) else None
        if isinstance(usage, dict):
            lines.append(f"  Tokens:      {usage.get('total_tokens', '-')}")
        context = cx.get("turn_context") or {}
        lines.append(f"  Legacy WS:   {status(context.get('mentions_workspace_write') is False)}")
        lines.append(f"  Failures:    {len(cx.get('failures', []))}")
        lines.extend(f"    - {failure[:240]}" for failure in cx.get("failures", [])[-4:])
    else:
        lines.append(f"  Status:      unavailable ({cx.get('reason')})")
    lines += ["", "Unity"]
    lock = data["unity_lock"]
    lines.append(f"  Lock held:   {lock.get('held')}")
    if lock.get("held"):
        lines.append(f"  Owner:       {lock.get('owner') or '-'}")
    if isinstance(unity, dict):
        lines.append(f"  Last result: {unity.get('result', '-')}")
        lines.append(f"  Platform:    {unity.get('testPlatform', '-')}")
        lines.append(f"  Run:         {unity.get('runId', '-')}")
    else:
        lines.append("  Last result: none")
    lines += ["", "Recent Unity runs"]
    runs = data.get("unity_runs") or []
    if not runs:
        lines.append("  none")
    for item in runs[:10]:
        duration = item.get("durationSeconds")
        duration_text = f"{duration:.1f}s" if isinstance(duration, (int, float)) else "-"
        lines.append(f"  {item.get('status', '-'):12} {item.get('operation') or '-':8} {duration_text:8} {item.get('requestId') or '-'}")
        if item.get("testFilter"):
            lines.append(f"    filter: {item['testFilter']}")
        diagnosis = item.get("diagnosis")
        if isinstance(diagnosis, dict):
            lines.append(f"    {diagnosis.get('title')}: {diagnosis.get('message')}")
    if gh.get("latest_comment"):
        lines += ["", "Latest issue comment", f"  {gh['latest_comment'][:1000]}"]
    return "\n".join(lines)


PAGE = """<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>RPG Kingdom Supervisor Diagnostics</title>
<style>
body{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;background:#111;color:#ddd;margin:0;padding:20px}h1{font:600 22px system-ui;margin:0 0 16px}.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px}input,button,select{background:#1d1d1d;color:#eee;border:1px solid #444;padding:8px 10px;border-radius:6px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:12px}.card{background:#191919;border:1px solid #333;border-radius:8px;padding:12px;min-height:120px}.card h2{font:600 15px system-ui;margin:0 0 10px;color:#fff}.row{display:grid;grid-template-columns:120px 1fr;gap:8px;margin:5px 0}.muted{color:#888}.bad{color:#ff8a80}.good{color:#9ccc65}pre{white-space:pre-wrap;word-break:break-word;font-size:12px}.wide{grid-column:1/-1}.run{border-top:1px solid #333;padding:10px 0}.run summary{cursor:pointer}.badge{display:inline-block;border:1px solid #555;border-radius:999px;padding:2px 7px;margin-right:6px;font-size:11px}.passed{color:#9ccc65}.failed,.timed_out{color:#ff8a80}.running{color:#80cbc4}.path{color:#aaa;font-size:11px}.diag{margin:8px 0;padding:8px;border-left:3px solid #ff8a80;background:#221919}</style>
</head><body><h1>RPG Kingdom Supervisor Diagnostics</h1>
<div class='bar'>GH-<input id='issue' value='97' size='5'><button onclick='load()'>Refresh</button><label>operation <select id='op' onchange='renderRuns()'><option value=''>all</option><option>health</option><option>editmode</option><option>playmode</option></select></label><label>status <select id='st' onchange='renderRuns()'><option value=''>all</option><option>running</option><option>passed</option><option>failed</option><option>timed_out</option><option>no_tests</option></select></label><span id='stamp' class='muted'></span></div>
<div id='grid' class='grid'></div>
<script>
let latest=null;function esc(v){return String(v??'-').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));}function rows(obj,keys){return keys.map(([k,l])=>`<div class='row'><span class='muted'>${esc(l)}</span><span>${esc(obj?.[k])}</span></div>`).join('');}function dur(v){return Number.isFinite(Number(v))?`${Number(v).toFixed(1)}s`:'-';}
function renderRun(r){const d=r.diagnosis||null,comp=(d?.compilerErrors||[]).map(x=>`<div>${esc(x.path)}:${esc(x.line)}:${esc(x.column)} ${esc(x.code)}: ${esc(x.message)}</div>`).join(''),tests=(d?.failedTests||[]).map(x=>`<div><b>${esc(x.name)}</b><pre>${esc(x.message)}</pre></div>`).join('');return `<details class='run'><summary><span class='badge ${esc(r.status)}'>${esc(r.status)}</span><span class='badge'>${esc(r.operation)}</span>${esc(r.requestId)} <span class='muted'>${dur(r.durationSeconds)}</span></summary>${d?`<div class='diag'><b>${esc(d.title)}</b><div>${esc(d.message)}</div><div class='muted'>category=${esc(d.category)} retryable=${esc(d.retryable)}</div>${comp}${tests}</div>`:''}${rows(r,[['testFilter','filter'],['acceptedAt','started'],['completedAt','completed'],['exitCode','exit code'],['artifactDirectory','artifacts']])}<div class='path'>Editor.log: ${esc(r.editorLogPath)}</div><div class='path'>results.xml: ${esc(r.resultsPath)}</div><div class='path'>summary.json: ${esc(r.summaryPath)}</div>${r.stderr?`<pre class='bad'>${esc(r.stderr)}</pre>`:''}</details>`;}
function renderRuns(){if(!latest)return;const op=document.getElementById('op').value,st=document.getElementById('st').value,runs=(latest.unity_runs||[]).filter(r=>(!op||r.operation===op)&&(!st||r.status===st)),el=document.getElementById('runs');if(el)el.innerHTML=runs.length?runs.map(renderRun).join(''):`<span class='muted'>no matching runs</span>`;}
async function load(){const n=document.getElementById('issue').value;try{const d=await fetch('/api/issue/'+encodeURIComponent(n),{cache:'no-store'}).then(r=>r.json());latest=d;document.getElementById('stamp').textContent='updated '+new Date().toLocaleTimeString();const g=d.github||{},w=d.workspace||{},s=d.symphony||{},c=d.codex||{},u=d.unity_lock||{},last=w.latest_unity||{};document.getElementById('grid').innerHTML=`<div class='card'><h2>GitHub</h2>${rows(g,[['state','state'],['labels','labels'],['comment_count','comments'],['updated_at','updated']])}</div><div class='card'><h2>Workspace</h2>${rows(w,[['branch','branch'],['head','head'],['attempt_complete','attempt used']])}<pre>${esc(w.git_status||'clean / unavailable')}</pre></div><div class='card'><h2>Symphony</h2>${rows(s,[['session_id','session'],['turn','turn'],['thread_id','thread']])}</div><div class='card'><h2>Codex</h2>${rows(c,[['model','model'],['path','rollout']])}<pre class='bad'>${esc((c.failures||[]).slice(-5).join('\n\n')||'no matched failures')}</pre></div><div class='card'><h2>Unity</h2>${rows(u,[['held','lock held'],['owner','owner']])}${rows(last,[['result','last result'],['testPlatform','platform'],['runId','run']])}</div><div class='card wide'><h2>Unity Run History</h2><div id='runs'></div></div><div class='card wide'><h2>Latest issue comment</h2><pre>${esc(g.latest_comment||'-')}</pre></div><div class='card wide'><h2>Recent Symphony lines</h2><pre>${esc((s.lines||[]).join('\n'))}</pre></div>`;renderRuns();}catch(e){document.getElementById('grid').innerHTML=`<div class='card bad'>${esc(e)}</div>`;}}load();setInterval(load,5000);
</script></body></html>"""


def serve(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            match = re.fullmatch(r"/api/issue/(\d+)", parsed.path)
            if match:
                body = json.dumps(collect(int(match.group(1)))).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"RPG Kingdom diagnostics: http://127.0.0.1:{port}")
    print("Ctrl+C to stop. The server is read-only and bound to localhost.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    issue_parser = sub.add_parser("issue")
    issue_parser.add_argument("number", type=int)
    issue_parser.add_argument("--json", action="store_true")
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "issue":
        data = collect(args.number)
        print(json.dumps(data, indent=2) if args.json else render_text(data))
        return 0
    if args.command == "serve":
        serve(args.port)
        return 0
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
