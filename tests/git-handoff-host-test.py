#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "git-handoff-host.py"
spec = importlib.util.spec_from_file_location("rpgk_git_handoff_host", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load git-handoff-host.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def expect_error(status: str, fn: Any) -> None:
    try:
        fn()
    except module.HandoffError as exc:
        if exc.status != status:
            raise AssertionError(f"expected {status}, got {exc.status}: {exc}") from exc
    else:
        raise AssertionError(f"expected HandoffError({status})")


def write_summary(path: Path, run_id: str, test_filter: str = "FocusedTest") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "result": "Passed",
                "total": 1,
                "passed": 1,
                "failed": 0,
                "unityExitCode": 0,
                "testPlatform": "PlayMode",
                "testFilter": test_filter,
                "runId": run_id,
            }
        ),
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-git-handoff-test-") as temp:
        root = Path(temp)
        workspace_root = root / "workspaces"
        workspace = workspace_root / "GH-321"
        remote = root / "remote.git"
        workspace.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(workspace)], check=True, capture_output=True)
        git(workspace, "config", "user.name", "Test User")
        git(workspace, "config", "user.email", "test@example.invalid")
        # Match the RPG Kingdom repository contract: Unity/Supervisor validation output under Logs/
        # is runtime evidence, not source, and is ignored by Git in real GH workspaces.
        (workspace / ".gitignore").write_text("Logs/\n", encoding="utf-8")
        (workspace / "game.txt").write_text("base\n", encoding="utf-8")
        git(workspace, "add", ".gitignore", "game.txt")
        git(workspace, "commit", "-m", "base")
        git(workspace, "push", str(remote), "main")
        git(workspace, "remote", "add", "origin", "https://github.com/Shashakar/RPG-Kingdom.git")
        git(workspace, "fetch", str(remote), "+refs/heads/*:refs/remotes/origin/*")

        original_run_git = module.run_git

        def local_remote_git(repo: Path, *args: str, timeout: int = 120, check: bool = True):
            rewritten = list(args)
            if rewritten[:3] == ["fetch", "--prune", "origin"]:
                rewritten = ["fetch", "--prune", str(remote), "+refs/heads/*:refs/remotes/origin/*"]
            elif len(rewritten) >= 3 and rewritten[:2] == ["fetch", "origin"]:
                branch = rewritten[2]
                rewritten = ["fetch", str(remote), f"refs/heads/{branch}:refs/remotes/origin/{branch}"]
            elif len(rewritten) >= 3 and rewritten[0] == "push" and "origin" in rewritten:
                rewritten[rewritten.index("origin")] = str(remote)
            return original_run_git(repo, *rewritten, timeout=timeout, check=check)

        module.run_git = local_remote_git

        api_calls: list[tuple[str, str, Any]] = []
        api_state = {"existing_pr": False}

        def fake_api(
            api_root: str,
            token: str,
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
            *,
            allow_not_found: bool = False,
        ) -> Any:
            del api_root, token, allow_not_found
            api_calls.append((method, path, body))
            if method == "GET" and "/issues/321/labels" in path:
                return [{"name": "validation:unity-required"}, {"name": "symphony:ready"}]
            if method == "GET" and "/pulls?" in path:
                if api_state["existing_pr"]:
                    return [{"number": 42, "html_url": "https://example.invalid/pr/42"}]
                return []
            if method == "POST" and path.endswith("/pulls"):
                api_state["existing_pr"] = True
                return {"number": 42, "html_url": "https://example.invalid/pr/42"}
            if method == "PATCH" and "/pulls/42" in path:
                return {"number": 42, "html_url": "https://example.invalid/pr/42"}
            if method == "DELETE" and path.endswith("/labels/symphony%3Aready"):
                return []
            raise AssertionError(f"unexpected API call: {method} {path} {body}")

        module.api_request = fake_api

        issue = module.validate_workspace(workspace, workspace_root, "Shashakar/RPG-Kingdom")
        assert issue == 321

        # Reproduce the #98 recovery case: the model left a valid source edit on main.
        (workspace / "game.txt").write_text("fixed\n", encoding="utf-8")
        prepared = module.prepare_branch(workspace, "codex/gh-321-test")
        assert prepared["branch"] == "codex/gh-321-test"
        assert module.current_branch(workspace) == "codex/gh-321-test"
        assert (workspace / "game.txt").read_text(encoding="utf-8") == "fixed\n"

        summary_path = workspace / "Logs" / "SymphonyUnity" / "run-pass" / "summary.json"
        write_summary(summary_path, "run-pass")
        assert git(workspace, "status", "--short", "--ignored", "Logs/SymphonyUnity/run-pass/summary.json").startswith("!!")

        labels = module.issue_labels("https://api.invalid", "token", "Shashakar", "RPG-Kingdom", 321)
        assert "validation:unity-required" in labels
        evidence = module.validate_unity_evidence(workspace, ["run-pass"], True)
        assert evidence[0]["total"] == 1
        expect_error("ValidationEvidenceMissing", lambda: module.validate_unity_evidence(workspace, [], True))
        expect_error("InvalidBranch", lambda: module.validate_branch("feature/not-codex"))

        # A rearmed continuation must not be allowed to recycle a passing summary from before the
        # previous completed-attempt marker. A summary created after that boundary remains valid.
        marker_path = workspace / module.ATTEMPT_MARKER
        marker_path.write_text("completed prior attempt\n", encoding="utf-8")
        marker_ns = max(marker_path.stat().st_mtime_ns, summary_path.stat().st_mtime_ns + 1_000_000)
        os.utime(marker_path, ns=(marker_ns, marker_ns))
        boundary_ns = module.previous_attempt_boundary_ns(workspace)
        assert boundary_ns == marker_ns
        expect_error(
            "ValidationEvidenceStale",
            lambda: module.validate_unity_evidence(workspace, ["run-pass"], True, newer_than_ns=boundary_ns),
        )

        fresh_summary_path = workspace / "Logs" / "SymphonyUnity" / "run-fresh" / "summary.json"
        write_summary(fresh_summary_path, "run-fresh", "FreshFocusedTest")
        fresh_ns = marker_ns + 1_000_000
        os.utime(fresh_summary_path, ns=(fresh_ns, fresh_ns))
        fresh_evidence = module.validate_unity_evidence(
            workspace,
            ["run-fresh"],
            True,
            newer_than_ns=boundary_ns,
        )
        assert fresh_evidence[0]["runId"] == "run-fresh"
        marker_path.unlink()

        commit_sha, committed = module.commit_changes(workspace, "fix: test handoff")
        assert committed is True
        assert len(commit_sha) == 40
        module.ensure_safe_history(workspace, "codex/gh-321-test")
        assert module.remote_branch_sha(workspace, "codex/gh-321-test") is None
        pushed_sha = module.push_branch(workspace, "codex/gh-321-test")
        assert pushed_sha == commit_sha
        assert git(remote, "rev-parse", "refs/heads/codex/gh-321-test") == commit_sha
        assert module.remote_branch_sha(workspace, "codex/gh-321-test") == commit_sha

        assert module.handoff_has_reviewable_progress(None, commit_sha, []) is True
        assert module.handoff_has_reviewable_progress(commit_sha, commit_sha, []) is False
        assert module.handoff_has_reviewable_progress(commit_sha, commit_sha, fresh_evidence) is True

        pr_number, pr_url, created = module.create_or_update_pr(
            "https://api.invalid",
            "token",
            "Shashakar",
            "RPG-Kingdom",
            "codex/gh-321-test",
            "Fix test",
            "Closes #321",
        )
        assert (pr_number, pr_url, created) == (42, "https://example.invalid/pr/42", True)

        patch_calls_before = sum(1 for method, path, _ in api_calls if method == "PATCH" and "/pulls/42" in path)
        expect_error(
            "NoHandoffProgress",
            lambda: module.create_or_update_pr(
                "https://api.invalid",
                "token",
                "Shashakar",
                "RPG-Kingdom",
                "codex/gh-321-test",
                "Misleading rewrite",
                "Claims without new work",
                allow_existing_update=False,
            ),
        )
        patch_calls_after = sum(1 for method, path, _ in api_calls if method == "PATCH" and "/pulls/42" in path)
        assert patch_calls_after == patch_calls_before

        pr_number, pr_url, created = module.create_or_update_pr(
            "https://api.invalid",
            "token",
            "Shashakar",
            "RPG-Kingdom",
            "codex/gh-321-test",
            "Validation-only update",
            "Fresh current-attempt evidence",
            allow_existing_update=True,
        )
        assert (pr_number, pr_url, created) == (42, "https://example.invalid/pr/42", False)
        assert module.remove_dispatch_lease("https://api.invalid", "token", "Shashakar", "RPG-Kingdom", 321) is True
        assert any(method == "DELETE" and "symphony%3Aready" in path for method, path, _ in api_calls)

        git(workspace, "remote", "set-url", "origin", "https://github.com/Shashakar/Other.git")
        expect_error("InvalidOrigin", lambda: module.validate_workspace(workspace, workspace_root, "Shashakar/RPG-Kingdom"))
        git(workspace, "remote", "set-url", "origin", "https://github.com/Shashakar/RPG-Kingdom.git")

        (workspace / ".symphony-attempt-complete").write_text("runtime\n", encoding="utf-8")
        git(workspace, "add", "-A", "--", ".")
        expect_error("ForbiddenPath", lambda: module.reject_forbidden_paths(module.staged_paths(workspace)))
        git(workspace, "reset")
        (workspace / ".symphony-attempt-complete").unlink()

    print("git-handoff-host-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
