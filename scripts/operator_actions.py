#!/usr/bin/env python3
"""Narrow localhost operator actions for the Supervisor dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

REPO = os.environ.get("RPGK_REPO", "Shashakar/RPG-Kingdom")
ROOT = Path(__file__).resolve().parent.parent
STATE_ROOT = Path(os.path.expanduser(os.environ.get("RPGK_SUPERVISOR_STATE_ROOT", "~/.local/state/rpg-kingdom-supervisor")))
ISSUE = re.compile(r"^GH-(\d+)$")


class ActionError(RuntimeError):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def _issue_number(identifier: str) -> int:
    match = ISSUE.fullmatch(str(identifier).upper())
    if not match:
        raise ActionError("invalid issue identifier")
    return int(match.group(1))


def _json(args: list[str]) -> dict[str, Any]:
    result = _run(args)
    if result.returncode != 0:
        raise ActionError(result.stderr.strip() or result.stdout.strip() or "host action failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ActionError("host action returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ActionError("host action returned invalid payload")
    return value


def _labels(issue: int) -> set[str]:
    payload = _json(["gh", "issue", "view", str(issue), "--repo", REPO, "--json", "labels,state"])
    if payload.get("state") != "OPEN":
        raise ActionError("issue is no longer open")
    return {str(x.get("name")) for x in payload.get("labels") or [] if isinstance(x, dict)}


def rearm(identifier: str, *, allow_below_reserve: bool = False) -> dict[str, Any]:
    issue = _issue_number(identifier)
    labels = _labels(issue)
    if "symphony:halted" not in labels:
        raise ActionError("issue is not currently halted; refresh before rearming")
    if allow_below_reserve:
        path = STATE_ROOT / "operator-overrides" / f"GH-{issue}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "protocolVersion": 1,
            "issue": f"GH-{issue}",
            "kind": "below-reserve-continuation",
            "approvedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temp, path)
    result = _run([str(ROOT / "scripts" / "rearm-issue.sh"), str(issue)])
    if result.returncode != 0:
        raise ActionError(result.stderr.strip() or result.stdout.strip() or "rearm failed")
    return {"ok": True, "issue": f"GH-{issue}", "allowBelowReserve": allow_below_reserve}


def merge(identifier: str, pr_number: int, expected_head_sha: str) -> dict[str, Any]:
    issue = _issue_number(identifier)
    labels = _labels(issue)
    if "symphony:human-review" not in labels:
        raise ActionError("issue is not waiting for manual validation")
    if not isinstance(pr_number, int) or pr_number <= 0 or not re.fullmatch(r"[0-9a-f]{40}", expected_head_sha or ""):
        raise ActionError("invalid reviewed PR generation")
    pr = _json(["gh", "pr", "view", str(pr_number), "--repo", REPO, "--json", "headRefOid,state,mergeable,statusCheckRollup"])
    if pr.get("state") != "OPEN" or pr.get("headRefOid") != expected_head_sha:
        raise ActionError("PR head changed; refresh and validate the new generation")
    if pr.get("mergeable") != "MERGEABLE":
        raise ActionError("PR is not currently mergeable")
    checks = pr.get("statusCheckRollup") or []
    bad = [c for c in checks if isinstance(c, dict) and str(c.get("conclusion") or c.get("state") or "").upper() not in {"SUCCESS","NEUTRAL","SKIPPED"}]
    if bad:
        raise ActionError("required PR checks are not green")
    result = _run(["gh", "pr", "merge", str(pr_number), "--repo", REPO, "--squash", "--match-head-commit", expected_head_sha, "--delete-branch=false"])
    if result.returncode != 0:
        raise ActionError(result.stderr.strip() or result.stdout.strip() or "merge failed")
    return {"ok": True, "issue": f"GH-{issue}", "prNumber": pr_number, "headSha": expected_head_sha}
