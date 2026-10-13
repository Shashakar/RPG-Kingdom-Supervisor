#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile

path = Path(__file__).resolve().parents[1] / "scripts/unity-request-timing.py"
spec = importlib.util.spec_from_file_location("unity_request_timing", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as temp:
    workspace = Path(temp) / "GH-264"
    root = workspace / "Logs/SymphonyUnity/.broker"
    request_id = "GH-264-inspect-test"
    for part in ("history/requests", "acks", "progress", "responses"):
        (root / part).mkdir(parents=True)
    def write(part, obj):
        (root / part / (request_id + ".json")).write_text(json.dumps(obj))
    write("history/requests", {"requestId": request_id, "operation": "inspect", "requestedAt": "2026-10-13T07:20:00Z"})
    write("acks", {"acceptedAt": "2026-10-13T07:20:02Z"})
    write("progress", {"observedAt": "2026-10-13T07:28:04Z", "phase": "capture_running", "sequence": 20})
    write("responses", {"completedAt": "2026-10-13T07:28:07Z", "status": "ClientAbandoned", "exitCode": 93,
                        "details": {"reason": "worker_process_exited", "recoveryResult": "host_runner_exited"}})
    report = module.diagnose(workspace, request_id)
    assert report["timingsSeconds"] == {
        "queueToAcceptance": 2.0,
        "acceptanceToLastProgress": 482.0,
        "lastProgressToTerminal": 3.0,
        "requestToTerminal": 487.0,
    }
    assert report["terminalStatus"] == "ClientAbandoned"
    assert report["ownership"]["reason"] == "worker_process_exited"
    assert report["lastProgress"]["sequence"] == 20
    try:
        module.diagnose(workspace, "GH-263-inspect-test")
    except ValueError:
        pass
    else:
        raise AssertionError("wrong-workspace request accepted")
print("unity-request-timing-test: PASS")
