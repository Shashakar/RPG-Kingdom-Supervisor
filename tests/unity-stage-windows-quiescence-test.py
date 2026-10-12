#!/usr/bin/env python3
"""C5g fail-closed tests with fake Windows census and local disposable state."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / "scripts"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BASE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


quiescence = load("c5g_windows_quiescence_test_module", "unity-stage-windows-quiescence.py")
gate = quiescence.gate

GOOD_CENSUS = {
    "unityProcesses": [], "volumeDevice": "C:",
    "volumeFilesystem": "NTFS", "volumeSerial": "ABCD-9876",
}


class WindowsQuiescenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "host"
        self.state.mkdir()
        self.wsroot = self.root / "workspaces"
        self.wsroot.mkdir()
        self.ws = self.wsroot / "GH-261"
        self.ws.mkdir()
        self.stage = self.root / "fake-stage"
        self.stage.mkdir()
        self.record = {
            "schemaVersion": 1, "phase": "draining",
            "issue": "GH-261", "workspace": str(self.ws),
            "workspaceRevision": "a" * 40, "stagePath": "C:\\fake\\stage",
            "createdAt": "2026-10-11T00:00:00Z",
            "operationId": "b" * 32,
        }
        with gate.locked(self.state) as root:
            gate.write_locked(root, self.record)
        self.broker = self.state / "unity-broker/status.json"
        self.broker.parent.mkdir()
        self.broker.write_text(json.dumps({
            "protocolVersion": 1, "state": "draining",
            "pid": os.getpid(),
            "workspaceRoot": str(self.wsroot),
            "activeRequest": None,
        }))
        self.manifest = {
            "sourceRevision": "a" * 40,
            "sourceSceneSha256": "f" * 64, "stageSceneSha256": "f" * 64,
            "changes": [], "counts": {"changed": 0, "quarantined": 0},
        }
        self.receipt = {"unityPid": 8111}
        self.grant = {"unityPid": 8111, "stagePath": self.record["stagePath"]}

    def observed(self, census=lambda: dict(GOOD_CENSUS), manifest_fn=None):
        with patch.object(quiescence.c5e.c3.ro, "workspace_info",
                          return_value=(self.ws, "a" * 40)), \
             patch.object(quiescence.c5e.c3, "current_grant",
                          return_value=self.grant), \
             patch.object(quiescence.c5e.c3.ro, "stage_info",
                          return_value=(self.stage, self.receipt)), \
             patch.object(quiescence.c5e, "filesystem_transport",
                          return_value="9p"), \
             patch.object(quiescence.c5e, "project_version",
                          return_value=quiescence.c5e.SUPPORTED_UNITY), \
             patch.object(quiescence.c5e, "lease_absent"), \
             patch.object(quiescence.c5e.proposal, "manifest",
                          side_effect=manifest_fn or (lambda *a: dict(self.manifest))):
            return quiescence.observe(self.state, census=census)

    def test_valid_census_remains_observation_only(self):
        result = self.observed()
        self.assertEqual(result["status"], "windows-quiescence-observed-not-frozen")
        self.assertEqual(result["windowsProcessChecks"], 3)
        self.assertEqual(result["volumeSerialObserved"], "ABCD-9876")
        self.assertEqual(result["operationId"], "b" * 32)
        for key in ("stageFrozen", "immutableSnapshotVerified", "independentHumanAuthorization",
                    "promotionAuthorized", "copyback", "nonceConsumed"):
            self.assertFalse(result[key])

    def test_process_or_volume_identity_denied(self):
        for invalid in [
            dict(GOOD_CENSUS, unityProcesses=[{"ProcessId": 8111, "CommandLine": "Unity.exe"}]),
            dict(GOOD_CENSUS, unityProcesses=None),
            dict(GOOD_CENSUS, unityProcesses={}),
            dict(GOOD_CENSUS, volumeDevice="D:"),
            dict(GOOD_CENSUS, volumeFilesystem="exFAT"),
            dict(GOOD_CENSUS, volumeSerial=""),
            dict(GOOD_CENSUS, volumeSerial="1"*40),
            dict(GOOD_CENSUS, additionalApproval=True),
            {},
            [],
        ]:
            with self.subTest(data=invalid):
                with self.assertRaises(quiescence.QuiescenceDenied):
                    quiescence.validate_census(invalid)

    def test_process_appears_between_scans_denied(self):
        snapshots = iter([
            dict(GOOD_CENSUS),
            dict(GOOD_CENSUS, volumeSerial="1234-5678"),
        ])
        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "changed"):
            self.observed(lambda: next(snapshots))

    def test_broker_busy_or_gone_denied(self):
        status = json.loads(self.broker.read_text())
        for value in ("ready", "running", "blocked", "dispatching"):
            status["state"] = value
            self.broker.write_text(json.dumps(status))
            with self.subTest(value=value), self.assertRaisesRegex(
                    quiescence.QuiescenceDenied, "not drained"):
                self.observed()
        status["state"] = "draining"
        status["pid"] = 999999999
        self.broker.write_text(json.dumps(status))
        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "not alive"):
            self.observed()

    def test_revoked_or_mismatched_grant_denied(self):
        self.grant["stagePath"] = r"C:\other\stage"
        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "grant changed"):
            self.observed()
        self.grant["stagePath"] = self.record["stagePath"]
        self.grant["unityPid"] = 9000
        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "PID disagree"):
            self.observed()

    def test_stage_changes_between_manifests_denied(self):
        counter = [0]

        def changing(*args):
            counter[0] += 1
            return {**self.manifest, "changes": [{"path": str(counter[0])}]}

        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "manifest changed"):
            self.observed(manifest_fn=changing)

    def test_stale_source_revision_denied_after_scan(self):
        def stale(*args):
            return {**self.manifest, "sourceRevision": "c" * 40}
        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "source revision"):
            self.observed(manifest_fn=stale)

    def test_missing_drain_denied(self):
        (self.state / "unity-stage-drain/state.json").unlink()
        with self.assertRaisesRegex(quiescence.QuiescenceDenied, "not active"):
            self.observed()

    def test_windows_failure_and_json_error_denied(self):
        for result in [
            subprocess.CompletedProcess([], 1, "", "denied"),
            subprocess.CompletedProcess([], 0, "{bad", ""),
        ]:
            with self.subTest(result=result), self.assertRaises(quiescence.QuiescenceDenied):
                quiescence.windows_census(run=lambda *a, **k: result)


if __name__ == "__main__":
    unittest.main()
