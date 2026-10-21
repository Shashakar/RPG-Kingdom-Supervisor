#!/usr/bin/env python3
"""Provider provenance and separate quota snapshots without live subscriptions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import claude_quota  # noqa: E402
import supervisor_provider  # noqa: E402
import supervisor_telemetry  # noqa: E402


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "RPGK_SUPERVISOR_STATE_ROOT": str(self.root),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_gh6_smoke_shows_claude_haiku_not_codex_risk(self):
        labels = ("agent:claude", "risk:mechanical", "claude-model:haiku", "claude-effort:low")
        pre = supervisor_provider.resolve(labels=labels, issue=6, repo="Shashakar/Rekindle-Claude")
        self.assertEqual(pre["provider"], "claude")
        self.assertEqual(pre["model"], "haiku")
        self.assertFalse(pre["executed"])
        journal_dir = self.root / "claude-dispatch"
        journal_dir.mkdir()
        (journal_dir / "GH-6.json").write_text(json.dumps({
            "issue": 6, "branch": "claude/gh-6-implementation", "stage": "review",
            "model": "haiku", "effort": "low", "reportedCostUsd": 0.62,
        }))
        executed = supervisor_provider.resolve(labels=labels, issue=6, repo="Shashakar/Rekindle-Claude")
        self.assertEqual(executed["source"], "claude-dispatch")
        self.assertTrue(executed["executed"])
        self.assertEqual((executed["provider"], executed["model"], executed["effort"]),
                         ("claude", "haiku", "low"))
        other = supervisor_provider.resolve(labels=labels, issue=6, repo="Shashakar/RPG-Kingdom")
        self.assertFalse(other["executed"])

    def test_codex_execution_beats_reassigned_label(self):
        values = supervisor_provider.resolve(
            labels=["agent:claude", "claude-model:haiku"],
            issue=9, repo="Shashakar/Rekindle-Claude",
            worker={"runId": "GH-9-implementation-1", "role": "implementation",
                    "model": "gpt-6-sol", "effort": "high"},
        )
        self.assertEqual(values["provider"], "codex")
        self.assertEqual(values["source"], "worker-telemetry")
        self.assertTrue(values["executed"])

    def test_pilot_requires_exact_repository_branch_and_completed_status(self):
        pilot = self.root / "claude-pilot/GH-6"
        pilot.mkdir(parents=True)
        sample = {"issue": "GH-6", "repository": "Shashakar/Rekindle-Claude",
                  "branch": "claude/gh-6-implementation", "status": "completed",
                  "requestedModel": "haiku", "requestedEffort": "low", "reportedCostUsd": 1.2}
        f = pilot / "latest.json"
        f.write_text(json.dumps(sample))
        self.assertTrue(supervisor_provider.claude_execution(6, "Shashakar/Rekindle-Claude")["executed"])
        for change in ({"branch": "claude/gh-7-implementation"}, {"status": "dry-run"},
                       {"repository": "Shashakar/RPG-Kingdom"}):
            f.write_text(json.dumps({**sample, **change}))
            self.assertEqual(supervisor_provider.claude_execution(6, "Shashakar/Rekindle-Claude"), {})
        f.unlink()
        f.symlink_to(self.root / "other.json")
        self.assertEqual(supervisor_provider.claude_execution(6, "Shashakar/Rekindle-Claude"), {})

    def test_conflicting_agent_labels_do_not_claim_executed(self):
        item = supervisor_provider.resolve(labels=["agent:codex", "agent:claude"])
        self.assertIsNone(item["provider"])
        self.assertFalse(item["executed"])

    def test_claude_is_distinct_from_codex_in_operations(self):
        codex = self.root / "usage"
        codex.mkdir(parents=True)
        (codex / "current.json").write_text(json.dumps({
            "status": "available", "rateLimits": {"primary": {"remainingPercent": 79}},
        }))
        data = supervisor_telemetry.collect_operations()
        self.assertEqual(data["providerQuotas"]["codex"]["rateLimits"]["primary"]["remainingPercent"], 79)
        self.assertEqual(data["providerQuotas"]["claude"]["status"], "unavailable")
        self.assertEqual(data["quota"], data["providerQuotas"]["codex"])
        self.assertNotIn("rateLimits", data["providerQuotas"]["claude"])

    def test_manual_claude_quota_is_distinct_stale_and_never_derived_from_cost(self):
        unavailable = claude_quota.current_quota()
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIn("non-interactive", unavailable["reason"])
        path = claude_quota.record(61.5, 83, None, None)
        current = claude_quota.current_quota()
        self.assertEqual(current["status"], "available")
        self.assertEqual(current["source"], "operator:claude-code-/usage")
        self.assertEqual(current["rateLimits"]["primary"]["remainingPercent"], 61.5)
        record = json.loads(path.read_text())
        record["observedAt"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps(record))
        self.assertEqual(claude_quota.current_quota()["status"], "stale")
        record["observedAt"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        path.write_text(json.dumps(record))
        self.assertEqual(claude_quota.current_quota()["status"], "unavailable")
        record["source"] = "claude-cli:result"
        path.write_text(json.dumps(record))
        self.assertNotEqual(claude_quota.current_quota()["status"], "available")

    def test_reject_invalid_quota_inputs_without_writing(self):
        for value in (-1, 101, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                claude_quota.record(value, 20, None, None)
        with self.assertRaises(ValueError):
            claude_quota.record(80, 50, "yesterday", None)
        self.assertFalse(claude_quota.snapshot_path().exists())

    def test_last_claude_pilot_cost_never_becomes_quota(self):
        path = self.root / "claude-pilot/GH-6"
        path.mkdir(parents=True)
        (path / "latest.json").write_text(json.dumps({
            "issue": "GH-6", "repository": "Shashakar/Rekindle-Claude",
            "status": "completed", "requestedModel": "haiku",
            "reportedCostUsd": 0.74,
        }))
        usage = claude_quota.recent_pilot_usage()
        self.assertEqual(usage["latest"]["reportedCostUsd"], 0.74)
        self.assertEqual(usage["status"], "available")
        self.assertEqual(claude_quota.current_quota()["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
