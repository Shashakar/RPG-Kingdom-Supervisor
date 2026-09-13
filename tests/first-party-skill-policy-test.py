#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "first-party-skill-policy.py"
spec = importlib.util.spec_from_file_location("first_party_skill_policy", SCRIPT)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

old_env = os.environ.copy()
try:
    os.environ["RPGK_SUPERVISOR_ROOT"] = str(ROOT)
    expected = {
        "risk:mechanical": "rpgk-mechanical-change",
        "risk:investigative": "rpgk-investigate-bug",
        "risk:architecture": "rpgk-architecture-change",
        "risk:end-to-end": "rpgk-end-to-end-change",
    }
    for risk, skill in expected.items():
        selected, catalog = policy.select([risk])
        assert selected == [skill]
        entry = next(item for item in catalog if item["name"] == skill)
        assert entry["selected"] is True
        assert entry["available"] is True
        assert entry["risk"] == risk

    selected, catalog = policy.select(["risk:normal"])
    assert selected == []
    assert all(item["selected"] is False for item in catalog)

    try:
        policy.select(["risk:mechanical", "risk:architecture"])
    except RuntimeError as exc:
        assert "conflicting" in str(exc)
    else:
        raise AssertionError("conflicting risk labels did not fail skill selection")

    with tempfile.TemporaryDirectory() as temp:
        state_root = Path(temp) / "state"
        os.environ["RPGK_SUPERVISOR_STATE_ROOT"] = str(state_root)
        state_file = Path(temp) / "capabilities.json"
        state_file.write_text(
            json.dumps({
                "protocolVersion": 1,
                "issue": "GH-321",
                "route": "luna",
                "riskLabels": ["risk:mechanical"],
                "selectedSkills": [],
                "skillCatalog": [],
                "mcp": [],
            }),
            encoding="utf-8",
        )
        result = policy.augment(state_file, ["risk:mechanical"])
        assert result["selectedSkills"] == ["rpgk-mechanical-change"]
        persisted = json.loads(state_file.read_text(encoding="utf-8"))
        assert persisted["selectedSkills"] == ["rpgk-mechanical-change"]
        events = (state_root / "telemetry" / "events.jsonl").read_text(encoding="utf-8")
        assert "worker_first_party_skills_selected" in events
        assert "rpgk-mechanical-change" in events
finally:
    os.environ.clear()
    os.environ.update(old_env)

print("first-party-skill-policy-test: PASS")
