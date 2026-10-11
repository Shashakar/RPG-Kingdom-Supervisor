#!/usr/bin/env python3
"""C2c mock host and gate regressions. Never changes Windows users or ACLs."""
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


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


bridge = load("c2c_isolated_bridge_test", "unity-isolated-mailbox.py")
broker = load("c2c_isolated_broker_test", "unity-host-broker.py")


class IsolatedBridgeTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.workspaces = self.root / "workspaces"
        self.ws = self.workspaces / "GH-999999"
        self.ws.mkdir(parents=True)
        self.state = self.root / "state"
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit", "-qm",
                        "Synthetic baseline", "--allow-empty"], check=True)
        self.sid = "S-1-5-21-111-222-333-1001"
        self.id = "isolated-" + "a" * 32
        self.host = self.root / self.id
        self.queue = self.host / "queue"
        for name in ("requests", "responses", "status"):
            (self.queue / name).mkdir(parents=True)
        self.host_win = r"C:\ProgramData\RPGKingdomSupervisor\IsolatedUnity\\".rstrip("\\") + "\\" + self.id
        self.config = {
            "schemaVersion": 1, "enabled": True, "mode": "isolated-readonly",
            "hostId": self.id, "SandboxSid": self.sid,
            "StagePath": self.host_win + r"\project",
            "QueueRoot": self.host_win + r"\queue", "AllowedScene": bridge.SCENE,
        }
        (self.host / "config.json").write_text(json.dumps(self.config))
        (self.queue / "status/probes.json").write_text(json.dumps({
            "status": "denial-probes-passed", "sandboxSid": self.sid,
            "readProbeCount": 2, "writeProbeCount": 1,
            "readyForArbitraryEval": False, "readyForCopyback": False,
            "configAndAgentWriteDenied": True, "operatorRequestWriteDenied": True,
            "stageWriteConfirmed": True,
        }))
        (self.queue / "status/editor.json").write_text(json.dumps({
            "status": "launched", "sandboxSid": self.sid,
            "stagePath": self.config["StagePath"], "unityPid": 123,
        }))
        (self.queue / "status/ready.json").write_text(json.dumps({
            "schemaVersion": 1, "status": "scene-opened", "sandboxSid": self.sid,
            "scenePath": bridge.SCENE, "unityPid": 123,
            "readyForArbitraryEval": False, "readyForCopyback": False,
        }))
        self.env = patch.dict(os.environ, {
            "RPGK_UNITY_ISOLATED_READONLY_ENABLED": "1",
            "RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.workspaces),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, action, *args):
        out, err = io.StringIO(), io.StringIO()
        argv = ["mailbox", action, "--workspace", str(self.ws),
                "--state-root", str(self.state), *args]
        with patch.object(sys, "argv", argv), \
             patch.object(bridge, "host_dir_windows", return_value=self.host), \
             redirect_stdout(out), redirect_stderr(err):
            result = bridge.main()
        return result, out.getvalue(), err.getvalue()

    def grant(self):
        code, out, err = self.invoke("grant", "--host-dir-windows", self.host_win)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "granted-readonly")

    def test_host_path_canonical_and_sid_match(self):
        self.assertEqual(bridge.trusted_config(self.host)["SandboxSid"], self.sid)
        self.assertEqual(bridge.health(self.host, self.config), 123)
        self.assertTrue(bridge.SID.fullmatch(self.sid))
        for bad in (r"D:\ProgramData\Elsewhere", r"C:\Users\Dex\Unity",
                    r"C:\ProgramData\RPGKingdomSupervisor\IsolatedUnity\..\bad"):
            with patch.object(bridge.subprocess, "run") as fake:
                with self.assertRaises(bridge.ro.Denied):
                    bridge.host_dir_windows(bad)
                fake.assert_not_called()

    def test_ungranted_and_disabled_denied(self):
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("no explicit operator grant", err)
        self.grant()
        os.environ.pop(bridge.ENABLED, None)
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("disabled by default", err)

    def test_probe_receipt_mismatch_and_changed_editor_denied(self):
        bad = json.loads((self.queue / "status/probes.json").read_text())
        bad["sandboxSid"] = "S-1-5-21-4-5-6-1001"
        (self.queue / "status/probes.json").write_text(json.dumps(bad))
        code, _, err = self.invoke("grant", "--host-dir-windows", self.host_win)
        self.assertEqual(code, 82)
        self.assertIn("not acceptable", err)
        bad["sandboxSid"] = self.sid
        (self.queue / "status/probes.json").write_text(json.dumps(bad))
        self.grant()
        receipt = json.loads((self.queue / "status/editor.json").read_text())
        receipt["unityPid"] = 124
        (self.queue / "status/editor.json").write_text(json.dumps(receipt))
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("not acceptable", err)

    def test_revision_binding_and_revoke(self):
        self.grant()
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit", "-qm",
                        "Rev changed", "--allow-empty"], check=True)
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("grant no longer binds", err)
        code, out, _ = self.invoke("revoke")
        self.assertEqual(code, 0)
        self.assertFalse(bridge.grant_path(self.state, self.ws.name).exists())

    def test_mock_readonly_mailbox_response(self):
        self.grant()
        wrote = []
        def deliver(_):
            files = list((self.queue / "requests").glob("*.json"))
            if not files:
                return
            req = json.loads(files[0].read_text())
            self.assertEqual(set(req), {"schemaVersion", "requestId", "operation", "scenePath"})
            self.assertEqual(req["operation"], "get_scene_hierarchy")
            response = {
                "schemaVersion": 1, "requestId": req["requestId"],
                "operation": "get_scene_hierarchy", "success": True,
                "scenePath": bridge.SCENE, "gameObjectCount": 12087,
                "rootCount": 7, "terrainPaths": ["/World/PlaytestEnvironment/AuthoredGround"],
                "stageOnly": True, "copyback": False,
            }
            (self.queue / "responses" / (req["requestId"] + ".json")).write_text(json.dumps(response))
            wrote.append(True)
        with patch.object(bridge.time, "sleep", side_effect=deliver):
            code, out, err = self.invoke("run")
        self.assertEqual(code, 0, err)
        output = json.loads(out)
        self.assertEqual(output["gameObjectCount"], 12087)
        self.assertFalse(output["copyback"])
        self.assertFalse(output["readyForArbitraryEval"])
        self.assertTrue(wrote)
        self.assertFalse(list((self.queue / "requests").glob("*.json")))

    def test_broker_access_and_forbidden_parameters(self):
        folder = self.ws / "Logs/SymphonyUnity/.broker/requests"
        folder.mkdir(parents=True)
        req = folder / "one.json"
        def prepare(extra=None):
            data = {"protocolVersion": 1, "operation": "isolated-scene-summary",
                    "scenePath": bridge.SCENE}
            data.update(extra or {})
            req.write_text(json.dumps(data))
            return broker.prepare_request(req, self.workspaces, self.state)
        denied = prepare()
        self.assertIsInstance(denied, dict)
        self.assertEqual(denied["exitCode"], 82)
        lock = self.state / "locks/unity-editor.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("GH-999999")
        (lock / "workspace").write_text(str(self.ws))
        prepared = prepare()
        self.assertIsInstance(prepared, broker.RequestSpec)
        for extra in ({"cameraPath": "Camera"}, {"testFilter": "Anything"},
                      {"scenePath": "Assets/../Bad.unity"}):
            self.assertEqual(prepare(extra)["exitCode"], 64)
        with patch.object(broker.subprocess, "Popen") as popen:
            active = broker.start_operation(prepared, self.state, self.root / "legacy-host")
            args = popen.call_args.args[0]
            self.assertTrue(args[1].endswith("unity-isolated-mailbox.py"))
            self.assertIn("run", args)
            self.assertNotIn("powershell.exe", args)
            broker.close_operation(active)

    def test_windows_scripts_never_expose_worker_shell_or_eval(self):
        install = (ROOT / "scripts/windows/install-isolated-unity-host.ps1").read_text()
        worker = (ROOT / "scripts/windows/run-isolated-unity-host.ps1").read_text()
        self.assertIn("Register-ScheduledTask", install)
        self.assertIn("Get-Credential", install)
        self.assertIn("Get-LocalGroupMember", install)
        self.assertIn("Set-Acl", install)
        self.assertIn("IsReadDenied", worker)
        self.assertIn("IsWriteDenied", worker)
        self.assertIn("IsFileWriteDenied", worker)
        self.assertIn("IsStageWritable", worker)
        self.assertIn("get_scene_hierarchy", worker)
        # One trusted, constant Editor command opens the pinned scene at startup;
        # the mailbox accepts only read-only hierarchy inspection.
        self.assertEqual(worker.count('CliJson @("command","eval"'), 1)
        self.assertIn("$sceneCode = 'return UnityEditor.SceneManagement.EditorSceneManager.OpenScene", worker)
        self.assertIn('$req.operation -eq "get_scene_hierarchy"', worker)
        self.assertIn('@($req.PSObject.Properties.Name).Count -eq 4', worker)
        self.assertNotIn("Invoke-Expression", worker)
        self.assertNotIn("scriptBlock", worker)


if __name__ == "__main__":
    unittest.main()
