#!/usr/bin/env python3
"""Host-runner wrapper that refreshes reviewed continuations before normal Git handoff logic."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

BRANCH_PATTERN = re.compile(r"^codex/[A-Za-z0-9][A-Za-z0-9._/-]*$")
ATTEMPT_MARKER = ".symphony-attempt-complete"


def emit_error(status: str, code: int, message: str, *, details: dict[str, Any] | None = None) -> int:
    print(
        json.dumps(
            {
                "status": status,
                "exitCode": code,
                "operation": "prepare",
                "message": message,
                "details": details or {},
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return code


def run_git(workspace: Path, *args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(message)
    return result


def ref_exists(workspace: Path, ref: str) -> bool:
    return run_git(workspace, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def is_ancestor(workspace: Path, older: str, newer: str) -> bool:
    return run_git(workspace, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def hydrate_lfs(workspace: Path, branch: str) -> None:
    attributes = workspace / ".gitattributes"
    if not attributes.is_file() or "filter=lfs" not in attributes.read_text(encoding="utf-8", errors="ignore"):
        return

    probe = subprocess.run(["git", "lfs", "version"], cwd=workspace, text=True, capture_output=True)
    if probe.returncode != 0:
        raise RuntimeError("Git LFS is required by the refreshed repository but is unavailable")

    run_git(workspace, "lfs", "fetch", "origin", "main", timeout=300)
    if ref_exists(workspace, f"refs/remotes/origin/{branch}"):
        run_git(workspace, "lfs", "fetch", "origin", branch, timeout=300)
    run_git(workspace, "lfs", "checkout", timeout=300)


def sync_rearmed_branch(workspace: Path, branch: str) -> tuple[bool, str | None]:
    """Refresh a clean durable continuation branch using host-writable Git metadata."""
    if not (workspace / ATTEMPT_MARKER).exists():
        return True, None

    if not BRANCH_PATTERN.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith("/"):
        return False, f"branch '{branch}' is not an allowed codex/* continuation branch"

    current_result = run_git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    current = current_result.stdout.strip() if current_result.returncode == 0 else ""
    if current != branch:
        # Branch creation/switching remains the responsibility of the normal prepare operation.
        return True, None

    dirty = run_git(workspace, "status", "--porcelain", "--untracked-files=all").stdout.strip()
    if dirty:
        return False, "workspace has uncommitted source changes; preserving prior work instead of refreshing"

    run_git(workspace, "fetch", "--prune", "origin")
    if not ref_exists(workspace, "refs/remotes/origin/main"):
        return False, "origin/main is unavailable after fetch"

    remote_ref = f"refs/remotes/origin/{branch}"
    if not ref_exists(workspace, remote_ref):
        return False, f"rearmed branch origin/{branch} is unavailable; preserving local-only continuation state"

    local_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    remote_head = run_git(workspace, "rev-parse", remote_ref).stdout.strip()
    if local_head != remote_head:
        if is_ancestor(workspace, local_head, remote_ref):
            run_git(workspace, "merge", "--ff-only", remote_ref)
        elif is_ancestor(workspace, remote_ref, "HEAD"):
            return False, f"workspace contains local commits not present on origin/{branch}; preserving unpushed work"
        else:
            return False, f"workspace branch '{branch}' diverged from origin/{branch}; preserving state for review"

    if not is_ancestor(workspace, "refs/remotes/origin/main", "HEAD"):
        name = os.environ.get("RPGK_GIT_AUTHOR_NAME", "RPG Kingdom Symphony")
        email = os.environ.get("RPGK_GIT_AUTHOR_EMAIL", "symphony@local.invalid")
        merge = run_git(
            workspace,
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "merge",
            "--no-edit",
            "refs/remotes/origin/main",
            check=False,
        )
        if merge.returncode != 0:
            run_git(workspace, "merge", "--abort", check=False)
            message = merge.stderr.strip() or merge.stdout.strip() or "merge conflict"
            return False, f"current main conflicts with the durable continuation branch: {message}"

    try:
        hydrate_lfs(workspace, branch)
    except RuntimeError as exc:
        return False, str(exc)

    if run_git(workspace, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        return False, "workspace refresh left unexpected source changes"

    return True, None


def value_after(args: list[str], flag: str) -> str:
    try:
        index = args.index(flag)
    except ValueError:
        return ""
    return args[index + 1] if index + 1 < len(args) else ""


def main() -> int:
    args = sys.argv[1:]
    request_value = value_after(args, "--request")
    workspace_value = value_after(args, "--workspace")
    if not request_value or not workspace_value:
        return emit_error("InvalidRequest", 64, "host wrapper requires --request and --workspace")

    request_path = Path(request_value).resolve()
    workspace = Path(workspace_value).resolve()
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return emit_error("InvalidRequest", 64, f"unable to read broker request: {exc}")

    operation = str(payload.get("operation", "")) if isinstance(payload, dict) else ""
    branch = str(payload.get("branch", "")) if isinstance(payload, dict) else ""
    completion_mode = str(payload.get("completionMode", "")) if isinstance(payload, dict) else ""

    if operation == "prepare" and (workspace / ATTEMPT_MARKER).exists():
        try:
            ok, message = sync_rearmed_branch(workspace, branch)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            return emit_error("GitFailed", 71, f"continuation workspace refresh failed: {exc}")
        if not ok:
            return emit_error("ContinuationSyncBlocked", 73, message or "continuation workspace refresh blocked")

    if operation == "handoff" and completion_mode == "report-only":
        report_core = Path(__file__).with_name("report-complete-host.py")
        os.execv(sys.executable, [sys.executable, str(report_core), *args])
        return 70

    core = Path(__file__).with_name("git-handoff-host.py")
    os.execv(sys.executable, [sys.executable, str(core), *args])
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
