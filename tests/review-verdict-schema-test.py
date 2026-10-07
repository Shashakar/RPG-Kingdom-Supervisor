#!/usr/bin/env python3
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / "schemas" / "review-verdict.schema.json").read_text(encoding="utf-8"))
assessment = schema["properties"]["human_rework_assessment"]

assert "oneOf" not in assessment
assert assessment["type"] == ["object", "null"]
assert assessment["additionalProperties"] is False
assert set(assessment["required"]) == {
    "baseline_head", "current_head", "directives_satisfied", "evidence_paths"
}
print("review-verdict-schema-test: PASS")
