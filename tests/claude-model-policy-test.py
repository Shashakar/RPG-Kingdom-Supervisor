#!/usr/bin/env python3
"""Focused tests for issue-label-driven Claude model routing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from claude_model_policy import ClaudeModelPolicyError, read_labels, resolve


class PolicyTest(unittest.TestCase):
    def test_defaults(self):
        for labels, model, effort in [
            ([], "sonnet", "medium"),
            (["risk:mechanical"], "haiku", "low"),
            (["risk:normal"], "sonnet", "medium"),
            (["risk:investigative"], "opus", "medium"),
            (["risk:architecture"], "opus", "high"),
            (["risk:end-to-end"], "opus", "high"),
            (["work:visual-unity"], "sonnet", "high"),
            (["agent:claude", "work:visual-unity"], "sonnet", "high"),
        ]:
            with self.subTest(labels=labels):
                route = resolve(labels)
                self.assertEqual((route["model"], route["effort"]), (model, effort))

    def test_independent_override(self):
        result = resolve(["risk:architecture", "claude-model:haiku", "claude-effort:low"])
        self.assertEqual(result["model"], "haiku")
        self.assertEqual(result["effort"], "low")
        self.assertEqual(result["modelSource"], "issue-label")
        self.assertEqual(result["effortSource"], "issue-label")
        override = resolve(["claude-model:opus"], cli_model="sonnet", cli_effort="medium")
        self.assertEqual((override["model"], override["effort"]), ("sonnet", "medium"))
        self.assertEqual(override["modelSource"], "operator-cli")

    def test_fail_closed_on_invalid_labels(self):
        for labels in [
            ["claude-model:opus", "claude-model:sonnet"],
            ["claude-effort:low", "claude-effort:high"],
            ["claude-model:unknown"],
            ["claude-effort:xhigh"],
            ["risk:normal", "risk:investigative"],
        ]:
            with self.subTest(labels=labels):
                with self.assertRaises(ClaudeModelPolicyError):
                    resolve(labels)

    def test_json_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "labels.json"
            p.write_text(json.dumps({"labels": [{"name": "claude-model:opus"}, {"name": "risk:architecture"}]}))
            self.assertEqual(read_labels(str(p)), ["claude-model:opus", "risk:architecture"])
            p.write_text('["CLAUDE-EFFORT:HIGH"]')
            self.assertEqual(read_labels(str(p)), ["claude-effort:high"])
            p.write_text('{"labels":"invalid"}')
            with self.assertRaises(ClaudeModelPolicyError):
                read_labels(str(p))


if __name__ == "__main__":
    unittest.main()
