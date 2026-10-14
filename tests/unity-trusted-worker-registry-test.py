#!/usr/bin/env python3
"""Registry contract: separate identities, immutable PID start, replay, revocation."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import unity_trusted_worker_registry as registry

with tempfile.TemporaryDirectory() as temp:
    base=Path(temp)
    wsroot=base/"workspaces"
    workspace=wsroot/"GH-321"
    workspace.mkdir(parents=True)
    state=base/"state"
    leases=state/"workers"/"active"
    leases.mkdir(parents=True)
    leases.chmod(0o700)
    run="GH-321-registry-test"
    uid=os.getuid()+1000
    with patch.object(registry, "validate_process", return_value="123456") as checked:
        record=registry.register(state, wsroot, issue="GH-321", workspace=str(workspace),
                                 pid=1234, worker_uid=uid, run_id=run)
        assert record["pid"]==1234 and record["processStartTicks"]=="123456"
        assert checked.call_count == 2
        assert registry.revoke(state, run_id=run, pid=1234, identity="123456")
        assert not registry.revoke(state, run_id=run, pid=1234, identity="123456")
        registry.register(state, wsroot, issue="GH-321", workspace=str(workspace),
                          pid=1234, worker_uid=uid, run_id=run)
        try:
            registry.register(state, wsroot, issue="GH-321", workspace=str(workspace),
                              pid=1234, worker_uid=uid, run_id=run)
        except FileExistsError:
            pass
        else:
            raise AssertionError("duplicate lease permitted")
        try:
            registry.revoke(state, run_id=run, pid=1234, identity="reused")
        except PermissionError:
            pass
        else:
            raise AssertionError("stale revocation permitted")
        for issue,path in [("GH-322",str(workspace)),("GH-321",str(base/"outside"))]:
            try:
                registry.register(state,wsroot,issue=issue,workspace=path,pid=1234,
                                  worker_uid=uid,run_id="GH-321-other")
            except (PermissionError,ValueError,FileNotFoundError):
                pass
            else:
                raise AssertionError("cross-issue workspace admitted")
    leases.chmod(0o770)
    try:
        registry.protected_directory(leases,os.getuid())
    except PermissionError:
        pass
    else:
        raise AssertionError("worker-writable registry accepted")
    leases.chmod(0o700)
    with patch.object(registry, "start_ticks", side_effect=["7","8"]):
        with patch.object(registry, "process_uid", return_value=uid):
            try:
                registry.validate_process(1234,worker_uid=uid)
            except PermissionError:
                pass
            else:
                raise AssertionError("PID reuse admitted")
    # A process with elevated or shifted effective credentials cannot be admitted.
    fake_proc=base/"proc"
    fake_status=fake_proc/"1234"
    fake_status.mkdir(parents=True)
    (fake_status/"status").write_text("Name:\tworker\nUid:\t1001\t0\t1001\t1001\n")
    try:
        registry.process_uid(1234,proc=fake_proc)
    except PermissionError:
        pass
    else:
        raise AssertionError("mixed worker UIDs accepted")
    (fake_status/"status").write_text("Uid:\t1001\t1001\t1001\t1001\n")
    assert registry.process_uid(1234,proc=fake_proc)==1001
print("unity-trusted-worker-registry-test: PASS")
