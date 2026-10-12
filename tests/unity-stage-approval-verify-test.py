#!/usr/bin/env python3
"""Offline C5b verification tests. No approval issuer or copyback API."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "c5b_verify", Path(__file__).resolve().parents[1] / "scripts/unity-stage-approval-verify.py"
)
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.key = b"k" * 32
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        self.paths = [verify.proposal.PROOF, verify.proposal.PROOF + ".meta",
                      verify.proposal.FOLDER_META]
        self.current = {
            "schemaVersion": 1, "mode": "proposal-only", "issue": "GH-261",
            "sourceWorkspace": "/home/dex/workspaces/GH-261",
            "sourceRevision": "a" * 40,
            "stageProject": "/mnt/c/stages/stage-one",
            "sourceSceneSha256": "b" * 64,
            "stageSceneSha256": "b" * 64,
            "changes": [
                {"path": p, "change": "added",
                 "classification": "candidate-for-human-review",
                 "stage": {"sha256": str(i) * 64, "guid": chr(99+i) * 32}}
                for i, p in enumerate(self.paths)
            ] + [
                {"path": "Assets/Settings/Mobile_RPAsset.asset", "change": "modified",
                 "classification": "quarantined",
                 "stage": {"sha256": "f" * 64}}],
            "authorization": None, "copyback": False
        }
        self.payload = {
            "schemaVersion": 1, "purpose": "c5b-review-verification-only",
            "issue": "GH-261", "reviewer": "Dex-Operator",
            "sourceRevision": "a" * 40,
            "sourceSceneSha256": "b" * 64,
            "stageProject": "/mnt/c/stages/stage-one",
            "proposalSha256": verify.manifest_digest(self.current),
            "approvedPaths": list(self.paths), "nonce": "8" * 32,
            "issuedAt": (self.now - timedelta(minutes=1)).isoformat(),
            "expiresAt": (self.now + timedelta(minutes=10)).isoformat()
        }

    def signed(self, payload=None):
        payload = payload or self.payload
        return {"payload": payload,
                "mac": hmac.new(self.key, verify.canonical(payload), hashlib.sha256).hexdigest()}

    def accept(self, *, current=None, approval=None, spent=None):
        return verify.validate(approval or self.signed(), current or self.current,
                               key=self.key, spent=spent or set(), now=self.now)

    def test_exact_signed_review_is_verification_only(self):
        result = self.accept()
        self.assertEqual(result["status"], "verified-for-review-only")
        self.assertFalse(result["copyback"])
        self.assertFalse(result["consumed"])

    def test_payload_tamper_and_wrong_key_denied(self):
        forged = self.signed()
        forged["payload"]["issue"] = "GH-999"
        with self.assertRaisesRegex(verify.ApprovalDenied, "attestation mismatch"):
            self.accept(approval=forged)
        with self.assertRaisesRegex(verify.ApprovalDenied, "attestation mismatch"):
            verify.validate(self.signed(), self.current, key=b"q"*32,
                            spent=set(), now=self.now)

    def test_changed_stage_urp_scene_or_source_is_denied(self):
        import copy
        for mutation in ("urp", "scene", "revision", "extra"):
            current = copy.deepcopy(self.current)
            if mutation == "urp":
                current["changes"][-1]["stage"]["sha256"] = "e" * 64
            elif mutation == "scene":
                current["stageSceneSha256"] = "d" * 64
            elif mutation == "revision":
                current["sourceRevision"] = "c" * 40
            else:
                current["changes"].append({"path": "Assets/Unplanned.asset", "change": "added",
                                          "classification": "quarantined"})
            with self.subTest(mutation=mutation), self.assertRaises(verify.ApprovalDenied):
                self.accept(current=current)

    def test_incomplete_metadata_and_quarantined_asset_denied(self):
        from copy import deepcopy
        for paths in ([self.paths[0]], self.paths + ["Assets/Settings/Mobile_RPAsset.asset"],
                      [self.paths[0], self.paths[0], self.paths[2]]):
            payload = deepcopy(self.payload)
            payload["approvedPaths"] = paths
            with self.subTest(paths=paths), self.assertRaises(verify.ApprovalDenied):
                self.accept(approval=self.signed(payload))

    def test_expiration_wrong_issue_and_replay_ledger(self):
        with self.assertRaisesRegex(verify.ApprovalDenied, "already used"):
            self.accept(spent={"8" * 32})
        from copy import deepcopy
        payload = deepcopy(self.payload)
        payload["expiresAt"] = (self.now - timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(verify.ApprovalDenied, "expired"):
            self.accept(approval=self.signed(payload))
        payload = deepcopy(self.payload)
        payload["issue"] = "GH-999"
        with self.assertRaisesRegex(verify.ApprovalDenied, "issue or source revision"):
            self.accept(approval=self.signed(payload))

    def test_host_key_permissions_and_short_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "host-key"
            p.write_bytes(self.key)
            p.chmod(0o600)
            self.assertEqual(verify.safe_secret(p), self.key)
            p.chmod(0o644)
            with self.assertRaisesRegex(verify.ApprovalDenied, "permissions too broad"):
                verify.safe_secret(p)
            p.chmod(0o600)
            p.write_bytes(b"short")
            with self.assertRaisesRegex(verify.ApprovalDenied, "too short"):
                verify.safe_secret(p)


if __name__ == "__main__":
    unittest.main()
