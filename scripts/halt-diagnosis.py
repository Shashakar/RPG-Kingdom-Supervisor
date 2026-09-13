#!/usr/bin/env python3
"""Build a durable, evidence-based diagnosis for an unfinished worker lifetime."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402

STATUS_PATH = SCRIPT_DIR / "worker-status.py"
STATUS_SPEC = importlib.util.spec_from_file_location("rpgk_worker_status", STATUS_PATH)
if STATUS_SPEC is None or STATUS_SPEC.loader is None:
    raise RuntimeError(f"unable to load worker status module: {STATUS_PATH}")
worker_status = importlib.util.module_from_spec(STATUS_SPEC)
STATUS_SPEC.loader.exec_module(worker_status)

TURN_HISTORY = ".symphony-turn-history.jsonl"
CONTINUATION_MARKER = ".symphony-continuation-stop.json"
USAGE_MARKER = ".symphony-usage-limit.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_turn(workspace: Path) -> dict[str, Any] | None:
    path = workspace / TURN_HISTORY
    latest: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    latest = value
    except OSError:
        return None
    return latest


def _workspace_snapshot(workspace: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=workspace, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    status = run("status", "--short")
    return {
        "branch": run("branch", "--show-current") or None,
        "head": run("rev-parse", "HEAD") or None,
        "dirty": bool(status),
        "changedFiles": [line[3:] if len(line) > 3 else line for line in status.splitlines() if line.strip()][:50],
    }


def _latest_unity(latest_turn: dict[str, Any] | None, continuation: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = []
    if continuation:
        candidates.append(continuation.get("unity"))
    if latest_turn:
        candidates.extend([latest_turn.get("unityAfter"), latest_turn.get("continuationPolicy", {}).get("unity") if isinstance(latest_turn.get("continuationPolicy"), dict) else None])
    for value in candidates:
        if isinstance(value, dict) and value:
            return value
    return None


def _deterministic_stop(kind: str, latest_turn: dict[str, Any] | None, continuation: dict[str, Any], usage: dict[str, Any]) -> tuple[str, str]:
    if kind == "usage_limit_exceeded":
        message = str(usage.get("message") or "Codex reported usage_limit_exceeded")
        return "usage_limit_exceeded", message
    if kind == "continuation_policy":
        return "continuation_policy", str(continuation.get("reason") or "dynamic continuation policy declined another turn")

    if latest_turn:
        try:
            turn = int(latest_turn.get("turn") or 0)
            hard_max = int(latest_turn.get("hardMaxTurns") or 0)
        except (TypeError, ValueError):
            turn, hard_max = 0, 0
        if hard_max > 0 and turn >= hard_max:
            return "hard_turn_ceiling", f"worker reached the hard turn ceiling ({turn}/{hard_max}) before completing handoff"
        decision = str(latest_turn.get("decision") or "")
        if decision and decision not in {"continue", "completed", "handoff"}:
            return "worker_lifetime_ended", f"worker ended after turn {turn or '?'} with decision {decision!r} and no completed handoff"
    return "worker_lifetime_ended", "worker lifetime ended while the dispatch lease remained and no trusted completion/handoff was observed"


def _render_markdown(payload: dict[str, Any]) -> str:
    sup = payload["supervisor"]
    task = payload.get("taskStatus")
    latest_turn = payload.get("latestTurn") or {}
    latest_unity = payload.get("latestUnity") or {}
    workspace = payload.get("workspace") or {}
    lines = [
        "## Symphony halt diagnosis",
        "",
        f"**Supervisor stop:** `{sup['classification']}` — {sup['reason']}",
    ]
    if latest_turn:
        lines.append(f"**Turn:** {latest_turn.get('turn', '?')}/{latest_turn.get('hardMaxTurns', '?')}")
    if latest_unity:
        run_id = latest_unity.get("runId") or latest_unity.get("requestId") or "unknown"
        result = latest_unity.get("result") or latest_unity.get("status") or "unknown"
        lines.append(f"**Latest Unity evidence:** `{run_id}` — `{result}`")
    if workspace.get("branch"):
        dirty = "dirty" if workspace.get("dirty") else "clean"
        lines.append(f"**Workspace:** `{workspace['branch']}` ({dirty})")

    lines.append("")
    if task:
        lines.extend([
            f"**Task status:** `{task.get('classification', 'unknown')}` — {task.get('summary', 'No summary supplied.')}",
            f"**Manual action required:** {'yes' if task.get('manualActionRequired') else 'no'}",
        ])
        if task.get("blockedBy"):
            lines.append(f"**Blocked by:** {task['blockedBy']}")
        remaining = [str(item) for item in task.get("remainingAcceptanceCriteria") or [] if str(item).strip()]
        if remaining:
            lines.extend(["", "**Remaining acceptance criteria:**"])
            lines.extend([f"- {item}" for item in remaining])
        if task.get("recommendedNextAction"):
            lines.extend(["", f"**Recommended next action:** {task['recommendedNextAction']}"])
    else:
        lines.extend([
            "**Task status:** unavailable — the worker did not leave a fresh structured task-status record for this lifetime.",
            "**Manual action required:** unknown",
        ])
        if payload.get("taskStatusUnavailableReason"):
            lines.append(f"**Evidence gap:** {payload['taskStatusUnavailableReason']}")
        lines.extend(["", "**Recommended next action:** inspect the preserved workspace/latest validation before deciding whether to rearm; do not assume the task merely needs more turns."])

    if workspace.get("changedFiles"):
        lines.extend(["", "**Preserved changed files:**"])
        lines.extend([f"- `{item}`" for item in workspace["changedFiles"][:20]])
    return "\n".join(lines).strip() + "\n"


def collect(workspace: Path, issue: int, expected_boundary: str, kind: str, *, state_root: Path | None = None) -> dict[str, Any]:
    continuation = _read_json(workspace / CONTINUATION_MARKER)
    usage = _read_json(workspace / USAGE_MARKER)
    latest_turn = _latest_turn(workspace)
    task_status, unavailable_reason = worker_status.read_fresh_status(workspace, issue, expected_boundary)
    classification, reason = _deterministic_stop(kind, latest_turn, continuation, usage)
    payload: dict[str, Any] = {
        "protocolVersion": 1,
        "observedAt": telemetry.iso_now(),
        "issue": issue,
        "supervisor": {"classification": classification, "reason": reason},
        "taskStatus": task_status,
        "taskStatusUnavailableReason": unavailable_reason,
        "latestTurn": latest_turn,
        "latestUnity": _latest_unity(latest_turn, continuation),
        "workspace": _workspace_snapshot(workspace),
        "attemptBoundary": str(expected_boundary),
    }
    payload["markdown"] = _render_markdown(payload)
    payload = telemetry.sanitize(payload)
    root = state_root or telemetry.state_root()
    telemetry.atomic_json(root / "halt-diagnostics" / f"GH-{issue}.json", payload)
    telemetry.append_event(
        "worker_halt_diagnosed",
        issue=issue,
        supervisorClassification=classification,
        supervisorReason=reason,
        taskClassification=(task_status or {}).get("classification"),
        manualActionRequired=(task_status or {}).get("manualActionRequired"),
        latestUnity=payload.get("latestUnity"),
        workspace=payload.get("workspace"),
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive and persist a structured Supervisor halt diagnosis")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--attempt-boundary", required=True)
    parser.add_argument("--kind", choices=["worker_lifetime_ended", "continuation_policy", "usage_limit_exceeded"], required=True)
    parser.add_argument("--state-root")
    parser.add_argument("--markdown-only", action="store_true")
    args = parser.parse_args()
    payload = collect(
        Path(args.workspace).expanduser().resolve(),
        args.issue,
        args.attempt_boundary,
        args.kind,
        state_root=Path(args.state_root).expanduser().resolve() if args.state_root else None,
    )
    if args.markdown_only:
        print(payload["markdown"], end="")
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
