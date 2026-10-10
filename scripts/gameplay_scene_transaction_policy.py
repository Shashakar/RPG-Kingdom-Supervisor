#!/usr/bin/env python3
"""Fail-closed authorization for a single staged gameplay-scene transaction.

Validates dispatch-scoped request intents. Does not execute Unity or attest scene
object mutations. The project executor must enforce protected scene roots and
output manifests independently before host copy-back.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import PurePosixPath
from typing import Any

TIERS = frozenset({"existing-scene-gameplay"})
ISSUE = re.compile(r"^GH-[1-9][0-9]*$")
BRANCH = re.compile(r"^codex/[A-Za-z0-9._/-]+$")
# Only typed reviewed operations; never an agent-supplied script or command.
OPERATIONS = frozenset({"compose-authored-opening-encounter"})


class AuthorizationError(ValueError):
    pass


def _path(value: Any, suffix: str | None = None) -> str:
    if not isinstance(value, str) or not value.startswith("Assets/") or "\\" in value or "\x00" in value:
        raise AuthorizationError("asset path must be under Assets/ and use forward slashes")
    path = PurePosixPath(value)
    if any(segment in ("", ".", "..") for segment in value.split("/")) or str(path) != value:
        raise AuthorizationError("asset path must be normalized without traversal")
    if suffix and not value.endswith(suffix):
        raise AuthorizationError(f"asset path must end in {suffix}")
    return value


def _root(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise AuthorizationError("scene hierarchy root must be nonempty")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise AuthorizationError("scene hierarchy root must be normalized")
    return value


def _array(obj: dict, key: str) -> list:
    value = obj.get(key)
    if not isinstance(value, list) or not value:
        raise AuthorizationError(f"{key} must be a nonempty array")
    return value


def validate(grant: dict[str, Any], request: dict[str, Any], *, issue: str, workspace: str, branch: str) -> dict[str, Any]:
    """Validate a request against a host-owned immutable grant.

    Callers must obtain the grant from the authenticated dispatch receipt, not
    from a file within the agent's writable worktree.
    """
    if not isinstance(grant, dict) or not isinstance(request, dict):
        raise AuthorizationError("grant and request must be objects")
    if not ISSUE.fullmatch(issue) or not BRANCH.fullmatch(branch) or not workspace:
        raise AuthorizationError("invalid authenticated dispatch context")
    for key, actual in (("issue", issue), ("workspace", workspace), ("branch", branch)):
        if grant.get(key) != actual:
            raise AuthorizationError(f"grant {key} differs from authenticated dispatch")
    if grant.get("protocolVersion") != 1 or request.get("protocolVersion") != 1:
        raise AuthorizationError("unsupported protocol")
    if grant.get("tier") not in TIERS or request.get("tier") != grant["tier"]:
        raise AuthorizationError("request tier is not authorized")
    scene = _path(grant.get("scene"), ".unity")
    if request.get("scene") != scene:
        raise AuthorizationError("request scene differs from authorized scene")
    if any(key in request for key in ("sourceScene", "sourcePrefab", "destinationPrefab", "script", "command", "executeMethod", "protectedCompositionPaths")):
        raise AuthorizationError("request contains an unapproved execution or scope override")

    roots = [_root(root) for root in _array(grant, "allowedRoots")]
    if len(roots) != len(set(roots)):
        raise AuthorizationError("duplicate approved roots")
    operations = _array(grant, "operations")
    if not all(isinstance(op, str) and op in OPERATIONS for op in operations):
        raise AuthorizationError("grant contains unsupported operation")
    request_ops = _array(request, "operations")
    if len(request_ops) != 1:
        raise AuthorizationError("only one bounded transaction operation is allowed")
    operation = request_ops[0]
    if not isinstance(operation, dict) or operation.get("kind") not in operations:
        raise AuthorizationError("operation not in authenticated grant")
    if any(field in operation for field in ("script", "command", "executeMethod", "componentType", "propertyPath", "objectPath", "targetScene")):
        raise AuthorizationError("operation attempts arbitrary code or field authoring")
    # Executor independently validates typed arguments, hierarchy scope,
    # protected roots, component wiring and functional correctness.
    return {"tier": grant["tier"], "scene": scene, "operation": operation["kind"], "allowedRoots": roots}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grant", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    try:
        with open(args.grant, encoding="utf-8") as stream:
            grant = json.load(stream)
        with open(args.request, encoding="utf-8") as stream:
            request = json.load(stream)
        print(json.dumps({"authorized": True, "scope": validate(grant, request, issue=args.issue, workspace=args.workspace, branch=args.branch)}, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, AuthorizationError) as exc:
        print(json.dumps({"authorized": False, "reason": str(exc)}, sort_keys=True))
        return 83


if __name__ == "__main__":
    raise SystemExit(main())
