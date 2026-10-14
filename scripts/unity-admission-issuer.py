"""Host-owned Unix socket admission issuer; never accept claimed PID as identity.

The server should run as a separately managed Supervisor host process. The socket
and signing key must never be exposed to Windows or committed to a workspace.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import stat

from unity_admission_receipt import issue_receipt
from unity_peer_identity import unix_peer_credentials, authorized_worker_peer
from unity_worker_lease import resolve_unique_live_lease

ISSUE = re.compile(r"GH-[0-9]+$")
OPERATIONS = frozenset({"editmode", "playmode", "capture", "inspect", "author"})


def process_start_ticks(pid: int, root: Path = Path("/proc")) -> str | None:
    """Kernel starttime (field 22), robust to spaces/parentheses in comm."""
    try:
        raw = (root / str(pid) / "stat").read_text()
        after = raw.rsplit(") ", 1)[1].split()
        return after[19]  # 22nd field; tail starts at field 3
    except (OSError, IndexError):
        return None


def request_admission(payload: object, *, peer_pid: int, peer_uid: int,
                      state_root: Path, workspace_root: Path,
                      secret: bytes, host_uid: int | None = None) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"workspace", "operation", "requestId"}:
        raise ValueError("invalid request shape")
    if not all(isinstance(v, str) for v in payload.values()):
        raise ValueError("invalid request types")
    workspace = Path(payload["workspace"]).resolve()
    if workspace.parent != workspace_root.resolve() or not ISSUE.fullmatch(workspace.name):
        raise PermissionError("workspace outside registered issue root")
    operation, request_id = payload["operation"], payload["requestId"]
    if operation not in OPERATIONS or not request_id.startswith(workspace.name + "-"):
        raise PermissionError("operation or request outside issue scope")
    if peer_uid != (os.getuid() if host_uid is None else host_uid):
        raise PermissionError("peer UID mismatch")
    run_id, worker_pid = resolve_unique_live_lease(
        state_root, workspace,
        lambda pid: process_start_ticks(pid) is not None,
    )
    if not run_id or not worker_pid or not authorized_worker_peer(
        peer_pid, peer_uid, worker_pid, peer_uid,
    ):
        raise PermissionError("peer not in unique live worker tree")
    lock = state_root / "locks" / "unity-editor.lock"
    try:
        owner = (lock / "owner").read_text().strip()
        lock_workspace = Path((lock / "workspace").read_text().strip()).resolve()
    except OSError as exc:
        raise PermissionError("editor lock missing") from exc
    if owner != workspace.name or lock_workspace != workspace:
        raise PermissionError("editor lock belongs to another worker")
    # Reject PID reuse/lease records without a stable process start identity.
    lease_matches = []
    for path in (state_root / "workers" / "active").glob("*.json"):
        try:
            lease = json.loads(path.read_text())
            if (lease.get("runId") == run_id and
                Path(str(lease.get("workspace"))).resolve() == workspace and
                lease.get("pid") == worker_pid):
                lease_matches.append(lease)
        except (OSError, ValueError, TypeError):
            continue
    if len(lease_matches) != 1:
        raise PermissionError("ambiguous worker lease")
    expected_start = lease_matches[0].get("processStartTicks")
    if not isinstance(expected_start, str) or expected_start != process_start_ticks(worker_pid):
        raise PermissionError("missing or mismatched worker start identity")
    if operation == "author" and not (state_root / "authoring" / (workspace.name + ".json")).is_file():
        raise PermissionError("authoring grant absent")
    receipt = issue_receipt(secret=secret, issue=workspace.name, workspace=str(workspace),
                            worker_run_id=run_id, operation=operation, request_id=request_id)
    return {"expectedWorkerRunId": run_id, "requiresWorkerLease": True, "admissionReceipt": receipt}


def serve(*, socket_path: Path, state_root: Path, workspace_root: Path, key_path: Path, worker_uid: int | None = None, worker_gid: int | None = None) -> None:
    mode = key_path.stat().st_mode
    if stat.S_IMODE(mode) & 0o077 or key_path.stat().st_uid != os.getuid():
        raise PermissionError("host signing key must be owned by issuer with mode 0600 or stricter")
    secret = key_path.read_bytes()
    if len(secret) < 32:
        raise ValueError("signing key too short")
    # The worker must not be able to read the key even with the same UID.
    # POSIX file modes cannot isolate secrets between processes sharing a UID.
    # Refuse unsafe deployment until the issuer runs under a distinct identity.
    if worker_uid is None and os.environ.get("RPGK_UNITY_ISSUER_TEST_MODE") != "1":
        raise PermissionError("explicit worker UID required for deployment")
    if worker_uid is not None and (worker_gid is None or worker_gid < 0):
        raise PermissionError("separate worker group required for socket admission")
    if worker_uid is not None and worker_uid == os.getuid():
        raise PermissionError("issuer and worker must use separate Unix identities")
    if socket_path.exists():
        raise FileExistsError("refusing to unlink an existing socket")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        if worker_uid is not None:
            os.chown(socket_path, os.getuid(), worker_gid)
            os.chmod(socket_path, 0o660)
        else:
            os.chmod(socket_path, 0o600)
        listener.listen(16)
        try:
            while True:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)
                    try:
                        pid, uid, _ = unix_peer_credentials(connection)
                        raw = bytearray()
                        while b"\n" not in raw and len(raw) < 65536:
                            chunk = connection.recv(min(4096, 65536-len(raw)))
                            if not chunk:
                                break
                            raw.extend(chunk)
                        if b"\n" not in raw:
                            raise ValueError("unterminated request")
                        request = json.loads(raw.split(b"\n", 1)[0])
                        if worker_uid is not None and uid != worker_uid:
                            raise PermissionError("unexpected worker Unix UID")
                        result = request_admission(request, peer_pid=pid, peer_uid=uid,
                                                   state_root=state_root, workspace_root=workspace_root,
                                                   secret=secret, host_uid=uid)
                        reply = {"ok": True, "admission": result}
                    except (ValueError, PermissionError, OSError, json.JSONDecodeError) as exc:
                        reply = {"ok": False, "error": type(exc).__name__}
                    connection.sendall(json.dumps(reply, separators=(",", ":")).encode() + b"\n")
        finally:
            socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    for field in ("socket", "state-root", "workspace-root", "key-file"):
        parser.add_argument("--"+field, required=True)
    args = parser.parse_args()
    serve(socket_path=Path(args.socket), state_root=Path(args.state_root),
          workspace_root=Path(args.workspace_root), key_path=Path(args.key_file),
          worker_uid=args.worker_uid, worker_gid=args.worker_gid)


if __name__ == "__main__":
    main()
