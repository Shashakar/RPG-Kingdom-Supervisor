#!/usr/bin/env python3
"""Protocol regressions for read-only Unity scene inspection requests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("unity_host_broker", ROOT / "scripts" / "unity-host-broker.py")
assert SPEC is not None and SPEC.loader is not None
broker = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = broker
SPEC.loader.exec_module(broker)


class UnityInspectionProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspaces = self.base / "workspaces"
        self.workspace = self.workspaces / "GH-260"
        self.directory = self.workspace / "Logs" / "SymphonyUnity" / ".broker" / "requests"
        self.directory.mkdir(parents=True)
        self.state = self.base / "state"
        lock = self.state / "locks" / "unity-editor.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("GH-260")
        (lock / "workspace").write_text(str(self.workspace))
        self.request_file = self.directory / "inspection-test.json"

    def prepare(self, **kwargs):
        data = {"protocolVersion": 1, "requestId": "inspection-test", "operation": "inspect",
                "scenePath": "Assets/RPGKingdom/Scenes/PlaytestScene.unity"}
        data.update(kwargs)
        self.request_file.write_text(json.dumps(data))
        return broker.prepare_request(self.request_file, self.workspaces.resolve(), self.state)

    def test_inspection_is_exact_scene_and_workspace_locked(self):
        spec = self.prepare()
        self.assertIsInstance(spec, broker.RequestSpec)
        self.assertEqual("inspect", spec.operation)
        self.assertEqual("Assets/RPGKingdom/Scenes/PlaytestScene.unity", spec.scene_path)

    def test_inspection_rejects_camera_pose_and_missing_scene(self):
        for kwargs in ({"scenePath": ""}, {"cameraPath": "MainCamera"},
                       {"lookAtPath": "World/Target"}, {"cameraRotation": "0,0,0"}):
            with self.subTest(kwargs=kwargs):
                self.assertIsInstance(self.prepare(**kwargs), dict)

    def test_inspection_denied_without_lock(self):
        (self.state / "locks" / "unity-editor.lock" / "owner").write_text("GH-258")
        self.assertIsInstance(self.prepare(), dict)

    def test_inspection_command_routes_scene_to_host(self):
        from unittest.mock import patch, MagicMock
        spec = self.prepare()
        commands = []
        def fake_popen(command, **kwargs):
            commands.append(command)
            child = MagicMock()
            child.pid = 12345
            child.poll.return_value = None
            return child
        with patch.object(broker.subprocess, "Popen", side_effect=fake_popen):
            active = broker.start_operation(spec, self.state, self.base / "host.sh")
        self.assertEqual("inspect", commands[0][2])
        self.assertIn("--scene", commands[0])
        self.assertIn(spec.scene_path, commands[0])
        active.stdout_handle.close()
        active.stderr_handle.close()


if __name__ == "__main__":
    unittest.main()
