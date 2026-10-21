#!/usr/bin/env python3
"""Check Rekindle migration preflight against a disposable target checkout."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/rekindle-migration-preflight.sh"
HOST = ROOT / "scripts/run-symphony.sh"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


class TargetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.ws = self.base / "GH-12"
        self.ws.mkdir()
        git(self.ws, "init", "-q")
        git(self.ws, "config", "user.name", "Test")
        git(self.ws, "config", "user.email", "test@example.invalid")
        (self.ws / "AGENTS.md").write_text("safe\n")
        (self.ws / "Docs").mkdir()
        (self.ws / "Docs/ARCHITECTURE.md").write_text("architecture\n")
        git(self.ws, "add", ".")
        git(self.ws, "commit", "-qm", "contracts")
        git(self.ws, "remote", "add", "origin", "https://github.com/Shashakar/Rekindle-Claude.git")
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/bash\nprintf '%s\\n' \"${TEST_LABELS:-}\"\n")
        gh.chmod(0o755)
        self.env = dict(os.environ,
            RPGK_TARGET_PROFILE="rekindle",
            RPGK_REPO="Shashakar/Rekindle-Claude",
            RPGK_BASE_BRANCH="rekindle-vertical-slice",
            PATH=f"{bin_dir}:{os.environ['PATH']}",
        )

    def run_guard(self, **env):
        return subprocess.run(["bash", str(GUARD)], cwd=self.ws,
                              env=dict(self.env, **env),
                              capture_output=True, text=True)

    def test_normal_code_is_admitted(self):
        self.assertEqual(self.run_guard().returncode, 0)

    def test_unity_authoring_requires_explicit_target_validation(self):
        result = self.run_guard(TEST_LABELS="resource:unity-editor")
        self.assertEqual(result.returncode, 74, result.stderr)
        self.assertIn("save isolation", result.stderr)

    def test_narrow_claude_visual_capability_does_not_enable_other_unity_work(self):
        visual_labels = "\n".join([
            "agent:claude", "work:visual-unity", "resource:unity-editor",
            "validation:unity-required", "authoring:scene-new-composition",
        ])
        ready = dict(
            RPGK_CLAUDE_VISUAL_HOST_READY="1",
            RPGK_CLAUDE_VISUAL_UNITY_ENABLED="1",
            RPGK_CLAUDE_LICENSED_VISUAL_REVIEW_APPROVED="1",
        )
        result = self.run_guard(TEST_LABELS=visual_labels, **ready)
        self.assertEqual(result.returncode, 0, result.stderr)
        for missing in ready:
            env = dict(ready)
            env[missing] = "0"
            blocked = self.run_guard(TEST_LABELS=visual_labels, **env)
            self.assertEqual(blocked.returncode, 74, (missing, blocked.stderr))
        codex_labels = visual_labels.replace("agent:claude", "agent:codex")
        result = self.run_guard(TEST_LABELS=codex_labels, **ready)
        self.assertEqual(result.returncode, 74)
        extra = visual_labels + "\nauthoring:scene-existing-composition"
        result = self.run_guard(TEST_LABELS=extra, **ready)
        self.assertEqual(result.returncode, 74)
        result = self.run_guard(TEST_LABELS="resource:unity-editor", **ready)
        self.assertEqual(result.returncode, 74)

    def test_reject_incorrect_origin(self):
        git(self.ws, "remote", "set-url", "origin", "https://github.com/Shashakar/RPG-Kingdom.git")
        self.assertEqual(self.run_guard().returncode, 73)

    def test_contract_must_be_committed(self):
        (self.ws / "AGENTS.md").unlink()
        self.assertEqual(self.run_guard().returncode, 73)

    def test_git_handoff_prepares_from_rekindle_integration_branch(self):
        import importlib.util
        import subprocess
        module_path = ROOT / "scripts/git-handoff-host.py"
        spec = importlib.util.spec_from_file_location("rpgk_test_migration_handoff", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.BASE_BRANCH = "rekindle-vertical-slice"

        git(self.ws, "switch", "-qc", "rekindle-vertical-slice")
        git(self.ws, "update-ref", "refs/remotes/origin/rekindle-vertical-slice", "HEAD")
        original_git = module.run_git

        def offline_fetch(workspace, *args, **kwargs):
            if args[:3] == ("fetch", "--prune", "origin"):
                return subprocess.CompletedProcess(args, 0, "", "")
            return original_git(workspace, *args, **kwargs)

        module.run_git = offline_fetch
        result = module.prepare_branch(self.ws, "codex/gh-12-migration")
        self.assertTrue(result["created"])
        self.assertEqual(result["branch"], "codex/gh-12-migration")
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(self.ws), "merge-base",
                 "codex/gh-12-migration", "rekindle-vertical-slice"], text=True
            ).strip(),
            subprocess.check_output(
                ["git", "-C", str(self.ws), "rev-parse", "rekindle-vertical-slice"], text=True
            ).strip()
        )

    def test_run_script_has_isolated_profiles(self):
        source = HOST.read_text()
        assert 'RPGK_TARGET_PROFILE:-rekindle' in source
        assert 'RPGK_BASE_BRANCH="rekindle-vertical-slice"' in source
        assert 'RPGK_BASE_BRANCH="main"' in source
        assert "rekindle-symphony-workspaces" in source
        assert "rpg-kingdom-symphony-workspaces" in source
        assert '  "$WORKFLOW_FILE"' in source


if __name__ == "__main__":
    unittest.main()
