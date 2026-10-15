#!/usr/bin/env python3
"""Fail-closed static readiness audit of the installed Codex router.

Read-only. Does not interpret or execute shell source. This is a migration
checklist guard, not proof that arbitrary shell code is safe.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import json

def inspect(text: str) -> dict:
    lines=text.splitlines()
    sensitive={
      "github_issue_lookup":r"\bgh\s+issue\s+view\b",
      "continuation_auth":r"build-continuation-context-with-auth",
      "concurrency_lock":r"\bflock\s+[89]\b",
      "worker_start_telemetry":r"supervisor_telemetry\.py.*worker-start",
      "final_exec_codex":r"^\s*exec\s+codex\s*(?:\\|$)",
      "state_writes":r"(?:mkdir\s+-p|worker-start|\bmktemp\b)",
      "secret_unset":r"\bunset\s+SYMPHONY_GITHUB_TOKEN\s+GH_TOKEN\s+GITHUB_TOKEN",
      "inherited_lock_fd_8":r"^\s*exec\s+8>",
      "inherited_lock_fd_9":r"^\s*exec\s+9>"
    }
    found={name:[i for i,line in enumerate(lines,1) if re.search(pattern,line)]
           for name,pattern in sensitive.items()}
    exec_points=found["final_exec_codex"]
    gaps=[name for name,positions in found.items() if not positions]
    if len(exec_points)!=1:
        gaps.append("unique_final_exec")
    # Untested: any lexical matches inside comments may be false positives.
    return {"observations":found,"missing":gaps,
            "requires_separated_uid_launcher":True,
            "requires_file_descriptor_audit":bool(found["inherited_lock_fd_8"] or found["inherited_lock_fd_9"]),
            "requires_environment_audit":True,
            "safe_to_enable_strict_admission":False}

def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument("router",type=Path)
    args=parser.parse_args()
    result=inspect(args.router.read_text())
    print(json.dumps(result,indent=2,sort_keys=True))
    if result["missing"]:
        raise SystemExit(2)

if __name__=="__main__":
    main()
