#!/usr/bin/env python3
"""Client speaks Unix-socket protocol; rejects issuer errors without modifying request."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
path=Path(__file__).resolve().parents[1]/"scripts/unity-admission-client.py"
spec=importlib.util.spec_from_file_location("unity_admission_client",path)
client=importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)

def run_case(permit):
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)
        socket_path=base/"issuer.sock"
        request_path=base/"request.json"
        request_path.write_text(json.dumps({"protocolVersion":1,"requestId":"GH-321-job","operation":"editmode"}))
        ready=threading.Event()
        messages=[]
        def host():
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as srv:
                srv.bind(str(socket_path));srv.listen(1);ready.set()
                conn,_=srv.accept()
                with conn:
                    raw=conn.recv(4096)
                    messages.append(json.loads(raw.split(b"\n")[0]))
                    reply={"ok":True,"admission":{
                        "expectedWorkerRunId":"GH-321-valid",
                        "requiresWorkerLease":True,
                        "admissionReceipt":{"payload":{},"mac":"test"}
                    }} if permit else {"ok":False,"error":"PermissionError"}
                    conn.sendall(json.dumps(reply).encode()+b"\n")
        thread=threading.Thread(target=host,daemon=True)
        thread.start();assert ready.wait(3)
        failed=False
        try: client.attach(str(socket_path),request_path,"/tmp/GH-321","editmode","GH-321-job")
        except PermissionError: failed=True
        thread.join(3)
        assert not thread.is_alive()
        assert messages==[{"workspace":"/tmp/GH-321","operation":"editmode","requestId":"GH-321-job"}]
        data=json.loads(request_path.read_text())
        assert failed != permit
        assert ("admissionReceipt" in data)==permit
        assert data["requestId"]=="GH-321-job"
run_case(True)
run_case(False)
print("unity-v2-admission-client-test: PASS")
