#!/usr/bin/env python3
"""Unprivileged integration exercise for keeper -> registrar client -> gate.

The simulated registrar response is NOT authentication. Production registrar
performs kernel SO_PEERCRED and ancestry verification separately.
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"scripts"))
spec=importlib.util.spec_from_file_location("keeper",root/"scripts"/"codex-trusted-lock-keeper.py")
keeper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(keeper)

with tempfile.TemporaryDirectory() as folder:
    base=Path(folder)
    client=base/"client.py"
    client.write_text('''import argparse,json,os,sys
p=argparse.ArgumentParser()
p.add_argument("--socket")
p.add_argument("action")
p.add_argument("--issue")
p.add_argument("--workspace")
p.add_argument("--pid",type=int)
p.add_argument("--run-id")
p.add_argument("--process-start-ticks")
a=p.parse_args()
if a.action=="register":
    if os.environ.get("FAKE_DENY")=="1":
        print(json.dumps({"ok":False}))
    else:
        print(json.dumps({"ok":True,"registration":{"pid":a.pid,"runId":a.run_id,"processStartTicks":"987"}}))
elif a.action=="revoke":
    with open(os.environ["REVOKE_MARK"],"w") as f: f.write(a.run_id)
    print(json.dumps({"ok":True}))
''')
    child=base/"child.py"
    child.write_text('''import os,sys,select
fd=int(sys.argv[sys.argv.index("--gate-fd")+1])
r,_,_=select.select([fd],[],[],4)
if not r or os.read(fd,1)!=bytes([71]):sys.exit(76)
with open(os.environ["START_MARK"],"w") as f:f.write("started")
''')
    command=[sys.executable,str(child),"--gate-fd","{RPGK_GATE_FD}"]
    start=base/"start"
    revoke=base/"revoke"
    os.environ["START_MARK"]=str(start)
    os.environ["REVOKE_MARK"]=str(revoke)
    assert keeper.start_gated_session(command,client_script=str(client),
           socket_path="/unused",issue="GH-999",workspace="/tmp/GH-999",run_id="run-ok")==0
    assert start.read_text()=="started"
    assert revoke.read_text()=="run-ok"
    start.unlink()
    revoke.unlink()
    os.environ["FAKE_DENY"]="1"
    try:
        keeper.start_gated_session(command,client_script=str(client),
                socket_path="/unused",issue="GH-999",workspace="/tmp/GH-999",run_id="run-deny")
    except PermissionError:
        pass
    else:
        raise AssertionError("denied worker accepted")
    assert not start.exists(), "worker ran despite registrar denial"
    assert not revoke.exists(), "denied worker should have no established lease"
    del os.environ["FAKE_DENY"]
print("unity-v2-keeper-integration-test: PASS")
