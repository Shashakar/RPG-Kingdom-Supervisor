#!/usr/bin/env python3
"""Operator-invoked C5f broker drain, never a frozen-stage or promotion grant.

Activation calls the C5e owner/consistency preflight and publishes an
exclusive WSL broker-dispatch drain marker. Windows Unity Editor C# remains
capable of modifying the disposable stage until separately stopped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys

BASE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    assert spec and spec.loader
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


gate = load("c5f_operator_drain_gate", "unity-stage-drain-gate.py")
preflight = load("c5f_operator_host_preflight", "unity-stage-host-preflight.py")


class HandoffDenied(Exception):
    pass


def activate(workspace: Path, state_root: Path) -> dict:
    # Preliminary check before acquiring lock is only advisory; the second
    # checks happen under the same WSL flock used for broker Popen.
    baseline = preflight.host_inspection(workspace, state_root)
    if (baseline["status"] != "inspection-consistent-not-quiescent"
            or baseline["stageFrozen"] is not False
            or baseline["promotionAuthorized"] is not False):
        raise HandoffDenied("unexpected C5e ownership result")
    with gate.locked(state_root) as directory:
        if gate.read_locked(directory) is not None:
            raise HandoffDenied("another operator stage drain is active")
        # Prevent an operation starting between the consistency observation
        # and gate activation. Broker serializes start_operation under flock.
        preflight.broker_idle(state_root, workspace.resolve())
        preflight.lease_absent(state_root)
        checked_ws, revision = preflight.c3.ro.workspace_info(str(workspace))
        if revision != baseline["workspaceRevision"] or checked_ws.name != baseline["issue"]:
            raise HandoffDenied("source revision or issue changed before drain")
        new_preflight = preflight.c3.persistent_preflight(checked_ws, revision, state_root)
        if (new_preflight["unityPid"] != baseline["unityPid"]
                or new_preflight["stagePath"] != baseline["stagePath"]):
            raise HandoffDenied("owned Unity Editor changed before drain")
        stage, _ = preflight.c3.ro.stage_info(baseline["stagePath"])
        new_manifest = preflight.proposal.manifest(checked_ws, stage)
        if preflight.review.manifest_digest(new_manifest) != baseline["proposalSha256"]:
            raise HandoffDenied("stage changed between inspection and drain gate")
        stamp = {
            "schemaVersion": 1, "phase": "draining",
            "issue": baseline["issue"], "workspace": str(checked_ws),
            "workspaceRevision": revision, "stagePath": baseline["stagePath"],
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "operationId": secrets.token_hex(16),
        }
        gate.write_locked(directory, stamp)
    return {
        "status": "broker-dispatch-draining-not-frozen",
        "issue": stamp["issue"], "operationId": stamp["operationId"],
        "brokerDispatchGateActive": True,
        "unityEditorStillRunningAtHandoff": True,
        "independentHumanAuthorization": False,
        "stageFrozen": False, "promotionAuthorized": False,
        "copyback": False,
    }


def cancel(issue: str, operation_id: str, state_root: Path) -> dict:
    if not gate.ISSUE.fullmatch(issue) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise HandoffDenied("invalid exact owner or operation ID")
    with gate.locked(state_root) as directory:
        current = gate.read_locked(directory)
        if current is None or current["issue"] != issue or current["operationId"] != operation_id:
            raise HandoffDenied("drain owner or operation ID mismatch")
        # This is procedural cleanup of a dispatch gate, NOT independent
        # operator authentication. No snapshot/promotion service exists yet.
        broker = state_root / "unity-broker/status.json"
        if broker.is_symlink() or not broker.is_file():
            raise HandoffDenied("Unity broker status unavailable; restart and reconcile")
        data = json.loads(broker.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("protocolVersion") != 1
                or data.get("state") not in ("draining", "ready")
                or data.get("activeRequest") is not None
                or data.get("workspaceRoot") != str(Path(current["workspace"]).parent)):
            raise HandoffDenied("broker is not demonstrably idle; refuse drain cancel")
        broker_pid = data.get("pid")
        if type(broker_pid) is not int or broker_pid <= 0:
            raise HandoffDenied("invalid broker PID")
        try:
            os.kill(broker_pid, 0)
        except OSError as exc:
            raise HandoffDenied("broker PID is not live") from exc
        gate.cancel_locked(directory, operation_id)
    return {
        "status": "broker-dispatch-drain-cancelled",
        "issue": issue, "brokerDispatchGateActive": False,
        "stageFrozen": False, "independentHumanAuthorization": False,
        "promotionAuthorized": False, "copyback": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("activate", "cancel"))
    p.add_argument("--workspace")
    p.add_argument("--issue")
    p.add_argument("--operation-id")
    p.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT",
        str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    args = p.parse_args()
    try:
        if args.action == "activate":
            if not args.workspace or args.issue or args.operation_id:
                raise HandoffDenied("activation requires --workspace only")
            result = activate(Path(args.workspace), Path(args.state_root))
        else:
            if not args.issue or not args.operation_id or args.workspace:
                raise HandoffDenied("cancel requires --issue and --operation-id only")
            result = cancel(args.issue, args.operation_id, Path(args.state_root))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (HandoffDenied, gate.DrainDenied, preflight.InspectionDenied,
            preflight.c3.ro.Denied, preflight.proposal.AuditDenied,
            OSError, TypeError, ValueError, KeyError) as exc:
        print(f"C5f operator drain denied: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
