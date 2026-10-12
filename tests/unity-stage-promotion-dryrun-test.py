#!/usr/bin/env python3
"""C5d contract tests: genuine source/stage files, NO actual promotion writes."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "c5d_promotion_simulation",
    Path(__file__).resolve().parents[1] / "scripts/unity-stage-promotion-dryrun.py")
dryrun = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dryrun
spec.loader.exec_module(dryrun)


class PromotionDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / "GH-261"
        self.stage = self.base / "stage"
        self.ws.mkdir()
        self.stage.mkdir()
        for root in (self.ws, self.stage):
            for folder in dryrun.review.proposal.CHECK_ROOTS:
                (root / folder).mkdir()
            self.create(root, "Assets/RPGKingdom/Scenes/PlaytestScene.unity", "original")
        self.create(self.ws, "Assets/Settings/Mobile_RPAsset.asset", "k_AssetVersion: 12")
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test", "-c",
                        "user.email=test@example.com", "commit", "-qm", "initial"], check=True)
        head = subprocess.check_output(["git", "-C", str(self.ws), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.ws), "update-ref", "refs/remotes/origin/main", head], check=True)
        self.proof = dryrun.review.proposal.PROOF
        self.create(self.stage, self.proof, "saved additive fixture")
        self.create(self.stage, self.proof + ".meta", "guid: " + "a" * 32 + "\n")
        self.create(self.stage, dryrun.review.proposal.FOLDER_META, "guid: " + "b" * 32 + "\n")
        self.create(self.stage, "Assets/Settings/Mobile_RPAsset.asset", "k_AssetVersion: 13")
        self.secret = b"s" * 32
        self.at = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        self.current = dryrun.review.proposal.manifest(self.ws, self.stage)
        self.envelope = self.sign(self.current)

    @staticmethod
    def create(root, rel, contents):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents)
        return p

    def sign(self, current):
        payload = {
            "schemaVersion": 1, "purpose": "c5b-review-verification-only",
            "issue": current["issue"], "reviewer": "HostOperator",
            "sourceRevision": current["sourceRevision"],
            "sourceSceneSha256": current["sourceSceneSha256"],
            "stageProject": current["stageProject"],
            "proposalSha256": dryrun.review.manifest_digest(current),
            "approvedPaths": [
                self.proof, self.proof + ".meta", dryrun.review.proposal.FOLDER_META],
            "nonce": "1" * 48, "issuedAt": (self.at - timedelta(minutes=1)).isoformat(),
            "expiresAt": (self.at + timedelta(minutes=30)).isoformat()
        }
        return {"payload": payload,
                "mac": hmac.new(self.secret, dryrun.review.canonical(payload),
                                hashlib.sha256).hexdigest()}

    def plan(self, **kwargs):
        # C5b verification needs a deterministic test clock.
        real_validate = dryrun.review.validate
        def test_clock(approval, current, *, key, spent):
            return real_validate(approval, current, key=key, spent=spent, now=self.at)
        from unittest.mock import patch
        with patch.object(dryrun.review, "validate", side_effect=test_clock):
            return dryrun.exact_plan(kwargs.get("current", self.current),
                                     kwargs.get("approval", self.envelope),
                                     key=kwargs.get("key", self.secret),
                                     spent_nonces=kwargs.get("spent", set()))

    def test_exact_review_plan_quarantines_urp_and_never_promotes(self):
        plan = self.plan()
        self.assertEqual(plan["mode"], "dry-run-only")
        self.assertEqual(plan["issue"], "GH-261")
        self.assertFalse(plan["copyback"])
        self.assertFalse(plan["promotionAuthorized"])
        self.assertEqual(len(plan["candidateWrites"]), 3)
        self.assertEqual(plan["quarantinedExclusions"], ["Assets/Settings/Mobile_RPAsset.asset"])
        self.assertTrue(all(f["expectedSource"] is None for f in plan["candidateWrites"]))
        result = dryrun.simulate(plan)
        self.assertEqual(result["status"], "simulated-commit")
        self.assertEqual(result["realFilesWritten"], 0)
        self.assertFalse(result["realNonceConsumed"])
        self.assertFalse(result["virtualSourceRestored"])

    def test_every_checkpoint_rolls_back_and_preserves_real_files(self):
        plan = self.plan()
        original = (self.ws / "Assets/RPGKingdom/Scenes/PlaytestScene.unity").read_bytes()
        fixture = (self.stage / self.proof).read_bytes()
        for checkpoint in dryrun.CHECKPOINTS:
            with self.subTest(checkpoint=checkpoint):
                result = dryrun.simulate(plan, checkpoint)
                self.assertEqual(result["status"], "simulated-rollback")
                self.assertTrue(result["virtualSourceRestored"])
                self.assertEqual(result["virtualSourceAfter"], result["virtualSourceBefore"])
                self.assertFalse(result["copyback"])
                self.assertFalse(result["realNonceConsumed"])
                self.assertEqual(result["realFilesWritten"], 0)
        self.assertEqual((self.ws / "Assets/RPGKingdom/Scenes/PlaytestScene.unity").read_bytes(), original)
        self.assertEqual((self.stage / self.proof).read_bytes(), fixture)
        self.assertFalse((self.ws / self.proof).exists())

    def test_nonce_replay_bad_mac_and_wrong_issue_denied(self):
        with self.assertRaisesRegex(dryrun.review.ApprovalDenied, "already used"):
            self.plan(spent={"1" * 48})
        with self.assertRaisesRegex(dryrun.review.ApprovalDenied, "attestation mismatch"):
            self.plan(key=b"z"*32)
        forged = copy.deepcopy(self.envelope)
        forged["payload"]["issue"] = "GH-999"
        with self.assertRaisesRegex(dryrun.review.ApprovalDenied, "attestation mismatch"):
            self.plan(approval=forged)

    def test_modified_stage_after_signature_and_dirty_source_denied(self):
        self.create(self.stage, self.proof, "changed scene contents")
        changed = dryrun.review.proposal.manifest(self.ws, self.stage)
        with self.assertRaisesRegex(dryrun.review.ApprovalDenied, "proposal content changed"):
            self.plan(current=changed)
        self.create(self.ws, "Assets/Rogue.txt", "unauthorized")
        with self.assertRaisesRegex(dryrun.review.proposal.AuditDenied, "not clean"):
            dryrun.review.proposal.manifest(self.ws, self.stage)

    def test_new_unexpected_files_and_urp_change_denied(self):
        self.create(self.stage, "Assets/Other.unity", "unreviewed")
        changed = dryrun.review.proposal.manifest(self.ws, self.stage)
        with self.assertRaisesRegex(dryrun.review.ApprovalDenied, "proposal content changed"):
            self.plan(current=changed)
        (self.stage / "Assets/Other.unity").unlink()
        self.create(self.stage, "Assets/Settings/Mobile_RPAsset.asset", "k_AssetVersion: 14")
        changed = dryrun.review.proposal.manifest(self.ws, self.stage)
        with self.assertRaisesRegex(dryrun.review.ApprovalDenied, "proposal content changed"):
            self.plan(current=changed)

    def test_missing_meta_or_duplicate_guid_is_denied(self):
        (self.stage / (self.proof + ".meta")).unlink()
        changed = dryrun.review.proposal.manifest(self.ws, self.stage)
        with self.assertRaises(dryrun.review.ApprovalDenied):
            self.plan(current=changed)
        self.create(self.stage, self.proof + ".meta", "guid: " + "a"*32 + "\n")
        self.create(self.stage, "Assets/Other.asset.meta", "guid: " + "a"*32 + "\n")
        with self.assertRaisesRegex(dryrun.review.proposal.AuditDenied, "duplicate stage GUID"):
            dryrun.review.proposal.manifest(self.ws, self.stage)

    def test_real_source_unchanged_and_symlink_blocks_inventory(self):
        self.create(self.stage, "Assets/RPGKingdom/Scenes/PlaytestScene.unity", "MUTATED")
        with self.assertRaisesRegex(dryrun.review.proposal.AuditDenied, "original PlaytestScene modified"):
            dryrun.review.proposal.manifest(self.ws, self.stage)
        self.create(self.stage, "Assets/RPGKingdom/Scenes/PlaytestScene.unity", "original")
        (self.stage / "Assets/Link.unity").symlink_to(self.stage / self.proof)
        with self.assertRaisesRegex(dryrun.review.proposal.AuditDenied, "linked"):
            dryrun.review.proposal.manifest(self.ws, self.stage)

    def test_plan_does_not_accept_wrong_mode_or_checkpoint(self):
        plan = self.plan()
        with self.assertRaisesRegex(dryrun.PromotionDryRunDenied, "unknown failure checkpoint"):
            dryrun.simulate(plan, "overwrite-production")
        with self.assertRaisesRegex(dryrun.PromotionDryRunDenied, "no production promotion plan"):
            dryrun.simulate(dict(plan, copyback=True))
        altered = dict(self.current, mode="promotion-approved")
        # Even if signing was reissued for an unsafe mode, fail the simulation contract.
        signed = self.sign(altered)
        with self.assertRaisesRegex(dryrun.PromotionDryRunDenied, "proposal must be read-only"):
            self.plan(current=altered, approval=signed)


if __name__ == "__main__":
    unittest.main()
