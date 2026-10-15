#!/usr/bin/env python3
"""Regression: optional live-worker claim must be parsed without breaking legacy requests."""
import importlib.util
import json
from pathlib import Path
import tempfile

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("unity_host_broker",root/"scripts"/"unity-host-broker.py")
m=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=m
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory() as tmp:
    base=Path(tmp)
    workspace_root=base/"workspaces"
    ws=workspace_root/"GH-264"
    req_dir=ws/"Logs"/"SymphonyUnity"/".broker"/"requests"
    req_dir.mkdir(parents=True)
    state=base/"state"
    def parse(data):
        p=req_dir/"test.json"
        p.write_text(json.dumps({"protocolVersion":1,"requestId":"test",
            "operation":"health","testFilter":"",**data}))
        return m.prepare_request(p,workspace_root.resolve(),state)
    legacy=parse({})
    assert isinstance(legacy,m.RequestSpec)
    assert legacy.requires_worker_lease is False
    matched=parse({"requiresWorkerLease":True,"expectedWorkerRunId":"GH-264-attempt-7"})
    assert isinstance(matched,m.RequestSpec),matched
    assert matched.requires_worker_lease and matched.expected_worker_run_id=="GH-264-attempt-7"
    assert m.worker_lease_matches(matched,"GH-264-attempt-7",12345)
    assert not m.worker_lease_matches(matched,"GH-264-attempt-8",12345)
    assert not m.worker_lease_matches(matched,None,None)
    assert not m.worker_lease_matches(matched,"GH-264-attempt-7",None)
    assert not m.worker_lease_matches(matched,"GH-264-attempt-7",0)
    assert m.worker_lease_matches(legacy,None,None)
    for invalid in [{"requiresWorkerLease":True,"expectedWorkerRunId":""},
                    {"requiresWorkerLease":"yes"},
                    {"expectedWorkerRunId":"unbound"},
                    {"requiresWorkerLease":True,"expectedWorkerRunId":123}]:
        rejected=parse(invalid)
        assert isinstance(rejected,dict) and rejected["exitCode"]==64,rejected
print("unity-worker-lease-contract-test: PASS")
