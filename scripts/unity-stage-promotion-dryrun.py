#!/usr/bin/env python3
"""C5d transaction-design dry run: NO real copyback, nonce consumption or writes.

This executable specification makes an independently reviewed C5b envelope
into an exact-path promotion PLAN and exercises simulated transaction failure
and rollback. It is not a production promotion command, broker, or authorization.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    "c5d_review_verify", Path(__file__).with_name("unity-stage-approval-verify.py"))
assert spec and spec.loader
review = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = review
spec.loader.exec_module(review)

CHECKPOINTS = (
    "exclusive-lock", "stage-quiescent", "rehash-before-write", "reserve-nonce",
    "durable-backup", "virtual-copy", "unity-validation", "finalize",
)


class PromotionDryRunDenied(Exception):
    pass


class InjectedFailure(Exception):
    """Only affects in-memory simulation. Never represents an actual OS crash."""


def exact_plan(current: dict, envelope: dict, *, key: bytes,
               spent_nonces: set[str]) -> dict:
    """Reject any imprecise proposed asset set; all real copies remain disabled."""
    checked = review.validate(envelope, current, key=key, spent=spent_nonces)
    if (current.get("schemaVersion") != 1 or current.get("mode") != "proposal-only"
            or current.get("authorization") is not None or current.get("copyback") is not False):
        raise PromotionDryRunDenied("proposal must be read-only and unapproved")
    expected = {review.proposal.PROOF, review.proposal.PROOF + ".meta",
                review.proposal.FOLDER_META}
    if set(checked["approvedPaths"]) != expected:
        raise PromotionDryRunDenied("unexpected approved asset set")
    by_path = {entry["path"]: entry for entry in current["changes"]}
    if len(by_path) != len(current["changes"]):
        raise PromotionDryRunDenied("duplicate changed file path")
    entries = []
    guids = set()
    for rel in sorted(expected):
        entry = by_path.get(rel)
        if not isinstance(entry, dict) or entry.get("change") != "added" or entry.get("source") is not None:
            raise PromotionDryRunDenied("approved asset not a new stage-only file")
        stage = entry.get("stage")
        if not isinstance(stage, dict) or not isinstance(stage.get("sha256"), str):
            raise PromotionDryRunDenied("missing approved stage hash")
        if not review.HEXDIGEST.fullmatch(stage["sha256"]) or not isinstance(stage.get("bytes"), int) or stage["bytes"] < 1:
            raise PromotionDryRunDenied("invalid stage file size or SHA-256")
        if rel.endswith(".meta"):
            guid = stage.get("guid")
            if not isinstance(guid, str) or not review.proposal.GUID.fullmatch("guid: " + guid):
                raise PromotionDryRunDenied("missing or malformed approved Unity GUID")
            if guid in guids:
                raise PromotionDryRunDenied("duplicate approved Unity GUID")
            guids.add(guid)
        entries.append({"path": rel, "sha256": stage["sha256"],
                        "bytes": stage["bytes"],
                        "guid": stage.get("guid") if rel.endswith(".meta") else None,
                        "expectedSource": None})
    # The manifest includes *all* unexpected stage changes in its signed digest.
    quarantined = sorted(entry["path"] for entry in current["changes"]
                         if entry["path"] not in expected)
    if any(entry["classification"] != "quarantined"
           for entry in current["changes"] if entry["path"] not in expected):
        raise PromotionDryRunDenied("unexpected unclassified stage changes")
    if current["sourceSceneSha256"] != current["stageSceneSha256"]:
        raise PromotionDryRunDenied("protected original scene differs")
    return {
        "schemaVersion": 1, "mode": "dry-run-only", "issue": current["issue"],
        "sourceRevision": current["sourceRevision"],
        "sourceSceneSha256": current["sourceSceneSha256"],
        "stageProject": current["stageProject"],
        "proposalSha256": review.manifest_digest(current),
        "nonce": checked["nonce"], "candidateWrites": entries,
        "quarantinedExclusions": quarantined,
        "requires": [
            "separate operator-authenticated promotion grant",
            "exclusive host stage and source locks",
            "persistent Editor quiescence and process census",
            "stable stage snapshot plus source/stage rehash at point of use",
            "durable atomic nonce reservation and recovery journal",
            "atomic copy with GUID and Unity import validation",
            "human-approved PR merge",
        ],
        "promotionAuthorized": False, "copyback": False,
    }


def simulate(plan: dict, inject: str | None = None) -> dict:
    """A fully in-memory simulation. Every failure restores the virtual baseline.

    An actual host transaction MUST implement durable locks/journals separately.
    """
    if inject is not None and inject not in CHECKPOINTS:
        raise PromotionDryRunDenied("unknown failure checkpoint")
    if plan.get("mode") != "dry-run-only" or plan.get("copyback") is not False:
        raise PromotionDryRunDenied("no production promotion plan accepted")
    # This is a virtual destination; no Path.open, write, rename, git or Unity calls.
    before = {file["path"]: file["expectedSource"] for file in plan["candidateWrites"]}
    destination = copy.deepcopy(before)
    trace = []
    nonce_reserved_in_simulation = False
    try:
        for checkpoint in CHECKPOINTS:
            trace.append(checkpoint)
            if inject == checkpoint:
                raise InjectedFailure(checkpoint)
            if checkpoint == "reserve-nonce":
                nonce_reserved_in_simulation = True
            elif checkpoint == "virtual-copy":
                for file in plan["candidateWrites"]:
                    destination[file["path"]] = file["sha256"]
            elif checkpoint == "unity-validation":
                for file in plan["candidateWrites"]:
                    if destination[file["path"]] != file["sha256"]:
                        raise PromotionDryRunDenied("virtual fixture validation mismatch")
        phase = "simulated-commit"
    except InjectedFailure:
        # Restoration is simulated in memory; never describe this as a disk backup.
        destination = copy.deepcopy(before)
        phase = "simulated-rollback"
    if phase == "simulated-rollback" and destination != before:
        raise PromotionDryRunDenied("virtual rollback mismatch")
    return {
        "status": phase, "injectedFailure": inject, "checkpointTrace": trace,
        "virtualSourceBefore": before, "virtualSourceAfter": destination,
        "virtualSourceRestored": destination == before,
        "simulatedNonceReserved": nonce_reserved_in_simulation,
        "realNonceConsumed": False, "realFilesWritten": 0,
        "simulationOnly": True, "copyback": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--host-key", required=True)
    parser.add_argument("--spent-nonces", required=True)
    parser.add_argument("--inject-failure", choices=CHECKPOINTS)
    args = parser.parse_args()
    try:
        workspace = Path(args.workspace).resolve()
        host_key = Path(args.host_key)
        ledger_path = Path(args.spent_nonces)
        if host_key.is_symlink() or ledger_path.is_symlink():
            raise PromotionDryRunDenied("linked host authority path")
        for path in (host_key.resolve(), ledger_path.resolve()):
            if workspace == path or workspace in path.parents:
                raise PromotionDryRunDenied("host authority inside issue workspace")
        key = review.safe_secret(host_key)
        if not ledger_path.is_file() or ledger_path.stat().st_mode & 0o077:
            raise PromotionDryRunDenied("insecure or absent host nonce ledger")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if not isinstance(ledger, list) or any(
            not isinstance(x, str) or not review.NONCE.fullmatch(x) for x in ledger
        ):
            raise PromotionDryRunDenied("invalid host nonce ledger")
        envelope = json.loads(Path(args.approval).read_text(encoding="utf-8"))
        current = review.proposal.manifest(workspace, Path(args.stage))
        plan = exact_plan(current, envelope, key=key, spent_nonces=set(ledger))
        result = {"plan": plan, "simulation": simulate(plan, args.inject_failure)}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PromotionDryRunDenied, review.ApprovalDenied, review.proposal.AuditDenied,
            OSError, ValueError, TypeError) as exc:
        print(f"C5d dry-run denied: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
