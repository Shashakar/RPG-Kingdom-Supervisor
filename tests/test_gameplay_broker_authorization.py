"""Fail-closed broker gameplay-scene receipt integration tests."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("author_broker_gameplay", ROOT / "scripts" / "unity-author-broker.py")
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class GameplayBrokerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspaces = self.root / "workspaces"
        self.workspace = self.workspaces / "GH-255"
        self.state = self.root / "state"
        self.requests = self.workspace / "Logs" / "SymphonyUnity" / ".author-broker" / "requests"
        self.requests.mkdir(parents=True)
        (self.state / "authoring").mkdir(parents=True)
        self.request_path = self.requests / "GH-255-test.json"
        self.grant_path = self.state / "authoring" / "GH-255.json"
        self.authoring = {
            "protocolVersion": 1, "tier": "existing-scene-gameplay",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": [{"kind": "compose-opening-encounter"}],
        }
        self.grant = {
            "protocolVersion": 1, "issue": "GH-255", "workspace": str(self.workspace.resolve()),
            "branch": "codex/gh-255-opening", "tier": "existing-scene-gameplay",
            "scene": self.authoring["scene"],
            "allowedRoots": ["World/TownArea", "Systems/Encounter_FirstApproach"],
            "operations": ["compose-opening-encounter"],
        }

    def check(self):
        self.grant_path.write_text(json.dumps(self.grant))
        self.request_path.write_text(json.dumps({
            "protocolVersion": 1, "operation": "author", "authoring": self.authoring,
        }))
        with patch.object(broker, "validate_shared_lock", return_value=None), patch.object(
            broker.subprocess, "run",
            return_value=SimpleNamespace(stdout="codex/gh-255-opening\n"),
        ):
            return broker.validate_request(self.request_path, self.workspaces.resolve(), self.state)

    def test_valid_receipt_still_does_not_enable_execution(self):
        workspace, payload, error = self.check()
        self.assertIsNone(workspace)
        self.assertIsNone(payload)
        self.assertEqual(error["exitCode"], 83)
        self.assertIn("execution is disabled", error["stderr"])

    def test_missing_grant_denied(self):
        self.grant["issue"] = "GH-123"
        self.assertIn("invalid gameplay transaction grant", self.check()[2]["stderr"])

    def test_wrong_branch_denied(self):
        self.grant["branch"] = "codex/other"
        self.assertIn("grant branch differs", self.check()[2]["stderr"])

    def test_wrong_scene_denied(self):
        self.authoring["scene"] = "Assets/Other.unity"
        self.assertIn("request scene differs", self.check()[2]["stderr"])

    def test_injected_command_denied(self):
        self.authoring["executeMethod"] = "Attack"
        self.assertIn("unapproved execution", self.check()[2]["stderr"])

    def test_bad_operation_denied(self):
        self.authoring["operations"] = [{"kind": "delete-object"}]
        self.assertIn("operation not in authenticated grant", self.check()[2]["stderr"])


if __name__ == "__main__":
    unittest.main()
