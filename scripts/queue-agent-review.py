#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone

import review_orchestrator as review


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: queue-agent-review.py <issue-number> <pr-number> <head-sha>", file=sys.stderr)
        return 64
    issue_number = int(sys.argv[1])
    pr_number = int(sys.argv[2])
    head_sha = sys.argv[3].strip()
    prior = review.latest_state(issue_number)
    state = dict(prior)
    state.update(
        {
            "state": "agent_review",
            "issue": issue_number,
            "prNumber": pr_number,
            "prHeadSha": head_sha,
            "repairAttempts": int(prior.get("repairAttempts", 0)),
            "maxRepairAttempts": int(prior.get("maxRepairAttempts", review.MAX_REPAIRS)),
            "history": prior.get("history") if isinstance(prior.get("history"), list) else [],
            "reason": "none",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    review.persist_state(
        issue_number,
        state,
        f"Implementation/repair handoff completed for PR #{pr_number} at `{head_sha}`; independent automated review is queued.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
