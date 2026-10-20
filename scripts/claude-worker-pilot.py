#!/usr/bin/env python3
"""Bounded, operator-started Claude Code pilot. NOT a Symphony app-server."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

from claude_model_policy import EFFORTS, MODELS, read_labels, resolve

ISSUE_DIR = re.compile(r"^GH-[1-9][0-9]*$")
CLAUDE_BRANCH = re.compile(r"^claude/[A-Za-z0-9][A-Za-z0-9._/-]*$")
BLOCKED_ENV = {
    "SYMPHONY_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
    "GIT_ASKPASS", "SSH_ASKPASS", "RPGK_GITHUB_TOKEN",
}
DISALLOWED = "Bash,Agent,WebFetch,WebSearch,NotebookEdit"
READ_TOOLS = "Read,Glob,Grep"
EDIT_TOOLS = "Read,Glob,Grep,Edit,Write"


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args], text=True,
        capture_output=True, timeout=20, check=True,
    )
    return result.stdout.strip()


def normalized_origin(url: str) -> str:
    value = url.strip()
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", value, re.I
    )
    if match is None:
        raise ValueError("workspace origin is not an ordinary github.com repository")
    return match.group(1).lower()


def validate(workspace: Path, root: Path, repo: str, mode: str) -> str:
    workspace = workspace.resolve(strict=True)
    root = root.resolve(strict=True)
    if workspace.parent != root or ISSUE_DIR.fullmatch(workspace.name) is None:
        raise ValueError("workspace must be a direct GH-N child of the configured root")
    if Path(git(workspace, "rev-parse", "--show-toplevel")).resolve() != workspace:
        raise ValueError("workspace is not the Git repository root")
    if normalized_origin(git(workspace, "remote", "get-url", "origin")) != repo.lower():
        raise ValueError("workspace origin does not match the configured target repository")
    branch = git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
    if git(workspace, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("workspace must be clean before a pilot run")
    if mode == "edit" and CLAUDE_BRANCH.fullmatch(branch) is None:
        raise ValueError("edit pilot requires a dedicated claude/* branch")
    if not (workspace / "AGENTS.md").is_file():
        raise ValueError("target worktree must have committed AGENTS.md instructions")
    return branch


def command(binary: str, prompt: str, mode: str, turns: int, budget: float, model: str, effort: str) -> list[str]:
    return [
        binary, "--print", prompt, "--output-format", "stream-json",
        "--model", model, "--effort", effort,
        "--verbose", "--permission-mode", "dontAsk",
        "--allowedTools", EDIT_TOOLS if mode == "edit" else READ_TOOLS,
        "--disallowedTools", DISALLOWED, "--max-turns", str(turns),
        "--max-budget-usd", f"{budget:.2f}",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-session-persistence",
    ]


def terminal_result(stdout: str) -> dict:
    last = None
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict) and item.get("type") == "result":
            last = item
    return last or {}


def execute(args: argparse.Namespace) -> dict:
    workspace = Path(args.workspace).expanduser()
    root = Path(args.workspace_root).expanduser()
    branch = validate(workspace, root, args.repo, args.mode)
    prompt_path = Path(args.prompt_file).expanduser().resolve(strict=True)
    if not prompt_path.is_file() or prompt_path.stat().st_size > 64_000:
        raise ValueError("prompt file must be an existing regular file of at most 64 KiB")
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("empty prompt is not allowed")
    if args.mode == "edit" and not args.enable_edits:
        raise ValueError("edit mode requires --enable-edits; no automatic PR or merge")
    binary = args.claude_bin if os.path.sep in args.claude_bin else shutil.which(args.claude_bin)
    if not binary:
        raise ValueError("Claude Code executable not found on PATH")
    selected = resolve(read_labels(args.labels_json_file), cli_model=args.model, cli_effort=args.effort)
    cli = command(binary, prompt, args.mode, args.max_turns, args.max_budget_usd,
                  selected["model"], selected["effort"])
    metadata = {
        "issue": workspace.name,
        "repository": args.repo,
        "branch": branch,
        "mode": args.mode,
        "requestedModel": selected["model"],
        "requestedEffort": selected["effort"],
        "modelSource": selected["modelSource"],
        "effortSource": selected["effortSource"],
        "turnLimit": args.max_turns,
        "budgetUsd": args.max_budget_usd,
        "status": "dry-run" if args.dry_run else "running",
    }
    if args.dry_run:
        return metadata

    env = {k: v for k, v in os.environ.items()
           if k not in BLOCKED_ENV and not k.startswith("SYMPHONY_")}
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    state = Path(args.state_root).expanduser().resolve()
    dest = state / "claude-pilot" / workspace.name
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    # Reuse Codex's exact host lock paths and lock order for mutation and issue ownership.
    # A concurrent Codex implementation/reviewer or another Claude pilot must refuse.
    slots = [state / "locks" / "codex-mutation.lock",
             state / "locks" / "issues" / (workspace.name + ".lock")]
    for item in slots:
        item.parent.mkdir(parents=True, exist_ok=True)
    with slots[0].open("a+") as mutation_lock, slots[1].open("a+") as issue_lock:
        try:
            fcntl.flock(mutation_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(issue_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another Supervisor worker holds this mutation slot or issue") from exc
        start = time.monotonic()
        process = subprocess.Popen(
            cli, cwd=str(workspace), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=args.timeout_seconds)
            expired = False
        except subprocess.TimeoutExpired:
            expired = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        final = terminal_result(stdout[:2_000_000])
        # Claude CLI permissions are not an OS sandbox: verify the checkout's Git
        # identity and refuse to call read-only audit a success if it mutated files.
        try:
            post_branch = git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
            post_origin = normalized_origin(git(workspace, "remote", "get-url", "origin"))
            changed_paths = git(workspace, "status", "--porcelain", "--untracked-files=normal").splitlines()[:80]
            workspace_ok = post_branch == branch and post_origin == args.repo.lower()
        except (ValueError, subprocess.SubprocessError):
            workspace_ok, changed_paths = False, []
        audit_modified = args.mode == "audit" and bool(changed_paths)
        succeeded = (
            not expired and process.returncode == 0 and bool(final)
            and final.get("is_error") is False and final.get("subtype") == "success"
            and workspace_ok and not audit_modified
        )
        metadata.update({
            "changedPaths": changed_paths,
            "workspaceIdentityPreserved": workspace_ok,
            "status": "completed" if succeeded else ("timed-out" if expired else "failed"),
            "exitCode": process.returncode,
            "elapsedSeconds": round(time.monotonic() - start, 2),
            "sessionId": str(final.get("session_id") or "")[:128],
            "turns": final.get("num_turns"),
            # Report only provider-supplied cost; never infer subscription allowances.
            "reportedCostUsd": final.get("total_cost_usd"),
            "result": str(final.get("result") or "")[:1200],
            "stderrExcerpt": stderr[-1500:],
        })
        report = dest / "latest.json"
        tmp = dest / f".{process.pid}.tmp"
        tmp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(report)
        return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--repo", default="Shashakar/Rekindle-Claude")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--state-root", default=os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", "~/.local/state/rekindle-supervisor"))
    parser.add_argument("--mode", choices=("audit", "edit"), default="audit")
    parser.add_argument("--labels-json-file", help="JSON labels from gh issue view --json labels")
    parser.add_argument("--model", choices=MODELS, help="Explicit operator model override")
    parser.add_argument("--effort", choices=EFFORTS, help="Explicit operator effort override")
    parser.add_argument("--enable-edits", action="store_true")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.max_turns <= 8 and 0 < args.max_budget_usd <= 10
            and 10 <= args.timeout_seconds <= 3600):
        parser.error("bounds exceeded: turns 1..8, budget (0,10], timeout 10..3600")
    try:
        output = execute(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Claude pilot refused: {exc}", file=sys.stderr)
        return 70
    print(json.dumps(output, sort_keys=True))
    return 0 if output["status"] in ("completed", "dry-run") else 75


if __name__ == "__main__":
    raise SystemExit(main())
