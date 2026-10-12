#!/usr/bin/env python3
"""C5c host-operator review envelope issuer. Never promotes or copies assets.

Run manually in an operator terminal on a trusted host; not from Codex/Symphony.
The host signs only a fresh, clean, exact C4 proposal and persists issuance
evidence, but DOES NOT consume a promotion nonce.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys

spec = importlib.util.spec_from_file_location(
    "c5b_approval_verifier", Path(__file__).with_name("unity-stage-approval-verify.py"))
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)

class IssuanceDenied(Exception):
    pass


def reject_path(path: Path, worker: Path, description: str) -> Path:
    if path.is_symlink():
        raise IssuanceDenied(f"linked {description} forbidden")
    resolved = path.resolve()
    if resolved == worker or worker in resolved.parents:
        raise IssuanceDenied(f"{description} inside GH worker workspace forbidden")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise IssuanceDenied(f"{description} parent unavailable or linked")
    if path.parent.stat().st_mode & 0o022:
        raise IssuanceDenied(f"{description} parent writable by group/other")
    return resolved


def read_ledger(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise IssuanceDenied("host issuance ledger unavailable or insecure")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IssuanceDenied("invalid host issuance ledger") from exc
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "issued"} or data["schemaVersion"] != 1:
        raise IssuanceDenied("invalid issuance ledger structure")
    if not isinstance(data["issued"], list) or any(
        not isinstance(x, dict) or set(x) != {"nonce", "proposalSha256", "issue", "reviewer", "issuedAt"}
        or not isinstance(x["nonce"], str) or not verify.NONCE.fullmatch(x["nonce"])
        for x in data["issued"]
    ):
        raise IssuanceDenied("invalid issuance ledger entries")
    return data


def atomic_record(path: Path, data: dict) -> None:
    # Write once by rename in the same private host-owned directory.
    temp = path.with_name(path.name + ".tmp." + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def build_envelope(workspace: Path, stage: Path, reviewer: str, key: bytes,
                   *, now: datetime, nonce: str) -> tuple[dict, dict]:
    current = verify.proposal.manifest(workspace, stage)
    if not verify.ACTOR.fullmatch(reviewer) or not verify.NONCE.fullmatch(nonce):
        raise IssuanceDenied("invalid reviewer or nonce")
    expected = {verify.proposal.PROOF, verify.proposal.PROOF + ".meta",
                verify.proposal.FOLDER_META}
    available = {x["path"] for x in current["changes"]
                 if x["change"] == "added"
                 and x["classification"] == "candidate-for-human-review"}
    if available != expected:
        raise IssuanceDenied("exact C4 scene and metadata candidates not available")
    payload = {
        "schemaVersion": 1, "purpose": "c5b-review-verification-only",
        "issue": current["issue"], "reviewer": reviewer,
        "sourceRevision": current["sourceRevision"],
        "sourceSceneSha256": current["sourceSceneSha256"],
        "stageProject": current["stageProject"],
        "proposalSha256": verify.manifest_digest(current),
        "approvedPaths": sorted(expected), "nonce": nonce,
        "issuedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=30)).isoformat(),
    }
    envelope = {"payload": payload,
                "mac": hmac.new(key, verify.canonical(payload), hashlib.sha256).hexdigest()}
    if verify.validate(envelope, current, key=key, spent=set(), now=now)["status"] != "verified-for-review-only":
        raise IssuanceDenied("self-check failed")
    return current, envelope


def issue(args, *, input_fn=input, is_tty=None) -> dict:
    if is_tty is None:
        is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if not is_tty:
        raise IssuanceDenied("interactive operator terminal required")
    workspace = Path(args.workspace).resolve()
    if workspace.name != args.issue:
        raise IssuanceDenied("issue/workspace mismatch")
    # Use a host-controlled private directory. No credentials in worker tree.
    keypath = reject_path(Path(args.host_key), workspace, "signing key")
    ledgerpath = reject_path(Path(args.issuance_ledger), workspace, "issuance ledger")
    outputpath = reject_path(Path(args.output), workspace, "signed output")
    if outputpath.exists():
        raise IssuanceDenied("signed output must not already exist")
    if len({str(x) for x in (keypath, ledgerpath, outputpath)}) != 3:
        raise IssuanceDenied("host authority paths must be distinct")
    key = verify.safe_secret(keypath)
    if not ledgerpath.exists():
        raise IssuanceDenied("initialize private host issuance ledger before issuing")
    lockpath = reject_path(ledgerpath.with_suffix(ledgerpath.suffix + ".lock"), workspace, "host issuance lock")
    with lockpath.open("a+", encoding="utf-8") as lock:
        if lockpath.stat().st_mode & 0o077:
            raise IssuanceDenied("host issuance lock permissions too broad")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        ledger = read_ledger(ledgerpath)
        now = datetime.now(timezone.utc)
        nonce = secrets.token_hex(24)
        if any(entry["nonce"] == nonce for entry in ledger["issued"]):
            raise IssuanceDenied("duplicate approval nonce")
        current, envelope = build_envelope(workspace, Path(args.stage), args.reviewer,
                                           key, now=now, nonce=nonce)
        print("C5c REVIEW ONLY — no copyback or authority to promote.")
        print("Issue:", current["issue"], "Source:", current["sourceRevision"])
        print("Stage:", current["stageProject"])
        print("Manifest SHA-256:", envelope["payload"]["proposalSha256"])
        for change in current["changes"]:
            print(" ", change["classification"], change["change"], change["path"],
                  (change["stage"] or {}).get("sha256", "-"))
        answer = input_fn("Type exact issue ID to attest review (or anything else to cancel): ")
        if answer != current["issue"]:
            raise IssuanceDenied("operator declined review")
        # Recompute source and stage after potentially lengthy human review.
        if verify.manifest_digest(verify.proposal.manifest(workspace, Path(args.stage))) != envelope["payload"]["proposalSha256"]:
            raise IssuanceDenied("source/stage changed during review")
        if not lockpath.is_file():
            raise IssuanceDenied("host lock missing")
        fd = os.open(outputpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            ledger["issued"].append({
                "nonce": nonce, "proposalSha256": envelope["payload"]["proposalSha256"],
                "issue": current["issue"], "reviewer": args.reviewer,
                "issuedAt": envelope["payload"]["issuedAt"]
            })
            atomic_record(ledgerpath, ledger)
        except Exception:
            outputpath.unlink(missing_ok=True)
            raise
        return {"status": "host-review-envelope-issued", "issue": current["issue"],
                "output": str(outputpath), "nonce": nonce,
                "promotionAuthorized": False, "copyback": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--issue", required=True)
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--host-key", required=True)
    ap.add_argument("--issuance-ledger", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    try:
        print(json.dumps(issue(args), sort_keys=True))
        return 0
    except (IssuanceDenied, verify.ApprovalDenied, verify.proposal.AuditDenied,
            OSError, TypeError, ValueError) as exc:
        print(f"C5c approval issuer denied: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
