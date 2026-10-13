#!/usr/bin/env python3
"""Host-only lease authorization contracts for async Unity broker calls."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unity_host_broker", root / "scripts/unity-host-broker.py")
broker = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = broker
spec.loader.exec_module(broker)

with tempfile.TemporaryDirectory() as temp:
    state = Path(temp) / "state"
    workspace = Path(temp) / "workspaces" / "GH-321"
    workspace.mkdir(parents=True)
    leases = state / "workers" / "active"
    leases.mkdir(parents=True)
    requests = workspace / "Logs" / "SymphonyUnity" / ".broker" / "requests"
    requests.mkdir(parents=True)
    request = requests / "GH-321-inspect-expected.json"
    payload = {
        "protocolVersion": 1, "requestId": request.stem, "operation": "inspect",
        "scenePath": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
        "requiresWorkerLease": True, "expectedWorkerRunId": "GH-321-implementation-test",
    }
    def prepare():
        request.write_text(json.dumps(payload))
        result = broker.prepare_request(request, workspace.parent, state)
        assert isinstance(result, broker.RequestSpec), result
        return result
    def verify(expected=None):
        result = broker.verify_worker_ownership(prepare(), state)
        if expected is None:
            assert result is None, result
        else:
            assert result["status"] == "WorkerLeaseRejected", result
            assert result["exitCode"] == 75, result
        return result
    verify("missing")
    lease_file = leases / "implementation.json"
    lease_file.write_text(json.dumps({"workspace": str(workspace), "runId": "GH-321-implementation-wrong", "pid": os.getpid()}))
    verify("wrong-run")
    lease_file.write_text(json.dumps({"workspace": str(workspace), "runId": payload["expectedWorkerRunId"], "pid": 999999999}))
    verify("dead-pid")
    lease_file.write_text(json.dumps({"workspace": str(workspace), "runId": payload["expectedWorkerRunId"], "pid": os.getpid()}))
    verify()
    (leases / "duplicate.json").write_text(lease_file.read_text())
    verify("ambiguous")
    (leases / "duplicate.json").unlink()
    # A request attempting to claim an expected runId without the lease-required flag is invalid.
    payload["requiresWorkerLease"] = False
    request.write_text(json.dumps(payload))
    invalid = broker.prepare_request(request, workspace.parent, state)
    assert isinstance(invalid, dict) and invalid["exitCode"] == 64, invalid
print("unity-host-ownership-test: PASS")
