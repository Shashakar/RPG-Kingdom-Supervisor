#!/usr/bin/env python3
"""Submit one typed Unity broker request and poll its durable terminal response.

No subprocess or PID is created in Codex's ephemeral execution namespace.
A live, unambiguous Supervisor worker lease is required before submission.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

OPS = {"inspect", "capture", "health", "editmode", "playmode"}
SCENE = re.compile(r"^Assets/.*\.unity$")
REQUEST = re.compile(r"^GH-[0-9]+-(?:inspect|capture|health|editmode|playmode)-[A-Za-z0-9-]+$")

def read(path):
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except FileNotFoundError:
        return None

def write(path, payload):
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(payload) + "\n", encoding="utf8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def live(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        # Host-owned leases use host PIDs; do not trust an arbitrary Codex PID.
        os.kill(pid, 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True

def inspect_leases(state_root, workspace):
    """Diagnostic only: never substitute a historical record for live ownership."""
    active_dir = state_root / "workers" / "active"
    matches = []
    other = 0
    if not active_dir.is_dir():
        return {"reason": "active_directory_missing", "stateRoot": str(state_root),
                "activeDirectory": str(active_dir), "matching": []}
    for path in sorted(active_dir.glob("*.json")):
        try:
            lease = read(path)
            if not isinstance(lease, dict):
                matches.append({"file": path.name, "reason": "invalid_record"})
                continue
            recorded = Path(str(lease.get("workspace") or "")).resolve()
            if recorded != workspace:
                other += 1
                continue
            pid = lease.get("pid")
            alive = live(pid)
            matches.append({"file": path.name, "runId": lease.get("runId"),
                            "pid": pid, "pidVisible": alive,
                            "reason": ("live" if alive and lease.get("runId")
                                       else "pid_not_visible_or_dead" if not alive
                                       else "run_id_missing")})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            matches.append({"file": path.name, "reason": "unreadable_record"})
    owners = [m["runId"] for m in matches if m.get("reason") == "live"]
    reason = ("ready" if len(owners) == 1 else
              "multiple_live_leases" if len(owners) > 1 else
              "matching_lease_not_live" if matches else "no_matching_workspace_lease")
    return {"reason": reason, "stateRoot": str(state_root),
            "activeDirectory": str(active_dir), "matching": matches,
            "otherWorkspaceLeases": other, "owners": owners}


def leases(state_root, workspace):
    report = inspect_leases(state_root, workspace)
    return report.get("owners", [])


def present(workspace, job):
    request_id = job["requestId"]
    root = workspace / "Logs/SymphonyUnity/.broker"
    response = read(root / "responses" / f"{request_id}.json")
    ack = read(root / "acks" / f"{request_id}.json")
    progress = read(root / "progress" / f"{request_id}.json")
    status = "completed" if response is not None else ("running" if ack else "queued")
    payload = {"status": status, "jobId": request_id, "operation": job["operation"]}
    if response is not None:
        payload.update(exitCode=response.get("exitCode"), brokerStatus=response.get("status"),
                       responsePath=str(root / "responses" / f"{request_id}.json"))
    elif progress is not None:
        payload["progress"] = {"phase": progress.get("phase"), "sequence": progress.get("sequence")}
    print(json.dumps(payload))
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "wait", "diagnose"])
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--operation", choices=sorted(OPS))
    parser.add_argument("--scene", default="")
    parser.add_argument("--filter", default="")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--wait-seconds", type=int, default=60)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve(strict=True)
    base = Path(os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT",
                str(Path.home() / "code/rpg-kingdom-symphony-workspaces"))).resolve(strict=True)
    if workspace.parent != base or not re.fullmatch(r"GH-[0-9]+", workspace.name):
        parser.error("workspace must be a GH-N directory immediately under Symphony workspace root")
    root = workspace / "Logs/SymphonyUnity/.broker"
    state = workspace / "Logs/SymphonyUnity/.managed-wait"
    state.mkdir(parents=True, exist_ok=True)
    for name in ("requests", "responses", "acks", "history/requests"):
        (root / name).mkdir(parents=True, exist_ok=True)
    if args.action == "diagnose":
        state_root = Path(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT",
                     str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
        print(json.dumps(inspect_leases(state_root, workspace)))
        return 0
    with (state / "lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read(state / "active.json")
        if args.action == "wait":
            if args.wait_seconds < 1 or args.wait_seconds > 90:
                parser.error("--wait-seconds must be between 1 and 90")
            if args.job_id:
                if not REQUEST.fullmatch(args.job_id):
                    parser.error("invalid request ID")
                history = read(root / "history/requests" / f"{args.job_id}.json")
                if not isinstance(history, dict) or history.get("requestId") != args.job_id:
                    parser.error("request ID not found in workspace history")
                target = {"requestId": args.job_id, "operation": history["operation"]}
            else:
                if not isinstance(previous, dict) or "requestId" not in previous:
                    print('{"status":"none"}')
                    return 4
                target = previous
            deadline = time.monotonic() + args.wait_seconds
            while True:
                if read(root / "responses" / f"{target['requestId']}.json") is not None:
                    return present(workspace, target)
                if time.monotonic() >= deadline:
                    return present(workspace, target)
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        if args.action == "status":
            if args.job_id:
                if not REQUEST.fullmatch(args.job_id):
                    parser.error("invalid request ID")
                path = root / "history/requests" / f"{args.job_id}.json"
                history = read(path)
                if history is None:
                    parser.error("request ID not found in workspace history")
                return present(workspace, {"requestId": args.job_id, "operation": history["operation"]})
            if previous is None or "requestId" not in previous:
                print('{"status":"none"}')
                return 4
            return present(workspace, previous)
        if previous is not None and "requestId" in previous:
            prior_response = read(root / "responses" / f"{previous['requestId']}.json")
            if prior_response is None:
                print("A previous broker request has no terminal response; use status or recover with the operator.", file=sys.stderr)
                return present(workspace, previous)
        if args.operation is None:
            parser.error("--operation is required for start")
        if args.operation in {"inspect", "capture"}:
            if (not SCENE.fullmatch(args.scene) or
                    any(part in ("", ".", "..") for part in args.scene.split("/"))):
                parser.error("inspect/capture requires a safe Assets/*.unity scene path")
        elif args.scene:
            parser.error("--scene requires inspect or capture")
        if args.filter and args.operation not in {"editmode", "playmode"}:
            parser.error("--filter requires editmode or playmode")
        state_root = Path(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT",
                     str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
        matching = [m for m in inspect_leases(state_root, workspace).get("matching", [])
                    if m.get("runId") and m.get("pid") and m.get("reason") in
                    {"live", "pid_not_visible_or_dead"}]
        owners = [str(m["runId"]) for m in matching]
        if len(owners) != 1:
            print("Refusing request: " + json.dumps(inspect_leases(state_root, workspace)), file=sys.stderr)
            return 75
        request_id = f"{workspace.name}-{args.operation}-{uuid.uuid4().hex}"
        envelope = {
            "protocolVersion": 1, "requestId": request_id, "operation": args.operation,
            "testFilter": args.filter, "scenePath": args.scene,
            "cameraPath": "", "viewName": "", "cameraPosition": "",
            "cameraRotation": "", "lookAtPath": "", "fieldOfView": 60.0,
            "reuseStageLibrary": False, "captureWidth": 1920, "captureHeight": 1080,
            "requestedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requiresWorkerLease": True, "expectedWorkerRunId": owners[0],
            # No clientPid: Codex's PID namespace is not the broker's host namespace.
            # Broker validates this exact run ID against a live host PID; client cannot.
        }
        history = root / "history/requests" / f"{request_id}.json"
        write(history, envelope)
        write(root / "requests" / f"{request_id}.json", envelope)
        job = {"requestId": request_id, "operation": args.operation,
               "workerRunId": owners[0], "startedAt": time.time()}
        write(state / "active.json", job)
        return present(workspace, job)

if __name__ == "__main__":
    sys.exit(main())
