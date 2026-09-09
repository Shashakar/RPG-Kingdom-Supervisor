#!/usr/bin/env python3
"""Verify that Codex App Server selects the Supervisor's named permission profile.

This intentionally starts no model turn. It performs only the App Server
initialize + thread/start handshake against a disposable repository and checks
ThreadStartResponse.activePermissionProfile.
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
from typing import Any


def fail(message: str) -> "NoReturn":
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
        repo = Path(temp_dir) / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)

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
                            "version": "1.0.0",
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
                        "cwd": str(repo),
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
        f"PASS (active={profile})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
