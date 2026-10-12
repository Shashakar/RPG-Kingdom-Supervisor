#!/usr/bin/env python3
"""C5f unit tests: fake Windows probes; never access a live Unity stage."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/unity-stage-authority-audit.py"
spec = importlib.util.spec_from_file_location("c5f_authority_audit", MODULE)
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)

GOOD = {
    "unityPid": 9648,
    "unityName": "Unity.exe",
    "unitySid": "S-1-5-21-101-102-103-1001",
    "hostInteropSid": "S-1-5-21-101-102-103-1001",
    "volumeDevice": "C:",
    "volumeFilesystem": "NTFS",
}
BASELINE = {
    "status": "inspection-consistent-not-quiescent",
    "issue": "GH-261",
    "workspaceRevision": "a" * 40,
    "proposalSha256": "b" * 64,
    "unityPid": 9648,
    "stagePath": r"C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-123",
    "stageFrozen": False,
    "promotionAuthorized": False,
}


def reply(facts=GOOD, *, returncode=0):
    return subprocess.CompletedProcess(
        args=["powershell.exe"], returncode=returncode,
        stdout=json.dumps(facts), stderr=""
    )


class AuthorityAuditTests(unittest.TestCase):
    def test_same_windows_identity_is_not_independent_approval(self):
        with patch.object(audit.preflight, "host_inspection", return_value=BASELINE):
            observed = []

            def run(args, **kwargs):
                observed.append((args, kwargs))
                return reply()

            result = audit.audit(Path("/tmp/workspaces/GH-261"), Path("/tmp/state"), run=run)
        self.assertEqual(result["identityObservation"], "same-identity-no-isolation")
        self.assertTrue(result["sameWindowsIdentity"])
        self.assertFalse(result["hostServiceIdentityVerified"])
        self.assertFalse(result["operatorPresenceVerified"])
        self.assertFalse(result["immutableSnapshotVerified"])
        self.assertFalse(result["exclusiveStageLeaseVerified"])
        self.assertFalse(result["stageFrozen"])
        self.assertFalse(result["promotionAuthorized"])
        self.assertFalse(result["copyback"])
        self.assertEqual(result["volumeFilesystem"], "NTFS")
        args, kwargs = observed[0]
        self.assertEqual(args[:3], ["powershell.exe", "-NoProfile", "-NonInteractive"])
        self.assertIn("$targetPid = 9648", args[4])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 30)

    def test_different_sid_is_still_not_authorized(self):
        facts = dict(GOOD, hostInteropSid="S-1-5-21-101-102-103-1002")
        with patch.object(audit.preflight, "host_inspection", return_value=BASELINE):
            result = audit.audit(Path("/tmp/ws"), Path("/tmp/state"),
                                 run=lambda *a, **k: reply(facts))
        self.assertEqual(result["identityObservation"], "different-identities-not-yet-attested")
        self.assertFalse(result["sameWindowsIdentity"])
        self.assertFalse(result["independentHumanAuthorization"])
        self.assertFalse(result["promotionAuthorized"])

    def test_model_claims_cannot_augment_windows_evidence(self):
        for payload in (dict(GOOD, promotionAuthorized=True),
                        dict(GOOD, approval="signed"), None, [],
                        dict(GOOD, unityPid=0), dict(GOOD, unityPid=True),
                        dict(GOOD, unityPid=9999),
                        dict(GOOD, unityName="powershell.exe"),
                        dict(GOOD, unitySid="fake"),
                        dict(GOOD, hostInteropSid=""),
                        dict(GOOD, volumeFilesystem="exFAT"),
                        dict(GOOD, volumeDevice="D:")):
            with self.subTest(payload=payload):
                with self.assertRaises(audit.BoundaryDenied):
                    audit.validate_windows_facts(payload, 9648)

    def test_powershell_errors_or_invalid_json_fail_closed(self):
        with patch.object(audit.preflight, "host_inspection", return_value=BASELINE):
            for response in (subprocess.CompletedProcess([], 1, "", "error"),
                             subprocess.CompletedProcess([], 0, "garbage", ""),
                             subprocess.CompletedProcess([], 0, "[]", "")):
                with self.subTest(response=response):
                    with self.assertRaises(audit.BoundaryDenied):
                        audit.audit(Path("/tmp/ws"), Path("/tmp/state"),
                                    run=lambda *a, **k: response)
            with self.assertRaises(audit.BoundaryDenied):
                audit.audit(Path("/tmp/ws"), Path("/tmp/state"),
                            run=lambda *a, **k: (_ for _ in ()).throw(OSError()))

    def test_stale_or_unexpected_c5e_receipt_denied(self):
        for baseline in (dict(BASELINE, status="ready"),
                         dict(BASELINE, stageFrozen=True),
                         dict(BASELINE, promotionAuthorized=True),
                         dict(BASELINE, unityPid="9648"),
                         dict(BASELINE, stagePath="/tmp/forged"),
                         dict(BASELINE, stagePath="C:\\bad';Write-Host 'x"),
                         dict(BASELINE, stagePath="D:\\not-the-owned-volume")):
            with self.subTest(baseline=baseline):
                with patch.object(audit.preflight, "host_inspection", return_value=baseline):
                    with self.assertRaises(audit.BoundaryDenied):
                        audit.audit(Path("/tmp/ws"), Path("/tmp/state"),
                                    run=lambda *a, **k: reply())

    def test_c5e_failure_not_bypassed(self):
        with patch.object(audit.preflight, "host_inspection",
                          side_effect=audit.preflight.InspectionDenied("lease busy")):
            with self.assertRaisesRegex(audit.preflight.InspectionDenied, "lease busy"):
                audit.audit(Path("/tmp/ws"), Path("/tmp/state"),
                            run=lambda *a, **k: self.fail("must not query Windows"))


if __name__ == "__main__":
    unittest.main()
