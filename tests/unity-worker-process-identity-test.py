#!/usr/bin/env python3
"""Verify kernel identity binding independent of a supervising keeper."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"scripts"))
import unity_worker_process_identity as p
with TemporaryDirectory() as temp:
    proc=Path(temp)
    def write(pid,parent,uid,start):
        d=proc/str(pid)
        d.mkdir(exist_ok=True)
        values=["S",str(parent)]+["0"]*17+[str(start)]
        (d/"stat").write_text(str(pid)+" (codex app-server) "+" ".join(values))
        (d/"status").write_text("Uid:\t"+("\t".join([str(uid)]*4))+"\n")
    write(321,100,1001,883)
    write(322,100,1000,884)
    write(100,1,1000,111)
    result=p.verify_spawned_worker(keeper_pid=100,child_pid=321,expected_uid=1001,proc=proc)
    assert result=={"pid":321,"startTicks":"883","uid":1001}
    assert p.verify_controller_sibling(client_pid=322,child_pid=321,
                                       controller_uid=1000,worker_uid=1001,proc=proc)["pid"]==321
    for client,worker in [(321,322),(322,100),(100,321)]:
        try:p.verify_controller_sibling(client_pid=client,child_pid=worker,
                                        controller_uid=1000,worker_uid=1001,proc=proc)
        except (PermissionError,ValueError):pass
        else:raise AssertionError("invalid controller sibling accepted")
    for args in [
       {"keeper_pid":321,"child_pid":321,"expected_uid":1001},
       {"keeper_pid":101,"child_pid":321,"expected_uid":1001},
       {"keeper_pid":100,"child_pid":321,"expected_uid":0},
    ]:
        try:p.verify_spawned_worker(proc=proc,**args)
        except PermissionError:pass
        else:raise AssertionError("forged process identity accepted")
    (proc/"321"/"status").write_text("Uid:\t1001\t0\t1001\t1001\n")
    try:p.proc_identity(321,proc)
    except PermissionError:pass
    else:raise AssertionError("mixed UIDs accepted")
print("unity-worker-process-identity-test: PASS")
