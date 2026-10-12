#!/usr/bin/env python3
"""Host-only staged Unity copyback for the existing Supervisor Git broker.

Approval is a host-owned, per-issue immutable selection receipt. Workers never
provide a Windows stage path or choose additional files at execution time.
This is a bounded integration primitive; issue-stage provisioning and
automatic approval are separate work under Supervisor #234.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rpgk_stage_handoff", ROOT / "unity-stage-simple-handoff.py")
assert spec and spec.loader
stage_handoff = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stage_handoff
spec.loader.exec_module(stage_handoff)

SHA = re.compile(r"^[0-9a-f]{64}$")
HEAD = re.compile(r"^[0-9a-f]{40}$")


def refuse(message: str) -> None:
    raise ValueError(message)


def git(workspace: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(workspace), *args], text=True,
                       capture_output=True, timeout=30, check=False)
    if p.returncode:
        refuse("Git verification failed for " + args[0])
    return p.stdout.strip()


def copyback(workspace: Path, workspace_root: Path, state_root: Path,
             branch: str, issue: int) -> dict:
    workspace = workspace.resolve(strict=True)
    workspace_root = workspace_root.resolve(strict=True)
    if workspace.parent != workspace_root or workspace.name != f"GH-{issue}":
        refuse("workspace does not match issue and configured workspace root")
    branch = stage_handoff.checked_branch(branch)

    # Never let an unreviewed user request supply its own stage or allowlist.
    path = state_root / "stage-copyback-approvals" / f"GH-{issue}.json"
    if path.is_symlink() or not path.is_file():
        refuse("missing host-owned stage-copyback approval")
    approval = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(approval, dict) or approval.get("schemaVersion") != 1
            or approval.get("mode") != "host-reviewed-stage-copyback"
            or approval.get("approved") is not True
            or approval.get("issue") != f"GH-{issue}"
            or approval.get("workspace") != str(workspace)
            or approval.get("branch") != branch):
        refuse("approval does not match issue/workspace/branch")
    source_revision = approval.get("sourceRevision")
    if not isinstance(source_revision, str) or not HEAD.fullmatch(source_revision):
        refuse("missing pinned source revision")

    selected = approval.get("selectedHashes")
    if not isinstance(selected, dict) or not selected or len(selected) > 64:
        refuse("approval must pin between 1 and 64 selected file hashes")
    for rel, digest in selected.items():
        stage_handoff.checked_path(rel)
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            refuse("approval has invalid selected file hash")

    stage_name = approval.get("stagePath")
    if not isinstance(stage_name, str) or not stage_name:
        refuse("approval is missing host-selected stage path")
    stage = Path(stage_name)
    if stage.is_symlink():
        refuse("approved stage must not be a symlink")
    stage = stage.resolve(strict=True)
    if workspace == stage or workspace in stage.parents or stage in workspace.parents:
        refuse("approved stage overlaps source workspace")
    if not (stage / "session.json").is_file():
        refuse("approved stage has no Unity session receipt")
    receipt = json.loads((stage / "session.json").read_text(encoding="utf-8-sig"))
    if not isinstance(receipt, dict) or receipt.get("schemaVersion") != 1:
        refuse("invalid Unity stage receipt")
    if receipt.get("sessionId") != stage.name:
        refuse("Unity stage receipt does not match stage directory")
    # A stage may be copied only to the workspace from which its Editor was
    # originally created. Editor need not still be running for this operation.
    original_windows = receipt.get("sourceProjectPath")
    if not isinstance(original_windows, str) or not original_windows:
        refuse("stage receipt has no originating source project")
    mapped = subprocess.run(["wslpath", "-u", original_windows], text=True,
                            capture_output=True, timeout=10, check=False)
    if mapped.returncode or not mapped.stdout.strip():
        refuse("unable to map stage's originating workspace")
    if Path(mapped.stdout.strip()).resolve() != workspace:
        refuse("Unity stage originated from another issue workspace")
    git_origin = git(workspace, "remote", "get-url", "origin")
    if not re.fullmatch(r"(?:https://github.com/|git@github.com:|ssh://git@github.com/)Shashakar/RPG-Kingdom(?:\\.git)?", git_origin, re.I):
        refuse("unexpected game repository origin")

    if git(workspace, "branch", "--show-current") != branch:
        refuse("requested codex branch is not checked out")
    if git(workspace, "rev-parse", "HEAD") != source_revision:
        refuse("source HEAD differs from approved revision")
    if git(workspace, "rev-parse", "refs/remotes/origin/main") != source_revision:
        refuse("origin/main changed since approval")
    if git(workspace, "status", "--porcelain", "--untracked-files=all"):
        refuse("workspace is dirty; do not replace continuation work")
    if subprocess.run(["git", "-C", str(workspace), "show-ref", "--quiet", "--verify",
                       "refs/remotes/origin/" + branch], timeout=15).returncode == 0:
        refuse("remote feature branch already exists")

    # The host receipt pins all selected sidecars too, not just top-level assets.
    requested = [rel for rel in selected if not rel.endswith(".meta")]
    if not requested:
        refuse("approval must contain an asset, not metadata alone")
    proposal = stage_handoff.plan(workspace, stage, requested, prepared_branch=branch)
    found = {entry["path"]: entry["stage"]["sha256"] for entry in proposal["selected"]}
    if found != selected:
        refuse("approved selection or staged asset hashes changed")
    result = stage_handoff.apply(workspace, stage, branch, requested, use_prepared_branch=True)
    if set(result["copiedPaths"]) != set(selected):
        refuse("copyback result differs from approved selection")
    return {"status": "completed", "exitCode": 0, "operation": "stage-copyback",
            "issue": issue, "branch": branch, "sourceRevision": source_revision,
            "copiedPaths": result["copiedPaths"], "excludedCount": len(result["excludedPaths"]),
            "approvalFile": str(path), "validationRequired": True,
            "commit": False, "push": False, "merged": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--workspace-root", required=True, type=Path)
    ap.add_argument("--state-root", required=True, type=Path)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--issue", type=int, required=True)
    args = ap.parse_args()
    try:
        result = copyback(args.workspace, args.workspace_root, args.state_root,
                          args.branch, args.issue)
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError,
            stage_handoff.StageHandoffDenied, stage_handoff.proposal.AuditDenied) as exc:
        print(json.dumps({"status": "StageCopybackDenied", "exitCode": 82,
                          "operation": "stage-copyback", "message": str(exc)},
                         separators=(",", ":"), sort_keys=True))
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
