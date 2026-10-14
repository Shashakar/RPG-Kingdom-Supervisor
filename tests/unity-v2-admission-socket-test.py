#!/usr/bin/env python3
"""Real issuer socket + real client process, no Windows Unity required."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"scripts"))
from unity_admission_receipt import verify_receipt
import importlib.util
spec=importlib.util.spec_from_file_location("issuer",root/"scripts"/"unity-admission-issuer.py")
issuer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(issuer)

with tempfile.TemporaryDirectory() as temp:
    base=Path(temp)
    wsroot=base/"workspaces"
    ws=wsroot/"GH-321"
    ws.mkdir(parents=True)
    state=base/"state"
    leases=state/"workers"/"active"
    leases.mkdir(parents=True)
    (leases/"worker.json").write_text(json.dumps({
        "workspace":str(ws),"runId":"GH-321-e2e","pid":os.getpid(),
        "processStartTicks":issuer.process_start_ticks(os.getpid())
    }))
    lock=state/"locks"/"unity-editor.lock"
    lock.mkdir(parents=True)
    (lock/"owner").write_text(ws.name)
    (lock/"workspace").write_text(str(ws))
    key=base/"secret"
    key.write_bytes(b"X"*32)
    key.chmod(0o600)
    path=base/"issuer.sock"
    server=subprocess.Popen([sys.executable,str(root/"scripts"/"unity-admission-issuer.py"),
        "--socket",str(path),"--state-root",str(state),
        "--workspace-root",str(wsroot),"--key-file",str(key)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,\n        env={**os.environ, "RPGK_UNITY_ISSUER_TEST_MODE":"1"})
    try:
        deadline=time.monotonic()+5
        while not path.exists():
            assert server.poll() is None,"issuer exited before binding socket"
            assert time.monotonic()<deadline,"issuer did not start"
            time.sleep(.02)
        request=base/"request.json"
        request.write_text(json.dumps({"protocolVersion":1,"requestId":"GH-321-e2e-job",
                "operation":"editmode"}))
        cmd=[sys.executable,str(root/"scripts"/"unity-admission-client.py"),
            "--socket",str(path),"--request",str(request),"--workspace",str(ws),
            "--operation","editmode","--request-id","GH-321-e2e-job"]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
        assert result.returncode==0,result.stderr
        received=json.loads(request.read_text())
        assert received["expectedWorkerRunId"]=="GH-321-e2e"
        assert verify_receipt(received["admissionReceipt"],secret=key.read_bytes(),
            issue=ws.name,workspace=str(ws),worker_run_id="GH-321-e2e",
            operation="editmode",request_id="GH-321-e2e-job")
        (lock/"owner").write_text("GH-322")
        failed_request=base/"bad.json"
        failed_request.write_text(json.dumps({"protocolVersion":1,
            "requestId":"GH-321-e2e-bad","operation":"editmode"}))
        bad=subprocess.run([*cmd[:cmd.index("--request")+1],str(failed_request),
            "--workspace",str(ws),"--operation","editmode","--request-id","GH-321-e2e-bad"],
            capture_output=True,text=True,timeout=5)
        assert bad.returncode==75,(bad.stdout,bad.stderr)
        assert "admissionReceipt" not in json.loads(failed_request.read_text())
    finally:
        server.terminate()
        try: server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()
print("unity-v2-admission-socket-test: PASS")
