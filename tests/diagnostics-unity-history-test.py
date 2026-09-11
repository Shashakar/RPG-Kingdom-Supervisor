#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rpgk_diagnostics", ROOT / "scripts" / "diagnostics.py")
assert SPEC is not None and SPEC.loader is not None
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class UnityHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace_root = self.root / "workspaces"
        self.state_root = self.root / "state"
        self.workspace = self.workspace_root / "GH-98"
        self.broker = self.workspace / "Logs" / "SymphonyUnity" / ".broker"
        (self.broker / "acks").mkdir(parents=True)
        (self.broker / "responses").mkdir(parents=True)
        os.environ["RPGK_WORKSPACE_ROOT"] = str(self.workspace_root)
        os.environ["RPGK_SUPERVISOR_STATE_ROOT"] = str(self.state_root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_run(
        self,
        request_id: str,
        *,
        status: str,
        exit_code: int,
        artifact_name: str | None = None,
        summary: dict | None = None,
        stderr: str = "",
        editor_log: str | None = None,
        results_xml: str | None = None,
    ) -> None:
        write_json(
            self.broker / "acks" / f"{request_id}.json",
            {
                "requestId": request_id,
                "acceptedAt": "2026-09-11T06:20:00Z",
                "protocolVersion": 1,
            },
        )
        stdout = ""
        if artifact_name:
            artifact = self.workspace / "Logs" / "SymphonyUnity" / artifact_name
            artifact.mkdir(parents=True)
            stdout = f"RPG Kingdom Unity runner: artifacts -> {artifact}\n"
            if editor_log is not None:
                (artifact / "Editor.log").write_text(editor_log, encoding="utf-8")
            if results_xml is not None:
                (artifact / "results.xml").write_text(results_xml, encoding="utf-8")
            if summary is not None:
                write_json(artifact / "summary.json", summary)
        write_json(
            self.broker / "responses" / f"{request_id}.json",
            {
                "requestId": request_id,
                "operation": "playmode",
                "status": status,
                "exitCode": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "summary": summary,
                "completedAt": "2026-09-11T06:21:30Z",
            },
        )

    def test_successful_run_is_preserved(self) -> None:
        self.add_run(
            "GH-98-playmode-pass",
            status="completed",
            exit_code=0,
            artifact_name="pass-run",
            summary={"runId": "pass-run", "result": "Passed", "total": 1, "passed": 1, "failed": 0},
            results_xml="<test-run result='Passed'></test-run>",
        )
        run = DIAG.unity_run_history("GH-98")[0]
        self.assertEqual("passed", run["status"])
        self.assertIsNone(run["diagnosis"])
        self.assertEqual(90.0, run["durationSeconds"])

    def test_compile_failure_surfaces_exact_compiler_error(self) -> None:
        self.add_run(
            "GH-98-playmode-compile",
            status="failed",
            exit_code=87,
            artifact_name="compile-run",
            stderr="Unity exited with code 1 without producing test results.",
            editor_log=(
                "-testFilter\n"
                "RPGKingdom.Tests.Example.FocusedTest\n"
                "Assets\\RPGKingdom\\Tests\\Example.cs(54,50): error CS1061: "
                "'MeleeAttackTickResult' does not contain a definition for 'SuccessfulCommitCount'\n"
            ),
        )
        run = DIAG.unity_run_history("GH-98")[0]
        diagnosis = run["diagnosis"]
        self.assertEqual("compile", diagnosis["category"])
        self.assertIn("CS1061", diagnosis["message"])
        self.assertIn("Example.cs:54:50", diagnosis["message"])
        self.assertFalse(diagnosis["testsStarted"])
        self.assertEqual("RPGKingdom.Tests.Example.FocusedTest", run["testFilter"])

    def test_test_failure_reads_results_xml(self) -> None:
        self.add_run(
            "GH-98-playmode-test-failure",
            status="completed",
            exit_code=0,
            artifact_name="failed-test-run",
            summary={"runId": "failed-test-run", "result": "Failed", "total": 1, "passed": 0, "failed": 1},
            results_xml=(
                "<test-run result='Failed'><test-suite><test-case fullname='RPGK.Example' result='Failed'>"
                "<failure><message>expected true but was false</message></failure>"
                "</test-case></test-suite></test-run>"
            ),
        )
        diagnosis = DIAG.unity_run_history("GH-98")[0]["diagnosis"]
        self.assertEqual("test", diagnosis["category"])
        self.assertEqual("RPGK.Example", diagnosis["failedTests"][0]["name"])
        self.assertIn("expected true", diagnosis["message"])

    def test_host_failure_is_distinguished_from_code_failure(self) -> None:
        self.add_run(
            "GH-98-playmode-host-failure",
            status="failed",
            exit_code=82,
            stderr="RPG Kingdom Unity broker: unity-editor belongs to 'GH-97', not 'GH-98'",
        )
        diagnosis = DIAG.unity_run_history("GH-98")[0]["diagnosis"]
        self.assertEqual("infrastructure", diagnosis["category"])
        self.assertTrue(diagnosis["retryable"])
        self.assertIn("unity-editor belongs", diagnosis["message"])


if __name__ == "__main__":
    unittest.main()
