#!/usr/bin/env python3
"""Host issuer contract: forged peers, stale lease, lock, and valid request."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from unity_admission_receipt import verify_receipt

module_path=Path(__file__).resolve().parents[1]/"scripts/unity-admission-issuer.py"
spec=importlib.util.spec_from_file_location("unity_admission_issuer",module_path)
issuer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(issuer)
secret=b"k"*32
with tempfile.TemporaryDirectory() as tmp:
    base=Path(tmp)
    wsroot=base/"workspaces"
    ws=wsroot/"GH-321"
    ws.mkdir(parents=True)
    state=base/"state"
    lease_dir=state/"workers"/"active"
    lease_dir.mkdir(parents=True)
    lock=state/"locks"/"unity-editor.lock"
    lock.mkdir(parents=True)
    (lock/"owner").write_text(ws.name)
    (lock/"workspace").write_text(str(ws))
    pid=os.getpid()
    run="GH-321-testworker"
    lease=lease_dir/"worker.json"
    def set_lease(start):
        lease.write_text(json.dumps({"workspace":str(ws),"pid":pid,
            "runId":run,"processStartTicks":start}))
    set_lease(issuer.process_start_ticks(pid))
    request={"workspace":str(ws),"operation":"editmode","requestId":"GH-321-job1"}
    kwargs=dict(peer_pid=pid,peer_uid=os.getuid(),state_root=state,
                workspace_root=wsroot,secret=secret)
    result=issuer.request_admission(request,**kwargs)
    assert result["expectedWorkerRunId"]==run
    assert verify_receipt(result["admissionReceipt"],secret=secret,issue=ws.name,
        workspace=str(ws),worker_run_id=run,operation="editmode",request_id="GH-321-job1")
    def rejected(payload=request,**changes):
        try:
            issuer.request_admission(payload,**{**kwargs,**changes})
        except (PermissionError,ValueError):
            return
        raise AssertionError("unauthorized admission accepted")
    rejected(peer_pid=999999)
    rejected(peer_uid=os.getuid()+1)
    rejected({**request,"workspace":str(base/"other/GH-321")})
    rejected({**request,"operation":"health"})
    rejected({**request,"requestId":"GH-322-job1"})
    rejected({**request,"peerPid":pid})
    (lock/"owner").write_text("GH-322")
    rejected()
    (lock/"owner").write_text(ws.name)
    set_lease("0")
    rejected()
    set_lease(issuer.process_start_ticks(pid))
    second=lease_dir/"duplicate.json"
    second.write_text(lease.read_text())
    rejected()
    second.unlink()
    rejected({**request,"operation":"author"})
    (state/"authoring").mkdir()
    (state/"authoring"/"GH-321.json").write_text("{}")
    assert issuer.request_admission({**request,"operation":"author"},**kwargs)
print("unity-v2-admission-issuer-test: PASS")
