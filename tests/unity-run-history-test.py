#!/usr/bin/env python3
"""Regression coverage for Supervisor Unity run history/diagnostics."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import unity_run_history as history  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def request_fixture(root: Path, request_id: str, *, operation: str = "playmode", test_filter: str = "Focused.Tests") -> tuple[Path, Path]:
    workspace = root / "workspaces" / "GH-98"
    broker = workspace / "Logs" / "SymphonyUnity" / ".broker"
    write_json(
        broker / "history" / "requests" / f"{request_id}.json",
        {
            "protocolVersion": 1,
            "requestId": request_id,
            "operation": operation,
            "testFilter": test_filter,
            "requestedAt": "2026-09-11T07:00:00Z",
        },
    )
    write_json(broker / "acks" / f"{request_id}.json", {"acceptedAt": "2026-09-11T07:00:01Z"})
    return workspace, broker


def response(broker: Path, request_id: str, **values: object) -> None:
    payload = {
        "protocolVersion": 1,
        "requestId": request_id,
        "operation": "playmode",
        "status": "completed",
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "summary": None,
        "completedAt": "2026-09-11T07:00:06Z",
    }
    payload.update(values)
    write_json(broker / "responses" / f"{request_id}.json", payload)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-unity-history-") as temp:
        root = Path(temp)
        workspace_root = root / "workspaces"
        state_root = root / "state"

        workspace, broker = request_fixture(root, "pass")
        artifact = workspace / "Logs" / "SymphonyUnity" / "run-pass"
        artifact.mkdir(parents=True)
        summary = {
            "result": "Passed", "total": 2, "passed": 2, "failed": 0, "skipped": 0,
            "runId": "run-pass", "artifactPath": str(artifact), "testFilter": "Focused.Tests",
        }
        write_json(artifact / "summary.json", summary)
        (artifact / "results.xml").write_text(
            '<test-run total="2" passed="2" failed="0" skipped="0" result="Passed"/>', encoding="utf-8"
        )
        response(broker, "pass", summary=summary, stdout=json.dumps(summary))

        workspace, broker = request_fixture(root, "test-failure")
        artifact = workspace / "Logs" / "SymphonyUnity" / "run-test-failure"
        artifact.mkdir(parents=True)
        summary = {
            "result": "Failed", "total": 2, "passed": 1, "failed": 1, "skipped": 0,
            "runId": "run-test-failure", "artifactPath": str(artifact), "testFilter": "Focused.Tests",
        }
        write_json(artifact / "summary.json", summary)
        (artifact / "results.xml").write_text(
            '<test-run total="2" passed="1" failed="1" skipped="0" result="Failed">'
            '<test-suite><test-case name="DoesThing" fullname="Tests.DoesThing" result="Failed">'
            '<failure><message>Expected: 1 But was: 0</message><stack-trace>at Tests.DoesThing()</stack-trace></failure>'
            '</test-case></test-suite></test-run>', encoding="utf-8"
        )
        response(broker, "test-failure", status="failed", exitCode=1, summary=summary, stdout=json.dumps(summary))

        workspace, broker = request_fixture(root, "compile-failure")
        artifact = workspace / "Logs" / "SymphonyUnity" / "run-compile-failure"
        artifact.mkdir(parents=True)
        (artifact / "Editor.log").write_text(
            "Assets/Tests/CharacterSandboxHumanoidAnimationPlayModeTests.cs(54,17): error CS1061: "
            "'MeleeAttackTickResult' does not contain a definition for 'SuccessfulCommitCount'\n", encoding="utf-8"
        )
        response(
            broker, "compile-failure", status="failed", exitCode=87,
            stdout=f"RPG Kingdom Unity runner: artifacts -> {artifact}\n",
            stderr=f"Unity exited with code 1 without producing test results. Inspect '{artifact}/Editor.log'.\n",
        )

        _, broker = request_fixture(root, "host-timeout")
        response(
            broker, "host-timeout", status="TimedOut", exitCode=124,
            stderr="RPG Kingdom Unity broker: host operation timed out after 1800s",
        )

        write_json(
            state_root / "unity-broker" / "status.json",
            {
                "state": "running",
                "activeRequest": {
                    "requestId": "active", "issue": "GH-98", "workspace": str(workspace),
                    "operation": "editmode", "testFilter": "Active.Test",
                    "startedAt": "2026-09-11T07:01:00Z", "elapsedSeconds": 13,
                },
            },
        )

        runs = {
            item["requestId"]: item
            for item in history.collect_runs(
                workspace_root=workspace_root, state_root=state_root, retention_days=3650, limit=100
            )
        }
        assert runs["pass"]["finalStatus"] == "passed"
        assert runs["pass"]["durationSeconds"] == 5.0

        failed = runs["test-failure"]
        assert failed["diagnosis"]["category"] == "test_failure"
        assert failed["failedTests"][0]["name"] == "Tests.DoesThing"
        assert "Expected: 1" in failed["failedTests"][0]["message"]

        compiled = runs["compile-failure"]
        diagnosis = compiled["diagnosis"]
        assert diagnosis["category"] == "compile"
        assert diagnosis["sourcePath"].endswith("CharacterSandboxHumanoidAnimationPlayModeTests.cs")
        assert (diagnosis["line"], diagnosis["column"], diagnosis["code"]) == (54, 17, "CS1061")
        assert diagnosis["testsStarted"] is False
        assert compiled["paths"]["resultsXml"] is None
        assert compiled["paths"]["editorLog"].endswith("Editor.log")

        timeout = runs["host-timeout"]
        assert timeout["diagnosis"]["category"] == "timeout"
        assert timeout["diagnosis"]["retryable"] is True
        assert timeout["finalStatus"] == "timed_out"

        active = runs["active"]
        assert active["finalStatus"] == "running"
        assert active["durationSeconds"] == 13

        filtered = history.collect_runs(
            workspace_root=workspace_root,
            state_root=state_root,
            issue="GH-98",
            operation="playmode",
            status="failed",
            retention_days=3650,
        )
        assert {item["requestId"] for item in filtered} == {"test-failure", "compile-failure"}

        dashboard_path = SCRIPTS / "supervisor_dashboard.py"
        spec = importlib.util.spec_from_file_location("supervisor_dashboard", dashboard_path)
        assert spec and spec.loader
        dashboard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dashboard)
        assert dashboard.normalize_issue("98") == "GH-98"
        assert dashboard.normalize_issue("gh-98") == "GH-98"
        assert "/api/unity/runs" in dashboard.PAGE
        assert "/api/unity/run/" in dashboard.PAGE

    print("unity-run-history-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
