#!/usr/bin/env python3
"""Persist host-observable evidence for each Codex worker turn.

This module does not decide whether another turn should run. The existing
continuation policy owns that decision. It records the before/after evidence
needed to explain what each completed turn cost and accomplished.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402

POLICY_PATH = SCRIPT_DIR / "continuation-policy.py"
POLICY_SPEC = importlib.util.spec_from_file_location("rpgk_continuation_policy", POLICY_PATH)
if POLICY_SPEC is None or POLICY_SPEC.loader is None:
    raise RuntimeError(f"unable to load continuation policy: {POLICY_PATH}")
policy = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(policy)

PROTOCOL_VERSION = 1
START_NAME = ".symphony-turn-start.json"
HISTORY_NAME = ".symphony-turn-history.jsonl"
TOKEN_KEYS = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningTokens", "totalTokens")


def _ensure_local_excludes(workspace: Path) -> None:
    policy.ensure_local_excludes(workspace)
    probe = policy.run(["git", "rev-parse", "--git-path", "info/exclude"], workspace)
    if probe.returncode != 0 or not probe.stdout.strip():
        return
    path = Path(probe.stdout.strip())
    if not path.is_absolute():
        path = workspace / path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = set(existing.splitlines())
    with path.open("a", encoding="utf-8") as handle:
        for addition in (f"/{START_NAME}", f"/{HISTORY_NAME}"):
            if addition not in lines:
                handle.write(addition + "\n")


def _worker_run_id(workspace: Path, issue: str) -> str | None:
    worker = policy.active_worker(workspace, issue)
    value = (worker or {}).get("runId")
    return str(value) if value else None


def _snapshot(workspace: Path, issue: str) -> dict[str, Any]:
    return {
        "observedAt": policy.iso_now(),
        "quota": policy.refresh_quota(workspace),
        "usage": policy.usage_snapshot(workspace, issue),
        "workspace": policy.workspace_snapshot(workspace),
        "unity": policy.latest_unity(workspace),
    }


def _token_delta(before: dict[str, Any], after: dict[str, Any], turn: int) -> dict[str, Any]:
    if after.get("status") != "available":
        return {"status": "unavailable", "reason": after.get("reason") or "ending token sample unavailable"}
    if before.get("status") == "available":
        return {
            "status": "available",
            "basis": "cumulative before/after snapshots",
            **{
                key: max(0, int(after.get(key) or 0) - int(before.get(key) or 0))
                for key in TOKEN_KEYS
            },
        }
    if turn == 1:
        return {
            "status": "available",
            "basis": "first-turn cumulative worker-session usage; no attributable pre-turn rollout existed",
            **{key: max(0, int(after.get(key) or 0)) for key in TOKEN_KEYS},
        }
    return {"status": "unavailable", "reason": "pre-turn cumulative token sample unavailable"}


def _duration_seconds(started_at: Any, ended_at: Any) -> float | None:
    started = telemetry.parse_time(started_at)
    ended = telemetry.parse_time(ended_at)
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started).total_seconds())


def _decode_reason(value: str) -> tuple[str, dict[str, Any] | None]:
    text = value.strip()
    if not text:
        return "unspecified", None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text, None
    if isinstance(parsed, dict):
        return str(parsed.get("reason") or text), parsed
    return text, None


def start_turn(workspace: Path, issue: str, turn: int, max_turns: int, labels: list[str]) -> int:
    _ensure_local_excludes(workspace)
    snapshot = _snapshot(workspace, issue)
    route = policy.route_class(labels)
    record = {
        "protocolVersion": PROTOCOL_VERSION,
        "workerRunId": _worker_run_id(workspace, issue),
        "issue": issue,
        "turn": turn,
        "hardMaxTurns": max_turns,
        "route": route,
        "automaticTurnLimit": min(max_turns, policy.auto_turn_limit(route)),
        "startedAt": snapshot["observedAt"],
        "quota": snapshot["quota"],
        "usage": snapshot["usage"],
        "workspace": snapshot["workspace"],
        "unity": snapshot["unity"],
    }
    telemetry.atomic_json(workspace / START_NAME, record)
    telemetry.append_event(
        "worker_turn_started",
        workerRunId=record["workerRunId"],
        issue=issue,
        turn=turn,
        hardMaxTurns=max_turns,
        route=route,
        automaticTurnLimit=record["automaticTurnLimit"],
        quota=record["quota"],
    )
    print(json.dumps(record, separators=(",", ":")))
    return 0


def finish_turn(workspace: Path, issue: str, turn: int, decision: str, reason: str) -> int:
    _ensure_local_excludes(workspace)
    start_path = workspace / START_NAME
    started = telemetry.read_json(start_path)
    if not started:
        print("turn telemetry: missing turn-start snapshot", file=sys.stderr)
        return 70
    if started.get("issue") != issue or int(started.get("turn") or -1) != turn:
        print("turn telemetry: turn-start snapshot does not match finishing turn", file=sys.stderr)
        return 70

    ended = _snapshot(workspace, issue)
    human_reason, policy_record = _decode_reason(reason)
    before_workspace = started.get("workspace") or {}
    after_workspace = ended.get("workspace") or {}
    before_unity = started.get("unity")
    after_unity = ended.get("unity")
    progress = {
        "workspaceChanged": before_workspace.get("fingerprint") != after_workspace.get("fingerprint"),
        "unityChanged": bool(after_unity and (after_unity or {}).get("runId") != (before_unity or {}).get("runId")),
    }
    record = {
        "protocolVersion": PROTOCOL_VERSION,
        "workerRunId": started.get("workerRunId") or _worker_run_id(workspace, issue),
        "issue": issue,
        "turn": turn,
        "hardMaxTurns": started.get("hardMaxTurns"),
        "route": started.get("route"),
        "automaticTurnLimit": started.get("automaticTurnLimit"),
        "startedAt": started.get("startedAt"),
        "endedAt": ended["observedAt"],
        "durationSeconds": _duration_seconds(started.get("startedAt"), ended["observedAt"]),
        "decision": decision,
        "reason": human_reason,
        "usageBefore": started.get("usage") or {"status": "unavailable"},
        "usageAfter": ended["usage"],
        "tokenDelta": _token_delta(started.get("usage") or {}, ended["usage"], turn),
        "quotaBefore": started.get("quota") or {"status": "unavailable"},
        "quotaAfter": ended["quota"],
        "quotaDelta": telemetry.quota_delta(started.get("quota") or {}, ended["quota"]),
        "workspaceBefore": before_workspace,
        "workspaceAfter": after_workspace,
        "unityBefore": before_unity,
        "unityAfter": after_unity,
        "progress": progress,
        "continuationPolicy": policy_record,
    }
    telemetry.append_jsonl(workspace / HISTORY_NAME, record)
    telemetry.append_event("worker_turn_completed", **record)
    try:
        start_path.unlink()
    except FileNotFoundError:
        pass
    print(json.dumps(record, separators=(",", ":")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record model-free before/after telemetry for one Codex worker turn")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--workspace", required=True)
    start.add_argument("--issue", required=True)
    start.add_argument("--turn", required=True, type=int)
    start.add_argument("--max-turns", required=True, type=int)
    start.add_argument("--labels-json", default="[]")

    finish = sub.add_parser("finish")
    finish.add_argument("--workspace", required=True)
    finish.add_argument("--issue", required=True)
    finish.add_argument("--turn", required=True, type=int)
    finish.add_argument("--decision", required=True)
    finish.add_argument("--reason", default="")

    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"turn telemetry: workspace does not exist: {workspace}", file=sys.stderr)
        return 70
    if args.command == "start":
        return start_turn(workspace, args.issue, args.turn, args.max_turns, policy.parse_labels(args.labels_json))
    return finish_turn(workspace, args.issue, args.turn, args.decision, args.reason)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"turn telemetry error: {exc}", file=sys.stderr)
        raise SystemExit(70)
