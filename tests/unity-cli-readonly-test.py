#!/usr/bin/env python3
"""No-Windows regression tests for the opt-in Unity CLI read-only broker lane."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ro = load("unity_cli_readonly", "scripts/unity-cli-readonly.py")
broker = load("unity_host_broker_cli_test", "scripts/unity-host-broker.py")


class SceneSummaryTests(unittest.TestCase):
    def setUp(self):
        self.scene = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
        self.data = {"data": {"result": {
            "sceneName": "PlaytestScene", "scenePath": self.scene,
            "isDirty": False, "roots": [
                {"name": "World", "components": ["Transform"], "children": [
                    {"name": "AuthoredGround", "hierarchyPath": "/World/AuthoredGround",
                     "components": ["Transform", "Terrain", "TerrainCollider"], "children": []}
                ]},
                {"name": "Systems", "components": [], "children": []},
            ]
        }}}

    def test_compact_hierarchy(self):
        summary = ro.compact_scene(self.data, self.scene)
        self.assertEqual(summary["rootCount"], 2)
        self.assertEqual(summary["gameObjectCount"], 3)
        self.assertEqual(summary["terrainPaths"], ["/World/AuthoredGround"])
        self.assertEqual(summary["rootNames"], ["World", "Systems"])
        self.assertNotIn("roots", summary)

    def test_reject_dirty_and_wrong_scene(self):
        with self.assertRaises(ro.Denied):
            ro.compact_scene(self.data, "Assets/Other.unity")
        self.data["data"]["result"]["isDirty"] = True
        with self.assertRaises(ro.Denied):
            ro.compact_scene(self.data, self.scene)

    def test_canonical_paths_only(self):
        self.assertEqual(ro.scene_path(self.scene), self.scene)
        for bad in ("/tmp/test.unity", "Assets/../secret.unity",
                    "Assets\\test.unity", "Assets//scene.unity", "Assets/test.cs"):
            with self.subTest(bad=bad), self.assertRaises(ro.Denied):
                ro.scene_path(bad)
        stage = r"C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-" + "a" * 32
        self.assertEqual(ro.normalized_windows(stage), stage)
        for bad in (r"C:\Temp\pilot-" + "a" * 32,
                    stage + r"\..\elsewhere", r"\\server\share\pilot-" + "a" * 32):
            with self.subTest(bad=bad), self.assertRaises(ro.Denied):
                ro.normalized_windows(bad)

    def test_pipeline_requires_matching_pid_project_and_reachable(self):
        stage = "C:\\Users\\Dex\\AppData\\Local\\RPGKingdomSupervisor\\UnityCliPilots\\pilot-" + "a"*32
        receipt = {"stagePath": stage, "unityPid": 73}
        instance = {"projectPath": stage, "pid": 73, "isRunning": True,
                    "hasPipelinePackage": True, "pipelineServer": {"isReachable": True}}
        response = {"success": True, "data": {"instances": [instance]}}
        with patch.object(ro, "cli_json", return_value=response):
            ro.verify_pipeline("fake", Path("/tmp"), receipt)
        for invalid in (
            dict(instance, pid=74),
            dict(instance, projectPath="C:\\Wrong"),
            dict(instance, pipelineServer={"isReachable": False}),
            dict(instance, hasPipelinePackage=False),
            dict(instance, isRunning=False),
        ):
            with self.subTest(invalid=invalid):
                with patch.object(ro, "cli_json", return_value={"data": {"instances": [invalid]}}):
                    with self.assertRaises(ro.Denied):
                        ro.verify_pipeline("fake", Path("/tmp"), receipt)


class GrantAndBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.workspace = self.workspaces / "GH-260"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test",
                        "-c", "user.email=test@example.com", "commit", "--allow-empty",
                        "-qm", "base"], check=True)
        self.state = self.root / "state"
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.scene = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
        dest = self.stage / self.scene
        dest.parent.mkdir(parents=True)
        dest.write_text("%YAML 1.1", encoding="utf-8")
        self.session = r"C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-" + "a" * 32
        self.receipt = {"schemaVersion": 1, "stagePath": self.session,
                        "sessionId": "pilot-" + "a" * 32, "unityPid": 73}
        self.env = patch.dict(os.environ, {
            "RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.workspaces),
            "RPGK_UNITY_CLI_READONLY_ENABLED": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["readonly"] + list(args)), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = ro.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def args(self):
        return ["--workspace", str(self.workspace),
                "--state-root", str(self.state)]

    def test_grant_inspect_revoke_and_revision_binding(self):
        with patch.object(ro, "stage_info", return_value=(self.stage, self.receipt)):
            code, message, _ = self.invoke("grant", *self.args(),
                                           "--session-windows", self.session, "--scene", self.scene)
            self.assertEqual(code, 0, message)
            grant = self.state / "unity-cli-grants" / "GH-260.json"
            self.assertEqual(grant.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(grant.read_text())["workspaceRevision"],
                             ro.workspace_info(str(self.workspace))[1])

            payload = {"data": {"result": {"sceneName": "PlaytestScene", "scenePath": self.scene,
                       "isDirty": False, "roots": []}}}
            with patch.object(ro, "cli_path", return_value="fake"), \
                 patch.object(ro, "verify_pipeline"), \
                 patch.object(ro, "cli_json", return_value=payload):
                code, output, _ = self.invoke("inspect", *self.args(), "--scene", self.scene)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output)["scenePath"], self.scene)
                code, _, err = self.invoke("inspect", *self.args(), "--scene", "Assets/other.unity")
                self.assertEqual(code, 82)
                self.assertIn("not covered", err)

                subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test",
                                "-c", "user.email=test@example.com", "commit", "--allow-empty",
                                "-qm", "new revision"], check=True)
                code, _, err = self.invoke("inspect", *self.args(), "--scene", self.scene)
                self.assertEqual(code, 82)
                self.assertIn("revision", err)

        code, _, _ = self.invoke("revoke", *self.args())
        self.assertEqual(code, 0)
        self.assertFalse(grant.exists())

    def test_default_disabled_even_if_granted(self):
        with patch.object(ro, "stage_info", return_value=(self.stage, self.receipt)):
            self.invoke("grant", *self.args(), "--session-windows", self.session, "--scene", self.scene)
        os.environ.pop("RPGK_UNITY_CLI_READONLY_ENABLED", None)
        code, _, err = self.invoke("inspect", *self.args(), "--scene", self.scene)
        self.assertEqual(code, 82)
        self.assertIn("disabled", err)

    def test_broker_issues_are_locked_and_restricted(self):
        broker_dir = self.workspace / "Logs/SymphonyUnity/.broker/requests"
        broker_dir.mkdir(parents=True)
        request = broker_dir / "r1.json"
        def prepare(extra=None):
            fields = {"protocolVersion": 1, "requestId": "r1",
                      "operation": "editor-scene-summary", "scenePath": self.scene}
            fields.update(extra or {})
            request.write_text(json.dumps(fields), encoding="utf-8")
            return broker.prepare_request(request, self.workspaces, self.state)
        denied = prepare()
        self.assertIsInstance(denied, dict)
        self.assertEqual(denied["exitCode"], 82)
        lock = self.state / "locks/unity-editor.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("GH-260")
        (lock / "workspace").write_text(str(self.workspace))
        success = prepare()
        self.assertIsInstance(success, broker.RequestSpec)
        self.assertEqual(success.operation, "editor-scene-summary")
        for payload in ({"cameraPath": "/Main Camera"}, {"testFilter": "*"},
                        {"scenePath": "Assets/../bad.unity"}):
            denied = prepare(payload)
            self.assertIsInstance(denied, dict)
            self.assertEqual(denied["exitCode"], 64)

        with patch("subprocess.Popen") as popen, \
             patch("tempfile.TemporaryFile", return_value=io.StringIO()):
            active = broker.start_operation(success, self.state, self.root / "old-runner")
            invoked = popen.call_args.args[0]
            self.assertEqual(invoked[1], str(ROOT / "scripts/unity-cli-readonly.py"))
            self.assertEqual(invoked[2], "inspect")
            self.assertNotIn("eval", invoked)
            self.assertNotIn(self.session, invoked)


if __name__ == "__main__":
    unittest.main()
