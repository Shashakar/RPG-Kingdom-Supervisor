#!/usr/bin/env python3
"""Offline C2b source/stage provenance, denial and copyback-preview tests."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("c2b_audit_test", ROOT / "scripts/unity-cli-c2b-audit.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class C2bAuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.workspaces = self.base / "workspaces"
        self.workspace = self.workspaces / "GH-999999"
        self.workspace.mkdir(parents=True)
        self.state = self.base / "state"
        self.stage = self.base / "stage"
        self.stage.mkdir()
        self.scene = module.SCENE
        self.target = module.CANDIDATE
        source = {
            self.scene: "%YAML 1.1\nPlaytestScene",
            self.scene + ".meta": "scene guid",
            "Assets/RPGKingdom/Editor/Prototype.cs": "public class Prototype {}",
            "Packages/manifest.json": '{"dependencies":{"com.unity.render-pipelines.universal":"17.0.0"}}',
            "Packages/packages-lock.json": '{"dependencies":{"com.unity.render-pipelines.universal":{"version":"17.0.0"}}}',
            "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 6000.3.10f1\n",
        }
        for rel, text in source.items():
            file = self.workspace / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.workspace), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test",
                        "-c", "user.email=test@example.com", "commit", "-qm",
                        "Clean staged source"], check=True)
        for name in module.ROOTS:
            shutil.copytree(self.workspace / name, self.stage / name)
        for name in ("manifest.json", "packages-lock.json"):
            file = self.stage / "Packages" / name
            value = json.loads(file.read_text())
            value["dependencies"]["com.unity.pipeline"] = (
                module.PIPELINE_VERSION if name == "manifest.json" else {"version": module.PIPELINE_VERSION}
            )
            file.write_text(json.dumps(value))
        self.session = r"C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-" + "a" * 32
        self.receipt = {"stagePath": self.session, "unityPid": 123}
        self.env = patch.dict(os.environ, {
            "RPGK_UNITY_C2B_AUDIT_ENABLED": "1",
            "RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.workspaces),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, action, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["audit", action, "--workspace", str(self.workspace),
                "--state-root", str(self.state)] + list(args)
        with patch.object(sys, "argv", argv), \
             patch.object(module.ro, "stage_info", return_value=(self.stage, self.receipt)), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def attest(self):
        status, out, err = self.invoke("baseline", "--session-windows", self.session)
        self.assertEqual(status, 0, err)
        result = json.loads(out)
        self.assertFalse(result["readyForCopyback"])
        return result

    def create_target(self):
        target = self.stage / self.target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("%YAML 1.1\nSupervisorCliBenchmarkRoot\nSupervisorCliBenchmarkCube")
        (self.stage / (self.target + ".meta")).write_text("guid: benchmark")

    def test_positive_matching_stage_and_no_copyback_audit(self):
        baseline = self.attest()
        self.assertEqual(baseline["fileCount"], 6)
        self.create_target()
        code, out, err = self.invoke("audit")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["addedPaths"], [self.target, self.target + ".meta"])
        self.assertEqual(result["targetSha256"], module.sha_file(self.stage / self.target))
        self.assertTrue(result["unchangedExistingFiles"])
        self.assertTrue(result["dryRun"])
        self.assertFalse(result["copyback"])
        self.assertFalse(result["readyForCopyback"])
        self.assertTrue(result["blockers"])
        self.assertFalse((self.workspace / self.target).exists())

    def test_no_output_denied(self):
        self.attest()
        code, _, err = self.invoke("audit")
        self.assertEqual(code, 82)
        self.assertIn("no new benchmark scene", err)

    def test_stage_changes_outside_allowlist_denied(self):
        self.attest()
        self.create_target()
        extra = self.stage / "Assets/RPGKingdom/BadScene.unity"
        extra.write_text("unexpected")
        code, _, err = self.invoke("audit")
        self.assertEqual(code, 82)
        self.assertIn("unexpected stage additions", err)

    def test_mutating_protected_asset_denied(self):
        self.attest()
        self.create_target()
        original = self.stage / self.scene
        original.write_text("%YAML 1.1\nmodified")
        code, _, err = self.invoke("audit")
        self.assertEqual(code, 82)
        self.assertIn("existing stage assets changed", err)

    def test_nonmatching_source_stage_denied(self):
        file = self.stage / "Assets/RPGKingdom/Editor/Prototype.cs"
        file.write_text("tampered")
        code, _, err = self.invoke("baseline", "--session-windows", self.session)
        self.assertEqual(code, 82)
        self.assertIn("baseline does not match clean issue workspace", err)

    def test_unauthorized_pipeline_package_change_denied(self):
        file = self.stage / "Packages/manifest.json"
        manifest = json.loads(file.read_text())
        manifest["dependencies"]["com.some.other"] = "1.2.3"
        file.write_text(json.dumps(manifest))
        code, _, err = self.invoke("baseline", "--session-windows", self.session)
        self.assertEqual(code, 82)
        self.assertIn("unapproved staged Unity package", err)

    def test_workspace_dirty_and_revision_change_denied(self):
        self.attest()
        self.create_target()
        source = self.workspace / self.scene
        source.write_text("dirty")
        code, _, err = self.invoke("audit")
        self.assertEqual(code, 82)
        self.assertIn("clean Git", err)
        subprocess.run(["git", "-C", str(self.workspace), "checkout", "--", self.scene], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test",
                        "-c", "user.email=test@example.com", "commit", "--allow-empty",
                        "-qm", "changed revision"], check=True)
        code, _, err = self.invoke("audit")
        self.assertEqual(code, 82)
        self.assertIn("workspace revision", err)

    def test_overwrite_and_linked_asset_denied(self):
        self.create_target()
        code, _, err = self.invoke("baseline", "--session-windows", self.session)
        self.assertEqual(code, 82)
        self.assertIn("benchmark output already exists", err)
        # An attacker must not smuggle a symlink through a project asset path.
        (self.stage / self.target).unlink()
        link = self.stage / "Assets/RPGKingdom/Editor/Linked.cs"
        link.symlink_to(self.base / "missing")
        code, _, err = self.invoke("baseline", "--session-windows", self.session)
        self.assertEqual(code, 82)
        self.assertIn("non-regular", err)

    def test_default_disabled_and_revoke(self):
        self.attest()
        os.environ.pop(module.FLAG, None)
        code, _, err = self.invoke("audit")
        self.assertEqual(code, 82)
        self.assertIn("disabled by default", err)
        os.environ[module.FLAG] = "1"
        code, out, err = self.invoke("revoke")
        self.assertEqual(code, 0, err)
        self.assertFalse(module.record_path(self.state, self.workspace.name).exists())

    def test_windows_identity_check_is_fail_closed(self):
        script = (ROOT / "scripts/windows/check-unity-cli-isolation.ps1").read_text()
        self.assertIn('GetOwnerSid', script)
        self.assertIn('if ($editorSid -eq $operatorSid)', script)
        self.assertIn('filesystemDenyProbePassed = $false', script)
        self.assertIn('readyForArbitraryEval = $false', script)
        self.assertIn('readyForCopyback = $false', script)
        self.assertNotIn("Set-Acl", script)
        self.assertNotIn("Start-Process", script)


if __name__ == "__main__":
    unittest.main()
