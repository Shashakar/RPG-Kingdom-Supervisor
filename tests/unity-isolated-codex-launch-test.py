#!/usr/bin/env python3
"""Static guard tests; do not run privileged launcher on CI."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("isolated",root/"scripts"/"unity-isolated-codex-launch.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with TemporaryDirectory() as temp:
    base=Path(temp)/"ws"
    issue=base/"GH-123"
    issue.mkdir(parents=True)
    assert m.validate_workspace(str(issue),str(base))==str(issue)
    other=Path(temp)/"GH-123"
    other.mkdir()
    for unsafe in (str(other),str(base),str(issue/"..")):
        try: m.validate_workspace(unsafe,str(base))
        except PermissionError: pass
        else: raise AssertionError("workspace scope escaped")
    scrubbed=m.safe_env("/worker/home","/bin",{
        "SYMPHONY_GITHUB_TOKEN":"secret","GH_TOKEN":"secret",
        "AWS_SECRET_ACCESS_KEY":"secret","PATH":"/untrusted",
        "HOME":"/root","LANG":"C.UTF-8"})
    assert scrubbed["HOME"]=="/worker/home"
    assert scrubbed["PATH"]=="/bin"
    assert "GH_TOKEN" not in scrubbed and "AWS_SECRET_ACCESS_KEY" not in scrubbed
    assert scrubbed["LANG"]=="C.UTF-8"
    with patch.object(m.pwd,"getpwnam",return_value=type("P",(),{
        "pw_uid":1001,"pw_gid":1001,"pw_dir":"/worker/home"})()):
        assert m.checked_identity("rpgk-codex",1000)==(1001,1001,"/worker/home")
    with patch.object(m.pwd,"getpwnam",return_value=type("P",(),{
        "pw_uid":1000,"pw_gid":1000,"pw_dir":"/home/dex"})()):
        try: m.checked_identity("rpgk-codex",1000)
        except PermissionError: pass
        else: raise AssertionError("controller account accepted as worker")
    m.validate_codex_args(["--config",'model="gpt-5.6"',"--config","model_reasoning_effort=high","app-server"])
    for bad in (["app-server"], ["--config","dangerous=true","app-server"],
                ["--help","app-server"], ["--config",'model="ok"',"shell","app-server"]):
        try: m.validate_codex_args(bad)
        except ValueError: pass
        else: raise AssertionError("unapproved Codex args accepted")
    # FDs 8/9 are the router's locks; prove test helper closes them.
    read_fd,write_fd=os.pipe()
    try:
        with patch.object(m.os,"listdir",return_value=[str(write_fd)]):
            m.close_inherited_fds()
        try: os.fstat(write_fd)
        except OSError: pass
        else: raise AssertionError("inherited FD survived")
    finally:
        os.close(read_fd)

    rfd,wfd=os.pipe()
    try:
        os.write(wfd,b"G")
        m.wait_for_admission(rfd,timeout=0.1)
    finally:
        os.close(rfd)
        os.close(wfd)
    rfd,wfd=os.pipe()
    try:
        os.close(wfd)
        try: m.wait_for_admission(rfd,timeout=0.1)
        except PermissionError: pass
        else: raise AssertionError("EOF released gate")
    finally:
        os.close(rfd)
print("unity-isolated-codex-launch-test: PASS")
