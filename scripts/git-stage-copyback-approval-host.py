#!/usr/bin/env python3
"""Host-issued, exact-file stage copyback approval derived from an existing C3 grant.

This is a host-side primitive, NOT a worker command. A future dispatch policy
will supply the reviewed path selection. Do not allow workers to invoke it
with self-selected paths or expose it as an unrestricted broker operation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


handoff = load("rpgk_approval_handoff", "unity-stage-simple-handoff.py")
readonly = load("rpgk_approval_readonly", "unity-cli-readonly.py")


def deny(message: str) -> None:
    raise ValueError(message)


def generate(workspace: Path, workspace_root: Path, state_root: Path,
             branch: str, selected_paths: list[str]) -> dict:
    workspace = workspace.resolve(strict=True)
    workspace_root = workspace_root.resolve(strict=True)
    state_root = state_root.resolve()
    if workspace.parent != workspace_root or not readonly.ISSUE.fullmatch(workspace.name):
        deny("approval requires a direct GH-N issue workspace")
    branch = handoff.checked_branch(branch)
    if not selected_paths or len(selected_paths) > 64:
        deny("approval requires between 1 and 64 explicit asset paths")
    if len(set(selected_paths)) != len(selected_paths):
        deny("duplicate selected asset path")
    for item in selected_paths:
        handoff.checked_path(item)
        if item.endswith(".meta"):
            deny("select an asset rather than a metadata companion")

    grant_file = state_root / "unity-staged-code-grants" / (workspace.name + ".json")
    if grant_file.is_symlink():
        deny("stage grant may not be a symlink")
    try:
        grant = json.loads(grant_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        deny("no existing host-issued C3 stage grant")
    revision = handoff.git(workspace, "rev-parse", "HEAD").stdout.strip()
    if (not isinstance(grant, dict) or grant.get("schemaVersion") != 1
            or grant.get("enabled") is not True
            or grant.get("mode") != "operator-trusted-staged-editor-code"
            or grant.get("issue") != workspace.name
            or grant.get("workspace") != str(workspace)
            or grant.get("workspaceRevision") != revision):
        deny("stage grant does not match this issue and Git revision")
    stage_windows = grant.get("stagePath")
    if not isinstance(stage_windows, str) or not stage_windows:
        deny("stage grant has no pinned Windows stage")
    stage, receipt = readonly.stage_info(stage_windows)
    if grant.get("unityPid") != receipt.get("unityPid"):
        deny("stage receipt PID differs from the C3 grant")
    # The original Windows source must belong to this GH-N workspace.
    conversion = handoff.git(workspace, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(conversion).resolve() != workspace:
        deny("source workspace is not the Git root")
    recorded_windows = receipt.get("sourceProjectPath", "")
    mapped = __import__("subprocess").run(["wslpath", "-u", recorded_windows],
                                          capture_output=True, text=True, timeout=10)
    if mapped.returncode or not mapped.stdout.strip() or Path(mapped.stdout.strip()).resolve() != workspace:
        deny("Unity stage originated from another workspace")

    plan = handoff.plan(workspace, stage, selected_paths, prepared_branch=branch)
    chosen = {entry["path"]: entry["stage"]["sha256"] for entry in plan["selected"]}
    if not chosen or len(chosen) > 64:
        deny("stage selection is empty or too large")
    # The prepared branch must still be identical to origin/main; this API is
    # deliberately not a generic mutable continuation or branch switch.
    if plan["sourceRevision"] != revision:
        deny("source revision changed during proposal")
    approval = {
        "schemaVersion": 1,
        "mode": "host-reviewed-stage-copyback",
        "approved": True,
        "issue": workspace.name,
        "workspace": str(workspace),
        "branch": branch,
        "sourceRevision": revision,
        "stagePath": str(stage),
        "selectedHashes": chosen,
    }
    output = state_root / "stage-copyback-approvals" / (workspace.name + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or output.exists():
        deny("an approval already exists; refuse replacement")
    fd, tmp = tempfile.mkstemp(prefix=".issue-approval-", dir=output.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(approval, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        # Exclusive create: a parallel attempt may not silently replace an approval.
        os.link(tmp, output)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {
        "status": "approved", "issue": workspace.name, "branch": branch,
        "sourceRevision": revision, "selectedPaths": sorted(chosen),
        "excludedCount": len(plan["excluded"]), "approvalFile": str(output),
        "copyback": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--path", action="append", default=[])
    args = parser.parse_args()
    try:
        result = generate(args.workspace, args.workspace_root,
                          args.state_root, args.branch, args.path)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (ValueError, OSError, handoff.StageHandoffDenied,
            handoff.proposal.AuditDenied) as exc:
        print(json.dumps({"status": "StageApprovalDenied", "exitCode": 82,
                          "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
