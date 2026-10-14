"""Host-issued, request-bound Unity admission receipts.

A receipt is not a replacement for trusted issuance. The host must verify worker
identity and operation scope *before* minting one; model-writable request fields
must never be treated as an issuance instruction.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

WORKER_RE = re.compile(r"^GH-[0-9]+-[A-Za-z0-9_-]{1,160}$")
REQUEST_RE = re.compile(r"^GH-[0-9]+-[A-Za-z0-9_-]{1,200}$")
MAX_TTL_SECONDS = 300


def canonical_payload(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def issue_receipt(*, secret: bytes, issue: str, workspace: str,
                  worker_run_id: str, operation: str, request_id: str,
                  now: int | None = None, ttl_seconds: int = 120) -> dict[str, Any]:
    if len(secret) < 32:
        raise ValueError("host secret must have at least 32 bytes")
    if not WORKER_RE.fullmatch(worker_run_id) or not REQUEST_RE.fullmatch(request_id):
        raise ValueError("invalid worker or request identifier")
    if not re.fullmatch(r"GH-[0-9]+", issue) or not workspace.startswith("/"):
        raise ValueError("invalid issue or workspace")
    if operation not in {"editmode", "playmode", "capture", "inspect", "author"}:
        raise ValueError("operation cannot receive worker admission receipt")
    if not (1 <= ttl_seconds <= MAX_TTL_SECONDS):
        raise ValueError("invalid receipt lifetime")
    current = int(time.time()) if now is None else now
    data = {"version": 1, "issue": issue, "workspace": workspace,
            "workerRunId": worker_run_id, "operation": operation,
            "requestId": request_id, "issuedAt": current,
            "expiresAt": current + ttl_seconds}
    return {"payload": data, "mac": hmac.new(secret, canonical_payload(data), hashlib.sha256).hexdigest()}


def verify_receipt(receipt: object, *, secret: bytes, issue: str, workspace: str,
                   worker_run_id: str, operation: str, request_id: str,
                   now: int | None = None) -> bool:
    if len(secret) < 32 or not isinstance(receipt, dict):
        return False
    payload = receipt.get("payload")
    mac = receipt.get("mac")
    if not isinstance(payload, dict) or not isinstance(mac, str) or len(mac) != 64:
        return False
    if set(payload) != {"version", "issue", "workspace", "workerRunId", "operation",
                        "requestId", "issuedAt", "expiresAt"}:
        return False
    if any(type(payload.get(k)) is not str for k in ("issue", "workspace", "workerRunId", "operation", "requestId")):
        return False
    if type(payload.get("issuedAt")) is not int or type(payload.get("expiresAt")) is not int:
        return False
    current = int(time.time()) if now is None else now
    if not (payload["version"] == 1 and payload["issue"] == issue
            and payload["workspace"] == workspace
            and payload["workerRunId"] == worker_run_id
            and payload["operation"] == operation
            and payload["requestId"] == request_id
            and payload["issuedAt"] <= current < payload["expiresAt"]
            and 0 < payload["expiresAt"] - payload["issuedAt"] <= MAX_TTL_SECONDS):
        return False
    expected = hmac.new(secret, canonical_payload(payload), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac)
