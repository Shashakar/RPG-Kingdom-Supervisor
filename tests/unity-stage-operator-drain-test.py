#!/usr/bin/env python3
"""C5f operator broker-drain unit and real subprocess dispatch tests.

The broker subprocess uses a fake shell runner in private temp directories.
No Windows process, Unity installation, or game source files are accessed.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / "scripts"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BASE / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


gate = load("c5f_gate_tests", "unity-stage-drain-gate.py")
operator = load("c5f_operator_tests", "unity-stage-operator-drain.py")


class DrainTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "host"
        self.state.mkdir()
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.workspace = self.workspaces / "GH-261"
        self.workspace.mkdir()
        self.base = {
            "status": "inspection-consistent-not-quiescent",
            "issue": "GH-261", "workspaceRevision": "a" * 40,
            "stagePath": r"C:\stage\pilot-abc",
            "unityPid": 9648, "proposalSha256": "b" * 64,
            "stageFrozen": False, "promotionAuthorized": False,
        }

    def stubbed_activate(self, **override):
        base = dict(self.base, **override)
        with patch.object(operator.preflight, "host_inspection", return_value=base), \
             patch.object(operator.preflight, "broker_idle"), \
             patch.object(operator.preflight, "lease_absent"), \
             patch.object(operator.preflight.c3.ro, "workspace_info",
                          return_value=(self.workspace, base["workspaceRevision"])), \
             patch.object(operator.preflight.c3, "persistent_preflight",
                          return_value={"stagePath": base["stagePath"], "unityPid": base["unityPid"]}), \
             patch.object(operator.preflight.c3.ro, "stage_info",
                          return_value=(self.root / "stage", {})), \
             patch.object(operator.preflight.proposal, "manifest", return_value={}), \
             patch.object(operator.preflight.review, "manifest_digest",
                          return_value=base["proposalSha256"]):
            return operator.activate(self.workspace, self.state)

    def test_activation_is_dispatch_drain_only_and_stays_durable(self):
        result = self.stubbed_activate()
        self.assertEqual(result["status"], "broker-dispatch-draining-not-frozen")
        self.assertTrue(result["brokerDispatchGateActive"])
        for key in ("stageFrozen", "independentHumanAuthorization", "promotionAuthorized", "copyback"):
            self.assertFalse(result[key])
        self.assertEqual(gate.status(self.state), "draining")
        with gate.locked(self.state) as d:
            observed = gate.read_locked(d)
        self.assertEqual(observed["issue"], "GH-261")
        self.assertEqual(observed["operationId"], result["operationId"])
        with self.assertRaises(operator.HandoffDenied):
            self.stubbed_activate()

    def test_identity_or_stage_race_blocks_before_activation(self):
        with self.assertRaisesRegex(operator.HandoffDenied, "changed"):
            self.stubbed_activate(unityPid=9600) if False else self._mismatch_test()
        self.assertEqual(gate.status(self.state), "ready")

    def _mismatch_test(self):
        with patch.object(operator.preflight, "host_inspection", return_value=self.base), \
             patch.object(operator.preflight, "broker_idle"), \
             patch.object(operator.preflight, "lease_absent"), \
             patch.object(operator.preflight.c3.ro, "workspace_info",
                          return_value=(self.workspace, "a" * 40)), \
             patch.object(operator.preflight.c3, "persistent_preflight",
                          return_value={"stagePath": self.base["stagePath"], "unityPid": 777}):
            return operator.activate(self.workspace, self.state)

    def test_cancel_refuses_forged_owner_or_busy_broker(self):
        record = self.stubbed_activate()
        opid = record["operationId"]
        broker = self.state / "unity-broker/status.json"
        broker.parent.mkdir()
        broker.write_text(json.dumps({
            "protocolVersion": 1, "state": "draining",
            "activeRequest": None, "workspaceRoot": str(self.workspaces),
            "pid": os.getpid(),
        }))
        with self.assertRaises(operator.HandoffDenied):
            operator.cancel("GH-262", opid, self.state)
        with self.assertRaises(operator.HandoffDenied):
            operator.cancel("GH-261", "0" * 32, self.state)
        data = json.loads(broker.read_text())
        data["activeRequest"] = {"requestId": "GH-261-running"}
        broker.write_text(json.dumps(data))
        with self.assertRaisesRegex(operator.HandoffDenied, "not demonstrably idle"):
            operator.cancel("GH-261", opid, self.state)
        self.assertEqual(gate.status(self.state), "draining")
        data["activeRequest"] = None
        broker.write_text(json.dumps(data))
        out = operator.cancel("GH-261", opid, self.state)
        self.assertEqual(out["status"], "broker-dispatch-drain-cancelled")
        self.assertEqual(gate.status(self.state), "ready")

    def test_malformed_and_linked_gate_state_fail_closed(self):
        with gate.locked(self.state) as d:
            marker = d / "state.json"
            marker.write_text("{bad")
        self.assertEqual(gate.status(self.state), "blocked")
        with gate.locked(self.state) as d:
            with self.assertRaises(gate.DrainDenied):
                gate.read_locked(d)
            (d / "state.json").unlink()
            (d / "state.json").symlink_to(self.root / "outside")
        self.assertEqual(gate.status(self.state), "blocked")

    def test_broker_rejects_requests_while_draining_then_resumes(self):
        # Synthetic broker environment, no Unity or Windows runner.
        fake = self.root / "fake-unity-host.sh"
        launched = self.root / "launched"
        fake.write_text("#!/usr/bin/env bash\nprintf 'LAUNCHED\\n' >> " + repr(str(launched)) + "\n")
        broker_script = BASE / "unity-host-broker.py"
        proc = subprocess.Popen(
            [sys.executable, str(broker_script),
             "--workspace-root", str(self.workspaces),
             "--state-root", str(self.state),
             "--host-runner", str(fake), "--poll-ms", "30"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.shutdown, proc)
        status_path = self.state / "unity-broker/status.json"
        self.wait_for(lambda: status_path.is_file()
                      and json.loads(status_path.read_text()).get("state") == "ready")
        # Hold the same flock the broker uses for Popen; the marker must be
        # installed atomically before any pending request can be dispatched.
        with gate.locked(self.state) as d:
            self.request("blocked")
            gate.write_locked(d, {
                "schemaVersion": 1, "phase": "draining", "issue": "GH-261",
                "workspace": str(self.workspace),
                "workspaceRevision": "a" * 40,
                "stagePath": r"C:\fake-stage",
                "createdAt": "2026-10-11T00:00:00Z",
                "operationId": "a" * 32,
            })
            time.sleep(0.15)
            self.assertFalse(launched.exists())
        denied = self.wait_response("blocked")
        self.assertEqual(denied["status"], "StageDraining")
        self.assertEqual(denied["exitCode"], 82)
        self.assertFalse(launched.exists())
        self.wait_for(lambda: json.loads(status_path.read_text()).get("state") == "draining")
        # A broker restart while draining must also remain blocked.
        proc.terminate()
        proc.wait(timeout=5)
        proc2 = subprocess.Popen(
            [sys.executable, str(broker_script),
             "--workspace-root", str(self.workspaces),
             "--state-root", str(self.state),
             "--host-runner", str(fake), "--poll-ms", "30"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.shutdown, proc2)
        self.wait_for(lambda: json.loads(status_path.read_text()).get("state") == "draining")
        self.request("restarted")
        self.assertEqual(self.wait_response("restarted")["status"], "StageDraining")
        self.assertFalse(launched.exists())
        # Operator cancellation after idle broker check re-enables ordinary
        # broker scheduling; still no asset-copy/write authorization.
        operator.cancel("GH-261", "a" * 32, self.state)
        self.request("allowed")
        response = self.wait_response("allowed")
        self.assertNotEqual(response["status"], "StageDraining")
        self.wait_for(launched.exists)

    @staticmethod
    def shutdown(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    def request(self, name):
        folder = self.workspace / "Logs/SymphonyUnity/.broker/requests"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.json").write_text(json.dumps({
            "protocolVersion": 1, "requestId": name, "operation": "health",
        }))

    def wait_response(self, name):
        target = self.workspace / f"Logs/SymphonyUnity/.broker/responses/{name}.json"
        self.wait_for(target.is_file)
        return json.loads(target.read_text())

    def wait_for(self, action, seconds=7):
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            try:
                if action():
                    return
            except (json.JSONDecodeError, OSError):
                pass
            time.sleep(0.05)
        self.fail("timed out waiting for synthetic broker state")


if __name__ == "__main__":
    unittest.main()
