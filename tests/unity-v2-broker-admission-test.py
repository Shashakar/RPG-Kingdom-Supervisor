#!/usr/bin/env python3
"""Exercise actual Unity broker request admission against claimed worker lifetimes."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "scripts"))

def load(name, file):
    spec=importlib.util.spec_from_file_location(name,root / "scripts" / file)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

validation=load("unity_host_broker_v2", "unity-host-broker.py")
author=load("unity_author_broker_v2", "unity-author-broker.py")
claim="GH-321-implementation-test"
wrong="GH-321-implementation-wrong"

with tempfile.TemporaryDirectory() as tmp:
    base=Path(tmp)
    wsroot=base/"workspaces"
    ws=wsroot/"GH-321"
    state=base/"state"
    for component in (".broker",".author-broker"):
        (ws/"Logs"/"SymphonyUnity"/component/"requests").mkdir(parents=True)
    lock=state/"locks"/"unity-editor.lock"
    lock.mkdir(parents=True)
    (lock/"owner").write_text("GH-321")
    (lock/"workspace").write_text(str(ws))
    leases=state/"workers"/"active"
    leases.mkdir(parents=True)
    (leases/"implementation.json").write_text(json.dumps({"workspace":str(ws),"runId":claim,"pid":111}))

    vr=ws/"Logs"/"SymphonyUnity"/".broker"/"requests"/"GH-321-editmode-test.json"
    def validate(**more):
        vr.write_text(json.dumps({"protocolVersion":1,"requestId":vr.stem,"operation":"editmode",**more}))
        return validation.prepare_request(vr,wsroot,state)
    parsed=validate(expectedWorkerRunId=wrong,requiresWorkerLease=True)
    assert isinstance(parsed,validation.RequestSpec)
    with patch.object(validation,"process_is_alive",side_effect=lambda pid:pid==111):
        _,_,mismatch=validation.verify_claim(parsed.expected_worker_run_id,state,parsed.workspace,validation.process_is_alive)
        assert mismatch
        correct=validate(expectedWorkerRunId=claim,requiresWorkerLease=True)
        _,_,mismatch=validation.verify_claim(correct.expected_worker_run_id,state,correct.workspace,validation.process_is_alive)
        assert not mismatch
    invalid=validate(expectedWorkerRunId=claim,requiresWorkerLease=False)
    assert isinstance(invalid,dict) and invalid["exitCode"]==64

    ar=ws/"Logs"/"SymphonyUnity"/".author-broker"/"requests"/"GH-321-author-test.json"
    envelope={"protocolVersion":1,"requestId":ar.stem,"operation":"author",
              "authoring":{"protocolVersion":1,"tier":"existing-scene-composition",
                           "scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity",
                           "operations":[{"kind":"inspect-scene"}]}}
    def auth(**fields):
        ar.write_text(json.dumps({**envelope,**fields}))
        return author.validate_request(ar,wsroot,state)
    with patch.object(author.os,"kill",side_effect=lambda pid,sig:None if pid==111 else (_ for _ in ()).throw(ProcessLookupError())):
        _,_,rejection=auth(expectedWorkerRunId=wrong,requiresWorkerLease=True)
        assert rejection and rejection["status"]=="WorkerLeaseRejected" and rejection["exitCode"]==75,rejection
        _,_,rejection=auth(expectedWorkerRunId=claim,requiresWorkerLease=False)
        assert rejection and rejection["exitCode"]==64,rejection
        # Valid claim reaches the *next* grant gate, rather than being lease-rejected.
        _,_,rejection=auth(expectedWorkerRunId=claim,requiresWorkerLease=True)
        assert rejection and rejection["status"]!="WorkerLeaseRejected",rejection
print("unity-v2-broker-admission-test: PASS")
