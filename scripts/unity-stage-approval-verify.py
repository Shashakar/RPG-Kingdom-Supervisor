#!/usr/bin/env python3
"""C5b read-only verifier for host-issued, HMAC-attested stage review decisions.

This does NOT issue approvals, mutate a nonce registry, or copy files.
An approval is never inferred from model-provided JSON alone.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location("c5_stage_proposal", Path(__file__).with_name("unity-stage-proposal.py"))
assert spec and spec.loader
proposal = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proposal
spec.loader.exec_module(proposal)

HEXDIGEST = re.compile(r"[0-9a-f]{64}\Z")
NONCE = re.compile(r"[0-9a-f]{32,64}\Z")
ACTOR = re.compile(r"[A-Za-z0-9_.@-]{3,128}\Z")


class ApprovalDenied(Exception):
    pass


def canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def manifest_digest(data: dict) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()


def safe_secret(path: Path) -> bytes:
    # Host-only: secret must be outside GH workspaces; refuse symlinks.
    if path.is_symlink() or not path.is_file():
        raise ApprovalDenied("host attestation key unavailable")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ApprovalDenied("host attestation key permissions too broad")
    secret = path.read_bytes()
    if len(secret) < 32:
        raise ApprovalDenied("host attestation key too short")
    return secret


def validate(approval: dict, current: dict, *, key: bytes, spent: set[str],
             now: datetime | None = None) -> dict:
    if not isinstance(approval, dict) or set(approval) != {"payload", "mac"}:
        raise ApprovalDenied("invalid approval envelope")
    payload = approval["payload"]
    if not isinstance(payload, dict) or set(payload) != {
        "schemaVersion", "purpose", "issue", "reviewer", "sourceRevision",
        "sourceSceneSha256", "stageProject", "proposalSha256", "approvedPaths",
        "nonce", "issuedAt", "expiresAt"
    }:
        raise ApprovalDenied("invalid approval payload")
    if payload["schemaVersion"] != 1 or payload["purpose"] != "c5b-review-verification-only":
        raise ApprovalDenied("unexpected approval purpose")
    reviewer, nonce = payload["reviewer"], payload["nonce"]
    if not isinstance(reviewer, str) or not ACTOR.fullmatch(reviewer):
        raise ApprovalDenied("invalid reviewer")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ApprovalDenied("invalid approval nonce")
    if nonce in spent:
        raise ApprovalDenied("approval nonce already used")
    signature = approval["mac"]
    if not isinstance(signature, str) or not HEXDIGEST.fullmatch(signature):
        raise ApprovalDenied("invalid attestation")
    expected = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ApprovalDenied("host attestation mismatch")
    try:
        issued = datetime.fromisoformat(payload["issuedAt"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(payload["expiresAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ApprovalDenied("invalid approval timestamps") from exc
    clock = now or datetime.now(timezone.utc)
    if issued.tzinfo is None or expires.tzinfo is None or not issued <= clock < expires:
        raise ApprovalDenied("approval expired, premature, or timezone missing")
    if (expires - issued).total_seconds() > 3600:
        raise ApprovalDenied("approval lifetime exceeds one hour")
    if payload["issue"] != current["issue"] or payload["sourceRevision"] != current["sourceRevision"]:
        raise ApprovalDenied("issue or source revision changed")
    if (payload["sourceSceneSha256"] != current["sourceSceneSha256"]
            or payload["stageProject"] != current["stageProject"]):
        raise ApprovalDenied("stage identity or source scene changed")
    if payload["proposalSha256"] != manifest_digest(current):
        raise ApprovalDenied("proposal content changed")
    paths = payload["approvedPaths"]
    if not isinstance(paths, list) or not paths or len(paths) != len(set(map(str, paths))):
        raise ApprovalDenied("approval paths invalid or duplicated")
    candidates = {x["path"]: x for x in current["changes"]
                  if x["classification"] == "candidate-for-human-review"
                  and x["change"] == "added"}
    if any(not isinstance(path, str) or path not in candidates for path in paths):
        raise ApprovalDenied("noncandidate asset in approval")
    # Scene and both metadata files must be reviewed together.
    mandatory = {proposal.PROOF, proposal.PROOF + ".meta", proposal.FOLDER_META}
    if set(paths) != mandatory:
        raise ApprovalDenied("incomplete fixture/metadata approval")
    # An unexpected URP mutation remains quarantined even if explicitly named by a worker.
    return {"status": "verified-for-review-only", "nonce": nonce,
            "issue": current["issue"], "approvedPaths": sorted(paths),
            "copyback": False, "consumed": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--approval", required=True)
    ap.add_argument("--host-key", required=True)
    ap.add_argument("--spent-nonces", required=True)
    args = ap.parse_args()
    try:
        workspace = Path(args.workspace).resolve()
        host_key = Path(args.host_key).resolve()
        spent_file = Path(args.spent_nonces).resolve()
        if workspace == host_key or workspace in host_key.parents or workspace in spent_file.parents:
            raise ApprovalDenied("host authority cannot reside in worker workspace")
        key = safe_secret(host_key)
        if spent_file.is_symlink() or not spent_file.is_file():
            raise ApprovalDenied("host nonce ledger missing")
        ledger = json.loads(spent_file.read_text(encoding="utf-8"))
        if not isinstance(ledger, list) or any(not isinstance(x, str) or not NONCE.fullmatch(x) for x in ledger):
            raise ApprovalDenied("invalid host nonce ledger")
        approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
        current = proposal.manifest(workspace, Path(args.stage))
        output = validate(approval, current, key=key, spent=set(ledger))
        print(json.dumps(output, sort_keys=True))
        return 0
    except (ApprovalDenied, proposal.AuditDenied, OSError, ValueError, TypeError) as exc:
        print(f"C5b approval verification denied: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
