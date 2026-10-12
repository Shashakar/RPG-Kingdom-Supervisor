#!/usr/bin/env python3
"""Offline C5 read-only proposal tests; no Unity process or stage writes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "stage_proposal", Path(__file__).resolve().parents[1] / "scripts/unity-stage-proposal.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class StageProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ws = root / "GH-216"
        self.stage = root / "stage"
        self.ws.mkdir()
        self.stage.mkdir()
        for project in (self.ws, self.stage):
            for folder in mod.CHECK_ROOTS:
                (project / folder).mkdir()
            (project / "Assets/RPGKingdom/Scenes").mkdir(parents=True)
            (project / "Assets/RPGKingdom/Scenes/PlaytestScene.unity").write_text("source")
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=test",
                        "-c", "user.email=test@test", "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=test",
                        "-c", "user.email=test@test", "commit", "-qm", "initial"], check=True)
        head = subprocess.check_output(["git", "-C", str(self.ws), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.ws), "update-ref", "refs/remotes/origin/main", head], check=True)

    def file(self, project, relative, content):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def fixture(self):
        self.file(self.stage, mod.PROOF, "scene")
        self.file(self.stage, mod.PROOF + ".meta", "fileFormatVersion: 2\nguid: " + "a" * 32 + "\n")
        self.file(self.stage, mod.FOLDER_META, "fileFormatVersion: 2\nguid: " + "b" * 32 + "\n")
        self.file(self.ws, "Assets/Settings/Mobile_RPAsset.asset", "k_AssetVersion: 12\n")
        # Fixture source files must be committed to satisfy source-clean gate.
        subprocess.run(["git", "-C", str(self.ws), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=test",
                        "-c", "user.email=test@test", "commit", "-qm", "baseline"], check=True)
        head = subprocess.check_output(["git", "-C", str(self.ws), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.ws), "update-ref", "refs/remotes/origin/main", head], check=True)
        self.file(self.stage, "Assets/Settings/Mobile_RPAsset.asset", "k_AssetVersion: 13\n")

    def test_classifies_expected_fixture_and_quarantines_urp(self):
        self.fixture()
        result = mod.manifest(self.ws, self.stage)
        paths = {row["path"]: row for row in result["changes"]}
        self.assertEqual(result["counts"], {"changed": 4, "reviewCandidates": 3, "quarantined": 1})
        self.assertEqual(paths[mod.PROOF]["classification"], "candidate-for-human-review")
        self.assertEqual(paths[mod.FOLDER_META]["classification"], "candidate-for-human-review")
        self.assertEqual(paths["Assets/Settings/Mobile_RPAsset.asset"]["classification"], "quarantined")
        self.assertTrue(all(row["promotable"] is False for row in result["changes"]))
        self.assertIsNone(result["authorization"])
        self.assertFalse(result["copyback"])
        self.assertEqual(result["sourceSceneSha256"], result["stageSceneSha256"])
        self.assertEqual(result["changes"], sorted(result["changes"], key=lambda r: r["path"]))

    def test_dirty_and_stale_source_fail_closed(self):
        self.fixture()
        self.file(self.ws, "Assets/new.txt", "dirty")
        with self.assertRaisesRegex(mod.AuditDenied, "not clean"):
            mod.manifest(self.ws, self.stage)
        (self.ws / "Assets/new.txt").unlink()
        subprocess.run(["git", "-C", str(self.ws), "update-ref", "refs/remotes/origin/main",
                        "0" * 40], check=True)
        with self.assertRaisesRegex(mod.AuditDenied, "origin/main"):
            mod.manifest(self.ws, self.stage)

    def test_duplicate_stage_guid_fails(self):
        self.fixture()
        self.file(self.stage, "Assets/Other.asset.meta", "guid: " + "a" * 32 + "\n")
        with self.assertRaisesRegex(mod.AuditDenied, "duplicate stage GUID"):
            mod.manifest(self.ws, self.stage)

    def test_links_and_missing_meta_guid_fail(self):
        self.fixture()
        self.file(self.stage, "Assets/Broken.asset.meta", "fileFormatVersion: 2\n")
        with self.assertRaisesRegex(mod.AuditDenied, "missing .meta GUID"):
            mod.manifest(self.ws, self.stage)
        (self.stage / "Assets/Broken.asset.meta").unlink()
        (self.stage / "Assets/Link.asset").symlink_to(self.stage / mod.PROOF)
        with self.assertRaisesRegex(mod.AuditDenied, "linked"):
            mod.manifest(self.ws, self.stage)

    def test_source_and_stage_cannot_be_same(self):
        with self.assertRaisesRegex(mod.AuditDenied, "different"):
            mod.manifest(self.ws, self.ws)

    def test_project_config_always_quarantined(self):
        self.file(self.stage, "ProjectSettings/NewSetting.asset", "x")
        result = mod.manifest(self.ws, self.stage)
        self.assertEqual(result["counts"]["quarantined"], 1)
        self.assertEqual(result["changes"][0]["classification"], "quarantined")


if __name__ == "__main__":
    unittest.main()
