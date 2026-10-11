#!/usr/bin/env python3
"""Offline checks for real issue workspace/stage ownership and grant setup."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("real_issue_setup_test", ROOT / "scripts/unity-c3-real-worker-setup.py")
setup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = setup
spec.loader.exec_module(setup)


class RealIssueSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ws = self.root / "workspaces/GH-261"
        self.ws.mkdir(parents=True)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        for rel in (setup.SCENE, "ProjectSettings/ProjectVersion.txt",
                    "Packages/manifest.json"):
            file = self.ws / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(rel)
            staged = self.stage / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, staged)
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "remote", "add", "origin", setup.REPO], check=True)
        subprocess.run(["git", "-C", str(self.ws), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit",
                        "-qm", "clean game"], check=True)
        self.rev = setup.revision(self.ws)
        subprocess.run(["git", "-C", str(self.ws), "update-ref",
                        "refs/remotes/origin/main", self.rev], check=True)
        self.session = r"C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-" + "a" * 32
        self.receipt = {"sourceProjectPath": r"\\wsl.localhost\Ubuntu\home\dex\game\GH-261",
                        "stagePath": self.session, "unityPid": 123}

    def test_clean_game_repo_required(self):
        self.assertEqual(setup.verify_game_workspace(self.ws, fetch=False), self.rev)
        (self.ws / "Assets/untracked.txt").write_text("bad")
        with self.assertRaisesRegex(setup.ro.Denied, "untracked changes"):
            setup.verify_game_workspace(self.ws, fetch=False)
        (self.ws / "Assets/untracked.txt").unlink()
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit",
                        "--allow-empty", "-qm", "new head"], check=True)
        with self.assertRaisesRegex(setup.ro.Denied, "latest origin/main"):
            setup.verify_game_workspace(self.ws, fetch=False)

    def test_stage_must_be_created_from_exact_issue_workspace(self):
        with patch.object(setup.ro, "stage_info", return_value=(self.stage, self.receipt)), \
             patch.object(setup, "source_windows_path",
                          return_value=setup.ntpath.normcase(setup.ntpath.normpath(self.receipt["sourceProjectPath"]))):
            stage, receipt = setup.valid_stage(self.ws, self.session)
            self.assertEqual(receipt["unityPid"], 123)
            self.assertEqual(stage, self.stage)
            changed = dict(self.receipt, sourceProjectPath=r"C:\Different\workspace")
            with patch.object(setup.ro, "stage_info", return_value=(self.stage, changed)):
                with self.assertRaisesRegex(setup.ro.Denied, "not bound"):
                    setup.valid_stage(self.ws, self.session)
            (self.stage / setup.SCENE).write_text("modified")
            with self.assertRaisesRegex(setup.ro.Denied, "differs"):
                setup.valid_stage(self.ws, self.session)

    def test_stage_only_generated_output_must_not_exist_before_grant(self):
        target = self.stage / setup.TARGET
        target.parent.mkdir(parents=True)
        target.write_text("%YAML 1.1")
        with patch.object(setup.ro, "stage_info", return_value=(self.stage, self.receipt)), \
             patch.object(setup, "source_windows_path",
                          return_value=setup.ntpath.normcase(setup.ntpath.normpath(self.receipt["sourceProjectPath"]))):
            with self.assertRaisesRegex(setup.ro.Denied, "already exists"):
                setup.valid_stage(self.ws, self.session)

    def test_grant_opens_expected_clean_playtest_scene_without_copyback(self):
        hierarchy = {"success": True, "data": {"result": {
            "scenePath": setup.SCENE, "sceneName": "PlaytestScene",
            "isDirty": False, "roots": []
        }}}
        real_run = subprocess.run
        def fake_run(args, **kwargs):
            if args[0] == "git":
                return real_run(args, **kwargs)
            return subprocess.CompletedProcess(args, 0, '{"status":"granted"}', "")
        with patch.object(setup, "valid_stage", return_value=(self.stage, self.receipt)), \
             patch.object(setup.ro, "cli_path", return_value="fake"), \
             patch.object(setup.ro, "verify_pipeline"), \
             patch.object(setup.ro, "cli_json", return_value=hierarchy), \
             patch.object(setup.subprocess, "run", side_effect=fake_run) as runner:
            data = setup.grant(self.ws, self.session, self.root / "state")
        self.assertEqual(data["status"], "ready-to-arm")
        self.assertFalse(data["copyback"])
        self.assertEqual(data["revision"], self.rev)
        self.assertEqual(runner.call_args.kwargs["env"]["RPGK_UNITY_STAGED_CODE_ENABLED"], "1")


if __name__ == "__main__":
    unittest.main()
