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
from urllib import error, parse, request

BRANCH_PATTERN = re.compile(r"^codex/[A-Za-z0-9][A-Za-z0-9._/-]*$")
ATTEMPT_MARKER = ".symphony-attempt-complete"
SYNC_EVIDENCE_ENV = "RPGK_CONTINUATION_SYNC_EVIDENCE"


def emit_error(
    status: str,
    code: int,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    operation: str = "prepare",
) -> int:
    print(
        json.dumps(
            {
                "status": status,
                "exitCode": code,
                "operation": operation,
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


def sync_rearmed_main(workspace: Path) -> tuple[bool, str | None, dict[str, Any]]:
    """Fast-forward a clean rearmed main workspace before dispatch-time preflight."""
    current_result = run_git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    current = current_result.stdout.strip() if current_result.returncode == 0 else ""
    if current != "main":
        return False, f"workspace is on '{current or 'detached HEAD'}', not main", {"reason": "not-main"}

    dirty = dirty_paths(workspace)
    if dirty:
        return False, "rearmed main workspace contains source changes; refusing automatic refresh", {
            "reason": "dirty-main",
            "dirtyPaths": dirty,
        }

    old_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    run_git(workspace, "fetch", "--prune", "origin")
    if not ref_exists(workspace, "refs/remotes/origin/main"):
        return False, "origin/main is unavailable after fetch", {"reason": "origin-main-unavailable", "oldHead": old_head}

    new_base = run_git(workspace, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    if not is_ancestor(workspace, old_head, "refs/remotes/origin/main"):
        return False, "local main has diverged from origin/main; preserving workspace for review", {
            "reason": "main-diverged",
            "oldHead": old_head,
            "originMain": new_base,
        }

    if old_head != new_base:
        run_git(workspace, "merge", "--ff-only", "refs/remotes/origin/main")
    hydrate_lfs(workspace, "main")
    new_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    if dirty_paths(workspace):
        return False, "main refresh left unexpected source changes", {
            "reason": "dirty-after-sync",
            "oldHead": old_head,
            "newHead": new_head,
        }

    return True, None, {
        "performed": old_head != new_head,
        "result": "completed",
        "oldHead": old_head,
        "newHead": new_head,
        "newBase": new_base,
        "fastForwarded": old_head != new_head,
    }


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


def tracker_api(method: str, path: str, body: dict[str, Any] | None = None, *, allow_not_found: bool = False) -> Any:
    token = os.environ.get("SYMPHONY_GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("SYMPHONY_GITHUB_TOKEN is required for review handoff")
    api_root = os.environ.get("RPGK_GITHUB_API_ROOT", "https://api.github.com").rstrip("/")
    owner = os.environ.get("RPGK_REPO_OWNER", "Shashakar")
    repo = os.environ.get("RPGK_REPO_NAME", "RPG-Kingdom")
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        f"{api_root}/repos/{owner}/{repo}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "rpg-kingdom-supervisor-review-handoff",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        raise RuntimeError(f"GitHub {method} {path} failed: HTTP {exc.code}") from exc


def queue_review_from_handoff(result: dict[str, Any]) -> None:
    issue_number = int(result["issue"])
    pr_number = int(result["prNumber"])
    head_sha = str(result.get("pushedSha") or result.get("commitSha") or "").strip()
    if not head_sha:
        raise RuntimeError("successful handoff did not return a pushed PR head")

    writer = Path(__file__).with_name("queue-agent-review.py")
    subprocess.run(
        [sys.executable, str(writer), str(issue_number), str(pr_number), head_sha],
        check=True,
        env=os.environ.copy(),
    )

    for label in (
        "symphony:rework",
        "symphony:halted",
        "symphony:rearm",
        "symphony:ready",
        "repair-route:luna",
        "repair-route:terra",
        "repair-route:sol",
        "repair-route:astra",
    ):
        tracker_api("DELETE", f"/issues/{issue_number}/labels/{parse.quote(label, safe='')}", allow_not_found=True)
    tracker_api("POST", f"/issues/{issue_number}/labels", {"labels": ["symphony:agent-review"]})


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

    if operation == "sync-main":
        if not (workspace / ATTEMPT_MARKER).exists():
            return emit_error("InvalidRequest", 64, "sync-main is only allowed for a reviewed rearm workspace", operation=operation)
        try:
            ok, message, evidence = sync_rearmed_main(workspace)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            return emit_error("GitFailed", 71, f"rearmed main refresh failed: {exc}", operation=operation)
        if not ok:
            status = "DirtyWorkspace" if evidence.get("reason") in {"dirty-main", "dirty-after-sync"} else "MainDiverged"
            if evidence.get("reason") in {"not-main", "origin-main-unavailable"}:
                status = "UnexpectedBranch"
            return emit_error(status, 73, message or "rearmed main refresh blocked", details={"continuationSync": evidence}, operation=operation)
        print(json.dumps({
            "status": "completed",
            "exitCode": 0,
            "operation": operation,
            "branch": "main",
            "head": evidence.get("newHead"),
            "continuationSync": evidence,
        }, separators=(",", ":"), sort_keys=True))
        return 0

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
    if operation != "handoff":
        os.execv(sys.executable, [sys.executable, str(core), *args])
        return 70

    completed = subprocess.run(
        [sys.executable, str(core), *args],
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode != 0:
        return completed.returncode

    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError as exc:
        return emit_error("ReviewHandoffFailed", 78, f"successful Git handoff returned unreadable result: {exc}")
    if not isinstance(result, dict) or result.get("status") != "completed":
        return emit_error("ReviewHandoffFailed", 78, "successful Git handoff did not return completed structured output")

    try:
        queue_review_from_handoff(result)
    except Exception as exc:
        return emit_error(
            "ReviewHandoffFailed",
            78,
            f"Git handoff succeeded but agent-review lifecycle reconciliation failed: {exc}",
            details={"handoff": result},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
