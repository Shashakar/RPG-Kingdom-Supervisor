#!/usr/bin/env python3
"""Controller-only CLI for the protected Unity worker registrar.

No direct lease file access, no authority to mint identities by itself.
Registrar verifies peer credentials from the kernel.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import sys

def submit(socket_path: str, request: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(socket_path)
        connection.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        result = bytearray()
        while b"\n" not in result and len(result) < 16384:
            data = connection.recv(4096)
            if not data:
                break
            result.extend(data)
    if b"\n" not in result:
        raise RuntimeError("registrar did not return a complete reply")
    reply = json.loads(result.split(b"\n",1)[0])
    if not isinstance(reply,dict) or reply.get("ok") is not True:
        raise PermissionError("registrar rejected request")
    return reply

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--socket",required=True)
    commands=parser.add_subparsers(dest="action",required=True)
    register=commands.add_parser("register")
    register.add_argument("--issue",required=True)
    register.add_argument("--workspace",required=True)
    register.add_argument("--pid",required=True,type=int)
    register.add_argument("--run-id",required=True)
    revoke=commands.add_parser("revoke")
    revoke.add_argument("--pid",required=True,type=int)
    revoke.add_argument("--run-id",required=True)
    revoke.add_argument("--process-start-ticks",required=True)
    args=parser.parse_args()
    if args.action=="register":
        request={"action":"register","issue":args.issue,"workspace":args.workspace,
                 "pid":args.pid,"runId":args.run_id}
    else:
        request={"action":"revoke","pid":args.pid,"runId":args.run_id,
                 "processStartTicks":args.process_start_ticks}
    try:
        reply=submit(args.socket,request)
    except (PermissionError,OSError,ValueError,RuntimeError) as exc:
        print(f"registration failed: {type(exc).__name__}",file=sys.stderr)
        return 75
    print(json.dumps(reply,separators=(",",":"),sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
