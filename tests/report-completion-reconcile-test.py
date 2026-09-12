#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "reconcile-report-completion.py"
SPEC = importlib.util.spec_from_file_location("rpgk_report_reconcile", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load reconcile-report-completion.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
report = module.report
core = report.core


def invoke(issue: int, workspace: Path, state_root: Path, boundary: str) -> int:
    previous = sys.argv
    try:
        sys.argv = [
            str(MODULE_PATH),
            "--issue",
            str(issue),
            "--workspace",
            str(workspace),
            "--state-root",
            str(state_root),
            "--attempt-boundary",
            boundary,
        ]
        return module.main()
    finally:
        sys.argv = previous


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-report-reconcile-") as temp:
        root = Path(temp)
        workspace = root / "workspaces" / "GH-123"
        workspace.mkdir(parents=True)
        state_root = root / "state"
        receipt_path = report.receipt_path(state_root, 123)
        report.atomic_json(
            receipt_path,
            {
                "protocolVersion": 1,
                "state": "verified",
                "issueNumber": 123,
                "digest": "abc",
                "marker": "<!-- marker -->",
                "commentId": 77,
                "commentUrl": "https://example.invalid/comment/77",
                "head": "deadbeef",
                "attemptBoundaryNs": None,
                "validationRunIds": [],
                "validation": [],
            },
        )

        core.validate_workspace = lambda workspace_arg, root_arg, expected_repo: 123
        core.run_git = lambda workspace_arg, *args, **kwargs: SimpleNamespace(stdout="deadbeef\n", returncode=0)
        core.issue_labels = lambda *args, **kwargs: {report.REPORT_ONLY_LABEL, "symphony:ready"}
        report.find_existing_report_comment = lambda *args, **kwargs: {
            "id": 77,
            "html_url": "https://example.invalid/comment/77",
            "body": "report\n<!-- marker -->",
        }
        lifecycle_calls: list[int] = []
        report.apply_report_lifecycle = lambda *args, **kwargs: lifecycle_calls.append(123) or True

        assert invoke(123, workspace, state_root, "none") == 0
        assert lifecycle_calls == [123]
        reconciled = report.read_receipt(receipt_path)
        assert reconciled and reconciled["state"] == "completed"

        # A receipt from an older lifetime must not be replayed after a new attempt boundary exists.
        lifecycle_calls.clear()
        assert invoke(123, workspace, state_root, "999") == 2
        assert lifecycle_calls == []

        # The host receipt is also pinned to repository HEAD; unrelated later source state cannot
        # inherit an earlier report completion.
        reconciled["attemptBoundaryNs"] = 999
        report.atomic_json(receipt_path, reconciled)
        core.run_git = lambda workspace_arg, *args, **kwargs: SimpleNamespace(stdout="cafebabe\n", returncode=0)
        assert invoke(123, workspace, state_root, "999") == 2
        assert lifecycle_calls == []

    print("report-completion-reconcile-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
