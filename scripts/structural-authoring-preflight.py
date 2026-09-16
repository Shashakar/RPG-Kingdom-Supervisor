#!/usr/bin/env python3
"""Evaluate explicit structural scene-authoring requirements against project capabilities."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
MARKER = "symphony-scene-authoring-requirements"
MARKER_RE = re.compile(
    rf"<!--\s*{re.escape(MARKER)}\s*(\{{.*?\}})\s*-->",
    re.IGNORECASE | re.DOTALL,
)
VALID_MODES = {"known", "deferred"}


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return [item.strip() for item in value]


def parse_requirements(body: str) -> dict[str, Any] | None:
    matches = MARKER_RE.findall(body or "")
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"exactly one {MARKER} block is allowed")
    try:
        raw = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {MARKER} JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("scene-authoring requirements must be a JSON object")
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError("mode must be 'known' or 'deferred'")
    tier = str(raw.get("tier") or "").strip()
    if not tier:
        raise ValueError("tier is required")
    source_phase_only = bool(raw.get("sourcePhaseOnly", False))
    if mode == "deferred" and not source_phase_only:
        raise ValueError("deferred requirements must set sourcePhaseOnly=true")
    return {
        "mode": mode,
        "tier": tier,
        "sourcePhaseOnly": source_phase_only,
        "operations": _strings(raw.get("operations"), "operations"),
        "componentAdditions": _strings(raw.get("componentAdditions"), "componentAdditions"),
        "componentRemovals": _strings(raw.get("componentRemovals"), "componentRemovals"),
        "dependency": str(raw.get("dependency") or "").strip() or None,
    }


def evaluate(
    body: str,
    contract: dict[str, Any] | None,
    *,
    revision: str | None,
    contract_path: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "authorizationTier": "mechanical-structural",
        "contractPath": contract_path,
        "contractRevision": revision or None,
    }
    try:
        requirements = parse_requirements(body)
    except ValueError as exc:
        return {
            **base,
            "status": "invalid",
            "supported": False,
            "reason": str(exc),
            "requirements": None,
        }
    if requirements is None:
        return {
            **base,
            "status": "unknown",
            "supported": False,
            "reason": f"missing explicit {MARKER} block",
            "requirements": None,
            "recommendedNextAction": "Add an explicit known or deferred structural requirements block before redispatch.",
        }
    base["requirements"] = requirements
    if not isinstance(contract, dict):
        return {
            **base,
            "status": "contract_unavailable",
            "supported": False,
            "reason": "project scene-authoring capability contract is missing or unreadable",
            "recommendedNextAction": "Restore the project-owned capability contract before redispatch.",
        }

    base["contractSchemaVersion"] = contract.get("schemaVersion")
    base["supportedProtocolVersions"] = contract.get("supportedProtocolVersions") or []
    tiers = set(contract.get("supportedTiers") or [])
    operations_by_tier: dict[str, set[str]] = {}
    for entry in contract.get("operationKindsByTier") or []:
        if isinstance(entry, dict) and isinstance(entry.get("tier"), str):
            operations_by_tier[entry["tier"]] = set(entry.get("operationKinds") or [])
    additions = set(contract.get("structuralComponentAdditions") or [])
    removals = set(contract.get("componentRemovals") or [])

    unsupported: list[dict[str, str]] = []
    tier = requirements["tier"]
    if tier not in tiers:
        unsupported.append({"kind": "tier", "value": tier})
    supported_ops = operations_by_tier.get(tier, set())
    for operation in requirements["operations"]:
        if operation not in supported_ops:
            unsupported.append({"kind": "operation", "value": operation})

    if requirements["mode"] == "deferred":
        if unsupported:
            return {
                **base,
                "status": "unsupported",
                "supported": False,
                "authoringAuthorized": False,
                "unsupported": unsupported,
                "reason": "known deferred-phase tier/operation requirements are unsupported",
                "recommendedNextAction": requirements.get("dependency")
                or "Resolve the unsupported project authoring capability before source-phase continuation.",
            }
        return {
            **base,
            "status": "deferred",
            "supported": True,
            "authoringAuthorized": False,
            "unsupported": [],
            "reason": "source phase may proceed, but production structural authoring remains intentionally unavailable",
            "recommendedNextAction": requirements.get("dependency")
            or "Implement source only, merge concrete types, then review/extend the project allowlist before scene wiring.",
        }

    for component in requirements["componentAdditions"]:
        if component not in additions:
            unsupported.append({"kind": "component-addition", "value": component})
    for component in requirements["componentRemovals"]:
        if component not in removals:
            unsupported.append({"kind": "component-removal", "value": component})

    if unsupported:
        return {
            **base,
            "status": "unsupported",
            "supported": False,
            "authoringAuthorized": False,
            "unsupported": unsupported,
            "reason": "one or more declared structural authoring requirements are not supported by the project contract",
            "recommendedNextAction": requirements.get("dependency")
            or "Land a separately reviewed project allowlist/capability extension, then explicitly rearm.",
        }

    return {
        **base,
        "status": "supported",
        "supported": True,
        "authoringAuthorized": True,
        "unsupported": [],
        "reason": "all declared structural authoring requirements are supported by the project contract",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-body-file", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--output")
    args = parser.parse_args()

    body = Path(args.issue_body_file).read_text(encoding="utf-8")
    contract_path = Path(args.contract)
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        contract = None

    result = evaluate(
        body,
        contract,
        revision=args.revision,
        contract_path=str(contract_path),
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result.get("status") in {"supported", "deferred"} else 78


if __name__ == "__main__":
    raise SystemExit(main())
