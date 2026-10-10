"""Host-only reconciliation of a gameplay receipt after branch preparation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "git-handoff-host.py"
spec = importlib.util.spec_from_file_location("git_handoff_receipt", SOURCE)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

class GrantBranchReconciliation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.workspace = Path(self.temp.name) / "GH-258"
        self.workspace.mkdir()
        self.marker = self.state / "authoring" / "GH-258.json"
        self.marker.parent.mkdir(parents=True)
        self.head = "a" * 40
        self.branch = "codex/gh-258-first-approach-encounter"
        self.grant = {
            "protocolVersion": 1, "issue": "GH-258",
            "workspace": str(self.workspace),
            "branch": "main", "executorRevision": self.head,
            "tier": "opening-encounter-composition",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": ["compose-authored-opening-encounter"],
            "allowedRoots": ["World/TownArea/PlayerSpawnPoint"],
        }

    def write(self):
        self.marker.write_text(json.dumps(self.grant))

    def test_initial_prepare_rebinds_only_branch(self):
        self.write()
        self.assertTrue(host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, self.head))
        actual = json.loads(self.marker.read_text())
        self.assertEqual(self.branch, actual.pop("branch"))
        original = dict(self.grant)
        original.pop("branch")
        self.assertEqual(original, actual)
        self.assertFalse(host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, self.head))

    def test_unissued_grant_never_created(self):
        self.assertFalse(host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, self.head))
        self.assertFalse(self.marker.exists())

    def test_wrong_source_revision_denied(self):
        self.write()
        with self.assertRaises(host.HandoffError):
            host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, "b" * 40)

    def test_wrong_issue_denied(self):
        self.grant["issue"] = "GH-999"
        self.write()
        with self.assertRaises(host.HandoffError):
            host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, self.head)

    def test_other_branch_cannot_be_rebound(self):
        self.grant["branch"] = "codex/different"
        self.write()
        with self.assertRaises(host.HandoffError):
            host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, self.head)

    def test_other_tiers_are_not_modified(self):
        self.grant["tier"] = "existing-scene-composition"
        self.write()
        self.assertFalse(host.reconcile_gameplay_authorization(self.state, self.workspace, self.branch, self.head))
        self.assertEqual("main", json.loads(self.marker.read_text())["branch"])

if __name__ == "__main__":
    unittest.main()
