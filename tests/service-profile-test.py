#!/usr/bin/env python3
"""Host lock and systemd unit/profile contract tests without a real system service."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "scripts" / "host-instance-lock.sh"


class ServiceProfileTest(unittest.TestCase):
    def test_both_services_target_rekindle(self):
        supervisor = (ROOT / "systemd/rpg-kingdom-supervisor.service.d/90-rekindle-target.conf").read_text()
        dashboard = (ROOT / "systemd/rpg-kingdom-diagnostics.service.d/90-rekindle-target.conf").read_text()
        self.assertIn("Environment=RPGK_TARGET_PROFILE=rekindle", supervisor)
        self.assertIn("RestartPreventExitStatus=75", supervisor)
        self.assertIn("Environment=RPGK_TARGET_PROFILE=rekindle", dashboard)
        self.assertIn("Environment=RPGK_REPO=Shashakar/Rekindle-Claude", dashboard)
        self.assertIn("Environment=RPGK_BASE_BRANCH=rekindle-vertical-slice", dashboard)
        self.assertIn("/home/dex/.local/state/rekindle-supervisor", dashboard)
        self.assertIn("Environment=RPGK_WORKSPACE_ROOT=/home/dex/code/rekindle-symphony-workspaces", dashboard)
        self.assertNotIn("ExecStart=", supervisor)
        self.assertNotIn("ExecStart=", dashboard)
        installer = (ROOT / "scripts/install-rekindle-service-profile.sh").read_text()
        self.assertNotIn("systemctl restart", installer)
        self.assertIn("daemon-reload", installer)
        launcher = (ROOT / "scripts/run-symphony.sh").read_text()
        self.assertIn('source "$SUPERVISOR_ROOT/scripts/host-instance-lock.sh"', launcher)
        self.assertIn("rpgk_acquire_host_instance_lock", launcher)

    def test_host_lock_excludes_overlap_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, HOME=temp)
            ready = Path(temp) / "ready"
            first = subprocess.Popen([
                "bash", "-c",
                f'source "{LOCK}"; rpgk_acquire_host_instance_lock || exit $?; touch "{ready}"; sleep 2'],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                for _ in range(50):
                    if ready.exists():
                        break
                    if first.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "first lock holder failed")
                second = subprocess.run(
                    ["bash", "-c", f'source "{LOCK}"; rpgk_acquire_host_instance_lock'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(second.returncode, 75, second.stderr)
                self.assertIn("another Supervisor host instance", second.stderr)
            finally:
                first.terminate()
                first.communicate(timeout=5)
            third = subprocess.run(
                ["bash", "-c", f'source "{LOCK}"; rpgk_acquire_host_instance_lock'],
                env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(third.returncode, 0, third.stderr)


if __name__ == "__main__":
    unittest.main()
