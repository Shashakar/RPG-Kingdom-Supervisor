#!/usr/bin/env python3
"""Stage C2 fixed-code Unity authoring gate / denial-path regressions."""
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


mod = load("stage_benchmark_under_test", "scripts/unity-cli-stage-benchmark.py")
broker = load("stage_benchmark_broker_test", "scripts/unity-host-broker.py")


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.workspace = self.workspaces / "GH-999999"
        self.workspace.mkdir()
        subprocess.run(["git", "-C", str(self.workspace), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit", "--allow-empty", "-qm",
                        "base"], check=True)
        self.state = self.root / "state"
        self.stage = self.root / "stage"
        self.stage.mkdir()
        for rel in (mod.SCENE, "Packages/manifest.json",
                    "ProjectSettings/ProjectVersion.txt"):
            f = self.stage / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(rel, encoding="utf-8")
        self.receipt = {"stagePath": "C:\\Users\\Dex\\AppData\\Local\\RPGKingdomSupervisor\\UnityCliPilots\\pilot-" + "a"*32,
                        "unityPid": 42, "unityVersion": mod.EDITOR_VERSION}
        self.env = patch.dict(os.environ, {
            "RPGK_UNITY_CLI_BENCHMARK_ENABLED": "1",
            "RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.workspaces),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, *args):
        cmd = ["test", *args, "--workspace", str(self.workspace),
               "--state-root", str(self.state)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", cmd), redirect_stdout(stdout), redirect_stderr(stderr):
            return mod.main(), stdout.getvalue(), stderr.getvalue()

    def grant(self):
        with patch.object(mod.ro, "stage_info", return_value=(self.stage, self.receipt)):
            code, out, err = self.invoke("grant", "--session-windows",
                                         self.receipt["stagePath"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "granted")
        return mod.grant_path(self.state, self.workspace.name)

    def test_allowlisted_fixed_code_and_destination(self):
        self.assertIn("NewSceneMode.Additive", mod.FIXED_CSHARP)
        self.assertIn("SaveScene(scene, relative, false)", mod.FIXED_CSHARP)
        self.assertIn("CloseScene(scene, true)", mod.FIXED_CSHARP)
        self.assertIn("SetActiveScene(original)", mod.FIXED_CSHARP)
        self.assertEqual(mod.DEST, "Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity")
        self.assertNotIn("DeleteAsset", mod.FIXED_CSHARP)
        self.assertNotIn("Process.Start", mod.FIXED_CSHARP)

    def test_ungranted_and_disabled_denied(self):
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("no active", err)
        self.grant()
        os.environ.pop(mod.ENABLE, None)
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("disabled", err)

    def test_grant_is_revision_and_pid_bound(self):
        file = self.grant()
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        with patch.object(mod.ro, "stage_info", return_value=(
            self.stage, dict(self.receipt, unityPid=43))):
            code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("PID", err)

        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit", "--allow-empty",
                        "-qm", "next"], check=True)
        code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("revision", err)

    def test_source_baseline_and_destination_attestation(self):
        self.grant()
        p = self.stage / mod.SCENE
        p.write_text("modified", encoding="utf-8")
        with patch.object(mod.ro, "stage_info", return_value=(self.stage, self.receipt)):
            code, _, err = self.invoke("run")
        self.assertEqual(code, 82)
        self.assertIn("baseline", err)
        p.write_text(mod.SCENE, encoding="utf-8")
        target = self.stage / mod.DEST
        target.parent.mkdir(parents=True)
        target.write_text("existing", encoding="utf-8")
        with self.assertRaises(mod.ro.Denied):
            mod.stage_target(self.stage)

    def test_no_arbitrary_scene_or_stage_selection_by_worker(self):
        self.grant()
        code, _, err = self.invoke("run", "--scene", "Assets/Other.unity")
        self.assertEqual(code, 82)
        self.assertIn("pinned", err)
        code, _, err = self.invoke("run", "--session-windows", self.receipt["stagePath"])
        self.assertEqual(code, 82)
        self.assertIn("worker", err)
        code, _, err = self.invoke("revoke")
        self.assertEqual(code, 0)
        self.assertIn("revoked", err or _)

    def test_author_checks_saves_and_reloads_unmodified_scene(self):
        expected = {"gameObjectCount": 12087}
        with patch.object(mod, "cli_versions"), \
             patch.object(mod, "verify_active_scene", return_value=expected) as scene, \
             patch.object(mod.ro, "cli_json") as cli:
            def write_scene(*args, **kwargs):
                dest = self.stage / mod.DEST
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("SupervisorCliBenchmarkCube\nSupervisorCliBenchmarkRoot",
                                encoding="utf-8")
                return {"success": True}
            cli.side_effect = write_scene
            result = mod.author(self.stage, self.receipt, exe="fake")
        self.assertTrue(result["protectedFilesUnchanged"])
        self.assertFalse(result["copyback"])
        self.assertEqual(result["sourceGameObjectCount"], 12087)
        self.assertEqual(scene.call_count, 2)
        argv = cli.call_args.args[2]
        self.assertEqual(argv[:4], ["command", "eval", "--code", mod.FIXED_CSHARP])
        self.assertNotIn("--source", argv)

    def test_broker_requires_issue_lease_and_prohibits_pose(self):
        request_dir = self.workspace / "Logs/SymphonyUnity/.broker/requests"
        request_dir.mkdir(parents=True)
        request = request_dir / "bench.json"
        def prepare(extra=None):
            data = {"protocolVersion": 1, "operation": "editor-stage-benchmark",
                    "scenePath": mod.SCENE}
            data.update(extra or {})
            request.write_text(json.dumps(data), encoding="utf-8")
            return broker.prepare_request(request, self.workspaces, self.state)
        denied = prepare()
        self.assertIsInstance(denied, dict)
        self.assertEqual(denied["exitCode"], 82)
        lock = self.state / "locks" / "unity-editor.lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("GH-999999")
        (lock / "workspace").write_text(str(self.workspace))
        spec = prepare()
        self.assertIsInstance(spec, broker.RequestSpec)
        for bad in ({"scenePath": "Assets/../Other.unity"},
                    {"cameraPath": "/Camera"}, {"testFilter": "*"}):
            denial = prepare(bad)
            self.assertIsInstance(denial, dict)
            self.assertEqual(denial["exitCode"], 64)
        with patch.object(broker.subprocess, "Popen") as popen:
            active = broker.start_operation(spec, self.state, self.root / "legacy-host")
            self.assertIn("unity-cli-stage-benchmark.py", popen.call_args.args[0][1])
            self.assertEqual(popen.call_args.args[0][2], "run")
            self.assertNotIn("eval", popen.call_args.args[0])
            broker.close_operation(active)


if __name__ == "__main__":
    unittest.main()
