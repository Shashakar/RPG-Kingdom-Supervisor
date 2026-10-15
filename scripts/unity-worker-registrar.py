#!/usr/bin/env python3
"""Kernel-authenticated registration endpoint for a separate trusted Symphony UID.

No token, claimed caller PID, or worker-provided lease is accepted as authority.
This service must be run under a dedicated registry UID. The caller is the
trusted Symphony controller UID, never the Codex worker UID.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import struct

from unity_trusted_worker_registry import register, revoke, protected_directory
from unity_worker_process_identity import verify_controller_sibling


def dispatch(message: object, *, peer_uid: int, controller_uid: int, peer_pid: int | None = None,
             worker_uid: int, state_root: Path, workspace_root: Path) -> dict:
    if peer_uid != controller_uid or controller_uid == worker_uid or os.getuid() in (controller_uid, worker_uid):
        raise PermissionError("registration requires three segregated Unix service identities")
    if not isinstance(message, dict):
        raise ValueError("invalid registration message")
    operation = message.get("action")
    if operation == "registerChild" and set(message) == {"action", "issue", "workspace", "pid", "runId"}:
        if type(message["pid"]) is not int or peer_pid is None:
            raise PermissionError("kernel peer PID required")
        verify_controller_sibling(client_pid=peer_pid, child_pid=message["pid"],
                                  controller_uid=controller_uid, worker_uid=worker_uid)
        record = register(state_root, workspace_root, issue=message["issue"],
                          workspace=message["workspace"], pid=message["pid"],
                          worker_uid=worker_uid, run_id=message["runId"])
        return {"ok": True, "registration": record}
    if operation == "revoke" and set(message) == {"action", "runId", "pid", "processStartTicks"}:
        if type(message["pid"]) is not int or not isinstance(message["processStartTicks"], str):
            raise ValueError("invalid revocation identity")
        removed = revoke(state_root, run_id=message["runId"],
                         pid=message["pid"], identity=message["processStartTicks"])
        return {"ok": True, "revoked": removed}
    raise ValueError("unsupported registry operation")


def serve(*, socket_path: Path, state_root: Path, workspace_root: Path,
          controller_uid: int, controller_gid: int, worker_uid: int) -> None:
    if len({controller_uid, worker_uid, os.getuid()}) != 3:
        raise PermissionError("registry, controller, and worker UIDs must differ")
    protected_directory(state_root / "workers" / "active", os.getuid())
    parent = socket_path.parent
    info = parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_gid != controller_gid or stat.S_IMODE(info.st_mode) & 0o022
            or not (info.st_mode & stat.S_IXGRP)):
        raise PermissionError("registrar socket directory unsafe")
    if socket_path.exists() or socket_path.is_symlink():
        raise FileExistsError("registrar socket already exists")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        os.chown(socket_path, os.getuid(), controller_gid)
        os.chmod(socket_path, 0o660)
        server.listen(8)
        try:
            while True:
                client, _ = server.accept()
                with client:
                    client.settimeout(3)
                    try:
                        pid, uid, gid = struct.unpack("3i", client.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
                        if uid != controller_uid:
                            raise PermissionError("unauthorized controller")
                        data = bytearray()
                        while b"\n" not in data and len(data) < 16384:
                            chunk = client.recv(4096)
                            if not chunk:
                                break
                            data.extend(chunk)
                        if b"\n" not in data:
                            raise ValueError("incomplete message")
                        message = json.loads(data.split(b"\n", 1)[0])
                        result = dispatch(message, peer_uid=uid, controller_uid=controller_uid, peer_pid=pid,
                                          worker_uid=worker_uid, state_root=state_root,
                                          workspace_root=workspace_root)
                    except (OSError, ValueError, PermissionError) as exc:
                        result = {"ok": False, "error": type(exc).__name__}
                    client.sendall(json.dumps(result, separators=(",", ":")).encode() + b"\n")
        finally:
            socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--controller-uid", required=True, type=int)
    parser.add_argument("--controller-gid", required=True, type=int)
    parser.add_argument("--worker-uid", required=True, type=int)
    args = parser.parse_args()
    serve(socket_path=Path(args.socket), state_root=Path(args.state_root),
          workspace_root=Path(args.workspace_root), controller_uid=args.controller_uid,
          controller_gid=args.controller_gid, worker_uid=args.worker_uid)
