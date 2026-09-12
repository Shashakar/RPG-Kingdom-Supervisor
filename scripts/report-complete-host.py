#!/usr/bin/env python3
"""Host-owned report-only completion path for RPG Kingdom Symphony work."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

CORE_PATH = Path(__file__).with_name("git-handoff-host.py")
SPEC = importlib.util.spec_from_file_location("rpgk_git_handoff_host", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load git-handoff-host.py")
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)

REPORT_ONLY_LABEL = "completion:report-only"
REPORT_COMPLETE_LABEL = "symphony:report-complete"
REPORT_RECEIPT_VERSION = 1
REPORT_MAX_BYTES = 50_000
CONFLICTING_LIFECYCLE_LABELS = (
    "symphony:halted",
    "symphony:agent-review",
    "symphony:rework",
    "symphony:human-review",
    "symphony:human-attention",
    "repair-route:luna",
    "repair-route:terra",
    "repair-route:sol",
    "repair-route:astra",
)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def receipt_path(state_root: Path, issue_number: int) -> Path:
    return state_root / "report-completions" / f"GH-{issue_number}.json"


def current_attempt_boundary_ns(workspace: Path) -> int | None:
    marker = workspace / core.ATTEMPT_MARKER
    return marker.stat().st_mtime_ns if marker.is_file() else None


def validate_report_body(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise core.HandoffError(
            "report-only completion requires a non-empty report body",
            code=64,
            status="ReportEvidenceMissing",
        )
    report = value.strip()
    if len(report.encode("utf-8")) > REPORT_MAX_BYTES:
        raise core.HandoffError(
            f"report body exceeds the {REPORT_MAX_BYTES}-byte host limit",
            code=64,
            status="ReportEvidenceInvalid",
        )
    return report


def validate_report_workspace(workspace: Path) -> str:
    core.run_git(workspace, "fetch", "--prune", "origin", timeout=180)
    status = core.run_git(workspace, "status", "--porcelain", "--untracked-files=all").stdout.strip()
    if status:
        raise core.HandoffError(
            "report-only completion requires a clean source workspace; source/test changes must use the normal PR handoff path",
            code=75,
            status="ReportWorkspaceDirty",
            details={"gitStatus": status.splitlines()[:50]},
        )
    head = core.run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    origin_main = core.run_git(workspace, "rev-parse", "origin/main").stdout.strip()
    if head != origin_main:
        raise core.HandoffError(
            "report-only completion requires HEAD to match origin/main; committed implementation work must use the normal PR handoff path",
            code=75,
            status="ReportWorkspaceDirty",
            details={"head": head, "originMain": origin_main},
        )
    return head


def report_digest(issue_number: int, report: str, run_ids: list[str], head: str) -> str:
    canonical = json.dumps(
        {"issue": issue_number, "report": report, "validationRunIds": run_ids, "head": head},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def comment_marker(issue_number: int, digest: str) -> str:
    return f"<!-- rpgk-symphony-report-complete:GH-{issue_number}:{digest} -->"


def render_report_comment(report: str, evidence: list[dict[str, Any]], marker: str) -> str:
    lines = [report.rstrip(), "", "---", "", "**Supervisor report-only completion evidence**"]
    if evidence:
        for item in evidence:
            lines.append(
                f"- Unity `{item.get('runId')}` — {item.get('platform') or '-'} — "
                f"{item.get('total')} tests — filter `{item.get('filter') or '(all)'}`"
            )
    else:
        lines.append("- Unity validation was not required/supplied for this report-only task.")
    lines.extend(["", marker])
    return "\n".join(lines)


def read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def find_existing_report_comment(
    api_root: str,
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    marker: str,
    receipt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if isinstance(receipt, dict):
        comment_id = receipt.get("commentId")
        if isinstance(comment_id, int) and receipt.get("marker") == marker:
            comment = core.api_request(
                api_root,
                token,
                "GET",
                f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
                allow_not_found=True,
            )
            if isinstance(comment, dict) and marker in str(comment.get("body") or ""):
                return comment

    for page in range(1, 6):
        comments = core.api_request(
            api_root,
            token,
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments?per_page=100&page={page}",
        )
        if not isinstance(comments, list):
            raise core.HandoffError("GitHub issue comments response was not a list", code=73, status="GitHubApiFailed")
        for item in comments:
            if isinstance(item, dict) and marker in str(item.get("body") or ""):
                return item
        if len(comments) < 100:
            break
    return None


def ensure_report_comment(
    api_root: str,
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
    marker: str,
    receipt: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    existing = find_existing_report_comment(api_root, token, owner, repo, issue_number, marker, receipt)
    if existing is not None:
        return existing, False
    created = core.api_request(
        api_root,
        token,
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        {"body": body},
    )
    if not isinstance(created, dict) or not isinstance(created.get("id"), int):
        raise core.HandoffError("GitHub did not return a durable report comment", code=73, status="GitHubApiFailed")
    return created, True


def remove_label_if_present(api_root: str, token: str, owner: str, repo: str, issue_number: int, label: str) -> None:
    from urllib.parse import quote

    core.api_request(
        api_root,
        token,
        "DELETE",
        f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{quote(label, safe='')}",
        allow_not_found=True,
    )


def apply_report_lifecycle(api_root: str, token: str, owner: str, repo: str, issue_number: int) -> bool:
    core.api_request(
        api_root,
        token,
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
        {"labels": [REPORT_COMPLETE_LABEL]},
    )
    for label in CONFLICTING_LIFECYCLE_LABELS:
        remove_label_if_present(api_root, token, owner, repo, issue_number, label)
    lease_removed = core.remove_dispatch_lease(api_root, token, owner, repo, issue_number)
    labels = core.issue_labels(api_root, token, owner, repo, issue_number)
    if REPORT_COMPLETE_LABEL not in labels or "symphony:ready" in labels:
        raise core.HandoffError(
            "report evidence was verified, but GitHub lifecycle reconciliation did not reach report-complete with the dispatch lease removed",
            code=73,
            status="ReportLifecycleIncomplete",
            details={"labels": sorted(labels)},
        )
    return lease_removed


def complete_report(
    *,
    payload: dict[str, Any],
    workspace: Path,
    workspace_root: Path,
    state_root: Path,
    owner: str,
    repo: str,
    api_root: str,
    token: str,
) -> dict[str, Any]:
    issue_number = core.validate_workspace(workspace, workspace_root, f"{owner}/{repo}")
    if int(payload.get("issueNumber", issue_number)) != issue_number:
        raise core.HandoffError("request issue number does not match GH workspace", code=81, status="InvalidWorkspace")

    labels = core.issue_labels(api_root, token, owner, repo, issue_number)
    if REPORT_ONLY_LABEL not in labels:
        raise core.HandoffError(
            f"report-only completion requires the explicit '{REPORT_ONLY_LABEL}' issue label",
            code=78,
            status="ReportOnlyNotAllowed",
        )

    report = validate_report_body(payload.get("reportBody"))
    run_ids_raw = payload.get("validationRunIds", [])
    if not isinstance(run_ids_raw, list) or not all(isinstance(item, str) for item in run_ids_raw):
        raise core.HandoffError("validationRunIds must be an array of strings", code=64, status="InvalidRequest")
    run_ids = list(dict.fromkeys(run_ids_raw))
    boundary_ns = current_attempt_boundary_ns(workspace)
    evidence = core.validate_unity_evidence(
        workspace,
        run_ids,
        "validation:unity-required" in labels,
        newer_than_ns=boundary_ns,
    )
    head = validate_report_workspace(workspace)

    digest = report_digest(issue_number, report, run_ids, head)
    marker = comment_marker(issue_number, digest)
    path = receipt_path(state_root, issue_number)
    existing_receipt = read_receipt(path)
    if (
        REPORT_COMPLETE_LABEL in labels
        and isinstance(existing_receipt, dict)
        and existing_receipt.get("digest") not in (None, digest)
    ):
        raise core.HandoffError(
            "this issue already has a different host-verified report-only completion receipt",
            code=78,
            status="ReportAlreadyComplete",
            details={"receipt": str(path)},
        )

    comment_body = render_report_comment(report, evidence, marker)
    comment, comment_created = ensure_report_comment(
        api_root,
        token,
        owner,
        repo,
        issue_number,
        comment_body,
        marker,
        existing_receipt if isinstance(existing_receipt, dict) and existing_receipt.get("digest") == digest else None,
    )

    verified_receipt = {
        "protocolVersion": REPORT_RECEIPT_VERSION,
        "state": "verified",
        "issueNumber": issue_number,
        "digest": digest,
        "marker": marker,
        "commentId": int(comment["id"]),
        "commentUrl": str(comment.get("html_url") or ""),
        "head": head,
        "attemptBoundaryNs": boundary_ns,
        "validationRunIds": run_ids,
        "validation": evidence,
        "verifiedAt": core.utc_now() if hasattr(core, "utc_now") else None,
    }
    atomic_json(path, verified_receipt)

    lease_removed = apply_report_lifecycle(api_root, token, owner, repo, issue_number)
    verified_receipt["state"] = "completed"
    verified_receipt["completedAt"] = core.utc_now() if hasattr(core, "utc_now") else None
    atomic_json(path, verified_receipt)

    return {
        "status": "completed",
        "exitCode": 0,
        "operation": "report-complete",
        "issue": issue_number,
        "head": head,
        "reportCommentId": int(comment["id"]),
        "reportCommentUrl": str(comment.get("html_url") or ""),
        "reportCommentCreated": comment_created,
        "reportDigest": digest,
        "receiptPath": str(path),
        "leaseRemoved": lease_removed,
        "lifecycle": REPORT_COMPLETE_LABEL,
        "validation": evidence,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="RPG Kingdom host report-only completion runner")
    parser.add_argument("--request", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).resolve()
    workspace = Path(args.workspace).resolve()
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    operation = "report-complete"

    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise core.HandoffError("request must be a JSON object", code=64, status="InvalidRequest")
        if payload.get("completionMode") != "report-only":
            raise core.HandoffError("report completion request is missing completionMode=report-only", code=64, status="InvalidRequest")

        owner = os.environ.get("RPGK_REPO_OWNER", "Shashakar")
        repo = os.environ.get("RPGK_REPO_NAME", "RPG-Kingdom")
        api_root = os.environ.get("RPGK_GITHUB_API_ROOT", "https://api.github.com")
        token = os.environ.get("SYMPHONY_GITHUB_TOKEN", "")
        result = complete_report(
            payload=payload,
            workspace=workspace,
            workspace_root=workspace_root,
            state_root=state_root,
            owner=owner,
            repo=repo,
            api_root=api_root,
            token=token,
        )
        core.emit(result)
        return 0
    except core.HandoffError as exc:
        core.emit(
            {
                "status": exc.status,
                "exitCode": exc.code,
                "operation": operation,
                "message": str(exc),
                "details": exc.details,
            }
        )
        return exc.code
    except Exception as exc:
        core.emit(
            {
                "status": "failed",
                "exitCode": 70,
                "operation": operation,
                "message": f"unexpected report-only completion failure: {exc}",
            }
        )
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
