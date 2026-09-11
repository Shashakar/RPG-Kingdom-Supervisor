#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

MARKER = "<!-- rpgk-review-state\n"
DEFAULT_REPO = "Shashakar/RPG-Kingdom"


def parse_state(body: str) -> dict[str, Any] | None:
    start = body.find(MARKER)
    if start < 0:
        return None
    end = body.find("\n-->", start)
    if end < 0:
        return None
    try:
        value = json.loads(body[start + len(MARKER):end])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def collect(issue_number: int, repo: str | None = None) -> dict[str, Any]:
    repo = repo or os.environ.get("RPGK_REPO", DEFAULT_REPO)
    proc = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json", "labels,comments"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
    )
    if proc.returncode != 0:
        return {"available": False, "error": proc.stderr.strip() or proc.stdout.strip()}
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "error": f"invalid gh JSON: {exc}"}
    state = None
    for comment in reversed(raw.get("comments") or []):
        state = parse_state(str(comment.get("body") or ""))
        if state is not None:
            break
    labels = [item.get("name") for item in raw.get("labels", []) if item.get("name")]
    return {
        "available": True,
        "state": (state or {}).get("state"),
        "reviewCycle": (state or {}).get("reviewCycle"),
        "repairAttempts": (state or {}).get("repairAttempts"),
        "maxRepairAttempts": (state or {}).get("maxRepairAttempts"),
        "lastVerdict": (state or {}).get("lastVerdict"),
        "lastSummary": (state or {}).get("lastSummary"),
        "reason": (state or {}).get("reason"),
        "prNumber": (state or {}).get("prNumber"),
        "prHeadSha": (state or {}).get("prHeadSha"),
        "routingRecommendation": (state or {}).get("routingRecommendation"),
        "humanActionRequired": "symphony:human-review" in labels or "symphony:human-attention" in labels,
        "labels": labels,
    }
