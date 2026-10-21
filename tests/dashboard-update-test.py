#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
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


    def test_deployment_state_is_host_global_not_game_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {
                "HOME": td,
                "RPGK_SUPERVISOR_STATE_ROOT": str(Path(td) / "rekindle-supervisor"),
            }, clear=True):
                spec = importlib.util.spec_from_file_location(
                    "dashboard_update_profile_test", ROOT / "scripts/dashboard_update.py"
                )
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                expected = Path(td) / ".local/state/rpg-kingdom-supervisor"
                self.assertEqual(module.STATE_ROOT, expected)
                self.assertEqual(module.STATUS_PATH, expected / "dashboard-update.json")

    def test_host_records_status_in_global_root_even_for_rekindle(self) -> None:
        """Exercise the actual update host script without touching Git/services."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mock_bin = base / "bin"
            mock_bin.mkdir()
            fake_git = mock_bin / "git"
            fake_git.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"rev-parse HEAD\"* ]]; then\n"
                "  printf '%040d\\n' 0\n"
                "elif [[ \"$*\" == *\"status --porcelain\"* ]]; then\n"
                "  echo ' M tracked-file'\n"
                "else\n"
                "  echo 'unexpected Git operation' >&2; exit 92\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            shared = base / "shared-deployment"
            target = base / "rekindle-supervisor"
            env = dict(os.environ, HOME=td, RPGK_SUPERVISOR_ROOT=str(base),
                       RPGK_DASHBOARD_UPDATE_STATE_ROOT=str(shared),
                       RPGK_SUPERVISOR_STATE_ROOT=str(target),
                       PATH=f"{mock_bin}:{os.environ['PATH']}")
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/dashboard-update-host.sh")],
                env=env, text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 20, result.stderr)
            status = json.loads((shared / "dashboard-update.json").read_text())
            self.assertEqual(status["state"], "failed")
            self.assertIn("local changes", status["message"])
            self.assertFalse((target / "dashboard-update.json").exists())

    def test_trigger_is_narrow_systemd_start(self) -> None:
        with mock.patch.object(dashboard_update, "status", return_value={"branch": "main", "dirty": False, "head": "abc", "lastUpdate": {"updatedAt": "2026-10-21T01:02:01Z"}}), \
             mock.patch.object(dashboard_update.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            value = dashboard_update.trigger()
        self.assertTrue(value["accepted"])
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", "/usr/bin/systemctl", "--no-block", "start", dashboard_update.UPDATE_SERVICE])
        self.assertEqual(value["previousUpdateAt"], "2026-10-21T01:02:01Z")

    def test_trigger_refuses_dirty_checkout(self) -> None:
        with mock.patch.object(dashboard_update, "status", return_value={"branch": "main", "dirty": True, "head": "abc"}):
            with self.assertRaises(dashboard_update.UpdateError):
                dashboard_update.trigger()


    def test_installed_privilege_bridge_matches_async_trigger(self) -> None:
        installer = (ROOT / "scripts/install-dashboard-update.sh").read_text(encoding="utf-8")
        self.assertIn("/usr/bin/systemctl --no-block start rpg-kingdom-dashboard-update.service", installer)
        self.assertNotIn("NOPASSWD: /usr/bin/systemctl start rpg-kingdom-dashboard-update.service", installer)

    def test_dashboard_recovery_ignores_stale_success_and_js_parses(self) -> None:
        source = (ROOT / "scripts/supervisor_dashboard.py").read_text(encoding="utf-8")
        match = re.search(r'MANAGEMENT_SCRIPT = r"""(.*?)"""', source, flags=re.S)
        self.assertIsNotNone(match)
        js_match = re.search(r"<script>(.*?)</script>", match.group(1), flags=re.S)
        self.assertIsNotNone(js_match)
        script = js_match.group(1)
        self.assertIn("d.lastUpdate.updatedAt!==previousUpdateAt", script)
        self.assertIn("let lastDisplayedUpdateAt=null", script)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dashboard-management.js"
            path.write_text(script, encoding="utf-8")
            checked = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

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
