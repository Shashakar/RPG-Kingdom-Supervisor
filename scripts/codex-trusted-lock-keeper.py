#!/usr/bin/env python3
"""Trusted controller lock keeper for isolated worker sessions.

Acquires both existing concurrency locks and holds them until the child has
exited. The untrusted process never inherits lock file descriptors.
For use only by trusted controller code; NOT an escalation endpoint.
"""
from __future__ import annotations
import argparse
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys

ISSUE = re.compile(r"^GH-[0-9]+$")
SLOT = {"implementation": "mutation", "repair": "mutation",
        "report-only": "mutation", "review": "review"}

def paths(state_root: Path, role: str, issue: str) -> tuple[Path, Path]:
    if role not in SLOT or not ISSUE.fullmatch(issue):
        raise ValueError("unsupported role or issue")
    root=state_root.resolve(strict=True)
    return root / "locks" / ("codex-" + SLOT[role] + ".lock"), root / "locks" / "issues" / (issue + ".lock")

def run(state_root: Path, role: str, issue: str, command: list[str]) -> int:
    if not command or any(not part for part in command):
        raise ValueError("missing command")
    slot, issue_file=paths(state_root,role,issue)
    if not slot.parent.is_dir() or not issue_file.parent.is_dir():
        raise FileNotFoundError("trusted lock directories absent")
    # For compatibility with the existing router, lock the same files in the
    # same order. Controller owns descriptors and child gets close_fds=True.
    with slot.open("a+b") as slot_handle:
        fcntl.flock(slot_handle,fcntl.LOCK_EX)
        with issue_file.open("a+b") as issue_handle:
            fcntl.flock(issue_handle,fcntl.LOCK_EX)
            try:
                child=subprocess.Popen(command, close_fds=True)
                return child.wait()
            finally:
                fcntl.flock(issue_handle,fcntl.LOCK_UN)
                fcntl.flock(slot_handle,fcntl.LOCK_UN)

def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--state-root",required=True)
    parser.add_argument("--role",required=True,choices=list(SLOT))
    parser.add_argument("--issue",required=True)
    parser.add_argument("command",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    # This CLI is staging-only: arbitrary commands must never be passed
    # across a worker-reachable privilege boundary. No sudo policy!
    if not args.command or args.command[0] != "--":
        raise ValueError("missing command separator")
    return run(Path(args.state_root),args.role,args.issue,args.command[1:])

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (ValueError,OSError) as exc:
        print(f"Codex lock keeper failed: {type(exc).__name__}",file=sys.stderr)
        raise SystemExit(75)
