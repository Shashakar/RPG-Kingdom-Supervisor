#!/usr/bin/env python3
"""Root-owned, fixed-policy Codex worker launcher for isolated host testing.

Only callable via a dedicated privilege transition configured out-of-band.
This script MUST be root-owned, non-worker-writable, and invoked as root.
It never executes a worker-supplied shell command or accepts an executable path.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import pwd
import grp
import stat
import sys

ALLOWED_ENV = ("TERM", "LANG", "LC_ALL")
MAX_ARGS = 128

def checked_identity(user: str, controller_uid: int) -> tuple[int,int,str]:
    account=pwd.getpwnam(user)
    if account.pw_uid == 0 or account.pw_uid == controller_uid:
        raise PermissionError("worker account cannot be root or controller")
    return account.pw_uid, account.pw_gid, account.pw_dir

def validate_workspace(workspace: str, root: str) -> str:
    base=Path(root).resolve(strict=True)
    target=Path(workspace).resolve(strict=True)
    if target.parent != base or not target.name.startswith("GH-") or not target.name[3:].isdigit():
        raise PermissionError("workspace is outside the allowed issue root")
    return str(target)

def safe_env(worker_home: str, worker_path: str, environ: dict) -> dict:
    result={"HOME":worker_home, "USER":"rpgk-codex", "LOGNAME":"rpgk-codex",
            "PATH":worker_path, "SHELL":"/bin/sh"}
    for key in ALLOWED_ENV:
        if key in environ and len(environ[key]) < 4096:
            result[key]=environ[key]
    return result

def close_inherited_fds(keep: tuple[int,...]=(0,1,2)) -> None:
    # Protect against inherited lock FDs, git tokens in memfds, socket handles.
    for entry in os.listdir("/proc/self/fd"):
        fd=int(entry)
        if fd not in keep:
            try: os.close(fd)
            except OSError: pass

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--workspace-root",required=True)
    parser.add_argument("--worker-user",default="rpgk-codex")
    parser.add_argument("codex_args",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if os.geteuid()!=0:
        raise PermissionError("launcher must be invoked by trusted privileged service")
    controller_uid=pwd.getpwnam("dex").pw_uid
    if controller_uid <= 0 or os.getuid()!=0:
        raise PermissionError("privileged launcher requires root invocation and pinned controller UID")
    if args.worker_user != "rpgk-codex":
        raise PermissionError("worker identity is fixed")
    worker_uid,worker_gid,home=checked_identity(args.worker_user,controller_uid)
    cwd=validate_workspace(args.workspace,args.workspace_root)
    binary=Path("/usr/local/libexec/rpgk/codex-worker")
    st=binary.stat()
    if (not binary.is_absolute() or not stat.S_ISREG(st.st_mode)
        or st.st_uid != 0 or st.st_mode & 0o022):
        raise PermissionError("Codex binary must be trusted-owned and non-group-writable")
    if len(args.codex_args)>MAX_ARGS or not args.codex_args or args.codex_args[-1]!="app-server":
        raise ValueError("only Codex app-server arguments are permitted")
    if any("\x00" in x or "\n" in x for x in args.codex_args):
        raise ValueError("unsafe argument")
    environment=safe_env(home,"/usr/local/bin:/usr/bin:/bin",os.environ)
    close_inherited_fds()
    os.initgroups(args.worker_user,worker_gid)
    os.setgid(worker_gid)
    os.setuid(worker_uid)
    os.chdir(cwd)
    os.execve(str(binary),[str(binary),*args.codex_args],environment)

if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,PermissionError,KeyError) as exc:
        print(f"isolated worker launch denied: {type(exc).__name__}",file=sys.stderr)
        raise SystemExit(77)
