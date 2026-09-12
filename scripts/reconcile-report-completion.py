#!/usr/bin/env python3
"""Reconcile a host-verified report-only completion after partial GitHub mutation."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

REPORT_PATH = Path(__file__).with_name("report-complete-host.py")
SPEC = importlib.util.spec_from_file_location("rpgk_report_complete_host", REPORT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load report-complete-host.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
core = report.core


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def parse_boundary(value: str) -> int | None:
    if value == "none":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid attempt boundary '{value}'") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile host-verified report-only completion")
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--attempt-boundary", required=True)
    args = parser.parse_args()

    state_root = Path(args.state_root).expanduser().resolve()
    workspace = Path(args.workspace).resolve()
    path = report.receipt_path(state_root, args.issue)
    receipt = report.read_receipt(path)
    if not isinstance(receipt, dict):
        return 2

    expected_boundary = parse_boundary(args.attempt_boundary)
    if receipt.get("issueNumber") != args.issue or receipt.get("attemptBoundaryNs") != expected_boundary:
        return 2
    if receipt.get("state") not in {"verified", "completed"}:
        return 2

    owner = os.environ.get("RPGK_REPO_OWNER", "Shashakar")
    repo = os.environ.get("RPGK_REPO_NAME", "RPG-Kingdom")
    api_root = os.environ.get("RPGK_GITHUB_API_ROOT", "https://api.github.com")
    token = os.environ.get("SYMPHONY_GITHUB_TOKEN", "")

    try:
        issue_number = core.validate_workspace(workspace, workspace.parent, f"{owner}/{repo}")
        if issue_number != args.issue:
            return 2
        head = core.run_git(workspace, "rev-parse", "HEAD").stdout.strip()
        if receipt.get("head") != head:
            return 2

        labels = core.issue_labels(api_root, token, owner, repo, args.issue)
        if report.REPORT_ONLY_LABEL not in labels:
            raise core.HandoffError(
                f"trusted report receipt exists but '{report.REPORT_ONLY_LABEL}' is no longer present",
                code=78,
                status="ReportOnlyNotAllowed",
            )

        marker = str(receipt.get("marker") or "")
        if not marker:
            raise core.HandoffError("trusted report receipt is missing its comment marker", code=78, status="ReportEvidenceInvalid")
        comment = report.find_existing_report_comment(
            api_root,
            token,
            owner,
            repo,
            args.issue,
            marker,
            receipt,
        )
        if comment is None:
            raise core.HandoffError(
                "trusted report receipt exists but the durable GitHub evidence comment cannot be verified",
                code=78,
                status="ReportEvidenceMissing",
            )

        report.apply_report_lifecycle(api_root, token, owner, repo, args.issue)
        receipt["state"] = "completed"
        receipt["commentId"] = int(comment["id"])
        receipt["commentUrl"] = str(comment.get("html_url") or receipt.get("commentUrl") or "")
        report.atomic_json(path, receipt)
        emit({"status": "completed", "issue": args.issue, "receiptPath": str(path), "commentId": int(comment["id"])})
        return 0
    except core.HandoffError as exc:
        emit({"status": exc.status, "message": str(exc), "details": exc.details})
        return exc.code
    except Exception as exc:
        emit({"status": "failed", "message": str(exc)})
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
