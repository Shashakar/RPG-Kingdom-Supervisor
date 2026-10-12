#!/usr/bin/env python3
"""C5f read-only Windows identity/volume audit for an owned Unity stage.

This tool is an observation, NOT human authentication, stage freeze, or
promotion permission. In particular, differing SIDs do not imply approval.
Never expose it as a worker-side broker operation or accept worker-supplied
identity facts in place of the Windows process census.
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

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "c5f_host_preflight", SCRIPTS / "unity-stage-host-preflight.py")
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = preflight
spec.loader.exec_module(preflight)

SID = re.compile(r"S-1-[0-9]+(?:-[0-9]+){2,}\Z", re.ASCII)
WINDOWS_STAGE = re.compile(r'^C:\\[^"\'*\x00-\x1f]+\Z', re.IGNORECASE)
REQUIRED = frozenset({
    "unityPid", "unityName", "unitySid", "hostInteropSid",
    "volumeDevice", "volumeFilesystem",
})

# Only the Editor PID attested by the C3 host grant is interpolated here.
# No JSON, SID, approval, script or arbitrary stage path comes from an issue.
PROBE = r"""
$ErrorActionPreference = 'Stop'
$targetPid = __UNITY_PID__
$unity = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $targetPid)
if ($null -eq $unity) { throw 'owned Unity process disappeared' }
$owner = Invoke-CimMethod -InputObject $unity -MethodName GetOwnerSid
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
if ($null -eq $owner -or $null -eq $disk) { throw 'identity or volume unavailable' }
[ordered]@{
    unityPid = [int]$unity.ProcessId
    unityName = [string]$unity.Name
    unitySid = [string]$owner.Sid
    hostInteropSid = [string]([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    volumeDevice = [string]$disk.DeviceID
    volumeFilesystem = [string]$disk.FileSystem
} | ConvertTo-Json -Compress -Depth 2
"""


class BoundaryDenied(Exception):
    pass


def validate_windows_facts(facts: object, expected_pid: int) -> dict:
    """Fail closed on ambiguous Windows evidence; never return a grant."""
    if not isinstance(expected_pid, int) or expected_pid <= 0:
        raise BoundaryDenied("invalid owned Unity PID")
    if not isinstance(facts, dict) or set(facts) != REQUIRED:
        raise BoundaryDenied("incomplete Windows identity/volume evidence")
    if type(facts["unityPid"]) is not int or facts["unityPid"] != expected_pid:
        raise BoundaryDenied("Unity PID changed during Windows identity audit")
    if not isinstance(facts["unityName"], str) or facts["unityName"].casefold() != "unity.exe":
        raise BoundaryDenied("Windows process is not Unity.exe")
    if not all(isinstance(facts[key], str) and SID.fullmatch(facts[key])
               for key in ("unitySid", "hostInteropSid")):
        raise BoundaryDenied("invalid or unavailable Windows process SID")
    if (facts["volumeDevice"] != "C:"
            or not isinstance(facts["volumeFilesystem"], str)
            or facts["volumeFilesystem"].upper() != "NTFS"):
        raise BoundaryDenied("owned Windows stage volume is not confirmed NTFS")
    same = facts["unitySid"] == facts["hostInteropSid"]
    return {
        "sameWindowsIdentity": same,
        "identityObservation": ("same-identity-no-isolation" if same
                                else "different-identities-not-yet-attested"),
        "volumeDevice": "C:",
        "volumeFilesystem": "NTFS",
        "independentHumanAuthorization": False,
        "stageFrozen": False,
        "promotionAuthorized": False,
        "copyback": False,
    }


def audit(workspace: Path, state: Path, *, run=subprocess.run) -> dict:
    # C5e establishes the pinned grant, owned Unity/worker process census,
    # clean source, idle broker, stage location and unchanged stage manifest.
    baseline = preflight.host_inspection(workspace, state)
    if (baseline.get("status") != "inspection-consistent-not-quiescent"
            or baseline.get("stageFrozen") is not False
            or baseline.get("promotionAuthorized") is not False):
        raise BoundaryDenied("unexpected C5e observation contract")
    pid = baseline.get("unityPid")
    stage = baseline.get("stagePath")
    if (type(pid) is not int or pid <= 0 or not isinstance(stage, str)
            or not WINDOWS_STAGE.fullmatch(stage)):
        raise BoundaryDenied("stage grant does not identify a local Windows C: project")
    script = PROBE.replace("__UNITY_PID__", str(pid))
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundaryDenied("Windows process/volume census unavailable") from exc
    if result.returncode:
        raise BoundaryDenied("Windows process/volume census failed")
    try:
        facts = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise BoundaryDenied("Windows census returned invalid JSON") from exc
    verified = validate_windows_facts(facts, pid)
    # C5e is a point-in-time observation. The Unity process could have
    # exited or files changed while the Windows SID/volume probe ran.
    # Deliberately do not claim stable ownership, quiescence or authority.
    return {
        "schemaVersion": 1,
        "status": "c5f-observation-only",
        "issue": baseline["issue"],
        "workspaceRevision": baseline["workspaceRevision"],
        "proposalSha256ObservedBeforeIdentityProbe": baseline["proposalSha256"],
        "unityPid": pid,
        "unityProcessSid": facts["unitySid"],
        "hostInteropProcessSid": facts["hostInteropSid"],
        **verified,
        "hostServiceIdentityVerified": False,
        "operatorPresenceVerified": False,
        "immutableSnapshotVerified": False,
        "exclusiveStageLeaseVerified": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT",
        str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    args = ap.parse_args()
    try:
        print(json.dumps(audit(Path(args.workspace), Path(args.state_root)),
                         sort_keys=True, separators=(",", ":")))
        return 0
    except (BoundaryDenied, preflight.InspectionDenied,
            preflight.c3.ro.Denied, preflight.proposal.AuditDenied,
            OSError, TypeError, ValueError) as exc:
        print("C5f boundary observation denied: " + str(exc), file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
