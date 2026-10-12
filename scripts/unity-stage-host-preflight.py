#!/usr/bin/env python3
"""C5e operator-host read-only stage inspection; explicitly NOT promotion authority.

Invoke from the trusted Supervisor host, not a Codex worker. Uses the existing
C3 issue grant and Editor/Pipeline process checks; does not expose a new broker
request operation, stop Unity, reserve a nonce, or touch game source files.

The persistent Editor is *running* at inspection: broker-idle != stage-frozen.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


c3 = load("c5e_staged_code", "unity-cli-staged-code.py")
proposal = load("c5e_proposal", "unity-stage-proposal.py")
review = load("c5e_review", "unity-stage-approval-verify.py")
ORIGINAL_SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
VERSION_FILE = "ProjectSettings/ProjectVersion.txt"
SUPPORTED_UNITY = "6000.3.10f1"
ALLOWED_WSL_FS = {"9p", "drvfs"}


class InspectionDenied(Exception):
    pass


def deny(message: str) -> None:
    raise InspectionDenied(message)


def unlinked_ancestors(path: Path, name: str) -> None:
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink():
            deny(f"{name} contains linked directory")


def broker_idle(state: Path, workspace: Path) -> dict:
    status = state / "unity-broker" / "status.json"
    unlinked_ancestors(status, "broker status")
    try:
        data = json.loads(status.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InspectionDenied("host Unity broker status unavailable") from exc
    if (not isinstance(data, dict) or data.get("protocolVersion") != 1
            or data.get("state") != "ready" or data.get("activeRequest") is not None):
        deny("Unity host broker is active, blocked or ambiguous")
    pid = data.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        deny("invalid Unity host broker PID")
    if data.get("workspaceRoot") != str(workspace.parent):
        deny("broker workspace root differs from issue workspace")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        raise InspectionDenied("Unity host broker PID is not alive") from exc
    return {"pid": pid, "state": "ready"}


def lease_absent(state: Path) -> None:
    lease = state / "locks" / "unity-editor.lock"
    unlinked_ancestors(lease, "Unity editor lease")
    # Never recover or remove stale lock here. Ambiguous state must block.
    if lease.exists() or lease.is_symlink():
        deny("Unity editor lease still exists; cannot attest idle host")


def filesystem_transport(stage: Path, source: Path) -> str:
    unlinked_ancestors(stage, "Windows stage")
    unlinked_ancestors(source, "issue workspace")
    if not stage.is_dir() or not source.is_dir():
        deny("stage or issue workspace directory missing")
    if not stage.as_posix().startswith("/mnt/c/"):
        deny("disposable Unity stage is not mapped through the expected Windows C: mount")
    query = subprocess.run(
        ["findmnt", "-T", str(stage), "-n", "-o", "FSTYPE"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if query.returncode or query.stdout.strip().lower() not in ALLOWED_WSL_FS:
        deny("Windows stage filesystem transport is unavailable or unsupported")
    # A 9p/drvfs mount does not independently prove the Windows volume is NTFS.
    return query.stdout.strip().lower()


def project_version(source: Path, stage: Path) -> str:
    versions = []
    for root in (source, stage):
        target = root / VERSION_FILE
        unlinked_ancestors(target, "project version")
        if not target.is_file():
            deny("missing Unity project version")
        match = re.search(r"(?m)^m_EditorVersion:\s*(\S+)\s*$",
                          target.read_text(encoding="utf-8-sig"))
        if not match:
            deny("Unity project version format unsupported")
        versions.append(match.group(1))
    if versions[0] != versions[1] or versions[0] != SUPPORTED_UNITY:
        deny("Unity source/stage project version changed or unsupported")
    return versions[0]


def check_guard(state: Path, workspace: Path) -> dict:
    # A drain marker must deny even if the broker has not yet refreshed status.
    # This remains an observation, never a guarantee against Unity C# writes.
    gate_marker = state / "unity-stage-drain" / "state.json"
    if gate_marker.exists() or gate_marker.is_symlink():
        deny("operator Unity broker drain is active or ambiguous")
    lease_absent(state)
    return broker_idle(state, workspace)


def host_inspection(workspace: Path, state: Path) -> dict:
    """Read-only, fail closed even after broker looks idle twice.

    This is a *consistency observation*, not an immutable stage snapshot or
    transaction lease. Never issue a promotion grant based on this response.
    """
    raw_ws = workspace.expanduser()
    unlinked_ancestors(raw_ws, "issue workspace")
    raw_state = state.expanduser()
    unlinked_ancestors(raw_state, "host state")
    checked_ws, revision = c3.ro.workspace_info(str(raw_ws))
    checked_state = raw_state.resolve(strict=True)
    unlinked_ancestors(checked_ws, "issue workspace")
    guard_before = check_guard(checked_state, checked_ws)

    grant = c3.current_grant(checked_state, checked_ws, revision)
    stage, receipt = c3.ro.stage_info(grant.get("stagePath"))
    transport = filesystem_transport(stage, checked_ws)
    editor_version = project_version(checked_ws, stage)
    if grant.get("unityPid") != receipt.get("unityPid"):
        deny("Unity Editor PID no longer matches operator grant")
    if (not isinstance(receipt.get("sourceProjectPath"), str)
            or not receipt.get("stagePath")):
        deny("Windows stage receipt missing explicit source/project identity")

    # C3 probes CLI Pipeline ownership, active clean PlaytestScene, exact
    # operator grant and allowed Windows Editor/import worker census on host.
    active_before = c3.persistent_preflight(checked_ws, revision, checked_state)
    first = proposal.manifest(checked_ws, stage)
    if first.get("sourceRevision") != revision:
        deny("source revision advanced during inspection")
    if first.get("sourceSceneSha256") != first.get("stageSceneSha256"):
        deny("protected PlaytestScene source/stage mismatch")

    active_after = c3.persistent_preflight(checked_ws, revision, checked_state)
    second = proposal.manifest(checked_ws, stage)
    guard_after = check_guard(checked_state, checked_ws)
    if guard_before != guard_after:
        deny("Unity broker identity or state changed during inspection")
    if active_before != active_after:
        deny("Unity Editor ownership or import worker census changed during inspection")
    if review.manifest_digest(first) != review.manifest_digest(second):
        deny("stage or source changed during inspection")
    # Read a third scene hash to catch a direct source write after the scan.
    if (proposal.digest(checked_ws / ORIGINAL_SCENE) != first["sourceSceneSha256"]
            or proposal.digest(stage / ORIGINAL_SCENE) != first["stageSceneSha256"]):
        deny("protected source scene changed after inventory")
    return {
        "schemaVersion": 1,
        "status": "inspection-consistent-not-quiescent",
        "mode": "host-read-only-stage-inspection",
        "issue": checked_ws.name,
        "workspaceRevision": revision,
        "stagePath": active_after["stagePath"],
        "unityPid": active_after["unityPid"],
        "assetImportWorkerPids": active_after["assetImportWorkerPids"],
        "brokerPid": guard_after["pid"],
        "unityVersion": editor_version,
        "wslStageFilesystem": transport,
        "proposalSha256": review.manifest_digest(second),
        "changedCount": second["counts"]["changed"],
        "quarantinedCount": second["counts"]["quarantined"],
        "protectedSourceSceneSha256": second["sourceSceneSha256"],
        "brokerIdleAtChecks": True,
        "persistentEditorRunning": True,
        "stageFrozen": False,
        "independentHumanAuthorization": False,
        "promotionAuthorized": False,
        "nonceConsumed": False,
        "copyback": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT",
        str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    args = parser.parse_args()
    try:
        print(json.dumps(host_inspection(Path(args.workspace), Path(args.state_root)),
                         sort_keys=True, separators=(",", ":")))
        return 0
    except (InspectionDenied, c3.ro.Denied, proposal.AuditDenied,
            OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(f"C5e host preflight blocked: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
