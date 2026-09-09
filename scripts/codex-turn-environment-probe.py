#!/usr/bin/env python3
"""Probe the actual model-backed Codex turn execution environment.

Unlike the model-free command/exec probes, this starts one deliberately tiny
Luna/low turn and asks it to execute a deterministic script in a disposable Git
repository. The script independently records whether the turn can mutate .git
metadata and whether WSL can invoke Windows through cmd.exe.

This consumes a small amount of Codex model allowance and is therefore never
run automatically by Supervisor startup or the normal test suite.
"""

from __future__ import annotations

import argparse
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


def read_message(proc: subprocess.Popen[str], timeout_seconds: float) -> dict[str, Any]:
    if proc.stdout is None:
        fail("App Server stdout is unavailable")
    readable, _, _ = select.select([proc.stdout], [], [], timeout_seconds)
    if not readable:
        fail("timed out waiting for App Server output")
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read().strip() if proc.stderr is not None else ""
        fail("App Server exited unexpectedly" + (f": {stderr}" if stderr else ""))
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def read_response(
    proc: subprocess.Popen[str], request_id: int, timeout_seconds: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        message = read_message(proc, max(0.1, deadline - time.monotonic()))
        if message.get("id") != request_id:
            continue
        if "error" in message:
            fail(f"App Server request id={request_id} failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            fail(f"App Server response id={request_id} has no object result")
        return result
    fail(f"timed out waiting for App Server response id={request_id}")


def wait_for_turn_completed(
    proc: subprocess.Popen[str], turn_id: str, timeout_seconds: float = 180.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        message = read_message(proc, max(0.1, deadline - time.monotonic()))
        if message.get("method") != "turn/completed":
            continue
        params = message.get("params")
        if not isinstance(params, dict):
            continue
        turn = params.get("turn")
        if isinstance(turn, dict) and turn.get("id") == turn_id:
            return turn
    fail(f"timed out waiting for turn/completed for {turn_id}")


def probe_script() -> str:
    return r'''#!/usr/bin/env bash
set +e

git_write="ok"
wsl_interop="ok"

git_error=""
interop_error=""

: > .git/FETCH_HEAD 2>.rpgk-git-error
if [[ $? -ne 0 ]]; then
  git_write="failed"
else
  git config user.email rpgk-turn-probe@local.invalid 2>>.rpgk-git-error
  git config user.name 'RPG Kingdom Turn Probe' 2>>.rpgk-git-error
  printf 'turn probe\n' > .rpgk-turn-probe.txt
  git add .rpgk-turn-probe.txt 2>>.rpgk-git-error
  git commit -q -m 'turn environment probe' 2>>.rpgk-git-error
  if [[ $? -ne 0 ]]; then
    git_write="failed"
  fi
fi

if command -v cmd.exe >/dev/null 2>&1; then
  cmd.exe /d /c "echo rpgk-wsl-interop-ok" >.rpgk-interop-output 2>.rpgk-interop-error
  if [[ $? -ne 0 ]] || ! grep -q 'rpgk-wsl-interop-ok' .rpgk-interop-output 2>/dev/null; then
    wsl_interop="failed"
  fi
else
  wsl_interop="unavailable"
  printf 'cmd.exe not found on PATH\n' > .rpgk-interop-error
fi

python3 - <<'PY'
import json
from pathlib import Path

def read(path):
    p = Path(path)
    return p.read_text(errors="replace").strip() if p.exists() else ""

payload = {
    "git_write": "''' + '${git_write}' + r'''",
    "wsl_interop": "''' + '${wsl_interop}' + r'''",
    "git_error": read(".rpgk-git-error"),
    "interop_error": read(".rpgk-interop-error"),
    "interop_output": read(".rpgk-interop-output"),
}
Path(".rpgk-turn-probe-result.json").write_text(json.dumps(payload), encoding="utf-8")
PY

# The heredoc above is single-quoted, so patch the two shell values safely.
python3 - "$git_write" "$wsl_interop" <<'PY'
import json, sys
from pathlib import Path
p = Path('.rpgk-turn-probe-result.json')
data = json.loads(p.read_text())
data['git_write'] = sys.argv[1]
data['wsl_interop'] = sys.argv[2]
p.write_text(json.dumps(data), encoding='utf-8')
PY

cat .rpgk-turn-probe-result.json
exit 0
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="store_true",
        help="acknowledge that this starts one small model-backed Codex turn",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("RPGK_TURN_PROBE_MODEL", "gpt-5.6-luna"),
    )
    args = parser.parse_args()

    if not args.run:
        print(
            "Refusing to start a model-backed probe without --run. "
            "This diagnostic consumes a small amount of Codex allowance.",
            file=sys.stderr,
        )
        return 64

    profile = os.environ.get("RPGK_CODEX_PERMISSION_PROFILE", "")
    profile_toml = os.environ.get("RPGK_CODEX_PERMISSION_PROFILE_TOML", "")
    if not profile or not profile_toml:
        print("Missing Supervisor Codex permission profile environment", file=sys.stderr)
        return 64

    with tempfile.TemporaryDirectory(prefix="rpgk-turn-environment-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "rpgk-host-probe@local.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "RPG Kingdom Host Probe"], cwd=repo, check=True)
        (repo / "README.txt").write_text("turn environment probe\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
        probe = repo / "probe-actions.sh"
        probe.write_text(probe_script(), encoding="utf-8")
        probe.chmod(0o755)

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
                            "name": "rpgk-turn-environment-probe",
                            "title": "RPG Kingdom Turn Environment Probe",
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
                        "runtimeWorkspaceRoots": [str(repo)],
                        "permissions": profile,
                        "model": args.model,
                        "ephemeral": True,
                    },
                },
            )
            thread_result = read_response(proc, 2)
            thread = thread_result.get("thread")
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                fail("thread/start did not return a thread id")
            active = thread_result.get("activePermissionProfile")
            if not isinstance(active, dict) or active.get("id") != profile:
                fail(f"thread/start did not select {profile!r}: {active!r}")
            thread_id = thread["id"]

            prompt = (
                "This is a deterministic environment diagnostic. Use the shell exactly once to run "
                "`bash ./probe-actions.sh`. Do not inspect other files, do not modify the script, "
                "do not use network tools, and do not attempt workarounds. After it finishes, return "
                "only the command output."
            )
            write_message(
                proc,
                {
                    "method": "turn/start",
                    "id": 3,
                    "params": {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": prompt, "textElements": []}],
                        "cwd": str(repo),
                        "runtimeWorkspaceRoots": [str(repo)],
                        "permissions": profile,
                        "model": args.model,
                        "effort": "low",
                    },
                },
            )
            turn_result = read_response(proc, 3)
            turn = turn_result.get("turn")
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                fail("turn/start did not return a turn id")
            completed = wait_for_turn_completed(proc, turn["id"])

            result_path = repo / ".rpgk-turn-probe-result.json"
            if not result_path.exists():
                fail(
                    "model turn completed without producing the probe result; "
                    f"turn status={completed.get('status')!r}"
                )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            print(json.dumps(result, indent=2))
            if result.get("git_write") != "ok" or result.get("wsl_interop") != "ok":
                print(
                    "RPG Kingdom Codex model-turn environment probe: FAILED "
                    f"(git_write={result.get('git_write')}, "
                    f"wsl_interop={result.get('wsl_interop')})",
                    file=sys.stderr,
                )
                return 1
        except Exception as exc:
            print(f"RPG Kingdom Codex model-turn environment probe: FAILED; {exc}", file=sys.stderr)
            return 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    print(
        "RPG Kingdom Codex model-turn environment probe: "
        f"PASS (model={args.model}, git_write=ok, wsl_interop=ok)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
