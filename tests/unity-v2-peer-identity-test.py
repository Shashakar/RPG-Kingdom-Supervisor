#!/usr/bin/env python3
"""Host peer process ancestry checks, including real /proc where available."""
from pathlib import Path
import os
import socket
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from unity_peer_identity import read_ppid, is_worker_descendant, unix_peer_credentials, authorized_worker_peer

parents={600:500,500:400,400:1,700:700,800:0}
getter=lambda pid:parents.get(pid)
assert is_worker_descendant(600,400,get_parent=getter)
assert is_worker_descendant(400,400,get_parent=getter)
assert not is_worker_descendant(600,401,get_parent=getter)
assert not is_worker_descendant(700,400,get_parent=getter)
assert not is_worker_descendant(800,400,get_parent=getter)
assert not is_worker_descendant(600,400,get_parent=getter,max_depth=2)
assert not is_worker_descendant(600,400,get_parent=lambda pid:None)
assert not is_worker_descendant(0,400,get_parent=getter)
assert not is_worker_descendant(600,0,get_parent=getter)
with tempfile.TemporaryDirectory() as tmp:
    base=Path(tmp)
    status=base/"601"/"status"
    status.parent.mkdir()
    status.write_text("Name:\tworker\nPPid:\t600\n")
    assert read_ppid(601,base)==600
    status.write_text("PPid:\tbogus\n")
    assert read_ppid(601,base) is None
    assert read_ppid(999,base) is None
# The running interpreter must at least have a valid parent in a Linux CI job.
if Path("/proc/self/status").exists():
    assert read_ppid(os.getpid())==os.getppid()
if hasattr(socket, "SO_PEERCRED"):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        pid, uid, gid = unix_peer_credentials(left)
        assert pid == os.getpid() and uid == os.getuid() and gid == os.getgid()
        assert authorized_worker_peer(pid, uid, os.getpid(), os.getuid())
        assert not authorized_worker_peer(pid, uid + 1, os.getpid(), os.getuid())
        assert not authorized_worker_peer(pid, uid, os.getpid() + 1000000, os.getuid())
    finally:
        left.close()
        right.close()
print("unity-v2-peer-identity-test: PASS")
