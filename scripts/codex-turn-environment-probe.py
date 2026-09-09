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


def progress(message: str) -> None:
    print(f"RPG Kingdom turn probe: {message}", file=sys.stderr, flush=True)


def write_message(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        fail("App Server stdin is unavailable")
    proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def read_message(
    proc: subprocess.Popen[str], timeout_seconds: float
) -> dict[str, Any] | None:
    if proc.stdout is None:
        fail("App Server stdout is unavailable")

    readable, _, _ = select.select([proc.stdout], [], [], timeout_seconds)
    if not readable:
        return None

    line = proc.stdout.readline()
    if not line:
        stderr = ""
        if proc.poll() is not None and proc.stderr is not None:
            stderr = proc.stderr.read().strip()
        fail("App Server exited unexpectedly" + (f": {stderr}" if stderr else ""))

    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def fail_on_unhandled_server_request(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None or not isinstance(method, str):
        return

    # A headless diagnostic client has no approval UI. The probe explicitly
    # runs with approvalPolicy=never, so any server-initiated approval request
    # means the effective turn configuration differs from what we intended.
    if "requestApproval" in method or "approval" in method.lower():
        fail(
            "App Server requested interactive approval despite approvalPolicy=never: "
            f"method={method!r}, params={message.get('params')!r}"
        )


def read_response(
    proc: subprocess.Popen[str], request_id: int, timeout_seconds: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message = read_message(proc, min(2.0, remaining))
        if message is None:
            continue
        fail_on_unhandled_server_request(message)
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
    proc: subprocess.Popen[str], turn_id: str, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    next_heartbeat = time.monotonic() + 10.0
    last_event = "turn/start response"

    while time.monotonic() < deadline:
        now = time.monotonic()
        remaining = max(0.0, deadline - now)
        message = read_message(proc, min(2.0, remaining))

        if message is None:
            now = time.monotonic()
            if now >= next_heartbeat:
                progress(
                    "still waiting for turn/completed "
                    f"({int(max(0.0, deadline - now))}s remaining; last={last_event})"
                )
                next_heartbeat = now + 10.0
            continue

        fail_on_unhandled_server_request(message)
        method = message.get("method")
        if isinstance(method, str):
            last_event = method

        if method == "turn/started":
            progress("model turn is running")
            continue

        if method == "item/started":
            params = message.get("params")
            item_type = None
            if isinstance(params, dict):
                item = params.get("item")
                if isinstance(item, dict):
                    item_type = item.get("type")
            progress(f"turn item started{f' ({item_type})' if item_type else ''}")
            continue

        if method == "item/completed":
            params = message.get("params")
            item_type = None
            if isinstance(params, dict):
                item = params.get("item")
                if isinstance(item, dict):
                    item_type = item.get("type")
            progress(f"turn item completed{f' ({item_type})' if item_type else ''}")
            continue

        if method != "turn/completed":
            continue

        params = message.get("params")
        if not isinstance(params, dict):
            continue
        turn = params.get("turn")
        if isinstance(turn, dict) and turn.get("id") == turn_id:
            progress(f"turn completed with status={turn.get('status')!r}")
            return turn

    fail(
        f"timed out after {timeout_seconds:.0f}s waiting for turn/completed for {turn_id}; "
        f"last App Server event was {last_event!r}"
    )


def probe_script() -> str:
    return r'''#!/usr/bin/env bash
set +e

git_write="ok"
wsl_interop="ok"
git_error=""
interop_error=""

if ! { : > .git/FETCH_HEAD; } 2>.rpgk-git-error; then
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

python3 - "$git_write" "$wsl_interop" <<'PY'
import json, sys
from pathlib import Path

def read(path):
    p = Path(path)
    return p.read_text(errors='replace').strip() if p.exists() else ''

payload = {
    'git_write': sys.argv[1],
    'wsl_interop': sys.argv[2],
    'git_error': read('.rpgk-git-error'),
    'interop_error': read('.rpgk-interop-error'),
    'interop_output': read('.rpgk-interop-output'),
}
Path('.rpgk-turn-probe-result.json').write_text(json.dumps(payload), encoding='utf-8')
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
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("RPGK_TURN_PROBE_TIMEOUT_SECONDS", "120")),
        help="maximum time to wait for the diagnostic turn (default: 120)",
    )
    args = parser.parse_args()

    if not args.run:
        print(
            "Refusing to start a model-backed probe without --run. "
            "This diagnostic consumes a small amount of Codex allowance.",
            file=sys.stderr,
        )
        return 64
    if args.timeout_seconds <= 0:
        print("--timeout-seconds must be positive", file=sys.stderr)
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
        subprocess.run(
            ["git", "config", "user.email", "rpgk-host-probe@local.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "RPG Kingdom Host Probe"],
            cwd=repo,
            check=True,
        )
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
        progress(f"starting Codex App Server (model={args.model}, effort=low)")
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            progress("initializing App Server")
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
                            "version": "1.1.0",
                        },
                    },
                },
            )
            read_response(proc, 1)
            write_message(proc, {"method": "initialized", "params": {}})

            progress("starting ephemeral diagnostic thread")
            write_message(
                proc,
                {
                    "method": "thread/start",
                    "id": 2,
                    "params": {
                        "cwd": str(repo),
                        "runtimeWorkspaceRoots": [str(repo)],
                        "permissions": profile,
                        "approvalPolicy": "never",
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
            if thread_result.get("approvalPolicy") not in (None, "never"):
                fail(
                    "thread/start did not retain approvalPolicy=never: "
                    f"{thread_result.get('approvalPolicy')!r}"
                )
            thread_id = thread["id"]
            progress(f"thread ready (permissions={profile}, approvals=never)")

            prompt = (
                "This is a deterministic environment diagnostic. Use the shell exactly once to run "
                "`bash ./probe-actions.sh`. Do not inspect other files, do not modify the script, "
                "do not use network tools, and do not attempt workarounds. After it finishes, return "
                "only the command output."
            )
            progress("starting model-backed diagnostic turn")
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
                        "approvalPolicy": "never",
                        "model": args.model,
                        "effort": "low",
                    },
                },
            )
            turn_result = read_response(proc, 3)
            turn = turn_result.get("turn")
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                fail("turn/start did not return a turn id")
            progress(
                f"turn started ({turn['id']}); waiting up to {args.timeout_seconds:.0f}s"
            )
            completed = wait_for_turn_completed(
                proc, turn["id"], timeout_seconds=args.timeout_seconds
            )

            result_path = repo / ".rpgk-turn-probe-result.json"
            if not result_path.exists():
                fail(
                    "model turn completed without producing the probe result; "
                    f"turn status={completed.get('status')!r}"
                )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            print(json.dumps(result, indent=2), flush=True)
            if result.get("git_write") != "ok" or result.get("wsl_interop") != "ok":
                print(
                    "RPG Kingdom Codex model-turn environment probe: FAILED "
                    f"(git_write={result.get('git_write')}, "
                    f"wsl_interop={result.get('wsl_interop')})",
                    file=sys.stderr,
                )
                return 1
        except Exception as exc:
            print(
                f"RPG Kingdom Codex model-turn environment probe: FAILED; {exc}",
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
        "RPG Kingdom Codex model-turn environment probe: "
        f"PASS (model={args.model}, git_write=ok, wsl_interop=ok)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
