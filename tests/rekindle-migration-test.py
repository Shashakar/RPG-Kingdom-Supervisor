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

    def test_reject_incorrect_origin(self):
        git(self.ws, "remote", "set-url", "origin", "https://github.com/Shashakar/RPG-Kingdom.git")
        self.assertEqual(self.run_guard().returncode, 73)

    def test_contract_must_be_committed(self):
        (self.ws / "AGENTS.md").unlink()
        self.assertEqual(self.run_guard().returncode, 73)

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
