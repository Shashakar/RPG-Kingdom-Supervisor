#!/usr/bin/env python3
"""Characterize actual v1 validation-broker request parsing and preflight behavior.

Not the proposed v2 enforcement test: the current main intentionally ignores
worker claims, and this test documents that compatibility gap.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unity_host_broker", root / "scripts/unity-host-broker.py")
assert spec and spec.loader
broker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = broker
spec.loader.exec_module(broker)

with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    workspaces = base / "workspaces"
    workspace = workspaces / "GH-321"
    requests = workspace / "Logs" / "SymphonyUnity" / ".broker" / "requests"
    requests.mkdir(parents=True)
    state = base / "state"
    path = requests / "GH-321-health-1.json"

    def prepare(operation="health", **fields):
        payload = {"protocolVersion": 1, "requestId": path.stem, "operation": operation, **fields}
        path.write_text(json.dumps(payload))
        return broker.prepare_request(path, workspaces, state)

    # Host health/preflight has no worker lease and requires no editor lock.
    result = prepare()
    assert isinstance(result, broker.RequestSpec), result
    assert result.worker_run_id is None
    assert result.client_pid is None

    # Current v1 does not bind submitted worker identity at admission.
    # This must be changed by the V2 migration with client compatibility tests.
    result = prepare(expectedWorkerRunId="GH-321-implementation-wrong", clientPid=123)
    assert isinstance(result, broker.RequestSpec), result
    assert result.client_pid == 123

    invalid = prepare(protocolVersion=2)
    assert isinstance(invalid, dict) and invalid["exitCode"] == 64, invalid
    invalid = prepare(operation="shell")
    assert isinstance(invalid, dict) and invalid["exitCode"] == 64, invalid
    invalid = prepare(clientPid=-1)
    assert isinstance(invalid, dict) and invalid["exitCode"] == 64, invalid

    # Non-health operation must hold the task-specific Unity resource lock.
    rejected = prepare(operation="editmode")
    assert isinstance(rejected, dict) and rejected["exitCode"] == 82, rejected
    lock = state / "locks" / "unity-editor.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("GH-321")
    (lock / "workspace").write_text(str(workspace))
    admitted = prepare(operation="editmode")
    assert isinstance(admitted, broker.RequestSpec), admitted

    # A lock for another issue does not authorize the request.
    (lock / "owner").write_text("GH-322")
    rejected = prepare(operation="editmode")
    assert isinstance(rejected, dict) and rejected["exitCode"] == 82, rejected

print("unity-v2-request-baseline-test: PASS")
