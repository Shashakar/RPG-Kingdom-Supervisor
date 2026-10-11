#!/usr/bin/env python3
"""Same-account staged Editor code bridge tests: fake Unity + issue broker."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


code = load("unity_staged_editor_code_test", "unity-cli-staged-code.py")
broker = load("unity_staged_editor_broker_test", "unity-host-broker.py")


class CodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.workspaces = self.root / "workspaces"
        self.ws = self.workspaces / "GH-999999"
        self.ws.mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit",
                        "--allow-empty", "-qm", "initial"], check=True)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        for rel in [code.SCENE, "Packages/manifest.json",
                    "ProjectSettings/ProjectVersion.txt"]:
            f = self.stage / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(rel, encoding="utf-8")
        self.receipt = {"stagePath": "C:\\Users\\Dex\\AppData\\Local\\RPGKingdomSupervisor\\UnityCliPilots\\pilot-" + "a" * 32,
                        "unityPid": 123}
        (self.ws / "AgentScripts").mkdir()
        (self.ws / "AgentScripts/Test.cs").write_text("return 1;", encoding="utf-8")
        self.state = self.root / "state"
        self.env = patch.dict(os.environ, {
            code.FLAG: "1",
            "RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.workspaces),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, action, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv",
                          ["cli", action, "--workspace", str(self.ws),
                           "--state-root", str(self.state), *args]), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            status = code.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def grant(self):
        with patch.object(code.ro, "stage_info", return_value=(self.stage, self.receipt)), \
             patch.object(code.ro, "cli_path", return_value="fake-cli"), \
             patch.object(code.ro, "verify_pipeline"):
            exit_code, _, err = self.invoke(
                "grant", "--session-windows", self.receipt["stagePath"]
            )
        self.assertEqual(exit_code, 0, err)

    def test_default_off_revoke_and_revision_binding(self):
        status, _, err = self.invoke("run", "--script", "AgentScripts/Test.cs")
        self.assertEqual(status, 82)
        self.assertIn("no host grant", err)
        self.grant()
        os.environ.pop(code.FLAG, None)
        status, _, err = self.invoke("run", "--script", "AgentScripts/Test.cs")
        self.assertEqual(status, 82)
        self.assertIn("disabled", err)
        os.environ[code.FLAG] = "1"
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit",
                        "--allow-empty", "-qm", "changed"], check=True)
        status, _, err = self.invoke("run", "--script", "AgentScripts/Test.cs")
        self.assertEqual(status, 82)
        self.assertIn("revision", err)
        status, _, _ = self.invoke("revoke")
        self.assertEqual(status, 0)

    def test_script_directory_boundary_size_and_symlink(self):
        script = code.code_path(self.ws, "AgentScripts/Test.cs")
        self.assertEqual(script.name, "Test.cs")
        for bad in ("../escape.cs", "Assets/foo.cs", "/tmp/x.cs",
                    "AgentScripts/../../Bad.cs", "AgentScripts/evil.ps1"):
            with self.subTest(bad=bad), self.assertRaises(code.ro.Denied):
                code.code_path(self.ws, bad)
        (self.ws / "AgentScripts/Test.cs").write_text("A" * (code.MAX_CODE + 1))
        with self.assertRaises(code.ro.Denied):
            code.code_path(self.ws, "AgentScripts/Test.cs")
        (self.ws / "AgentScripts/Test.cs").unlink()
        (self.ws / "AgentScripts/Test.cs").symlink_to(self.stage / code.SCENE)
        with self.assertRaises(code.ro.Denied):
            code.code_path(self.ws, "AgentScripts/Test.cs")

    def test_ignored_report_only_script_and_broker_request(self):
        folder = self.ws / "Logs/SymphonyUnity/AgentScripts"
        folder.mkdir(parents=True)
        path = folder / "RealProof.cs"
        path.write_text("return 42;")
        self.assertEqual(code.code_path(self.ws, "Logs/SymphonyUnity/AgentScripts/RealProof.cs"), path)
        requests = self.ws / "Logs/SymphonyUnity/.broker/requests"
        requests.mkdir(parents=True)
        request = requests / "real.json"
        request.write_text(json.dumps({
            "protocolVersion": 1, "operation": "editor-staged-code",
            "scenePath": code.SCENE,
            "scriptPath": "Logs/SymphonyUnity/AgentScripts/RealProof.cs",
        }))
        lock = self.state / "locks/unity-editor.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("GH-999999")
        (lock / "workspace").write_text(str(self.ws))
        spec = broker.prepare_request(request, self.workspaces, self.state)
        self.assertIsInstance(spec, broker.RequestSpec)
        self.assertEqual(spec.script_path, "Logs/SymphonyUnity/AgentScripts/RealProof.cs")
        with patch.object(broker.subprocess, "Popen") as popen:
            active = broker.start_operation(spec, self.state, self.root / "legacy")
            self.assertIn("Logs/SymphonyUnity/AgentScripts/RealProof.cs", popen.call_args.args[0])
            broker.close_operation(active)
        path.unlink()
        path.symlink_to(self.stage / code.SCENE)
        with self.assertRaises(code.ro.Denied):
            code.code_path(self.ws, "Logs/SymphonyUnity/AgentScripts/RealProof.cs")

    def test_run_can_iterate_on_stage_scene_and_reports_changes(self):
        payload = {"success": True, "data": {"result": {
            "sceneName": "PlaytestScene", "scenePath": code.SCENE,
            "isDirty": False, "roots": [
                {"name": "World", "hierarchyPath": "/World",
                 "components": ["Transform"], "children": []}
            ]
        }}}
        calls = []
        def fake(exe, stage, args):
            calls.append(args)
            if args[1] == "eval":
                (self.stage / code.SCENE).write_text("stage-only terrain edit")
                dest = self.stage / "Assets/NewScene.unity"
                dest.write_text("test scene")
                return {"success": True}
            return payload
        before = code.protected(self.stage)
        with patch.object(code.ro, "verify_pipeline"), \
             patch.object(code.ro, "cli_json", side_effect=fake):
            receipt = code.run_code(self.stage, self.receipt, "return 1;",
                                    "AgentScripts/Test.cs", "fake", before)
        self.assertTrue(receipt["success"])
        self.assertTrue(receipt["stageOnly"])
        self.assertFalse(receipt["copyback"])
        self.assertEqual(receipt["assetCounts"]["added"], 1)
        self.assertIn(code.SCENE, receipt["modifiedAssets"])
        self.assertEqual(receipt["addedAssets"], ["Assets/NewScene.unity"])
        self.assertEqual(calls[1][:4], ["command", "eval", "--code", "return 1;"])
        self.assertEqual(code.protected(self.stage), before)

    def test_reject_project_configuration_change(self):
        payload = {"success": True, "data": {"result": {
            "scenePath": code.SCENE, "sceneName": "PlaytestScene",
            "isDirty": False, "roots": []
        }}}
        before = code.protected(self.stage)
        def fake(exe, stage, args):
            if args[1] == "eval":
                (self.stage / "Packages/manifest.json").write_text("changed")
                return {"success": True}
            return payload
        with patch.object(code.ro, "verify_pipeline"), \
             patch.object(code.ro, "cli_json", side_effect=fake):
            response = code.run_code(self.stage, self.receipt, "return 1;",
                                     "AgentScripts/Test.cs", "fake", before)
        self.assertFalse(response["success"])
        self.assertEqual(response["protectedChanges"], ["Packages/manifest.json"])
        self.assertFalse(response["copyback"])

    def test_broker_denies_unleased_and_malformed_scripts(self):
        requests = self.ws / "Logs/SymphonyUnity/.broker/requests"
        requests.mkdir(parents=True)
        request = requests / "run.json"
        def prepare(extra=None):
            data = {
                "protocolVersion": 1, "operation": "editor-staged-code",
                "scenePath": code.SCENE, "scriptPath": "AgentScripts/Test.cs"
            }
            data.update(extra or {})
            request.write_text(json.dumps(data))
            return broker.prepare_request(request, self.workspaces, self.state)
        self.assertEqual(prepare()["exitCode"], 82)
        lock = self.state / "locks/unity-editor.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("GH-999999")
        (lock / "workspace").write_text(str(self.ws))
        spec = prepare()
        self.assertIsInstance(spec, broker.RequestSpec)
        self.assertEqual(spec.script_path, "AgentScripts/Test.cs")
        for extra in ({"scriptPath": "../outside.cs"},
                      {"scriptPath": "AgentScripts/evil.sh"},
                      {"scenePath": "Assets/../Other.unity"},
                      {"testFilter": "*"},
                      {"cameraPath": "/Main Camera"}):
            self.assertEqual(prepare(extra)["exitCode"], 64)
        with patch.object(broker.subprocess, "Popen") as popen:
            active = broker.start_operation(spec, self.state, self.root / "legacy")
            args = popen.call_args.args[0]
            self.assertTrue(args[1].endswith("unity-cli-staged-code.py"))
            self.assertEqual(args[2], "run")
            self.assertIn("AgentScripts/Test.cs", args)
            broker.close_operation(active)

    def test_stage_receipt_pid_change_denied(self):
        self.grant()
        with patch.object(code.ro, "stage_info", return_value=(
            self.stage, dict(self.receipt, unityPid=124))):
            status, _, err = self.invoke("run", "--script", "AgentScripts/Test.cs")
        self.assertEqual(status, 82)
        self.assertIn("Editor PID changed", err)


if __name__ == "__main__":
    unittest.main()
