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

def leases(state_root, workspace):
    found = []
    for path in (state_root / "workers/active").glob("*.json"):
        try:
            lease = read(path)
            if (isinstance(lease, dict) and
                Path(str(lease.get("workspace", ""))).resolve() == workspace and
                lease.get("runId") and live(lease.get("pid"))):
                found.append(str(lease["runId"]))
        except (OSError, ValueError):
            continue
    return found

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
    parser.add_argument("action", choices=["start", "status"])
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--operation", choices=sorted(OPS))
    parser.add_argument("--scene", default="")
    parser.add_argument("--filter", default="")
    parser.add_argument("--job-id", default="")
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
    with (state / "lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read(state / "active.json")
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
        owners = leases(state_root, workspace)
        if len(owners) != 1:
            print("Refusing request: exactly one live host-owned worker lease is required.", file=sys.stderr)
            return 75
        request_id = f"{workspace.name}-{args.operation}-{uuid.uuid4().hex}"
        envelope = {
            "protocolVersion": 1, "requestId": request_id, "operation": args.operation,
            "testFilter": args.filter, "scenePath": args.scene,
            "cameraPath": "", "viewName": "", "cameraPosition": "",
            "cameraRotation": "", "lookAtPath": "", "fieldOfView": 60.0,
            "reuseStageLibrary": False, "captureWidth": 1920, "captureHeight": 1080,
            "requestedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # No clientPid: Codex's PID namespace is not the broker's host namespace.
            # Broker resolves and checks the live Supervisor worker lease.
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
