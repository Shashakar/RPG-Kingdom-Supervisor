#!/usr/bin/env python3
"""Durable, secret-safe operations telemetry for RPG Kingdom Supervisor."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from typing import Any, Iterable

PROTOCOL_VERSION = 1
DEFAULT_STATE_ROOT = Path.home() / ".local/state/rpg-kingdom-supervisor"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
ISSUE_RE = re.compile(r"^GH-(\d+)$")
LIFECYCLE_PREFIXES = ("symphony:", "completion:", "validation:", "risk:", "model:", "effort:")
SENSITIVE_KEYS = {
    "authorization", "authentication", "accesstoken", "refreshtoken", "idtoken",
    "apikey", "password", "secret", "clientsecret", "githubtoken",
    "symphonygithubtoken", "openaikey", "credential", "credentials",
}


def state_root() -> Path:
    return Path(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", str(DEFAULT_STATE_ROOT))).expanduser().resolve()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(DEFAULT_CODEX_HOME))).expanduser().resolve()


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


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _known_secrets() -> list[str]:
    return [
        value
        for name in ("SYMPHONY_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY")
        if (value := os.environ.get(name, ""))
    ]


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            text_key = str(key)
            normalized = _normalized_key(text_key)
            if normalized in SENSITIVE_KEYS or any(term in normalized for term in ("authorization", "password", "clientsecret")):
                result[text_key] = "[REDACTED]"
            else:
                result[text_key] = sanitize(child)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        text = re.sub(
            r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}",
            "Bearer [REDACTED]",
            value,
            flags=re.IGNORECASE,
        )
        for secret in _known_secrets():
            text = text.replace(secret, "[REDACTED]")
        return text
    return value


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(sanitize(payload), handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sanitize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def append_event(event_type: str, **fields: Any) -> None:
    append_jsonl(
        state_root() / "telemetry" / "events.jsonl",
        {"protocolVersion": PROTOCOL_VERSION, "eventType": event_type, "observedAt": iso_now(), **fields},
    )


def process_alive(pid: Any) -> bool:
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    try:
        os.kill(number, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def service_status_path(service: str) -> Path:
    paths = {
        "symphony": state_root() / "symphony" / "status.json",
        "review": state_root() / "review-orchestrator" / "status.json",
        "unity": state_root() / "unity-broker" / "status.json",
        "git": state_root() / "git-broker" / "status.json",
    }
    if service not in paths:
        raise ValueError(f"unsupported service: {service}")
    return paths[service]


def write_service_status(service: str, state: str, pid: int | None = None, exit_code: int | None = None) -> dict[str, Any]:
    path = service_status_path(service)
    prior = read_json(path)
    now = iso_now()
    payload: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "service": service,
        "state": state,
        "pid": pid if pid is not None else prior.get("pid"),
        "updatedAt": now,
    }
    if state == "starting":
        payload["startedAt"] = now
    elif prior.get("startedAt"):
        payload["startedAt"] = prior["startedAt"]
    if exit_code is not None:
        payload["exitCode"] = exit_code
    if state in {"stopped", "failed"}:
        payload["completedAt"] = now
    atomic_json(path, payload)
    append_event("service_state", service=service, state=state, pid=payload.get("pid"), exitCode=exit_code)
    return payload


def _seconds_since(value: Any) -> float | None:
    parsed = parse_time(value)
    return max(0.0, (utc_now() - parsed).total_seconds()) if parsed else None


def normalize_service(service: str) -> dict[str, Any]:
    raw = read_json(service_status_path(service))
    if not raw:
        return {"service": service, "health": "stopped", "state": "missing", "reason": "no status record", "pid": None}
    pid = raw.get("pid")
    alive = process_alive(pid)
    state = str(raw.get("state") or "unknown")
    health = "unknown"
    reason = None
    if state in {"stopped", "failed"}:
        health, reason = "stopped", f"service recorded state={state}"
    elif pid is not None and not alive:
        health, reason = "stopped", "recorded PID is not alive"
    elif service == "review":
        if state == "blocked":
            health, reason = "blocked", raw.get("lastError") or "review service is blocked"
        elif state == "degraded":
            health, reason = "degraded", raw.get("lastError") or "review poll is backing off"
        elif state in {"starting", "polling", "ready"} and alive:
            health = "healthy"
            age = _seconds_since(raw.get("lastSuccessfulPoll"))
            if state == "ready" and age is not None and age > 180:
                health, reason = "degraded", f"last successful poll was {int(age)}s ago"
    elif service in {"unity", "git"}:
        if state == "running" and alive:
            health = "busy"
        elif state == "ready" and alive:
            health = "healthy"
        elif state in {"blocked", "degraded"} and alive:
            health, reason = state, raw.get("lastError") or raw.get("reason")
        elif alive:
            reason = f"unrecognized broker state={state}"
    elif service == "symphony":
        if state in {"running", "starting"} and alive:
            health = "healthy"
        elif not alive:
            health, reason = "stopped", "Symphony launcher is not alive"
    return sanitize({"service": service, "health": health, "state": state, "alive": alive, "reason": reason, **raw})


def current_quota() -> dict[str, Any]:
    payload = read_json(state_root() / "usage" / "current.json")
    return sanitize(payload) if payload else {
        "status": "unavailable",
        "reason": "no authoritative Codex rate-limit snapshot has been recorded",
    }


def _worker_active_dir() -> Path:
    return state_root() / "workers" / "active"


def _worker_history_dir() -> Path:
    return state_root() / "workers" / "history"


def infer_issue(workspace: Path | None = None) -> tuple[int, str]:
    workspace = (workspace or Path.cwd()).resolve()
    match = ISSUE_RE.fullmatch(workspace.name)
    if match is None:
        raise RuntimeError(f"workspace '{workspace}' is not GH-<issue>")
    return int(match.group(1)), f"GH-{match.group(1)}"


def worker_role_from_labels(labels: Iterable[str]) -> str:
    lowered = {item.lower() for item in labels}
    if "symphony:rework" in lowered:
        return "repair"
    if "completion:report-only" in lowered:
        return "report-only"
    return "implementation"


def worker_start(
    role: str,
    model: str,
    effort: str,
    route: str,
    workspace: Path | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    workspace = (workspace or Path.cwd()).resolve()
    issue_number, identifier = infer_issue(workspace)
    run_id = f"{identifier}-{role}-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    payload = {
        "protocolVersion": PROTOCOL_VERSION,
        "runId": run_id,
        "issue": issue_number,
        "identifier": identifier,
        "role": role,
        "model": model or None,
        "effort": effort or None,
        "route": route or None,
        "workspace": str(workspace),
        "pid": pid if pid is not None else os.getpid(),
        "startedAt": iso_now(),
        "quotaBefore": current_quota(),
    }
    active = _worker_active_dir() / f"{role}.json"
    existing = read_json(active)
    if existing and process_alive(existing.get("pid")):
        raise RuntimeError(f"active {role} worker already recorded: {existing.get('runId')}")
    atomic_json(active, payload)
    append_event("worker_started", **payload)
    return payload


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _token_usage_from_info(info: Any) -> dict[str, int] | None:
    if not isinstance(info, dict):
        return None
    usage = info.get("total_token_usage") or info.get("totalTokenUsage") or info.get("total") or info
    if not isinstance(usage, dict):
        return None

    def integer(*names: str) -> int:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    result = {
        "inputTokens": integer("input_tokens", "inputTokens"),
        "cachedInputTokens": integer("cached_input_tokens", "cachedInputTokens"),
        "outputTokens": integer("output_tokens", "outputTokens"),
        "reasoningTokens": integer("reasoning_output_tokens", "reasoningOutputTokens", "reasoning_tokens", "reasoningTokens"),
        "totalTokens": integer("total_tokens", "totalTokens"),
    }
    if not result["totalTokens"]:
        result["totalTokens"] = result["inputTokens"] + result["outputTokens"]
    return result if any(result.values()) else None


def find_rollout_usage(workspace: Path, started_at: Any) -> dict[str, Any]:
    sessions = codex_home() / "sessions"
    if not sessions.is_dir():
        return {"status": "unavailable", "reason": "Codex sessions directory is unavailable"}
    started = parse_time(started_at)
    min_mtime = started.timestamp() - 120 if started else 0
    candidates: list[Path] = []
    for path in sessions.glob("**/rollout-*.jsonl"):
        try:
            if path.stat().st_mtime >= min_mtime:
                candidates.append(path)
        except OSError:
            continue
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    workspace_text = str(workspace)
    fallback: list[tuple[Path, dict[str, int], str | None]] = []
    for path in candidates[:50]:
        latest_usage = None
        thread_id = None
        matched_workspace = False
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if workspace_text in "\n".join(_walk_strings(record)):
                        matched_workspace = True
                    payload = record.get("payload")
                    if isinstance(payload, dict):
                        if payload.get("type") == "session_meta":
                            thread_id = str(payload.get("id") or payload.get("thread_id") or "") or thread_id
                        elif payload.get("type") == "token_count":
                            parsed = _token_usage_from_info(payload.get("info"))
                            if parsed:
                                latest_usage = parsed
            if latest_usage:
                if matched_workspace:
                    return {"status": "available", "source": str(path), "threadId": thread_id, **latest_usage}
                fallback.append((path, latest_usage, thread_id))
        except OSError:
            continue
    if len(fallback) == 1:
        path, usage, thread_id = fallback[0]
        return {"status": "available", "source": str(path), "threadId": thread_id, "attribution": "single-session-window", **usage}
    return {"status": "unavailable", "reason": "no uniquely attributable Codex token usage record was found"}


def _quota_window(snapshot: dict[str, Any], name: str) -> dict[str, Any] | None:
    rate = snapshot.get("rateLimits") if isinstance(snapshot, dict) else None
    value = rate.get(name) if isinstance(rate, dict) else None
    return value if isinstance(value, dict) else None


def quota_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if before.get("status") != "available" or after.get("status") != "available":
        return {"status": "unavailable", "reason": "authoritative before/after quota samples are not both available"}
    if before.get("accountId") and after.get("accountId") and before.get("accountId") != after.get("accountId"):
        return {"status": "unavailable", "reason": "quota samples belong to different accounts"}
    result: dict[str, Any] = {"status": "available"}
    found = False
    for name in ("primary", "secondary"):
        first, second = _quota_window(before, name), _quota_window(after, name)
        if not first or not second:
            result[name] = None
            continue
        a, b = first.get("remainingPercent"), second.get("remainingPercent")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            result[name] = {
                "beforeRemainingPercent": a,
                "afterRemainingPercent": b,
                "remainingPercentagePointDelta": b - a,
            }
            found = True
        else:
            result[name] = None
    return result if found else {"status": "unavailable", "reason": "quota percentages are unavailable in one or both samples"}


def infer_github_lifecycle(issue_number: int) -> tuple[str, list[str]]:
    repo = os.environ.get("RPGK_REPO", "Shashakar/RPG-Kingdom")
    try:
        proc = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json", "labels", "--jq", ".labels[].name"],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown", []
    if proc.returncode != 0:
        return "unknown", []
    labels = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    lowered = {item.lower() for item in labels}
    order = (
        ("symphony:report-complete", "report-complete"),
        ("symphony:human-review", "human-review"),
        ("symphony:agent-review", "agent-review"),
        ("symphony:rework", "rework"),
        ("symphony:human-attention", "human-attention"),
        ("symphony:halted", "halted"),
        ("symphony:ready", "ready"),
    )
    outcome = next((state for label, state in order if label in lowered), "completed-or-idle")
    safe_labels = [item for item in labels if item.lower().startswith(LIFECYCLE_PREFIXES)]
    return outcome, safe_labels


def _find_active(role: str | None, issue_number: int) -> tuple[Path, dict[str, Any]]:
    candidates = [_worker_active_dir() / f"{role}.json"] if role else sorted(_worker_active_dir().glob("*.json"))
    matches = []
    for path in candidates:
        payload = read_json(path)
        if payload and payload.get("issue") == issue_number:
            matches.append((path, payload))
    if len(matches) != 1:
        raise RuntimeError(f"expected one active worker record, found {len(matches)}")
    return matches[0]


def worker_end(
    role: str | None = None,
    workspace: Path | None = None,
    outcome: str | None = None,
    outcome_file: Path | None = None,
    infer_lifecycle: bool = False,
) -> dict[str, Any]:
    workspace = (workspace or Path.cwd()).resolve()
    issue_number, _ = infer_issue(workspace)
    active_path, payload = _find_active(role, issue_number)
    started = parse_time(payload.get("startedAt"))
    resolved_outcome = outcome
    if outcome_file and outcome_file.is_file():
        try:
            value = json.loads(outcome_file.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                resolved_outcome = str(value.get("verdict") or value.get("outcome") or resolved_outcome or "unknown")
        except (OSError, json.JSONDecodeError):
            pass
    lifecycle_labels: list[str] = []
    if infer_lifecycle:
        lifecycle_outcome, lifecycle_labels = infer_github_lifecycle(issue_number)
        if resolved_outcome in (None, "", "unknown"):
            resolved_outcome = lifecycle_outcome
    complete = {
        **payload,
        "endedAt": iso_now(),
        "durationSeconds": (utc_now() - started).total_seconds() if started else None,
        "outcome": resolved_outcome or "unknown",
        "lifecycleLabels": lifecycle_labels,
        "tokenUsage": find_rollout_usage(workspace, payload.get("startedAt")),
        "quotaAfter": current_quota(),
    }
    complete["quotaDelta"] = quota_delta(payload.get("quotaBefore") or {}, complete["quotaAfter"])
    complete = sanitize(complete)
    atomic_json(_worker_history_dir() / f"{complete['runId']}.json", complete)
    append_jsonl(state_root() / "workers" / "history.jsonl", complete)
    append_event("worker_completed", **complete)
    try:
        active_path.unlink()
    except FileNotFoundError:
        pass
    return complete


def active_workers() -> list[dict[str, Any]]:
    items = []
    for path in sorted(_worker_active_dir().glob("*.json")):
        value = read_json(path)
        if value:
            value["alive"] = process_alive(value.get("pid"))
            items.append(sanitize(value))
    return items


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items = []
    for raw in lines[-max(1, limit):]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            items.append(sanitize(value))
    items.reverse()
    return items


def recent_workers(limit: int = 20) -> list[dict[str, Any]]:
    return _read_jsonl_tail(state_root() / "workers" / "history.jsonl", limit)


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    return _read_jsonl_tail(state_root() / "telemetry" / "events.jsonl", limit)


def collect_operations() -> dict[str, Any]:
    return sanitize({
        "protocolVersion": PROTOCOL_VERSION,
        "generatedAt": iso_now(),
        "services": {name: normalize_service(name) for name in ("symphony", "review", "unity", "git")},
        "quota": current_quota(),
        "activeWorkers": active_workers(),
        "recentWorkers": recent_workers(),
        "recentEvents": recent_events(),
    })


def cli() -> int:
    parser = argparse.ArgumentParser(description="RPG Kingdom Supervisor telemetry")
    sub = parser.add_subparsers(dest="command", required=True)
    service = sub.add_parser("service-write")
    service.add_argument("--service", choices=("symphony",), required=True)
    service.add_argument("--state", required=True)
    service.add_argument("--pid", type=int)
    service.add_argument("--exit-code", type=int)
    start = sub.add_parser("worker-start")
    start.add_argument("--role", choices=("implementation", "repair", "review", "report-only"), required=True)
    start.add_argument("--model", default="")
    start.add_argument("--effort", default="")
    start.add_argument("--route", default="")
    start.add_argument("--workspace")
    start.add_argument("--pid", type=int)
    end = sub.add_parser("worker-end")
    end.add_argument("--role", choices=("implementation", "repair", "review", "report-only"))
    end.add_argument("--workspace")
    end.add_argument("--outcome")
    end.add_argument("--outcome-file")
    end.add_argument("--infer-lifecycle", action="store_true")
    collect = sub.add_parser("collect")
    collect.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "service-write":
            result = write_service_status(args.service, args.state, args.pid, args.exit_code)
        elif args.command == "worker-start":
            result = worker_start(
                args.role, args.model, args.effort, args.route,
                Path(args.workspace) if args.workspace else None,
                args.pid,
            )
        elif args.command == "worker-end":
            result = worker_end(
                args.role,
                Path(args.workspace) if args.workspace else None,
                args.outcome,
                Path(args.outcome_file) if args.outcome_file else None,
                args.infer_lifecycle,
            )
        else:
            result = collect_operations()
        print(json.dumps(result, indent=2 if getattr(args, "pretty", False) else None, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"RPG Kingdom Supervisor telemetry: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
