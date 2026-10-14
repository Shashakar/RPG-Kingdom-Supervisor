#!/usr/bin/env python3
"""Request a scoped admission receipt from the host Unix socket.

Only run this from a genuine Supervisor worker process tree. Never read the
issuer key or accept a claimed identity from worker-controlled input.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys


def attach(socket_path: str, request_path: Path, workspace: str, operation: str, request_id: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(socket_path)
        message = {"workspace": workspace, "operation": operation, "requestId": request_id}
        connection.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        raw = bytearray()
        while b"\n" not in raw and len(raw) < 65536:
            chunk = connection.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
    if b"\n" not in raw:
        raise RuntimeError("issuer returned no complete response")
    response = json.loads(raw.split(b"\n", 1)[0])
    if response.get("ok") is not True:
        raise PermissionError("host admission rejected request")
    admission = response.get("admission")
    if not isinstance(admission, dict) or set(admission) != {
        "expectedWorkerRunId", "requiresWorkerLease", "admissionReceipt"
    }:
        raise ValueError("malformed issuer response")
    original = json.loads(request_path.read_text(encoding="utf-8"))
    if original.get("requestId") != request_id or original.get("operation") != operation:
        raise ValueError("request changed during admission")
    if set(original).intersection(admission):
        raise ValueError("refusing to overwrite supplied admission fields")
    updated = {**original, **admission}
    temp = request_path.with_suffix(request_path.suffix + ".admit")
    try:
        temp.write_text(json.dumps(updated, separators=(",", ":")) + "\n", encoding="utf-8")
        temp.replace(request_path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    for arg in ("socket", "request", "workspace", "operation", "request-id"):
        parser.add_argument("--" + arg, required=True)
    args = parser.parse_args()
    try:
        attach(args.socket, Path(args.request), args.workspace, args.operation, args.request_id)
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Unity admission unavailable: {type(exc).__name__}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
