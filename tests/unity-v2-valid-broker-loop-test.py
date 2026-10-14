#!/usr/bin/env python3
"""Real validation broker loop: a correct claimed lease runs the fake host exactly once."""
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
    run_id="GH-321-implementation-test"
    (leases/"worker.json").write_text(json.dumps({
        "workspace":str(ws),"runId":run_id,"pid":os.getpid()
    }))
    marker=base/"host-called"
    host=base/"fake-host.sh"
    host.write_text("#!/bin/sh\nprintf 'called\\n' >> '"+str(marker)+"'\nprintf '{\"status\":\"completed\",\"exitCode\":0,\"total\":1}\\n'\n")
    host.chmod(0o755)
    broker=subprocess.Popen([
        sys.executable,str(root/"scripts"/"unity-host-broker.py"),
        "--workspace-root",str(wsroot),"--state-root",str(state),
        "--host-runner",str(host),"--poll-ms","25"
    ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while not (state/"unity-broker"/"status.json").exists():
            assert broker.poll() is None,"broker exited at startup"
            assert time.monotonic()<deadline,"startup timeout"
            time.sleep(.025)
        request=reqdir/"GH-321-editmode-valid.json"
        response=respdir/request.name
        request.write_text(json.dumps({
            "protocolVersion":1,"requestId":request.stem,"operation":"editmode",
            "requiresWorkerLease":True,"expectedWorkerRunId":run_id,"clientPid":os.getpid()
        }))
        deadline=time.monotonic()+10
        while not response.exists():
            assert broker.poll() is None,"broker exited during valid request"
            assert time.monotonic()<deadline,"request timeout"
            time.sleep(.025)
        result=json.loads(response.read_text())
        assert result["status"] not in ("WorkerLeaseRejected","HostBusy"),result
        assert result["exitCode"]==0,result
        assert marker.read_text().splitlines()==["called"],"expected exactly one host invocation"
    finally:
        broker.terminate()
        try: broker.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.communicate()
print("unity-v2-valid-broker-loop-test: PASS")
