#!/usr/bin/env python3
"""Shared lease contract: parsed claims, uniqueness and wrong-owner rejection."""
import sys
import json
from pathlib import Path
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unity_worker_lease import parse_claim, verify_claim, REJECT_STATUS, REJECT_EXIT

assert REJECT_STATUS == "WorkerLeaseRejected" and REJECT_EXIT == 75
assert parse_claim({}) is None
assert parse_claim({"expectedWorkerRunId":"GH-321-implementation-test"}) == "GH-321-implementation-test"
assert parse_claim({"requiresWorkerLease":True,"expectedWorkerRunId":"GH-321-implementation-test"})
for bad in [
    {"expectedWorkerRunId":"bad"},
    {"expectedWorkerRunId":7},
    {"requiresWorkerLease":True},
    {"requiresWorkerLease":False,"expectedWorkerRunId":"GH-321-implementation-test"},
    {"requiresWorkerLease":"yes"},
]:
    try: parse_claim(bad)
    except ValueError: pass
    else: raise AssertionError(f"admitted invalid claim: {bad}")

with tempfile.TemporaryDirectory() as tmp:
    state=Path(tmp)/"state"
    ws=Path(tmp)/"GH-321"
    ws.mkdir()
    leases=state/"workers"/"active"
    leases.mkdir(parents=True)
    live=lambda pid:pid == 111
    claim="GH-321-implementation-test"
    assert verify_claim(None,state,ws,live)==(None,None,False)
    assert verify_claim(claim,state,ws,live)[2] is True
    p=leases/"implementation.json"
    p.write_text(json.dumps({"workspace":str(ws),"runId":claim,"pid":111}))
    assert verify_claim(claim,state,ws,live)==(claim,111,False)
    assert verify_claim("GH-321-implementation-other",state,ws,live)[2] is True
    q=leases/"duplicate.json"
    q.write_text(p.read_text())
    assert verify_claim(claim,state,ws,live)[2] is True
    q.unlink()
    p.write_text(json.dumps({"workspace":str(ws),"runId":claim,"pid":999}))
    assert verify_claim(claim,state,ws,live)[2] is True
print("unity-v2-worker-claim-test: PASS")
