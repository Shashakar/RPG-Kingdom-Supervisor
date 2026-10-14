#!/usr/bin/env python3
"""Host worker lease ID validation regression."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unity_host_broker", root / "scripts/unity-host-broker.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    workspaces = base / "workspaces"
    workspace = workspaces / "GH-264"
    requests = workspace / "Logs/SymphonyUnity/.broker/requests"
    requests.mkdir(parents=True)
    state = base / "state"
    lock = state / "locks/unity-editor.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("GH-264")
    (lock / "workspace").write_text(str(workspace))
    request = requests / "GH-264-editmode-test.json"
    envelope = {
        "protocolVersion": 1, "requestId": request.stem,
        "operation": "editmode", "testFilter": "", "clientPid": 123,
        "expectedWorkerRunId": "GH-264-implementation-20261014T041949Z-abc123"
    }
    request.write_text(json.dumps(envelope))
    parsed = module.prepare_request(request, workspaces, state)
    assert isinstance(parsed, module.RequestSpec), parsed
    assert parsed.expected_worker_run_id == envelope["expectedWorkerRunId"]
    envelope["expectedWorkerRunId"] = "other-worker"
    request.write_text(json.dumps(envelope))
    rejected = module.prepare_request(request, workspaces, state)
    assert isinstance(rejected, dict) and rejected.get("exitCode") == 64, rejected
    source = (root / "scripts/unity-host-broker.py").read_text()
    assert "WorkerLeaseMismatch" in source
    assert "worker_run_id != prepared.expected_worker_run_id" in source
    assert 'write_response(request_path, rejected)' in source

print("unity-lease-id-regression-test: PASS")
