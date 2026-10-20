#!/usr/bin/env python3
"""Offline regression of the operator-only Claude Code pilot."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "scripts/claude-worker-pilot.py"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class ClaudePilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.workspaces = self.base / "workspaces"
        self.ws = self.workspaces / "GH-11"
        self.ws.mkdir(parents=True)
        git(self.ws, "init", "-q")
        git(self.ws, "config", "user.name", "Test")
        git(self.ws, "config", "user.email", "test@example.invalid")
        (self.ws / "AGENTS.md").write_text("test contract\n")
        git(self.ws, "add", "AGENTS.md")
        git(self.ws, "commit", "-qm", "contract")
        git(self.ws, "switch", "-qc", "claude/gh11-spike")
        git(self.ws, "remote", "add", "origin", "https://github.com/Shashakar/Rekindle-Claude.git")
        self.prompt = self.base / "prompt.txt"
        self.prompt.write_text("Review this repository.\n")
        self.binary = self.base / "claude"
        self.binary.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "with open(os.environ['MOCK_RESULT_FILE'],'w') as f:\n"
            " json.dump({'argv':sys.argv[1:],'token':os.getenv('SYMPHONY_GITHUB_TOKEN'),"
            "'gh':os.getenv('GH_TOKEN'),'claudecode':os.getenv('CLAUDECODE')},f)\n"
            "print(json.dumps({'type':'system','subtype':'init','session_id':'fake'}))\n"
            "print(json.dumps({'type':'result','subtype':'error' if os.getenv('MOCK_ERROR') else 'success',"
            "'is_error':bool(os.getenv('MOCK_ERROR')),'num_turns':1,"
            "'total_cost_usd':0.02,'result':'checked','session_id':'fake'}))\n"
        )
        self.binary.chmod(0o755)
        self.env = dict(os.environ,
            MOCK_RESULT_FILE=str(self.base / "command.json"),
            SYMPHONY_GITHUB_TOKEN="should-not-leak",
            GH_TOKEN="should-not-leak",
            CLAUDECODE="interactive-parent",
        )

    def run_pilot(self, *extra, env=None):
        return subprocess.run([
            sys.executable, str(PILOT), "--workspace", str(self.ws),
            "--workspace-root", str(self.workspaces),
            "--prompt-file", str(self.prompt), "--claude-bin", str(self.binary),
            "--state-root", str(self.base / "state"), "--timeout-seconds", "30",
            *extra], text=True, capture_output=True, env=env or self.env)

    def test_audit_success_with_environment_and_tool_restrictions(self):
        result = self.run_pilot()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "completed")
        self.assertEqual(output["turns"], 1)
        args = json.loads((self.base / "command.json").read_text())
        self.assertIsNone(args["token"])
        self.assertIsNone(args["gh"])
        self.assertIsNone(args["claudecode"])
        self.assertIn("Read,Glob,Grep", args["argv"])
        self.assertNotIn("Read,Glob,Grep,Edit,Write", args["argv"])
        self.assertIn("dontAsk", args["argv"])
        self.assertTrue((self.base / "state/claude-pilot/GH-11/latest.json").exists())

    def test_edit_requires_explicit_acknowledgement(self):
        result = self.run_pilot("--mode", "edit")
        self.assertEqual(result.returncode, 70)
        self.assertFalse((self.base / "command.json").exists())

    def test_edit_mode_keeps_shell_denied(self):
        result = self.run_pilot("--mode", "edit", "--enable-edits")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads((self.base / "command.json").read_text())["argv"]
        self.assertIn("Read,Glob,Grep,Edit,Write", args)
        self.assertIn("Bash,Agent,WebFetch,WebSearch,NotebookEdit", args)

    def test_wrong_origin_fails_closed(self):
        git(self.ws, "remote", "set-url", "origin", "https://github.com/Shashakar/RPG-Kingdom.git")
        result = self.run_pilot()
        self.assertEqual(result.returncode, 70)
        self.assertIn("origin", result.stderr)

    def test_structured_error_is_failure(self):
        env = dict(self.env, MOCK_ERROR="1")
        result = self.run_pilot(env=env)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")

    def test_existing_worker_lock_refused(self):
        lock = self.base / "state/locks/issues/GH-11.lock"
        lock.parent.mkdir(parents=True)
        with lock.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_pilot()
            self.assertEqual(result.returncode, 70)
            self.assertIn("another Supervisor worker", result.stderr)
        self.assertFalse((self.base / "command.json").exists())

    def test_budget_bounds(self):
        result = self.run_pilot("--max-turns", "100")
        self.assertNotEqual(result.returncode, 0)

    def test_dirty_workspace_rejected(self):
        (self.ws / "new.txt").write_text("dirty")
        result = self.run_pilot()
        self.assertEqual(result.returncode, 70)
        self.assertIn("clean", result.stderr)


if __name__ == "__main__":
    unittest.main()
