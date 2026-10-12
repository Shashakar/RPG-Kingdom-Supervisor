#!/usr/bin/env python3
"""C5c host issuance contract tests; no actual game stage or credentials."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "c5c_issuer", Path(__file__).resolve().parents[1] / "scripts/unity-stage-approval-issue.py")
issuer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = issuer
spec.loader.exec_module(issuer)


class IssuerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "GH-261"
        self.stage = self.root / "stage"
        self.host = self.root / "host"
        self.workspace.mkdir()
        self.stage.mkdir()
        self.host.mkdir(mode=0o700)
        self.workspace.joinpath("Assets/RPGKingdom/Scenes").mkdir(parents=True)
        self.stage.joinpath("Assets/RPGKingdom/Scenes").mkdir(parents=True)
        for project in (self.workspace, self.stage):
            for name in ("Packages", "ProjectSettings"):
                (project / name).mkdir()
            project.joinpath("Assets/RPGKingdom/Scenes/PlaytestScene.unity").write_text("original")
        subprocess.run(["git", "-C", str(self.workspace), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test", "-c",
                        "user.email=test@example.com", "commit", "-qm", "initial"], check=True)
        revision = subprocess.check_output(["git", "-C", str(self.workspace), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.workspace), "update-ref", "refs/remotes/origin/main", revision], check=True)
        proof = issuer.verify.proposal.PROOF
        meta = issuer.verify.proposal.FOLDER_META
        for name, data in [
            (proof, "proof"),
            (proof + ".meta", "guid: " + "a"*32 + "\n"),
            (meta, "guid: " + "b"*32 + "\n"),
            ("Assets/Settings/Mobile_RPAsset.asset", "k_AssetVersion: 13")]:
            f = self.stage / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(data)
        self.key = self.host / "signing.key"
        self.key.write_bytes(b"k" * 32)
        self.key.chmod(0o600)
        self.ledger = self.host / "issued.json"
        self.ledger.write_text('{"schemaVersion":1,"issued":[]}')
        self.ledger.chmod(0o600)
        self.lock = self.host / "issued.json.lock"
        self.lock.write_text("")
        self.lock.chmod(0o600)
        self.output = self.host / "review.json"
        self.args = SimpleNamespace(workspace=str(self.workspace), stage=str(self.stage),
                                    issue="GH-261", reviewer="HostOperator",
                                    host_key=str(self.key), issuance_ledger=str(self.ledger),
                                    output=str(self.output))

    def test_build_envelope_matches_verifier_and_excludes_urp(self):
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        proposal, approval = issuer.build_envelope(
            self.workspace, self.stage, "HostOperator", b"k"*32,
            now=now, nonce="7"*48)
        result = issuer.verify.validate(approval, proposal, key=b"k"*32,
                                        spent=set(), now=now)
        self.assertEqual(result["status"], "verified-for-review-only")
        self.assertFalse(result["copyback"])
        self.assertNotIn("Assets/Settings/Mobile_RPAsset.asset", result["approvedPaths"])

    def test_issue_requires_real_tty_and_confirmation(self):
        with self.assertRaisesRegex(issuer.IssuanceDenied, "interactive"):
            issuer.issue(self.args, is_tty=False)
        with self.assertRaisesRegex(issuer.IssuanceDenied, "declined"):
            issuer.issue(self.args, is_tty=True, input_fn=lambda _: "NO")
        self.assertFalse(self.output.exists())
        self.assertEqual(issuer.read_ledger(self.ledger)["issued"], [])

    def test_issue_writes_signed_review_and_issuance_ledger(self):
        result = issuer.issue(self.args, is_tty=True, input_fn=lambda _: "GH-261")
        self.assertFalse(result["copyback"])
        self.assertFalse(result["promotionAuthorized"])
        approval = __import__("json").loads(self.output.read_text())
        current = issuer.verify.proposal.manifest(self.workspace, self.stage)
        checked = issuer.verify.validate(approval, current, key=b"k"*32,
                                         spent=set())
        self.assertFalse(checked["consumed"])
        self.assertEqual(len(issuer.read_ledger(self.ledger)["issued"]), 1)
        with self.assertRaisesRegex(issuer.IssuanceDenied, "already exist"):
            issuer.issue(self.args, is_tty=True, input_fn=lambda _: "GH-261")

    def test_host_paths_must_be_private_and_outside_worker(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(issuer.verify.ApprovalDenied, "permissions too broad"):
            issuer.issue(self.args, is_tty=True, input_fn=lambda _: "GH-261")
        self.key.chmod(0o600)
        self.args.output = str(self.workspace / "approval.json")
        with self.assertRaisesRegex(issuer.IssuanceDenied, "worker workspace"):
            issuer.issue(self.args, is_tty=True, input_fn=lambda _: "GH-261")

    def test_mutation_during_review_denied(self):
        def mutate(_):
            (self.stage / issuer.verify.proposal.PROOF).write_text("changed")
            return "GH-261"
        with self.assertRaisesRegex(issuer.IssuanceDenied, "changed during review"):
            issuer.issue(self.args, is_tty=True, input_fn=mutate)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
