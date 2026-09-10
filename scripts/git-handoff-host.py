#!/usr/bin/env python3
"""Host-side implementation for bounded RPG Kingdom Git prepare/handoff operations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any
from urllib import error, parse, request

BRANCH_PATTERN = re.compile(r"^codex/[A-Za-z0-9][A-Za-z0-9._/-]*$")
ISSUE_WORKSPACE = re.compile(r"^GH-(\d+)$")
ATTEMPT_MARKER = ".symphony-attempt-complete"
FORBIDDEN_PATHS = (
    ATTEMPT_MARKER,
    "Logs/SymphonyUnity/",
    "Logs/SymphonyGit/",
)


class HandoffError(RuntimeError):
    def __init__(self, message: str, *, code: int = 70, status: str = "failed", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def run_git(workspace: Path, *args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
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
        raise HandoffError(message, code=71, status="GitFailed")
    return result


def normalize_repository(url: str) -> str | None:
    value = url.strip().rstrip("/")
    https_match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?", value, re.IGNORECASE)
    if https_match:
        return f"{https_match.group(1)}/{https_match.group(2)}"
    ssh_match = re.fullmatch(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?", value, re.IGNORECASE)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"
    ssh_url_match = re.fullmatch(r"ssh://git@github\.com/([^/]+)/(.+?)(?:\.git)?", value, re.IGNORECASE)
    if ssh_url_match:
        return f"{ssh_url_match.group(1)}/{ssh_url_match.group(2)}"
    return None


def validate_branch(branch: str) -> None:
    if not BRANCH_PATTERN.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith("/"):
        raise HandoffError(
            f"branch '{branch}' is not an allowed codex/* branch",
            code=64,
            status="InvalidBranch",
        )


def validate_workspace(workspace: Path, workspace_root: Path, expected_repo: str) -> int:
    workspace = workspace.resolve()
    workspace_root = workspace_root.resolve()
    match = ISSUE_WORKSPACE.fullmatch(workspace.name)
    if match is None or workspace.parent != workspace_root:
        raise HandoffError(
            f"workspace '{workspace}' is not a GH issue workspace under '{workspace_root}'",
            code=81,
            status="InvalidWorkspace",
        )

    top = Path(run_git(workspace, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != workspace:
        raise HandoffError(
            f"Git top-level '{top}' does not match workspace '{workspace}'",
            code=81,
            status="InvalidWorkspace",
        )

    origin = run_git(workspace, "remote", "get-url", "origin").stdout.strip()
    actual_repo = normalize_repository(origin)
    if actual_repo is None or actual_repo.lower() != expected_repo.lower():
        raise HandoffError(
            f"origin '{origin}' does not match expected repository '{expected_repo}'",
            code=82,
            status="InvalidOrigin",
        )

    return int(match.group(1))


def configure_askpass(state_root: Path) -> None:
    token = os.environ.get("SYMPHONY_GITHUB_TOKEN", "")
    if not token:
        return
    root = state_root / "git-broker"
    root.mkdir(parents=True, exist_ok=True)
    script = root / "git-askpass.sh"
    contents = """#!/usr/bin/env bash
case "${1:-}" in
  *Username*) printf '%s\\n' 'x-access-token' ;;
  *Password*) printf '%s\\n' "${SYMPHONY_GITHUB_TOKEN:-}" ;;
  *) printf '%s\\n' "${SYMPHONY_GITHUB_TOKEN:-}" ;;
