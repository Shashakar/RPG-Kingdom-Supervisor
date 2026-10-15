#!/usr/bin/env python3
"""Test role/issue mutual exclusion and FD isolation for lock keeper."""
import importlib.util
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "scripts"))
spec=importlib.util.spec_from_file_location("keeper",root/"scripts"/"codex-trusted-lock-keeper.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with TemporaryDirectory() as temp:
    state=Path(temp)
    (state/"locks"/"issues").mkdir(parents=True)
    slot,issue=m.paths(state,"repair","GH-123")
    assert slot==state/"locks"/"codex-mutation.lock"
    assert issue==state/"locks"/"issues"/"GH-123.lock"
    assert m.paths(state,"report-only","GH-123")[0]==slot
    assert m.paths(state,"review","GH-123")[0]!=slot
    for role,issue_id in [("other","GH-123"),("repair","not-an-issue"),("review","GH-1/../../")]:
        try: m.paths(state,role,issue_id)
        except ValueError: pass
        else: raise AssertionError("bad role or issue accepted")
    # subprocess retains no inherited lock handles, and holds locks while
    # the child's process remains alive.
    with patch.object(m.subprocess,"Popen") as spawn:
        spawn.return_value.wait.return_value=0
        assert m.run(state,"repair","GH-123",["/bin/true"])==0
        assert spawn.call_args.kwargs["close_fds"] is True
        assert spawn.call_args.kwargs["start_new_session"] is True
        assert slot.exists() and issue.exists()
    # The lock keeper does not add a background process after return.
    assert m.run(state,"repair","GH-123",["/bin/true"])==0
    # Real process: one worker owns a process group with a descendant.
    # A SIGTERM to the keeper should forward to the child group; lock release
    # occurs after child termination.
    child = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    def stop():
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)
    killer=threading.Thread(target=stop)
    killer.start()
    try:
        outcome=m.wait_for_child(child,grace_seconds=0.5)
        assert outcome != 0
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    killer.join()
    # The registration endpoint must be invoked by the keeper's own
    # subprocess; the kernel, not JSON, identifies that client process.
    args=m.registrar_command("/tmp/client.py","/tmp/socket","register",
                             issue="GH-123",workspace="/srv/rpgk/workspaces/GH-123",
                             pid=1234,run_id="GH-123-test")
    assert args[-8:]==["--issue","GH-123","--workspace",
                       "/srv/rpgk/workspaces/GH-123","--pid","1234",
                       "--run-id","GH-123-test"]
    try:
        m.registrar_command("/tmp/client.py","/tmp/socket","revoke",
                            issue="GH-123",workspace="/tmp",pid=1234,run_id="GH-123-test")
    except ValueError:
        pass
    else:
        raise AssertionError("revocation without process identity accepted")
print("codex-trusted-lock-keeper-test: PASS")
