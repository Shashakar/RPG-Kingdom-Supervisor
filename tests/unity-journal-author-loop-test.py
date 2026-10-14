#!/usr/bin/env python3
"""Legacy author broker loop: reject duplicate request ID without re-execution."""
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
    workspaces=base/"workspaces"
    ws=workspaces/"GH-321"
    reqdir=ws/"Logs"/"SymphonyUnity"/".author-broker"/"requests"
    respdir=reqdir.parent/"responses"
    reqdir.mkdir(parents=True)
    state=base/"state"
    lock=state/"locks"/"unity-editor.lock"
    lock.mkdir(parents=True)
    (lock/"owner").write_text("GH-321")
    (lock/"workspace").write_text(str(ws))
    leases=state/"workers"/"active"
    leases.mkdir(parents=True)
    live_id="GH-321-implementation-test"
    (leases/"worker.json").write_text(json.dumps({"workspace":str(ws),"runId":live_id,"pid":os.getpid()}))
    grants=state/"authoring"
    grants.mkdir(parents=True)
    scene="Assets/RPGKingdom/Scenes/PlaytestScene.unity"
    grants.joinpath("GH-321.json").write_text(json.dumps({
        "protocolVersion":1,"issue":"GH-321","tier":"existing-scene-composition",
        "workspace":str(ws),"scene":scene,"operations":["noop"]
    }))
    marker=base/"host-called"
    host=base/"fake-author-host.sh"
    host.write_text("#!/bin/sh\nprintf 'called\\n' >> '"+str(marker)+"'\nprintf '{\"success\":true}\\n'\n")
    host.chmod(0o755)
    broker=subprocess.Popen([sys.executable,str(root/"scripts"/"unity-author-broker.py"),
        "--workspace-root",str(workspaces),"--state-root",str(state),"--host-runner",str(host),
        "--poll-seconds","0.025"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def issue(name, **fields):
        p=reqdir/("GH-321-"+name+".json")
        response=respdir/p.name
        p.write_text(json.dumps({"protocolVersion":1,"requestId":p.stem,"operation":"author",
            "authoring":{"protocolVersion":1,"tier":"existing-scene-composition",
                         "scene":scene,"operations":[{"kind":"noop"}]},**fields}))
        deadline=time.monotonic()+10
        while not response.exists():
            assert broker.poll() is None,"author broker exited early"
            assert time.monotonic()<deadline,"author request timeout"
            time.sleep(.025)
        return json.loads(response.read_text())
    try:
        valid=issue("valid")
        assert (valid["status"],valid["exitCode"])==("completed",0),valid
        assert marker.read_text().splitlines()==["called"],"expected one host invocation"
        replay_path=reqdir/"GH-321-valid.json"
        replay_response=respdir/replay_path.name
        replay_response.unlink()
        replay_path.write_text(json.dumps({
            "protocolVersion":1,"requestId":replay_path.stem,"operation":"author",
            "authoring":{"protocolVersion":1,"tier":"existing-scene-composition",
                         "scene":scene,"operations":[{"kind":"noop"}]}
        }))
        deadline=time.monotonic()+10
        while not replay_response.exists():
            assert broker.poll() is None,"author broker exited on replay"
            assert time.monotonic()<deadline,"replay timeout"
            time.sleep(.025)
        replay=json.loads(replay_response.read_text())
        assert (replay["status"],replay["exitCode"])==("DuplicateRequest",93),replay
        assert marker.read_text().splitlines()==["called"],"replay invoked author host"
    finally:
        broker.terminate()
        try: broker.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.communicate()
print("unity-v2-author-broker-loop-test: PASS")
