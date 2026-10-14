#!/usr/bin/env python3
"""Read-only readiness report for Unity V2 host identity separation.

Does not create users, modify file permissions, install files or restart services.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import pwd
import grp
import stat
import subprocess

def identity(name: str) -> dict:
    try:
        p = pwd.getpwnam(name)
        return {"exists": True, "uid": p.pw_uid, "gid": p.pw_gid}
    except KeyError:
        return {"exists": False}

def service_info(name: str) -> dict:
    try:
        result = subprocess.run(["systemctl", "show", name, "--property=User,Group,FragmentPath,ActiveState",
                                 "--no-pager"], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode != 0:
            return {"available": False, "detail": "systemctl show failed"}
        return {"available": True, **dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "detail": "systemctl unavailable"}

def check_path(path: Path) -> dict:
    try:
        value = path.lstat()
        return {"exists": True, "owner_uid": value.st_uid, "mode": oct(stat.S_IMODE(value.st_mode)),
                "is_directory": stat.S_ISDIR(value.st_mode), "is_symlink": stat.S_ISLNK(value.st_mode)}
    except FileNotFoundError:
        return {"exists": False}

def report(controller: str, registrar: str, worker: str, state: Path, workspace: Path) -> dict:
    identities = {k: identity(v) for k, v in {"controller": controller, "registrar": registrar, "worker": worker}.items()}
    present = [x["uid"] for x in identities.values() if x["exists"]]
    unique = len(present) == 3 and len(set(present)) == 3
    reg_state = check_path(state / "workers" / "active")
    protected = (reg_state.get("exists") and reg_state.get("is_directory")
                 and not reg_state.get("is_symlink") and reg_state.get("owner_uid") == identities["registrar"].get("uid")
                 and int(reg_state["mode"], 8) & 0o077 == 0)
    supervisor = service_info("rpg-kingdom-supervisor.service")
    configured_controller = supervisor.get("User") == controller
    readiness = {
        "distinct_existing_uids": unique,
        "supervisor_runs_as_controller": configured_controller,
        "protected_registrar_lease_directory": bool(protected),
        "workspace_root_exists": workspace.is_dir(),
        "controller_worker_launch_isolation_verified": False,
        "registrar_socket_peer_tests_verified": False,
        "unity_wsl_windows_acceptance_verified": False,
    }
    return {
        "identities": identities, "supervisor_service": supervisor,
        "paths": {"registration": reg_state, "workspace_root": check_path(workspace)},
        "checks": readiness, "safe_for_strict_admission": all(readiness.values())
    }

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", default="dex")
    parser.add_argument("--registrar", default="rpgk-registrar")
    parser.add_argument("--worker", default="rpgk-codex")
    parser.add_argument("--state-root", default=str(Path.home()/".local/state/rpg-kingdom-supervisor"))
    parser.add_argument("--workspace-root", default=str(Path.home()/"code/rpg-kingdom-symphony-workspaces"))
    args=parser.parse_args()
    print(json.dumps(report(args.controller,args.registrar,args.worker,
                            Path(args.state_root),Path(args.workspace_root)),indent=2,sort_keys=True))

if __name__=="__main__":
    main()
