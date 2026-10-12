#!/usr/bin/env python3
"""C5g read-only post-drain Unity/NTFS census. Not a freeze or promotion grant.

A Windows Editor may still write after this observation. Unity process absence,
NTFS filesystem evidence and repeated hashes do not establish OS exclusion.
No Editor shutdown, snapshot write or worker-facing broker action is provided.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BASE / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = load("c5g_drain_gate", "unity-stage-drain-gate.py")
c5e = load("c5g_preflight", "unity-stage-host-preflight.py")

# No request path/issue/workspace/script value interpolated into this command.
# All Unity.exe instances, including unrelated Editor/import/batchmode, block.
WINDOWS_PROBE = r"""
$ErrorActionPreference = 'Stop'
$unity = @(Get-CimInstance Win32_Process -Filter "Name = 'Unity.exe'" |
  Select-Object ProcessId, ParentProcessId, CommandLine)
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
if ($null -eq $disk) { throw 'C: volume census unavailable' }
[ordered]@{
  unityProcesses = @($unity)
  volumeDevice = [string]$disk.DeviceID
  volumeFilesystem = [string]$disk.FileSystem
  volumeSerial = [string]$disk.VolumeSerialNumber
} | ConvertTo-Json -Compress -Depth 4
"""
VOLUME_SERIAL = re.compile(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}\Z")


class QuiescenceDenied(Exception):
    pass


def validate_census(data: object) -> dict:
    if (not isinstance(data, dict) or set(data) != {
            "unityProcesses", "volumeDevice", "volumeFilesystem", "volumeSerial"}):
        raise QuiescenceDenied("incomplete or ambiguous Windows census")
    if data["volumeDevice"] != "C:" or data["volumeFilesystem"] != "NTFS":
        raise QuiescenceDenied("Windows stage volume is not verified C: NTFS")
    if not isinstance(data["volumeSerial"], str) or not VOLUME_SERIAL.fullmatch(data["volumeSerial"]):
        raise QuiescenceDenied("Windows NTFS volume serial unavailable")
    if not isinstance(data["unityProcesses"], list):
        raise QuiescenceDenied("Unity process census was not an array")
    if data["unityProcesses"]:
        # Even foreign Editors/import workers invalidate a post-handoff check.
        raise QuiescenceDenied("Windows Unity Editor, import worker or batchmode still active")
    return {"windowsUnityAbsentAtCheck": True,
            "volumeDevice": data["volumeDevice"],
            "volumeFilesystem": data["volumeFilesystem"],
            "volumeSerialObserved": data["volumeSerial"]}


def windows_census(*, run=subprocess.run) -> dict:
    try:
        result = run(
            [os.environ.get("RPGK_POWERSHELL_EXE", "powershell.exe"),
             "-NoProfile", "-NonInteractive", "-Command", WINDOWS_PROBE],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QuiescenceDenied("Windows host census unavailable") from exc
    if result.returncode:
        raise QuiescenceDenied("Windows host census command failed")
    try:
        value = json.loads(result.stdout.strip().lstrip("\ufeff"))
    except (ValueError, TypeError) as exc:
        raise QuiescenceDenied("invalid Windows census JSON") from exc
    return validate_census(value)


def broker_drained(state: Path, workspace: Path) -> None:
    path = state / "unity-broker/status.json"
    c5e.unlinked_ancestors(path, "broker status")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuiescenceDenied("Unity broker status missing or invalid") from exc
    if (not isinstance(data, dict) or data.get("protocolVersion") != 1
            or data.get("state") != "draining" or data.get("activeRequest") is not None
            or data.get("workspaceRoot") != str(workspace.parent)):
        raise QuiescenceDenied("Unity broker is not drained and idle")
    pid = data.get("pid")
    if type(pid) is not int or pid <= 0:
        raise QuiescenceDenied("invalid Unity broker PID")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        raise QuiescenceDenied("Unity broker is not alive") from exc


def observe(state: Path, *, census=windows_census) -> dict:
    raw_state = state.expanduser()
    c5e.unlinked_ancestors(raw_state, "host state")
    state = raw_state.resolve(strict=True)
    # Same broker-dispatch flock guards this gate read; no new operation can
    # begin while it is held. A Windows process can still mutate stage files.
    with gate.locked(state) as root:
        record = gate.read_locked(root)
        if record is None:
            raise QuiescenceDenied("operator broker drain not active")
        workspace, revision = c5e.c3.ro.workspace_info(record["workspace"])
        if (workspace.name != record["issue"]
                or revision != record["workspaceRevision"]):
            raise QuiescenceDenied("drain issue/workspace revision changed")
        grant = c5e.c3.current_grant(state, workspace, revision)
        if grant.get("stagePath") != record["stagePath"]:
            raise QuiescenceDenied("operator stage grant changed during drain")
        stage, receipt = c5e.c3.ro.stage_info(record["stagePath"])
        if grant.get("unityPid") != receipt.get("unityPid"):
            raise QuiescenceDenied("grant and original Editor PID disagree")
        if not stage.is_dir():
            raise QuiescenceDenied("stage no longer exists")
        if c5e.filesystem_transport(stage, workspace) not in c5e.ALLOWED_WSL_FS:
            raise QuiescenceDenied("unsupported WSL stage transport")
        if c5e.project_version(workspace, stage) != c5e.SUPPORTED_UNITY:
            raise QuiescenceDenied("Unity version mismatch")
        c5e.lease_absent(state)
        broker_drained(state, workspace)
        before = census()
        manifest_a = c5e.proposal.manifest(workspace, stage)
        middle = census()
        manifest_b = c5e.proposal.manifest(workspace, stage)
        after = census()
        broker_drained(state, workspace)
        c5e.lease_absent(state)
        if before != middle or middle != after:
            raise QuiescenceDenied("Windows census changed during observation")
        if c5e.review.manifest_digest(manifest_a) != c5e.review.manifest_digest(manifest_b):
            raise QuiescenceDenied("stage manifest changed during observation")
        if manifest_b["sourceRevision"] != revision:
            raise QuiescenceDenied("source revision changed during observation")
        # An observation is always invalidated by cancelling this drain,
        # restarting a process, creating an asset or modifying the stage.
        if gate.read_locked(root) != record:
            raise QuiescenceDenied("operator drain state changed during observation")
        return {
            "schemaVersion": 1,
            "status": "windows-quiescence-observed-not-frozen",
            "issue": record["issue"],
            "workspaceRevision": revision,
            "operationId": record["operationId"],
            "stageProject": record["stagePath"],
            "manifestSha256Observed": c5e.review.manifest_digest(manifest_b),
            "windowsProcessChecks": 3,
            **after,
            "brokerDispatchGateActive": True,
            "stageFrozen": False,
            "immutableSnapshotVerified": False,
            "independentHumanAuthorization": False,
            "promotionAuthorized": False,
            "nonceConsumed": False,
            "copyback": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT",
        str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    args = parser.parse_args()
    try:
        print(json.dumps(observe(Path(args.state_root)),
                         sort_keys=True, separators=(",", ":")))
        return 0
    except (QuiescenceDenied, gate.DrainDenied, c5e.InspectionDenied,
            c5e.c3.ro.Denied, c5e.proposal.AuditDenied,
            OSError, ValueError, TypeError, KeyError) as exc:
        print(f"C5g host census denied: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