esac
"""
    if not script.exists() or script.read_text(encoding="utf-8") != contents:
        script.write_text(contents, encoding="utf-8")
    script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    os.environ["GIT_ASKPASS"] = str(script)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"


def git_ref_exists(workspace: Path, ref: str) -> bool:
    return run_git(workspace, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def current_branch(workspace: Path) -> str:
    result = run_git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def remote_branch_sha(workspace: Path, branch: str) -> str | None:
    remote_ref = f"refs/remotes/origin/{branch}"
    if not git_ref_exists(workspace, remote_ref):
        return None
    return run_git(workspace, "rev-parse", remote_ref).stdout.strip()


def prepare_branch(workspace: Path, branch: str) -> dict[str, Any]:
    validate_branch(branch)
    run_git(workspace, "fetch", "--prune", "origin", timeout=180)

    current = current_branch(workspace)
    if current == branch:
        return {"branch": branch, "head": run_git(workspace, "rev-parse", "HEAD").stdout.strip(), "created": False}

    if current not in {"", "main"}:
        raise HandoffError(
            f"workspace is on branch '{current}', not 'main' or requested branch '{branch}'",
            code=83,
            status="UnexpectedBranch",
        )

    if current == "":
        head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
        origin_main = run_git(workspace, "rev-parse", "origin/main").stdout.strip()
        if head != origin_main:
            raise HandoffError(
                "detached workspace HEAD is not current origin/main",
                code=83,
                status="UnexpectedBranch",
            )

    if git_ref_exists(workspace, f"refs/heads/{branch}"):
        run_git(workspace, "switch", branch)
        created = False
    elif git_ref_exists(workspace, f"refs/remotes/origin/{branch}"):
        run_git(workspace, "switch", "--track", "-c", branch, f"origin/{branch}")
        created = False
    else:
        run_git(workspace, "switch", "-c", branch, "origin/main")
        created = True

    return {"branch": branch, "head": run_git(workspace, "rev-parse", "HEAD").stdout.strip(), "created": created}


def api_request(
    api_root: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    allow_not_found: bool = False,
) -> Any:
    if not token:
        raise HandoffError("SYMPHONY_GITHUB_TOKEN is required for GitHub handoff", code=73, status="GitHubAuthMissing")

    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        f"{api_root.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "rpg-kingdom-supervisor-git-handoff",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else None
    except error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        payload = exc.read().decode("utf-8", errors="replace")
        raise HandoffError(
            f"GitHub API {method} {path} failed with HTTP {exc.code}: {payload[:1000]}",
            code=73,
            status="GitHubApiFailed",
        ) from exc
    except error.URLError as exc:
        raise HandoffError(
            f"GitHub API {method} {path} failed: {exc.reason}",
            code=73,
            status="GitHubNetworkFailed",
        ) from exc


def issue_labels(api_root: str, token: str, owner: str, repo: str, issue_number: int) -> set[str]:
    payload = api_request(api_root, token, "GET", f"/repos/{owner}/{repo}/issues/{issue_number}/labels?per_page=100")
    if not isinstance(payload, list):
        raise HandoffError("GitHub issue label response was not a list", code=73, status="GitHubApiFailed")
    return {str(item.get("name", "")).lower() for item in payload if isinstance(item, dict)}


def previous_attempt_boundary_ns(workspace: Path) -> int | None:
    marker_path = workspace / ATTEMPT_MARKER
    if not marker_path.is_file():
        return None
    return marker_path.stat().st_mtime_ns


def validate_unity_evidence(
    workspace: Path,
    run_ids: list[str],
    required: bool,
    *,
    newer_than_ns: int | None = None,
) -> list[dict[str, Any]]:
    if required and not run_ids:
        raise HandoffError(
            "validation:unity-required is present but no Unity validation run IDs were supplied",
            code=72,
            status="ValidationEvidenceMissing",
        )

    evidence: list[dict[str, Any]] = []
    for run_id in run_ids:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
            raise HandoffError(f"invalid Unity validation run ID '{run_id}'", code=72, status="ValidationEvidenceInvalid")
        summary_path = workspace / "Logs" / "SymphonyUnity" / run_id / "summary.json"
        if not summary_path.is_file():
            raise HandoffError(
                f"Unity validation summary does not exist: {summary_path}",
                code=72,
                status="ValidationEvidenceMissing",
            )
        if newer_than_ns is not None and summary_path.stat().st_mtime_ns <= newer_than_ns:
            raise HandoffError(
                f"Unity validation run '{run_id}' predates the current reviewed continuation",
                code=72,
                status="ValidationEvidenceStale",
                details={"runId": run_id, "summaryPath": str(summary_path)},
            )
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HandoffError(
                f"Unity validation summary is unreadable: {summary_path}: {exc}",
                code=72,
                status="ValidationEvidenceInvalid",
            ) from exc
        if not isinstance(summary, dict):
            raise HandoffError(f"Unity validation summary is not an object: {summary_path}", code=72, status="ValidationEvidenceInvalid")
        try:
            total = int(summary.get("total", -1))
            exit_code = int(summary.get("unityExitCode", -1))
        except (TypeError, ValueError) as exc:
            raise HandoffError(f"Unity validation summary has invalid counts: {summary_path}", code=72, status="ValidationEvidenceInvalid") from exc
        if total <= 0 or exit_code != 0 or not str(summary.get("result", "")).startswith("Passed"):
            raise HandoffError(
                f"Unity validation run '{run_id}' is not a passing non-zero test result",
                code=72,
                status="ValidationEvidenceFailed",
                details={"summary": summary},
            )
        evidence.append(
            {
                "runId": run_id,
                "platform": summary.get("testPlatform"),
                "filter": summary.get("testFilter"),
                "total": total,
                "passed": summary.get("passed"),
            }
        )
    return evidence


def staged_paths(workspace: Path) -> list[str]:
    output = run_git(workspace, "diff", "--cached", "--name-only", "-z").stdout
    return [path for path in output.split("\0") if path]


def reject_forbidden_paths(paths: list[str]) -> None:
    rejected = []
    for path in paths:
        if path == ATTEMPT_MARKER or any(path.startswith(prefix) for prefix in FORBIDDEN_PATHS[1:]):
            rejected.append(path)
    if rejected:
        raise HandoffError(
            "handoff attempted to commit Supervisor runtime artifacts",
            code=74,
            status="ForbiddenPath",
            details={"paths": rejected},
        )


def commit_changes(workspace: Path, message: str) -> tuple[str, bool]:
    if not message.strip():
        raise HandoffError("commit message is required", code=64, status="InvalidRequest")

    run_git(workspace, "add", "-A", "--", ".")
    paths = staged_paths(workspace)
    reject_forbidden_paths(paths)

    committed = False
    if paths:
        name = os.environ.get("RPGK_GIT_AUTHOR_NAME", "RPG Kingdom Symphony")
        email = os.environ.get("RPGK_GIT_AUTHOR_EMAIL", "symphony@local.invalid")
        run_git(
            workspace,
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "-m",
            message,
        )
        committed = True

    if run_git(workspace, "status", "--porcelain").stdout.strip():
        raise HandoffError("workspace is not clean after commit", code=75, status="DirtyAfterCommit")

    return run_git(workspace, "rev-parse", "HEAD").stdout.strip(), committed


def ensure_safe_history(workspace: Path, branch: str) -> None:
    if run_git(workspace, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode != 0:
        raise HandoffError(
            "origin/main is not an ancestor of the handoff commit; integrate current main before handoff",
            code=76,
            status="MainNotIntegrated",
        )
    remote_ref = f"refs/remotes/origin/{branch}"
    if git_ref_exists(workspace, remote_ref):
        if run_git(workspace, "merge-base", "--is-ancestor", remote_ref, "HEAD", check=False).returncode != 0:
            raise HandoffError(
                f"remote branch origin/{branch} would require a non-fast-forward push",
                code=76,
                status="NonFastForward",
            )


def push_branch(workspace: Path, branch: str) -> str:
    run_git(workspace, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}", timeout=180)
    run_git(workspace, "fetch", "origin", branch, timeout=180)
    remote_sha = run_git(workspace, "rev-parse", f"origin/{branch}").stdout.strip()
    local_sha = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    if remote_sha != local_sha:
        raise HandoffError(
            f"remote branch verification mismatch: local {local_sha}, remote {remote_sha}",
            code=77,
            status="PushVerificationFailed",
        )
    return remote_sha


def handoff_has_reviewable_progress(
    remote_sha_before: str | None,
    commit_sha: str,
    evidence: list[dict[str, Any]],
) -> bool:
    return remote_sha_before != commit_sha or bool(evidence)


def create_or_update_pr(
    api_root: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
    title: str,
    body: str,
    *,
    allow_existing_update: bool = True,
) -> tuple[int, str, bool]:
    if not title.strip():
        raise HandoffError("PR title is required", code=64, status="InvalidRequest")
    query = parse.urlencode({"state": "open", "head": f"{owner}:{branch}", "base": "main", "per_page": 20})
    existing = api_request(api_root, token, "GET", f"/repos/{owner}/{repo}/pulls?{query}")
    if isinstance(existing, list) and existing:
        first = existing[0]
        number = int(first["number"])
        if not allow_existing_update:
            raise HandoffError(
                "existing PR cannot be rewritten because this handoff produced neither a remote branch advance nor fresh current-attempt validation evidence",
                code=75,
                status="NoHandoffProgress",
                details={"prNumber": number, "branch": branch},
            )
        updated = api_request(
            api_root,
            token,
            "PATCH",
            f"/repos/{owner}/{repo}/pulls/{number}",
            {"title": title, "body": body, "base": "main"},
        )
        return number, str(updated.get("html_url", first.get("html_url", ""))), False

    created = api_request(
        api_root,
        token,
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        {"title": title, "head": branch, "base": "main", "body": body, "draft": False},
    )
    return int(created["number"]), str(created["html_url"]), True


def remove_dispatch_lease(api_root: str, token: str, owner: str, repo: str, issue_number: int) -> bool:
    result = api_request(
        api_root,
        token,
        "DELETE",
        f"/repos/{owner}/{repo}/issues/{issue_number}/labels/symphony%3Aready",
        allow_not_found=True,
    )
    return result is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="RPG Kingdom host Git handoff runner")
    parser.add_argument("--request", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).resolve()
    workspace = Path(args.workspace).resolve()
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()

    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise HandoffError("request must be a JSON object", code=64, status="InvalidRequest")

        operation = str(payload.get("operation", ""))
        branch = str(payload.get("branch", ""))
        owner = os.environ.get("RPGK_REPO_OWNER", "Shashakar")
        repo = os.environ.get("RPGK_REPO_NAME", "RPG-Kingdom")
        expected_repo = f"{owner}/{repo}"
        api_root = os.environ.get("RPGK_GITHUB_API_ROOT", "https://api.github.com")
        token = os.environ.get("SYMPHONY_GITHUB_TOKEN", "")

        issue_number = validate_workspace(workspace, workspace_root, expected_repo)
        if int(payload.get("issueNumber", issue_number)) != issue_number:
            raise HandoffError("request issue number does not match GH workspace", code=81, status="InvalidWorkspace")

        configure_askpass(state_root)

        if operation == "health":
            emit(
                {
                    "status": "completed",
                    "exitCode": 0,
                    "operation": operation,
                    "issue": issue_number,
                    "branch": current_branch(workspace),
                    "head": run_git(workspace, "rev-parse", "HEAD").stdout.strip(),
                }
            )
            return 0

        if operation == "prepare":
            result = prepare_branch(workspace, branch)
            emit({"status": "completed", "exitCode": 0, "operation": operation, "issue": issue_number, **result})
            return 0

        if operation != "handoff":
            raise HandoffError(f"unsupported Git handoff operation '{operation}'", code=64, status="InvalidRequest")

        validate_branch(branch)
        prepare_branch(workspace, branch)
        labels = issue_labels(api_root, token, owner, repo, issue_number)
        run_ids_raw = payload.get("validationRunIds", [])
        if not isinstance(run_ids_raw, list) or not all(isinstance(item, str) for item in run_ids_raw):
            raise HandoffError("validationRunIds must be an array of strings", code=64, status="InvalidRequest")
        evidence = validate_unity_evidence(
            workspace,
            list(dict.fromkeys(run_ids_raw)),
            "validation:unity-required" in labels,
            newer_than_ns=previous_attempt_boundary_ns(workspace),
        )

        run_git(workspace, "fetch", "--prune", "origin", timeout=180)
        remote_sha_before = remote_branch_sha(workspace, branch)
        commit_sha, committed = commit_changes(workspace, str(payload.get("commitMessage", "")))
        ensure_safe_history(workspace, branch)

        if commit_sha == run_git(workspace, "rev-parse", "origin/main").stdout.strip():
            raise HandoffError("handoff contains no commit beyond origin/main", code=75, status="NoChanges")

        reviewable_progress = handoff_has_reviewable_progress(remote_sha_before, commit_sha, evidence)
        pushed_sha = push_branch(workspace, branch)
        pr_number, pr_url, pr_created = create_or_update_pr(
            api_root,
            token,
            owner,
            repo,
            branch,
            str(payload.get("prTitle", "")),
            str(payload.get("prBody", "")),
            allow_existing_update=reviewable_progress,
        )
        lease_removed = remove_dispatch_lease(api_root, token, owner, repo, issue_number)

        emit(
            {
                "status": "completed",
                "exitCode": 0,
                "operation": operation,
                "issue": issue_number,
                "branch": branch,
                "commitSha": commit_sha,
                "committed": committed,
                "branchAdvanced": remote_sha_before != commit_sha,
                "pushedSha": pushed_sha,
                "prNumber": pr_number,
                "prUrl": pr_url,
                "prCreated": pr_created,
                "leaseRemoved": lease_removed,
                "validation": evidence,
            }
        )
        return 0
    except HandoffError as exc:
        emit(
            {
                "status": exc.status,
                "exitCode": exc.code,
                "operation": locals().get("operation", "unknown"),
                "message": str(exc),
                "details": exc.details,
            }
        )
        return exc.code
    except subprocess.TimeoutExpired as exc:
        emit(
            {
                "status": "TimedOut",
                "exitCode": 124,
                "operation": locals().get("operation", "unknown"),
                "message": f"host Git command timed out: {exc}",
            }
        )
        return 124
    except Exception as exc:
        emit(
            {
                "status": "failed",
                "exitCode": 70,
                "operation": locals().get("operation", "unknown"),
                "message": f"unexpected host Git handoff failure: {exc}",
            }
        )
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
