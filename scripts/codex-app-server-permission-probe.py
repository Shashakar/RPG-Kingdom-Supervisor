#!/usr/bin/env python3
"""Verify Codex App Server selects and enforces the Supervisor permission profile.

This probe is intentionally model-free. It performs the App Server initialize +
ephemeral thread/start handshake to prove the selected profile identity, then
uses App Server command/exec with the same named permission profile to perform
an ordinary source-file write inside a disposable workspace.

Final Git metadata/network handoff is host-owned and is not a worker permission
requirement. No turn/start request is sent, so the probe does not consume model
allowance.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def write_message(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        fail("App Server stdin is unavailable")
    proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def read_response(
    proc: subprocess.Popen[str], request_id: int, timeout_seconds: float = 20.0
) -> dict[str, Any]:
    if proc.stdout is None:
        fail("App Server stdout is unavailable")

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail(f"timed out waiting for App Server response id={request_id}")

        readable, _, _ = select.select([proc.stdout], [], [], remaining)
        if not readable:
            continue

        line = proc.stdout.readline()
        if not line:
            stderr = ""
            if proc.stderr is not None:
                stderr = proc.stderr.read().strip()
            fail(
                f"App Server exited before response id={request_id}"
                + (f": {stderr}" if stderr else "")
            )

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        if message.get("id") != request_id:
            continue
        if "error" in message:
            fail(f"App Server request id={request_id} failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            fail(f"App Server response id={request_id} has no object result")
        return result


def workspace_write_probe_script() -> str:
    return r"""set -euo pipefail
printf 'probe\n' > rpgk-source-write-probe.txt
test "$(cat rpgk-source-write-probe.txt)" = probe
rm rpgk-source-write-probe.txt
printf 'app-server-workspace-write-ok\n'
"""


def main() -> int:
    profile = os.environ.get("RPGK_CODEX_PERMISSION_PROFILE", "")
    profile_toml = os.environ.get("RPGK_CODEX_PERMISSION_PROFILE_TOML", "")
    if not profile or not profile_toml:
        print(
            "RPG Kingdom Codex App Server permission probe: missing profile environment",
            file=sys.stderr,
        )
        return 64

    with tempfile.TemporaryDirectory(prefix="rpgk-app-server-permission-") as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        workspace.mkdir()

        command = [
            "codex",
            "--config",
            f'default_permissions="{profile}"',
            "--config",
            f"permissions.{profile}={profile_toml}",
            "app-server",
            "--stdio",
        ]
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            write_message(
                proc,
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "capabilities": {"experimentalApi": True},
                        "clientInfo": {
                            "name": "rpgk-supervisor-permission-probe",
                            "title": "RPG Kingdom Supervisor Permission Probe",
                            "version": "1.2.0",
                        },
                    },
                },
            )
            read_response(proc, 1)
            write_message(proc, {"method": "initialized", "params": {}})

            write_message(
                proc,
                {
                    "method": "thread/start",
                    "id": 2,
                    "params": {
                        "cwd": str(workspace),
                        "permissions": profile,
                        "ephemeral": True,
                    },
                },
            )
            result = read_response(proc, 2)
            active = result.get("activePermissionProfile")
            if not isinstance(active, dict):
                fail(
                    "thread/start did not report activePermissionProfile; "
                    "the named profile was not proven active"
                )
            if active.get("id") != profile:
                fail(
                    "thread/start selected unexpected permission profile "
                    f"{active.get('id')!r}; expected {profile!r}"
                )
            if active.get("extends") is not None:
                fail(
                    "Supervisor permission profile unexpectedly inherits "
                    f"{active.get('extends')!r}"
                )

            write_message(
                proc,
                {
                    "method": "command/exec",
                    "id": 3,
                    "params": {
                        "command": ["bash", "-lc", workspace_write_probe_script()],
                        "cwd": str(workspace),
                        "permissionProfile": profile,
                        "timeoutMs": 20000,
                    },
                },
            )
            command_result = read_response(proc, 3, timeout_seconds=25.0)
            exit_code = command_result.get("exitCode")
            stdout = command_result.get("stdout", "")
            stderr = command_result.get("stderr", "")
            if exit_code != 0:
                fail(
                    "command/exec could not perform ordinary workspace writes "
                    f"(exit={exit_code}, stderr={stderr!r}, stdout={stdout!r})"
                )
            if "app-server-workspace-write-ok" not in stdout:
                fail(
                    "command/exec exited successfully but did not complete the workspace write proof "
                    f"(stdout={stdout!r}, stderr={stderr!r})"
                )
        except Exception as exc:
            print(
                f"RPG Kingdom Codex App Server permission probe: FAILED; {exc}",
                file=sys.stderr,
            )
            return 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    print(
        "RPG Kingdom Codex App Server permission probe: "
        f"PASS (active={profile}, workspace_write=ok)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
