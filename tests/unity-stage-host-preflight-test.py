#!/usr/bin/env python3
"""C5e host stage preflight tests. No Unity/PowerShell or project writes."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "c5e_host_preflight",
    Path(__file__).resolve().parents[1] / "scripts/unity-stage-host-preflight.py")
inspect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inspect
SPEC.loader.exec_module(inspect)


class StageHostInspectionTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.parent = self.root / "workspaces"
        self.parent.mkdir()
        self.ws = self.parent / "GH-261"
        self.ws.mkdir()
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.state = self.root / "operator-state"
        (self.state / "unity-broker").mkdir(parents=True)
        for project in (self.ws, self.stage):
            for folder in inspect.proposal.CHECK_ROOTS:
                (project / folder).mkdir()
            self.add(project, inspect.ORIGINAL_SCENE, "ORIGINAL SOURCE SCENE")
            self.add(project, inspect.VERSION_FILE, "m_EditorVersion: 6000.3.10f1\n")
            self.add(project, "Packages/manifest.json", "{}\n")
        self.add(self.ws, "Assets/Settings/Mobile_RPAsset.asset", "version: 12\n")
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@test", "commit", "-qm", "initial"], check=True)
        self.rev = subprocess.check_output(
            ["git", "-C", str(self.ws), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.ws), "update-ref",
                        "refs/remotes/origin/main", self.rev], check=True)
        proof = inspect.proposal.PROOF
        self.add(self.stage, proof, "scene")
        self.add(self.stage, proof + ".meta", "guid: " + "a" * 32 + "\n")
        self.add(self.stage, inspect.proposal.FOLDER_META, "guid: " + "b" * 32 + "\n")
        self.add(self.stage, "Assets/Settings/Mobile_RPAsset.asset", "version: 13\n")
        self.windows = ("C:\\Users\\Dex\\AppData\\Local\\RPGKingdomSupervisor"
                        "\\UnityCliPilots\\pilot-" + "a" * 32)
        self.pid = 9648
        self.receipt = {
            "stagePath": self.windows,
            "unityPid": self.pid,
            "sourceProjectPath": "C:\\fake\\GH-261",
        }
        grant = {
            "schemaVersion": 1, "enabled": True,
            "mode": "operator-trusted-staged-editor-code",
            "issue": "GH-261", "workspace": str(self.ws),
            "workspaceRevision": self.rev,
            "scenePath": inspect.c3.SCENE, "stagePath": self.windows,
            "unityPid": self.pid, "protectedBaseline": {},
        }
        grantpath = self.state / "unity-staged-code-grants" / "GH-261.json"
        grantpath.parent.mkdir()
        grantpath.write_text(json.dumps(grant))
        self.brokerstatus = self.state / "unity-broker/status.json"
        self.set_broker()
        self.processes = [
            {"ProcessId": self.pid, "ParentProcessId": 20,
             "CommandLine": f'Unity.exe -projectPath "{self.windows}"'},
            {"ProcessId": 2064, "ParentProcessId": self.pid,
             "CommandLine": f'Unity.exe -adb2 -name AssetImportWorker0 -projectPath "{self.windows}"'},
        ]
        self.env = patch.dict(os.environ, {"RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.parent)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def add(self, root, rel, body):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        return p

    def set_broker(self, *, state="ready", pid=None, active=None):
        self.brokerstatus.write_text(json.dumps({
            "protocolVersion": 1, "state": state,
            "activeRequest": active, "pid": os.getpid() if pid is None else pid,
            "workspaceRoot": str(self.parent),
        }))

    def _process_preflight(self, workspace, revision, state):
        self.assertEqual(revision, self.rev)
        self.assertEqual(workspace, self.ws)
        self.assertEqual(state, self.state)
        granted = inspect.c3.current_grant(state, workspace, revision)
        self.assertEqual(granted["unityPid"], self.pid)
        parsed = inspect.c3.verify_exclusive_persistent_editor(self.receipt, self.processes)
        return {
            "status": "ready",
            "mode": "operator-granted-persistent-unity-editor",
            "issue": workspace.name, "workspaceRevision": revision,
            **parsed, "copyback": False,
        }

    def check(self):
        with patch.object(inspect, "filesystem_transport", return_value="9p"), \
             patch.object(inspect.c3.ro, "stage_info", return_value=(self.stage, self.receipt)), \
             patch.object(inspect.c3, "persistent_preflight", side_effect=self._process_preflight):
            return inspect.host_inspection(self.ws, self.state)

    def test_valid_snapshot_is_consistent_but_not_frozen_or_authorized(self):
        source_before = subprocess.check_output(["git", "-C", str(self.ws), "status", "--porcelain"])
        result = self.check()
        self.assertEqual(result["status"], "inspection-consistent-not-quiescent")
        self.assertEqual(result["issue"], "GH-261")
        self.assertEqual(result["unityPid"], self.pid)
        self.assertEqual(result["assetImportWorkerPids"], [2064])
        self.assertEqual(result["unityVersion"], "6000.3.10f1")
        self.assertGreaterEqual(result["quarantinedCount"], 1)
        self.assertFalse(result["stageFrozen"])
        self.assertFalse(result["independentHumanAuthorization"])
        self.assertFalse(result["promotionAuthorized"])
        self.assertFalse(result["copyback"])
        self.assertFalse(result["nonceConsumed"])
        self.assertEqual(subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]), source_before)

    def test_missing_grant_blocks(self):
        (self.state / "unity-staged-code-grants/GH-261.json").unlink()
        with self.assertRaisesRegex(inspect.c3.ro.Denied, "no host grant"):
            self.check()

    def test_foreign_unity_process_and_stale_editor_pid_block(self):
        self.processes.append({
            "ProcessId": 9999, "ParentProcessId": 1,
            "CommandLine": 'Unity.exe -projectPath "C:\\OtherProject"',
        })
        with self.assertRaisesRegex(inspect.c3.ro.Denied, "unrelated Unity"):
            self.check()
        self.processes.pop()
        self.processes[0]["ProcessId"] = 8811
        with self.assertRaisesRegex(inspect.c3.ro.Denied, "PID is missing"):
            self.check()

    def test_busy_broker_and_abandoned_lease_block(self):
        self.set_broker(state="running", active={"requestId": "other"})
        with self.assertRaisesRegex(inspect.InspectionDenied, "active, blocked"):
            self.check()
        self.set_broker()
        lease = self.state / "locks/unity-editor.lock"
        lease.mkdir(parents=True)
        (lease / "owner").write_text("GH-261")
        with self.assertRaisesRegex(inspect.InspectionDenied, "lease still exists"):
            self.check()

    def test_dead_broker_and_pid_change_during_inspection_block(self):
        self.set_broker(pid=999999999)
        with self.assertRaisesRegex(inspect.InspectionDenied, "PID is not alive"):
            self.check()
        self.set_broker()
        original = inspect.proposal.manifest
        observed = [0]

        def broker_race(workspace, stage):
            output = original(workspace, stage)
            observed[0] += 1
            if observed[0] == 1:
                self.set_broker(pid=100005999)
            return output

        with patch.object(inspect.proposal, "manifest", side_effect=broker_race):
            with self.assertRaisesRegex(inspect.InspectionDenied, "PID is not alive"):
                self.check()

    def test_modified_stage_or_protected_scene_during_scan_blocks(self):
        original = inspect.proposal.manifest
        observed = [0]

        def mutation(workspace, stage):
            output = original(workspace, stage)
            observed[0] += 1
            if observed[0] == 1:
                self.add(self.stage, inspect.proposal.PROOF, "changed after snapshot")
            return output

        with patch.object(inspect.proposal, "manifest", side_effect=mutation):
            with self.assertRaisesRegex(inspect.InspectionDenied, "stage or source changed"):
                self.check()
        self.add(self.stage, inspect.ORIGINAL_SCENE, "changed protected source scene")
        with self.assertRaisesRegex(inspect.proposal.AuditDenied, "original PlaytestScene modified"):
            self.check()

    def test_source_dirty_or_stale_ref_blocks(self):
        self.add(self.ws, "Assets/untracked.txt", "dirty")
        with self.assertRaisesRegex(inspect.proposal.AuditDenied, "not clean"):
            self.check()
        (self.ws / "Assets/untracked.txt").unlink()
        # Move only origin/main to an actual commit, keeping the operator
        # grant bound to the original HEAD. This reaches the source gate.
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=Test",
                        "-c", "user.email=test@test", "commit",
                        "--allow-empty", "-qm", "advance"], check=True)
        other_head = subprocess.check_output(
            ["git", "-C", str(self.ws), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.ws), "reset",
                        "--hard", self.rev], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.ws), "update-ref",
                        "refs/remotes/origin/main", other_head], check=True)
        with self.assertRaisesRegex(inspect.proposal.AuditDenied, "origin/main"):
            self.check()

    def test_symlink_and_wrong_editor_version_block(self):
        version = self.stage / inspect.VERSION_FILE
        version.unlink()
        version.symlink_to(self.ws / inspect.VERSION_FILE)
        with self.assertRaisesRegex(inspect.InspectionDenied, "linked directory"):
            self.check()
        version.unlink()
        version.write_text("m_EditorVersion: 6000.4.0f1\n")
        with self.assertRaisesRegex(inspect.InspectionDenied, "changed or unsupported"):
            self.check()

    def test_broker_state_change_even_same_pid_blocks(self):
        original = inspect.proposal.manifest
        observed = [0]

        def busy_midflight(workspace, stage):
            result = original(workspace, stage)
            observed[0] += 1
            if observed[0] == 1:
                self.set_broker(state="running", active={"requestId": "new"})
            return result

        with patch.object(inspect.proposal, "manifest", side_effect=busy_midflight):
            with self.assertRaisesRegex(inspect.InspectionDenied, "active, blocked"):
                self.check()

    def test_unsupported_filesystem_and_stage_identity_fail_closed(self):
        with patch.object(inspect.c3.ro, "stage_info", return_value=(self.stage, self.receipt)):
            with self.assertRaisesRegex(inspect.InspectionDenied, "Windows C: mount"):
                inspect.host_inspection(self.ws, self.state)
        self.receipt["sourceProjectPath"] = None
        with self.assertRaisesRegex(inspect.InspectionDenied, "missing explicit source"):
            self.check()


if __name__ == "__main__":
    unittest.main()
