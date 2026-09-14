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
SYNC_EVIDENCE_ENV = "RPGK_CONTINUATION_SYNC_EVIDENCE"


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


def dirty_paths(workspace: Path) -> list[str]:
    paths: set[str] = set()
    commands = (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    for args in commands:
        output = run_git(workspace, *args).stdout
        paths.update(item for item in output.split("\0") if item)
    return sorted(paths)


def set_sync_evidence(**values: Any) -> None:
    os.environ[SYNC_EVIDENCE_ENV] = json.dumps(values, separators=(",", ":"), sort_keys=True)


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


def restore_clean_head(workspace: Path, head: str) -> None:
    """Restore the pre-refresh clean continuation state after a failed host refresh."""
    run_git(workspace, "merge", "--abort", check=False)
    run_git(workspace, "reset", "--hard", head)


def stash_dirty_workspace(workspace: Path, branch: str) -> tuple[str | None, str | None, list[str]]:
    paths = dirty_paths(workspace)
    if not paths:
        return None, None, []

    message = f"symphony continuation sync {branch}"
    result = run_git(workspace, "stash", "push", "--include-untracked", "-m", message, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git stash failed"
        raise RuntimeError(f"could not preserve dirty continuation work: {detail}")
    if dirty_paths(workspace):
        raise RuntimeError("continuation stash left uncommitted source changes behind")

    stash_ref = "stash@{0}"
    stash_sha = run_git(workspace, "rev-parse", stash_ref).stdout.strip()
    return stash_ref, stash_sha, paths


def apply_preserved_stash(workspace: Path, stash_ref: str) -> subprocess.CompletedProcess[str]:
    return run_git(workspace, "stash", "apply", "--index", stash_ref, check=False)


def restore_original_with_stash(workspace: Path, original_head: str, stash_ref: str | None) -> None:
    restore_clean_head(workspace, original_head)
    if stash_ref is None:
        return

    # Remove only non-ignored untracked files created by a failed stash application. Supervisor
    # runtime evidence is ignored and therefore survives this recovery cleanup.
    run_git(workspace, "clean", "-fd", check=False)
    restored = apply_preserved_stash(workspace, stash_ref)
    if restored.returncode != 0:
        message = restored.stderr.strip() or restored.stdout.strip() or "stash restore failed"
        raise RuntimeError(
            f"failed to restore preserved continuation work; recovery stash remains at {stash_ref}: {message}"
        )


def sync_rearmed_branch(workspace: Path, branch: str) -> tuple[bool, str | None]:
    """Refresh a reviewed continuation branch while preserving valid dirty source work."""
    os.environ.pop(SYNC_EVIDENCE_ENV, None)
    if not (workspace / ATTEMPT_MARKER).exists():
        return True, None

    if not BRANCH_PATTERN.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith("/"):
        return False, f"branch '{branch}' is not an allowed codex/* continuation branch"

    current_result = run_git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    current = current_result.stdout.strip() if current_result.returncode == 0 else ""
    if current != branch:
        return True, None

    original_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    stash_ref: str | None = None
    stash_sha: str | None = None
    preserved_paths: list[str] = []

    try:
        stash_ref, stash_sha, preserved_paths = stash_dirty_workspace(workspace, branch)
        run_git(workspace, "fetch", "--prune", "origin")
        if not ref_exists(workspace, "refs/remotes/origin/main"):
            restore_original_with_stash(workspace, original_head, stash_ref)
            set_sync_evidence(performed=False, result="blocked", oldHead=original_head,
                              preservedPaths=preserved_paths, recoveryStash=stash_sha,
                              reason="origin-main-unavailable")
            return False, "origin/main is unavailable after fetch"

        remote_ref = f"refs/remotes/origin/{branch}"
        if ref_exists(workspace, remote_ref):
            local_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
            remote_head = run_git(workspace, "rev-parse", remote_ref).stdout.strip()
            if local_head != remote_head:
                if is_ancestor(workspace, local_head, remote_ref):
                    run_git(workspace, "merge", "--ff-only", remote_ref)
                elif is_ancestor(workspace, remote_ref, "HEAD"):
                    pass
                else:
                    restore_original_with_stash(workspace, original_head, stash_ref)
                    set_sync_evidence(performed=False, result="blocked", oldHead=original_head,
                                      preservedPaths=preserved_paths, recoveryStash=stash_sha,
                                      reason="branch-diverged")
                    return False, f"workspace branch '{branch}' diverged from origin/{branch}; preserving state for review"

        origin_main = run_git(workspace, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        merged_main = False
        if not is_ancestor(workspace, "refs/remotes/origin/main", "HEAD"):
            name = os.environ.get("RPGK_GIT_AUTHOR_NAME", "RPG Kingdom Symphony")
            email = os.environ.get("RPGK_GIT_AUTHOR_EMAIL", "symphony@local.invalid")
            merge = run_git(workspace, "-c", f"user.name={name}", "-c", f"user.email={email}",
                            "merge", "--no-edit", "refs/remotes/origin/main", check=False)
            if merge.returncode != 0:
                message = merge.stderr.strip() or merge.stdout.strip() or "merge conflict"
                restore_original_with_stash(workspace, original_head, stash_ref)
                set_sync_evidence(performed=False, result="conflict", oldHead=original_head,
                                  newBase=origin_main, preservedPaths=preserved_paths,
                                  recoveryStash=stash_sha, reason="main-merge-conflict")
                return False, f"current main conflicts with the continuation branch: {message}"
            merged_main = True

        try:
            hydrate_lfs(workspace, branch)
        except RuntimeError as exc:
            restore_original_with_stash(workspace, original_head, stash_ref)
            set_sync_evidence(performed=False, result="blocked", oldHead=original_head,
                              newBase=origin_main, preservedPaths=preserved_paths,
                              recoveryStash=stash_sha, reason="lfs-hydration-failed")
            return False, str(exc)

        if stash_ref is not None:
            reapplied = apply_preserved_stash(workspace, stash_ref)
            if reapplied.returncode != 0:
                message = reapplied.stderr.strip() or reapplied.stdout.strip() or "stash apply conflict"
                conflict_paths = run_git(workspace, "diff", "--name-only", "--diff-filter=U", check=False).stdout.splitlines()
                restore_original_with_stash(workspace, original_head, stash_ref)
                set_sync_evidence(performed=False, result="conflict", oldHead=original_head,
                                  newBase=origin_main, preservedPaths=preserved_paths,
                                  conflictPaths=conflict_paths, recoveryStash=stash_sha,
                                  reason="dirty-reapply-conflict")
                return False, f"current main conflicts with preserved uncommitted continuation work: {message}"
            run_git(workspace, "stash", "drop", stash_ref)

        new_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
        remaining_paths = dirty_paths(workspace)
        if stash_ref is None and remaining_paths:
            restore_original_with_stash(workspace, original_head, None)
            return False, "workspace refresh left unexpected source changes"

        set_sync_evidence(performed=bool(merged_main or preserved_paths), result="completed",
                          oldHead=original_head, newHead=new_head, newBase=origin_main,
                          preservedPaths=preserved_paths, restoredDirtyPaths=remaining_paths,
                          mergedMain=merged_main)
        return True, None
    except (RuntimeError, subprocess.TimeoutExpired):
        try:
            restore_original_with_stash(workspace, original_head, stash_ref)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        raise


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

        raw_evidence = os.environ.get(SYNC_EVIDENCE_ENV, "")
        if not ok:
            details: dict[str, Any] = {}
            if raw_evidence:
                try:
                    parsed = json.loads(raw_evidence)
                    if isinstance(parsed, dict):
                        details["continuationSync"] = parsed
                except json.JSONDecodeError:
                    pass
            return emit_error("ContinuationSyncBlocked", 73, message or "continuation workspace refresh blocked", details=details)

        if raw_evidence:
            try:
                parsed = json.loads(raw_evidence)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("performed"):
                print(
                    json.dumps({"continuationSync": parsed}, separators=(",", ":"), sort_keys=True),
                    file=sys.stderr,
                    flush=True,
                )

    if operation == "handoff" and completion_mode == "report-only":
        report_core = Path(__file__).with_name("report-complete-host.py")
        os.execv(sys.executable, [sys.executable, str(report_core), *args])
        return 70

    core = Path(__file__).with_name("git-handoff-host.py")
    os.execv(sys.executable, [sys.executable, str(core), *args])
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
