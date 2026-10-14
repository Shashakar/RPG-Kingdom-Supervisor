#!/usr/bin/env python3
"""Run the real validation broker event loop against a fake host runner.

No Unity installation or production workspace is required.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    base=Path(tmp)
    wsroot=base/"workspaces"
    ws=wsroot/"GH-321"
    reqdir=ws/"Logs"/"SymphonyUnity"/".broker"/"requests"
    respdir=reqdir.parent/"responses"
    reqdir.mkdir(parents=True)
    state=base/"state"
    lock=state/"locks"/"unity-editor.lock"
    lock.mkdir(parents=True)
    (lock/"owner").write_text("GH-321")
    (lock/"workspace").write_text(str(ws))
    leases=state/"workers"/"active"
    leases.mkdir(parents=True)
    host=base/"host.sh"
    marker=base/"host-called"
    host.write_text("#!/bin/sh\necho called >> "+str(marker)+"\necho '{}'\n")
    host.chmod(0o755)
    broker=subprocess.Popen([sys.executable,str(root/"scripts"/"unity-host-broker.py"),
        "--workspace-root",str(wsroot),"--state-root",str(state),"--host-runner",str(host),
        "--poll-ms","25"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while not (state/"unity-broker"/"status.json").exists():
            assert broker.poll() is None,"broker exited during startup"
            assert time.monotonic()<deadline,"broker startup timeout"
            time.sleep(.025)
        def issue(suffix, **fields):
            path=reqdir/("GH-321-"+suffix+".json")
            response=respdir/path.name
            payload={"protocolVersion":1,"requestId":path.stem,"operation":"editmode",**fields}
            path.write_text(json.dumps(payload))
            until=time.monotonic()+10
            while not response.exists():
                assert broker.poll() is None,"broker exited while processing request"
                assert time.monotonic()<until,"broker request timeout"
                time.sleep(.025)
            return json.loads(response.read_text())
        # No matching durable lease: claimed work is rejected by the event loop.
        rejected=issue("missing",expectedWorkerRunId="GH-321-implementation-test",requiresWorkerLease=True)
        assert (rejected["status"],rejected["exitCode"])==("WorkerLeaseRejected",75),rejected
        assert not marker.exists(),"host started for missing worker"
        # A claim for a wrong live run must fail before launching the host.
        leases.joinpath("worker.json").write_text(json.dumps({
            "workspace":str(ws),"runId":"GH-321-implementation-other","pid":os.getpid()}))
        rejected=issue("wrong",expectedWorkerRunId="GH-321-implementation-test",requiresWorkerLease=True)
        assert (rejected["status"],rejected["exitCode"])==("WorkerLeaseRejected",75),rejected
        assert not marker.exists(),"host started for wrong worker"
        # Duplicate live lease records must fail closed.
        leases.joinpath("duplicate.json").write_text(leases.joinpath("worker.json").read_text())
        rejected=issue("duplicate",expectedWorkerRunId="GH-321-implementation-other",requiresWorkerLease=True)
        assert (rejected["status"],rejected["exitCode"])==("WorkerLeaseRejected",75),rejected
        assert not marker.exists(),"host started for ambiguous worker"
        # Contradictory request is rejected at parse time.
        rejected=issue("schema",expectedWorkerRunId="GH-321-implementation-other",requiresWorkerLease=False)
        assert rejected["exitCode"]==64,rejected
    finally:
        broker.terminate()
        try: broker.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.communicate()
print("unity-v2-broker-loop-test: PASS")
