#!/usr/bin/env python3
"""Route-aware Codex skill/MCP capability selection for RPG Kingdom workers.

This policy is intentionally host-owned. It never installs third-party software, never rewrites
RPG Kingdom AGENTS.md, and never broadens the Codex permission profile. It selects only approved
capabilities that are already present and records why an optional capability was omitted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import supervisor_telemetry as telemetry  # type: ignore  # noqa: E402

DEEP_ROUTES = {"terra", "sol", "astra"}
GRAPHIFY_SERVER = "rpgk_graphify"
INVESTIGATIVE_SKILL = "rpgk-investigate-bug"
EVALUATED_GRAPHIFY_VERSION = "0.9.58"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_mode(name: str, default: str = "auto") -> str:
    value = os.environ.get(name, default).strip().lower()
    aliases = {"true": "on", "1": "on", "yes": "on", "false": "off", "0": "off", "no": "off"}
    value = aliases.get(value, value)
    if value not in {"auto", "on", "off"}:
        raise RuntimeError(f"{name} must be auto, on, or off; got {value!r}")
    return value


def parse_labels(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"labels JSON is invalid: {exc}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("labels JSON must be an array")
    return [str(item).strip().lower() for item in parsed if str(item).strip()]


def default_repo_root() -> Path:
    return Path(os.environ.get("RPGK_GRAPHIFY_REPO_ROOT", str(Path.home() / "src/RPG-Kingdom"))).expanduser().resolve()


def graph_path(repo_root: Path) -> Path:
    configured = os.environ.get("RPGK_GRAPHIFY_GRAPH")
    return Path(configured).expanduser().resolve() if configured else repo_root / "graphify-out/graph.json"


def executable(name: str) -> str | None:
    if os.path.sep in name:
        path = Path(name).expanduser().resolve()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(name)


def run(command: list[str], cwd: Path | None = None, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)


def graphify_version(mcp_command: str | None) -> str | None:
    candidates: list[str] = []
    if mcp_command:
        sibling = str(Path(mcp_command).with_name("graphify"))
        if executable(sibling):
            candidates.append(sibling)
    path_graphify = executable("graphify")
    if path_graphify and path_graphify not in candidates:
        candidates.append(path_graphify)
    for command in candidates:
        try:
            result = run([command, "--version"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        text = (result.stdout or result.stderr).strip()
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        if result.returncode == 0 and match:
            return match.group(1)
    return None


def git_value(repo: Path, *args: str) -> str | None:
    result = run(["git", *args], cwd=repo)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def graph_freshness(repo_root: Path, graph: Path, workspace: Path | None = None) -> dict[str, Any]:
    if not graph.is_file():
        return {"status": "missing", "fresh": False, "reason": f"graph file not found: {graph}"}
    if not (repo_root / ".git").exists():
        return {
            "status": "unknown",
            "fresh": False,
            "reason": f"canonical graph repository is not a Git checkout: {repo_root}",
            "graphModifiedAtEpoch": graph.stat().st_mtime,
        }

    canonical_head = git_value(repo_root, "rev-parse", "HEAD")
    head_time = git_value(repo_root, "log", "-1", "--format=%ct")
    branch = git_value(repo_root, "branch", "--show-current")
    status = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo_root)
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else True
    if not canonical_head or not head_time:
        return {"status": "unknown", "fresh": False, "reason": "could not read canonical repository HEAD"}
    try:
        head_epoch = float(head_time)
    except ValueError:
        return {"status": "unknown", "fresh": False, "reason": "repository HEAD timestamp was invalid"}

    worker_main = None
    if workspace and (workspace / ".git").exists():
        worker_main = git_value(workspace, "rev-parse", "origin/main") or git_value(workspace, "rev-parse", "HEAD")

    graph_epoch = graph.stat().st_mtime
    reasons: list[str] = []
    if branch not in {"main", "master"}:
        reasons.append(f"canonical graph repository is on branch {branch or 'unknown'}, not main")
    if dirty:
        reasons.append("canonical graph repository has tracked working-tree changes")
    if graph_epoch < head_epoch:
        reasons.append("graph file predates canonical repository HEAD")
    if worker_main and canonical_head != worker_main:
        reasons.append("canonical graph repository HEAD does not match this worker's origin/main")

    fresh = not reasons
    return {
        "status": "fresh" if fresh else "stale",
        "fresh": fresh,
        "reason": None if fresh else "; ".join(reasons) + "; refresh the canonical checkout/Graphify before using it",
        "repoHead": canonical_head,
        "repoBranch": branch,
        "repoDirty": dirty,
        "workerOriginMain": worker_main,
        "repoHeadCommittedAtEpoch": head_epoch,
        "graphModifiedAtEpoch": graph_epoch,
    }


def skill_state(labels: list[str]) -> dict[str, Any]:
    supervisor_root = Path(os.environ.get("RPGK_SUPERVISOR_ROOT", str(SCRIPT_DIR.parent))).expanduser().resolve()
    path = supervisor_root / "skills" / INVESTIGATIVE_SKILL / "SKILL.md"
    selected = "risk:investigative" in set(labels)
    return {
        "name": INVESTIGATIVE_SKILL,
        "selected": selected,
        "available": path.is_file(),
        "path": str(path),
        "reason": "risk:investigative" if selected else "not an investigative issue",
    }


def graphify_state(route: str, workspace: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    mode = env_mode("RPGK_GRAPHIFY_ENABLED", "auto")
    selected_by_route = route in DEEP_ROUTES
    repo_root = default_repo_root()
    graph = graph_path(repo_root)
    command_name = os.environ.get("RPGK_GRAPHIFY_MCP_COMMAND", "graphify-mcp").strip() or "graphify-mcp"
    command_path = executable(command_name)
    installed_version = graphify_version(command_path)
    expected_version = os.environ.get("RPGK_GRAPHIFY_EXPECTED_VERSION", EVALUATED_GRAPHIFY_VERSION).strip()
    version_ok = installed_version == expected_version
    freshness = graph_freshness(repo_root, graph, workspace)
    allow_stale = env_mode("RPGK_GRAPHIFY_ALLOW_STALE", "off") == "on"
    available = bool(command_path and version_ok and graph.is_file() and (freshness.get("fresh") or allow_stale))
    requested = mode == "on" or (mode == "auto" and selected_by_route)

    if mode == "off":
        reason = "disabled by RPGK_GRAPHIFY_ENABLED"
    elif not selected_by_route and mode == "auto":
        reason = "route does not request Graphify"
    elif command_path is None:
        reason = f"Graphify MCP executable not found: {command_name}"
    elif installed_version is None:
        reason = "Graphify version could not be determined"
    elif not version_ok:
        reason = f"Graphify version {installed_version} does not match evaluated version {expected_version}"
    elif not graph.is_file():
        reason = f"Graphify graph not found: {graph}"
    elif not freshness.get("fresh") and not allow_stale:
        reason = freshness.get("reason") or "Graphify graph is stale"
    else:
        reason = "available and selected"

    state = {
        "name": "graphify",
        "mcpServer": GRAPHIFY_SERVER,
        "mode": mode,
        "selectedByRoute": selected_by_route,
        "requested": requested,
        "available": available,
        "enabled": requested and available,
        "reason": reason,
        "command": command_path or command_name,
        "installedVersion": installed_version,
        "expectedVersion": expected_version,
        "graph": str(graph),
        "canonicalRepo": str(repo_root),
        "freshness": freshness,
        "allowStale": allow_stale,
        "readOnly": True,
    }

    if mode == "on" and requested and not available:
        raise RuntimeError(f"Graphify was explicitly required but is unavailable: {reason}")
    if not state["enabled"]:
        return state, []

    args = [
        "--config", f"mcp_servers.{GRAPHIFY_SERVER}.command={json.dumps(str(command_path))}",
        "--config", f"mcp_servers.{GRAPHIFY_SERVER}.args={json.dumps([str(graph)])}",
        "--config", f"mcp_servers.{GRAPHIFY_SERVER}.startup_timeout_sec=10",
        "--config", f"mcp_servers.{GRAPHIFY_SERVER}.tool_timeout_sec=30",
    ]
    return state, args


def route_payload(route: str, issue: str, workspace: Path, labels: list[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    if route not in {"luna", "terra", "sol", "astra"}:
        raise RuntimeError(f"unsupported route: {route}")
    normalized_labels = [item.lower() for item in (labels or [])]
    skill = skill_state(normalized_labels)
    if skill["selected"] and not skill["available"]:
        raise RuntimeError(f"selected first-party skill is missing: {skill['path']}")
    graphify, args = graphify_state(route, workspace)
    payload = {
        "protocolVersion": 1,
        "observedAt": iso_now(),
        "issue": issue,
        "workspace": str(workspace.resolve()),
        "route": route,
        "riskLabels": [item for item in normalized_labels if item.startswith("risk:")],
        "selectedSkills": [skill["name"]] if skill["selected"] else [],
        "skillCatalog": [skill],
        "mcp": [graphify],
        "codexConfigArgCount": len(args) // 2,
    }
    return telemetry.sanitize(payload), args


def persist(payload: dict[str, Any]) -> Path:
    identifier = str(payload.get("issue") or "unknown")
    path = telemetry.state_root() / "capabilities" / f"{identifier}.json"
    telemetry.atomic_json(path, payload)
    telemetry.append_event(
        "worker_capabilities_selected",
        issue=payload.get("issue"), route=payload.get("route"),
        selectedSkills=payload.get("selectedSkills"), mcp=payload.get("mcp"),
    )
    return path


def write_args(path: Path, args: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for value in args:
            handle.write(value.encode("utf-8"))
            handle.write(b"\0")


def status_payload() -> dict[str, Any]:
    routes = {}
    for route in ("luna", "terra", "sol", "astra"):
        labels = ["risk:investigative"] if route == "terra" else []
        skill = skill_state(labels)
        try:
            graphify, _ = graphify_state(route, None)
        except RuntimeError as exc:
            graphify = {"name": "graphify", "enabled": False, "available": False, "reason": str(exc)}
        routes[route] = {"skill": skill, "graphify": graphify}
    codex = executable("codex")
    version = None
    if codex:
        proc = run([codex, "--version"], timeout=10)
        version = proc.stdout.strip() or proc.stderr.strip()
    return telemetry.sanitize({"observedAt": iso_now(), "codex": {"executable": codex, "version": version}, "routes": routes})


def main() -> int:
    parser = argparse.ArgumentParser(description="Select approved route-specific Codex capabilities")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--pretty", action="store_true")
    route = sub.add_parser("route")
    route.add_argument("--route", required=True, choices=("luna", "terra", "sol", "astra"))
    route.add_argument("--issue", required=True)
    route.add_argument("--workspace", required=True)
    route.add_argument("--labels-json", default="[]")
    route.add_argument("--args-file", required=True)
    route.add_argument("--state-file")
    args = parser.parse_args()

    try:
        if args.command == "status":
            print(json.dumps(status_payload(), indent=2 if args.pretty else None, sort_keys=True))
            return 0
        workspace = Path(args.workspace).expanduser().resolve()
        payload, codex_args = route_payload(args.route, args.issue, workspace, parse_labels(args.labels_json))
        persisted = persist(payload)
        if args.state_file:
            telemetry.atomic_json(Path(args.state_file).expanduser().resolve(), payload)
        write_args(Path(args.args_file).expanduser().resolve(), codex_args)
        enabled = [item["name"] for item in payload["mcp"] if item.get("enabled")]
        print(
            f"RPG Kingdom capabilities: route={args.route} skills={','.join(payload['selectedSkills']) or '-'} "
            f"mcp={','.join(enabled) or '-'} state={persisted}", file=sys.stderr,
        )
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"RPG Kingdom capability policy: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
