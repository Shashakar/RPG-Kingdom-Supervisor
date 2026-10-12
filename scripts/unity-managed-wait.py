#!/usr/bin/env python3
"""Preserve one Unity broker client across short Codex execution-tool calls.

Start returns immediately; status checks the same invocation. The existing
unity-runner.sh performs the actual broker request and owns its response wait.
The broker's worker lease remains authoritative for abandonment/cancellation.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

OPS = {"inspect", "capture", "health", "editmode", "playmode"}

def write(path, payload):
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload) + "\n", encoding="utf8")
    os.replace(temp, path)

def read(path):
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except FileNotFoundError:
        return None

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def report(root, job):
    result = read(root / f"{job['id']}.result.json")
    status = "completed" if result else ("running" if alive(job["pid"]) else "lost")
    payload = {"status": status, "jobId": job["id"], "operation": job["operation"],
               "exitCode": result["exitCode"] if result else None}
    if result:
        payload["stdoutPath"] = str(root / f"{job['id']}.stdout")
        payload["stderrPath"] = str(root / f"{job['id']}.stderr")
    print(json.dumps(payload))
    return 75 if status == "lost" else 0

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["start", "status", "_run"])
    p.add_argument("--workspace", default=".")
    p.add_argument("--operation", choices=sorted(OPS))
    p.add_argument("--scene", default="")
    p.add_argument("--filter", default="")
    p.add_argument("--job-id", default="")
    a = p.parse_args()
    workspace = Path(a.workspace).resolve(strict=True)
    base = Path(os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT",
                str(Path.home() / "code/rpg-kingdom-symphony-workspaces"))).resolve(strict=True)
    if workspace.parent != base or not re.fullmatch(r"GH-\d+", workspace.name):
        p.error("workspace must be a GH-N directory immediately under Symphony workspace root")
    root = workspace / "Logs/SymphonyUnity/.managed-wait"
    root.mkdir(parents=True, exist_ok=True)
    if a.action == "_run":
        if not re.fullmatch(r"unity-\d+-\d+", a.job_id):
            p.error("invalid job ID")
        cmd = ["bash", str(Path(__file__).with_name("unity-runner.sh")), a.operation,
               "--project", str(workspace)]
        if a.scene:
            cmd += ["--scene", a.scene]
        if a.filter:
            cmd += ["--filter", a.filter]
        with (root / f"{a.job_id}.stdout").open("wb") as out, (root / f"{a.job_id}.stderr").open("wb") as err:
            try:
                code = subprocess.run(cmd, cwd=workspace, stdout=out, stderr=err).returncode
            except Exception as exc:
                err.write(f"Invocation failed: {exc}\n".encode())
                code = 86
        write(root / f"{a.job_id}.result.json", {"exitCode": code, "completedAt": time.time()})
        return code
    with (root / "lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read(root / "active.json")
        if a.action == "status":
            if not previous:
                print('{"status":"none"}')
                return 4
            return report(root, previous)
        if previous and not read(root / f"{previous['id']}.result.json"):
            if alive(previous["pid"]):
                print("An owned Unity invocation is still running; check status instead.", file=sys.stderr)
                return report(root, previous)
            print("Previous invocation lost its client; inspect broker recovery before starting anew.", file=sys.stderr)
            return 75
        if a.operation in {"inspect", "capture"}:
            if (not a.scene.startswith("Assets/") or not a.scene.endswith(".unity")
                    or any(part in ("", ".", "..") for part in a.scene.split("/"))):
                p.error("inspect/capture needs a safe Assets/*.unity scene path")
        elif a.scene:
            p.error("--scene requires inspect or capture")
        if a.filter and a.operation not in {"editmode", "playmode"}:
            p.error("--filter requires editmode or playmode")
        job_id = f"unity-{int(time.time())}-{os.getpid()}"
        cmd = [sys.executable, str(Path(__file__).resolve()), "_run", "--workspace",
               str(workspace), "--operation", a.operation, "--job-id", job_id]
        if a.scene:
            cmd += ["--scene", a.scene]
        if a.filter:
            cmd += ["--filter", a.filter]
        with (root / f"{job_id}.launcher.log").open("wb") as log:
            proc = subprocess.Popen(cmd, cwd=workspace, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                    close_fds=True)
        job = {"id": job_id, "pid": proc.pid, "operation": a.operation, "startedAt": time.time()}
        write(root / "active.json", job)
        return report(root, job)

if __name__ == "__main__":
    sys.exit(main())
