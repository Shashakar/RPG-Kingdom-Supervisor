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
MODULE_PATH = ROOT / "scripts" / "report-complete-host.py"
SPEC = importlib.util.spec_from_file_location("rpgk_report_complete_host", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load report-complete-host.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
core = report.core


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def expect_error(status: str, fn: Any) -> None:
    try:
        fn()
    except core.HandoffError as exc:
        if exc.status != status:
            raise AssertionError(f"expected {status}, got {exc.status}: {exc}") from exc
    else:
        raise AssertionError(f"expected HandoffError({status})")


def write_summary(workspace: Path, run_id: str) -> Path:
    path = workspace / "Logs" / "SymphonyUnity" / run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "result": "Passed",
                "total": 2,
                "passed": 2,
                "failed": 0,
                "unityExitCode": 0,
                "testPlatform": "PlayMode",
                "testFilter": "Diagnostic.Tests",
                "runId": run_id,
            }
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-report-complete-") as temp:
        root = Path(temp)
        workspace_root = root / "workspaces"
        workspace = workspace_root / "GH-321"
        state_root = root / "state"
        remote = root / "remote.git"
        workspace.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(workspace)], check=True, capture_output=True)
        git(workspace, "config", "user.name", "Test User")
        git(workspace, "config", "user.email", "test@example.invalid")
        (workspace / ".gitignore").write_text("Logs/\n.symphony-attempt-complete\n", encoding="utf-8")
        (workspace / "game.txt").write_text("base\n", encoding="utf-8")
        git(workspace, "add", ".gitignore", "game.txt")
        git(workspace, "commit", "-m", "base")
        git(workspace, "remote", "add", "origin", str(remote))
        git(workspace, "push", "-u", "origin", "main")

        # Report-only completion reuses the proven Git/Unity helpers but does not need the GitHub
        # origin check exercised by the normal handoff tests.
        core.validate_workspace = lambda workspace_arg, root_arg, expected_repo: 321

        labels = {report.REPORT_ONLY_LABEL, "validation:unity-required", "symphony:ready"}
        comments: list[dict[str, Any]] = []
        next_comment_id = 100
        fail_ready_delete_once = False

        def fake_issue_labels(api_root: str, token: str, owner: str, repo: str, issue_number: int) -> set[str]:
            del api_root, token, owner, repo
            assert issue_number == 321
            return {item.lower() for item in labels}

        def fake_api(
            api_root: str,
            token: str,
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
            *,
            allow_not_found: bool = False,
        ) -> Any:
            nonlocal next_comment_id, fail_ready_delete_once
            del api_root, token, allow_not_found
            if method == "GET" and "/issues/comments/" in path:
                comment_id = int(path.rsplit("/", 1)[-1])
                return next((item for item in comments if item["id"] == comment_id), None)
            if method == "GET" and "/issues/321/comments?" in path:
                return list(comments)
            if method == "POST" and path.endswith("/issues/321/comments"):
                assert isinstance(body, dict) and body.get("body")
                item = {
                    "id": next_comment_id,
                    "html_url": f"https://example.invalid/comment/{next_comment_id}",
                    "body": body["body"],
                }
                next_comment_id += 1
                comments.append(item)
                return item
            if method == "POST" and path.endswith("/issues/321/labels"):
                assert isinstance(body, dict)
                labels.update(str(item) for item in body.get("labels", []))
                return [{"name": item} for item in sorted(labels)]
            if method == "DELETE" and "/issues/321/labels/" in path:
                encoded = path.rsplit("/", 1)[-1]
                from urllib.parse import unquote

                label = unquote(encoded)
                if label == "symphony:ready" and fail_ready_delete_once:
                    fail_ready_delete_once = False
                    raise core.HandoffError("simulated transient mutation failure", code=73, status="GitHubNetworkFailed")
                labels.discard(label)
                return [{"name": item} for item in sorted(labels)]
            raise AssertionError(f"unexpected API call: {method} {path} {body}")

        def fake_remove_dispatch(api_root: str, token: str, owner: str, repo: str, issue_number: int) -> bool:
            result = fake_api(
                api_root,
                token,
                "DELETE",
                f"/repos/{owner}/{repo}/issues/{issue_number}/labels/symphony%3Aready",
                allow_not_found=True,
            )
            return result is not None

        core.issue_labels = fake_issue_labels
        core.api_request = fake_api
        core.remove_dispatch_lease = fake_remove_dispatch

        run_id = "diagnostic-pass"
        write_summary(workspace, run_id)
        payload = {
            "issueNumber": 321,
            "completionMode": "report-only",
            "reportBody": "Diagnostic baseline completed. No repository changes are required.",
            "validationRunIds": [run_id],
        }

        result = report.complete_report(
            payload=payload,
            workspace=workspace,
            workspace_root=workspace_root,
            state_root=state_root,
            owner="Shashakar",
            repo="RPG-Kingdom",
            api_root="https://api.invalid",
            token="token",
        )
        assert result["lifecycle"] == report.REPORT_COMPLETE_LABEL
        assert result["reportCommentCreated"] is True
        assert report.REPORT_COMPLETE_LABEL in labels
        assert "symphony:ready" not in labels
        assert len(comments) == 1
        receipt = report.read_receipt(report.receipt_path(state_root, 321))
        assert receipt and receipt["state"] == "completed"
        assert receipt["commentId"] == comments[0]["id"]
        assert receipt["attemptBoundaryNs"] is None

        # Duplicate/restarted completion is idempotent: same evidence reuses the durable comment.
        result2 = report.complete_report(
            payload=payload,
            workspace=workspace,
            workspace_root=workspace_root,
            state_root=state_root,
            owner="Shashakar",
            repo="RPG-Kingdom",
            api_root="https://api.invalid",
            token="token",
        )
        assert result2["reportCommentCreated"] is False
        assert result2["reportCommentId"] == result["reportCommentId"]
        assert len(comments) == 1

        # No-code completion is explicit. A normal implementation issue cannot use this escape hatch.
        labels.discard(report.REPORT_ONLY_LABEL)
        expect_error(
            "ReportOnlyNotAllowed",
            lambda: report.complete_report(
                payload=payload,
                workspace=workspace,
                workspace_root=workspace_root,
                state_root=state_root,
                owner="Shashakar",
                repo="RPG-Kingdom",
                api_root="https://api.invalid",
                token="token",
            ),
        )
        labels.add(report.REPORT_ONLY_LABEL)

        # Unity-required report tasks cannot omit fresh passing evidence.
        expect_error(
            "ValidationEvidenceMissing",
            lambda: report.complete_report(
                payload={**payload, "validationRunIds": []},
                workspace=workspace,
                workspace_root=workspace_root,
                state_root=state_root,
                owner="Shashakar",
                repo="RPG-Kingdom",
                api_root="https://api.invalid",
                token="token",
            ),
        )

        # Dirty/source-changing report work must take the normal implementation PR path.
        (workspace / "game.txt").write_text("changed\n", encoding="utf-8")
        expect_error("ReportWorkspaceDirty", lambda: report.validate_report_workspace(workspace))
        git(workspace, "checkout", "--", "game.txt")

        # A prior-attempt marker makes historical Unity evidence stale for a reviewed continuation.
        marker = workspace / core.ATTEMPT_MARKER
        summary = workspace / "Logs" / "SymphonyUnity" / run_id / "summary.json"
        marker.write_text("prior attempt\n", encoding="utf-8")
        marker_ns = max(marker.stat().st_mtime_ns, summary.stat().st_mtime_ns + 1_000_000)
        os.utime(marker, ns=(marker_ns, marker_ns))
        expect_error(
            "ValidationEvidenceStale",
            lambda: report.complete_report(
                payload=payload,
                workspace=workspace,
                workspace_root=workspace_root,
                state_root=state_root,
                owner="Shashakar",
                repo="RPG-Kingdom",
                api_root="https://api.invalid",
                token="token",
            ),
        )
        fresh = write_summary(workspace, "diagnostic-fresh")
        os.utime(fresh, ns=(marker_ns + 1_000_000, marker_ns + 1_000_000))
        fresh_payload = {**payload, "validationRunIds": ["diagnostic-fresh"]}

        # Simulate failure after durable evidence creation but before ready removal. The trusted
        # host receipt remains in verified state and a retry reconciles without duplicating comments.
        report.receipt_path(state_root, 321).unlink(missing_ok=True)
        labels.clear()
        labels.update({report.REPORT_ONLY_LABEL, "validation:unity-required", "symphony:ready"})
        comments.clear()
        fail_ready_delete_once = True
        expect_error(
            "GitHubNetworkFailed",
            lambda: report.complete_report(
                payload=fresh_payload,
                workspace=workspace,
                workspace_root=workspace_root,
                state_root=state_root,
                owner="Shashakar",
                repo="RPG-Kingdom",
                api_root="https://api.invalid",
                token="token",
            ),
        )
        partial = report.read_receipt(report.receipt_path(state_root, 321))
        assert partial and partial["state"] == "verified"
        assert "symphony:ready" in labels
        comment_count = len(comments)

        recovered = report.complete_report(
            payload=fresh_payload,
            workspace=workspace,
            workspace_root=workspace_root,
            state_root=state_root,
            owner="Shashakar",
            repo="RPG-Kingdom",
            api_root="https://api.invalid",
            token="token",
        )
        assert recovered["reportCommentCreated"] is False
        assert len(comments) == comment_count
        assert report.REPORT_COMPLETE_LABEL in labels and "symphony:ready" not in labels
        final_receipt = report.read_receipt(report.receipt_path(state_root, 321))
        assert final_receipt and final_receipt["state"] == "completed"

    print("report-complete-host-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
