#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dashboard_update", ROOT / "scripts/dashboard_update.py")
assert SPEC and SPEC.loader
dashboard_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard_update)


class DashboardUpdateTest(unittest.TestCase):
    def test_status_reports_update_and_last_result(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "dashboard-update.json").write_text(json.dumps({"state": "succeeded"}), encoding="utf-8")
            dashboard_update.STATUS_PATH = state / "dashboard-update.json"
            outputs = iter([
                subprocess.CompletedProcess([], 0, "main\n", ""),
                subprocess.CompletedProcess([], 0, "a" * 40 + "\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "b" * 40 + "\n", ""),
                subprocess.CompletedProcess([], 0, "0 3\n", ""),
            ])
            with mock.patch.object(dashboard_update, "_git", side_effect=lambda *a, **k: next(outputs)):
                value = dashboard_update.status()
        self.assertEqual(value["branch"], "main")
        self.assertTrue(value["updateAvailable"])
        self.assertEqual(value["behind"], 3)
        self.assertEqual(value["lastUpdate"]["state"], "succeeded")

    def test_trigger_is_narrow_systemd_start(self) -> None:
        with mock.patch.object(dashboard_update, "status", return_value={"branch": "main", "dirty": False, "head": "abc"}), \
             mock.patch.object(dashboard_update.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            value = dashboard_update.trigger()
        self.assertTrue(value["accepted"])
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", "/usr/bin/systemctl", "start", dashboard_update.UPDATE_SERVICE])

    def test_trigger_refuses_dirty_checkout(self) -> None:
        with mock.patch.object(dashboard_update, "status", return_value={"branch": "main", "dirty": True, "head": "abc"}):
            with self.assertRaises(dashboard_update.UpdateError):
                dashboard_update.trigger()

    def test_host_waits_for_cloudflared_task_completion(self) -> None:
        script = (ROOT / "scripts/dashboard-update-host.sh").read_text(encoding="utf-8")
        self.assertIn("Get-ScheduledTaskInfo", script)
        self.assertIn("LastRunTime -gt", script)
        self.assertIn("LastTaskResult -ne 0", script)
        self.assertIn("Timed out waiting for Cloudflared refresh task to complete", script)

    def test_host_verifies_deployed_head_matches_fetched_origin_main(self) -> None:
        script = (ROOT / "scripts/dashboard-update-host.sh").read_text(encoding="utf-8")
        self.assertIn('target="$(git_cmd rev-parse origin/main)"', script)
        self.assertIn('if [[ "$current" != "$target" ]]', script)
        self.assertIn("Update verification failed:", script)
        self.assertIn("Post-restart verification failed:", script)
        self.assertIn("Updated Supervisor to $current", script)

    def test_dashboard_requires_stable_public_recovery(self) -> None:
        script = (ROOT / "scripts/supervisor_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("stableSuccesses>=3", script)
        self.assertIn("stableSuccesses=0", script)
        self.assertIn("public dashboard did not remain stable", script)

    def test_dashboard_normal_refresh_validates_json_responses(self) -> None:
        page = (ROOT / "scripts/supervisor_dashboard.html").read_text(encoding="utf-8")
        self.assertIn("async function apiJson", page)
        self.assertIn("response.ok", page)
        self.assertIn("application/json", page)
        self.assertIn("Showing last successfully refreshed data; retrying automatically.", page)
        self.assertIn("apiJson('/api/operations')", page)
        self.assertNotIn("Dashboard refresh failed</strong>", page)


if __name__ == "__main__":
    unittest.main()
