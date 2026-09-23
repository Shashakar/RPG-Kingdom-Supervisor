#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "structural-authoring-preflight.py"
SPEC = importlib.util.spec_from_file_location("structural_authoring_preflight", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def marker(payload: dict) -> str:
    return "<!-- symphony-scene-authoring-requirements\n" + json.dumps(payload) + "\n-->"


CONTRACT = {
    "schemaVersion": 1,
    "supportedProtocolVersions": [1],
    "supportedTiers": ["mechanical", "mechanical-structural", "new-scene-composition"],
    "operationKindsByTier": [
        {
            "tier": "mechanical-structural",
            "operationKinds": ["add-component", "remove-component", "set-object-reference"],
        },
        {
            "tier": "new-scene-composition",
            "operationKinds": ["set-transform", "reparent-object", "delete-object", "instantiate-existing-prefab"],
        }
    ],
    "structuralComponentAdditions": [
        "RPGKingdom.Runtime.Character.CharacterState",
        "RPGKingdom.Runtime.Combat.CharacterStateCombatantAdapter",
    ],
    "componentRemovals": [
        "RPGKingdom.Runtime.PlayerCombat.PlayerCombatController",
    ],
}


class StructuralAuthoringPreflightTests(unittest.TestCase):
    def evaluate(self, body: str, contract=CONTRACT):
        return MODULE.evaluate(
            body,
            contract,
            revision="abc123",
            contract_path="Assets/RPGKingdom/Editor/SymphonyMechanicalSceneAuthoringCapabilities.json",
        )

    def test_supported_known_requirements_authorize_structural_work(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "mechanical-structural",
                    "operations": ["add-component", "set-object-reference"],
                    "componentAdditions": ["RPGKingdom.Runtime.Character.CharacterState"],
                }
            )
        )
        self.assertEqual("supported", result["status"])
        self.assertTrue(result["authoringAuthorized"])
        self.assertEqual([], result["unsupported"])


    def test_supported_new_scene_requirements_authorize_exact_tier(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "new-scene-composition",
                    "operations": ["set-transform", "reparent-object", "delete-object"],
                }
            )
        )
        self.assertEqual("supported", result["status"])
        self.assertTrue(result["authoringAuthorized"])
        self.assertEqual("new-scene-composition", result["authorizationTier"])

    def test_known_unsupported_component_is_rejected(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "mechanical-structural",
                    "operations": ["add-component"],
                    "componentAdditions": ["RPGKingdom.Runtime.Progression.ObjectiveAbilityGrantAdapter"],
                    "dependency": "RPG Kingdom allowlist extension",
                }
            )
        )
        self.assertEqual("unsupported", result["status"])
        self.assertFalse(result["supported"])
        self.assertEqual(
            [{"kind": "component-addition", "value": "RPGKingdom.Runtime.Progression.ObjectiveAbilityGrantAdapter"}],
            result["unsupported"],
        )
        self.assertEqual("RPG Kingdom allowlist extension", result["recommendedNextAction"])

    def test_known_unsupported_operation_is_rejected(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "mechanical-structural",
                    "operations": ["create-game-object"],
                }
            )
        )
        self.assertEqual("unsupported", result["status"])
        self.assertIn({"kind": "operation", "value": "create-game-object"}, result["unsupported"])

    def test_deferred_source_phase_is_allowed_without_authoring(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "deferred",
                    "tier": "mechanical-structural",
                    "sourcePhaseOnly": True,
                    "operations": ["add-component"],
                    "dependency": "merge source types, then extend allowlist",
                }
            )
        )
        self.assertEqual("deferred", result["status"])
        self.assertTrue(result["supported"])
        self.assertFalse(result["authoringAuthorized"])
        self.assertEqual("merge source types, then extend allowlist", result["recommendedNextAction"])

    def test_deferred_requires_explicit_source_only_flag(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "deferred",
                    "tier": "mechanical-structural",
                    "operations": ["add-component"],
                }
            )
        )
        self.assertEqual("invalid", result["status"])
        self.assertIn("sourcePhaseOnly=true", result["reason"])

    def test_contract_missing_fails_closed_when_requirements_are_declared(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "mechanical-structural",
                    "operations": ["add-component"],
                }
            ),
            contract=None,
        )
        self.assertEqual("contract_unavailable", result["status"])
        self.assertFalse(result["supported"])

    def test_legacy_structural_issue_without_marker_is_diagnosed_unknown(self):
        result = self.evaluate("No explicit structural metadata here.")
        self.assertEqual("unknown", result["status"])
        self.assertFalse(result["supported"])

    def test_multiple_requirement_blocks_are_invalid(self):
        block = marker({"mode": "known", "tier": "mechanical-structural"})
        result = self.evaluate(block + "\n" + block)
        self.assertEqual("invalid", result["status"])


if __name__ == "__main__":
    unittest.main()
