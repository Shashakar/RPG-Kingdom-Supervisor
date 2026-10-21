#!/usr/bin/env python3
"""Offline regression for opt-in Claude queue, host broker, review and crash boundaries."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClaudeDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.root = Path(self.temp.name) / "ws"
        with mock.patch.dict(os.environ, {
            "RPGK_REPO": "Shashakar/Rekindle-Claude",
            "RPGK_BASE_BRANCH": "rekindle-vertical-slice",
            "RPGK_SUPERVISOR_STATE_ROOT": str(self.state),
            "RPGK_SYMPHONY_WORKSPACE_ROOT": str(self.root),
        }):
            self.mod = load("claude_dispatcher_test", "claude-dispatcher.py")
        self.item = {
            "number": 91, "state": "OPEN", "title": "Test feature",
            "body": "Implement one small C# class", "comments": [],
            "labels": [{"name": label} for label in
                       ("agent:claude", "symphony:claude-ready", "risk:mechanical")],
        }

    def add(self, label):
        self.item["labels"].append({"name": label})

    def test_provider_queue_has_no_separate_unity_or_scene_authorization(self):
        self.mod.preflight(self.item)
        self.add("resource:unity-editor")
        self.add("validation:unity-required")
        self.add("authoring:scene-new-composition")
        self.add("work:visual-unity")
        self.mod.preflight(self.item)
        self.add("symphony:ready")
        with self.assertRaisesRegex(self.mod.DispatchError, "Codex queue"):
            self.mod.preflight(self.item)
        self.item["labels"] = [{"name": "symphony:claude-ready"}]
        with self.assertRaisesRegex(self.mod.DispatchError, "agent:claude"):
            self.mod.preflight(self.item)

    def test_shared_handoff_scope_allows_unity_source_and_assets(self):
        self.mod.enforce_scope([
            "Assets/Rekindle/Scripts/Inventory.cs",
            "Assets/Rekindle/Tests/PlayMode/ControlSchemeTest.cs",
            "Assets/Rekindle/Tests/EditMode/Fix.meta",
            "Assets/Rekindle/Scenes/ExperimentalPreview.unity",
            "Assets/Rekindle/Content/NewPrefab.prefab",
            "Docs/ArchitectureNotes.md",
        ])
        for unsafe in ("../outside.cs", ".git/config", "/tmp/unwanted.cs"):
            with self.subTest(path=unsafe), self.assertRaisesRegex(
                    self.mod.DispatchError, "unsafe changed path"):
                self.mod.enforce_scope([unsafe])
        with self.assertRaisesRegex(self.mod.DispatchError, "empty PR"):
            self.mod.enforce_scope([])

    def test_changed_symlink_denied(self):
        ws = self.root / "GH-91"
        (ws / "Assets").mkdir(parents=True)
        target = ws / "Assets" / "linked.cs"
        target.symlink_to("/etc/hosts")
        with self.assertRaisesRegex(self.mod.DispatchError, "symlink"):
            self.mod.enforce_scope(["Assets/linked.cs"], ws)

    def test_claude_uses_codex_unity_editor_lock_without_extra_grants(self):
        ws = self.root / "GH-91"
        ws.mkdir(parents=True)
        self.add("resource:unity-editor")
        with self.mod.common_unity_resource(self.item, ws):
            lock = self.state / "locks/unity-editor.lock"
            self.assertEqual((lock / "owner").read_text().strip(), "GH-91")
            self.assertEqual((lock / "workspace").read_text().strip(), str(ws.resolve()))
            with self.assertRaisesRegex(self.mod.DispatchError, "already held"):
                with self.mod.common_unity_resource(self.item, ws):
                    pass
        self.assertFalse(lock.exists())

    def test_host_unity_evidence_is_fresh_and_actually_passed(self):
        ws = self.root / "GH-91"
        report = ws / "Logs/SymphonyUnity/host-run-1/summary.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "total": 7, "unityExitCode": 0, "result": "Passed",
        }))
        self.assertEqual(self.mod.latest_passed_unity_run(ws, 0), "host-run-1")
        self.assertEqual(self.mod.latest_passed_unity_run(ws, report.stat().st_mtime_ns + 1), "")
        report.write_text(json.dumps({
            "total": 0, "unityExitCode": 0, "result": "Passed",
        }))
        self.assertEqual(self.mod.latest_passed_unity_run(ws, 0), "")
        report.write_text(json.dumps({
            "total": 7, "unityExitCode": 1, "result": "Failed",
        }))
        self.assertEqual(self.mod.latest_passed_unity_run(ws, 0), "")

    def test_git_control_fingerprint_detects_tampering(self):
        path = self.root / "GH-91"
        git_dir = path / ".git"
        hooks = git_dir / "hooks"
        hooks.mkdir(parents=True)
        (git_dir / "config").write_text("[core]\n repositoryformatversion = 0\n")
        original = self.mod.git_control_fingerprint(path)
        (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
        self.assertNotEqual(original, self.mod.git_control_fingerprint(path))
        (hooks / "pre-commit").unlink()
        self.assertEqual(original, self.mod.git_control_fingerprint(path))
        (git_dir / "config").write_text("[core]\n hooksPath = /tmp/evil\n")
        self.assertNotEqual(original, self.mod.git_control_fingerprint(path))

    def test_journal_permissions_and_no_replay_after_crash(self):
        self.mod.save(91, "prepared", preparedHead="old")
        journal = self.mod.journal_path(91)
        self.assertTrue(journal.exists())
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        with mock.patch.object(self.mod, "clone_or_verify") as clone:
            with self.assertRaisesRegex(self.mod.DispatchError, "interrupted"):
                self.mod.execute(self.item)
            clone.assert_not_called()

    def test_successful_broker_to_review_path(self):
        path = self.root / "GH-91"
        path.mkdir(parents=True)
        observed = []

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return "baseline-sha"
            if cmd[0] == "bash" and "rekindle-migration-preflight.sh" in cmd[1]:
                self.assertNotIn("RPGK_REKINDLE_UNITY_READY", kwargs["env"])
                return ""
            if cmd[0].endswith("python") or cmd[0] == os.sys.executable:
                if "claude-worker-pilot.py" in cmd[1]:
                    return json.dumps({
                        "status": "completed", "workspaceIdentityPreserved": True,
                        "requestedModel": "haiku", "requestedEffort": "low",
                        "reportedCostUsd": 0.15,
                    })
            raise AssertionError(f"unexpected run: {cmd}")

        def fake_broker(_path, operation, branch, title=""):
            observed.append((operation, branch))
            return ""

        with mock.patch.object(self.mod, "clone_or_verify", return_value=path), \
             mock.patch.object(self.mod, "run", side_effect=fake_run), \
             mock.patch.object(self.mod, "broker", side_effect=fake_broker), \
             mock.patch.object(self.mod, "changed_paths", return_value=["Assets/Rekindle/Scripts/Inventory.cs"]), \
             mock.patch.object(self.mod, "git_control_fingerprint", return_value="unchanged"), \
             mock.patch.object(self.mod, "reconcile", return_value=True):
            output = self.mod.execute(self.item)
        self.assertEqual(output, "review queued")
        self.assertEqual(observed, [
            ("prepare", "claude/gh-91-implementation"),
            ("handoff", "claude/gh-91-implementation"),
        ])
        state = self.mod.read_journal(91)
        self.assertEqual(state["stage"], "handoff")
        self.assertEqual(state["model"], "haiku")
        self.assertEqual(state["preparedHead"], "baseline-sha")

    def test_recover_only_advanced_pushed_pr(self):
        path = self.root / "GH-91"
        path.mkdir(parents=True)
        self.mod.save(91, "handoff", preparedHead="abc123")
        with mock.patch.object(self.mod, "run", return_value="abc123"), \
             mock.patch.object(self.mod, "reconcile") as reconcile:
            with self.assertRaisesRegex(self.mod.DispatchError, "interrupted"):
                self.mod.execute(self.item)
            reconcile.assert_not_called()
        with mock.patch.object(self.mod, "run", return_value="newSHA"), \
             mock.patch.object(self.mod, "reconcile", return_value=True):
            result = self.mod.execute(self.item)
        self.assertIn("recovered existing PR", result)

    def test_review_rework_uses_claude_queue(self):
        review = load("claude_review_integration_test", "review-orchestrator.py")
        written = []
        with mock.patch.object(review, "api", return_value={
            "labels": [{"name": "agent:claude"}]
        }), mock.patch.object(review, "add_labels",
                             side_effect=lambda n, *labels: written.extend(labels)), \
             mock.patch.object(review, "remove_label"), \
             mock.patch.object(review, "remove_lifecycle_except"), \
             mock.patch.object(review, "set_repair_route"):
            review.dispatch_rework(91, {"routingRecommendation": "unchanged"})
        self.assertIn("symphony:claude-ready", written)
        self.assertNotIn("symphony:ready", written)

    def test_broker_accepts_only_dedicated_branches(self):
        host = load("claude_git_handoff_contract_test", "git-handoff-host.py")
        for branch in ("claude/gh-91-implementation", "codex/gh-91-implementation"):
            host.validate_branch(branch)
        for branch in ("feature/a", "claude/../x", "claude//oops"):
            with self.subTest(branch=branch), self.assertRaises(Exception):
                host.validate_branch(branch)


if __name__ == "__main__":
    unittest.main()
