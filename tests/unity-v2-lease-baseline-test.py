#!/usr/bin/env python3
"""Behavioral baseline for existing Supervisor durable Unity lease resolution.

This is a characterization test, not the final V2 request authorization contract.
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
    state = base / "state"
    leases = state / "workers" / "active"
    leases.mkdir(parents=True)
    workspace = base / "GH-321"
    workspace.mkdir()
    other = base / "GH-322"
    other.mkdir()
    broker.process_is_alive = lambda pid: pid == 12345

    def lease(name, ws, run, pid):
        path = leases / (name + ".json")
        path.write_text(json.dumps({"workspace": str(ws), "runId": run, "pid": pid}))
        return path

    assert broker.resolve_worker_lease(state, workspace) == (None, None), "missing lease"
    one = lease("one", workspace, "GH-321-implementation-test", 12345)
    assert broker.resolve_worker_lease(state, workspace) == ("GH-321-implementation-test", 12345), "unique live lease"
    extraneous = lease("other", other, "GH-322-implementation-test", 12345)
    assert broker.resolve_worker_lease(state, workspace) == ("GH-321-implementation-test", 12345), "foreign workspace"
    extraneous.unlink()
    dead = lease("dead", workspace, "GH-321-implementation-dead", 999999)
    assert broker.resolve_worker_lease(state, workspace) == ("GH-321-implementation-test", 12345), "dead lease ignored"
    dead.unlink()
    duplicate = lease("duplicate", workspace, "GH-321-implementation-test", 12345)
    assert broker.resolve_worker_lease(state, workspace) == (None, None), "duplicate is ambiguous"
    duplicate.unlink()
    one.write_text("{invalid")
    assert broker.resolve_worker_lease(state, workspace) == (None, None), "malformed lease ignored"
    one.unlink()
    lease("invalid-pid", workspace, "GH-321-implementation-test", "bad")
    assert broker.resolve_worker_lease(state, workspace) == (None, None), "invalid pid ignored"

print("unity-v2-lease-baseline-test: PASS")
