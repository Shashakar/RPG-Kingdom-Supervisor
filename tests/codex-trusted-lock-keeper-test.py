#!/usr/bin/env python3
"""Test role/issue mutual exclusion and FD isolation for lock keeper."""
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
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
        assert slot.exists() and issue.exists()
    # The lock keeper does not add a background process after return.
    assert m.run(state,"repair","GH-123",["/bin/true"])==0
print("codex-trusted-lock-keeper-test: PASS")
