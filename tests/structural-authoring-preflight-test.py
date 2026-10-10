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
    "supportedTiers": ["mechanical", "mechanical-structural", "existing-scene-composition", "new-scene-composition", "prefab-derivative"],
    "operationKindsByTier": [
        {
            "tier": "mechanical-structural",
            "operationKinds": ["add-component", "remove-component", "set-object-reference"],
        },
        {
            "tier": "existing-scene-composition",
            "operationKinds": ["set-transform", "reparent-object", "instantiate-existing-prefab", "set-terrain-layer", "bake-navmesh"],
        },
        {
            "tier": "prefab-derivative",
            "operationKinds": ["create-prefab-derivative"],
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
    "newSceneComposition": {"creationOperationKind": "copy-scene"},
    "existingSceneEnvironment": {"protectedCompositionPathsSupported": True},
}


class StructuralAuthoringPreflightTests(unittest.TestCase):
    def evaluate(self, body: str, contract=CONTRACT):
        return MODULE.evaluate(
            body,
            contract,
            revision="abc123",
            contract_path="Assets/RPGKingdom/Editor/SymphonyMechanicalSceneAuthoringCapabilities.json",
        )

    def test_gameplay_transaction_requires_explicit_reviewed_scene_and_roots(self):
        contract = json.loads(json.dumps(CONTRACT))
        contract["supportedTiers"].append("opening-encounter-composition")
        contract["operationKindsByTier"].append({
            "tier": "opening-encounter-composition",
            "operationKinds": ["compose-authored-opening-encounter"],
        })
        body = marker({
            "mode": "known",
            "tier": "opening-encounter-composition",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": ["compose-authored-opening-encounter"],
            "allowedRoots": ["World/TownArea", "Systems/Encounter_FirstApproach"],
        })
        result = self.evaluate(body, contract)
        self.assertEqual("supported", result["status"])
        self.assertEqual(["World/TownArea", "Systems/Encounter_FirstApproach"],
                         result["authorizedAllowedRoots"])

    def test_gameplay_transaction_rejects_broad_and_invalid_scope(self):
        contract = json.loads(json.dumps(CONTRACT))
        contract["supportedTiers"].append("opening-encounter-composition")
        contract["operationKindsByTier"].append({
            "tier": "opening-encounter-composition",
            "operationKinds": ["compose-authored-opening-encounter"],
        })
        for roots in ([], ["World/../Systems"], ["World/TownArea", "World/TownArea"]):
            with self.subTest(roots=roots):
                result = self.evaluate(marker({
                    "mode": "known",
                    "tier": "opening-encounter-composition",
                    "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
                    "operations": ["compose-authored-opening-encounter"],
                    "allowedRoots": roots,
                }), contract)
                self.assertEqual("invalid", result["status"])

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


    def test_supported_existing_scene_requirements_authorize_exact_scene(self):
        result = self.evaluate(marker({
            "mode": "known",
            "tier": "existing-scene-composition",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": ["set-transform", "instantiate-existing-prefab", "bake-navmesh"],
        }))
        self.assertEqual("supported", result["status"])
        self.assertTrue(result["authoringAuthorized"])
        self.assertEqual("Assets/RPGKingdom/Scenes/PlaytestScene.unity", result["authorizedScene"])

    def test_existing_scene_can_authorize_exact_protected_composition_paths(self):
        result = self.evaluate(marker({
            "mode": "known",
            "tier": "existing-scene-composition",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": ["instantiate-existing-prefab", "set-transform", "set-terrain-layer"],
            "protectedCompositionPaths": [
                "Systems/WorldPhase_VerticalSlice/WorldPhaseTarget_SandboxRoute/Route_Blocked_Phase"
            ],
        }))
        self.assertEqual("supported", result["status"])
        self.assertTrue(result["authoringAuthorized"])
        self.assertEqual(
            ["Systems/WorldPhase_VerticalSlice/WorldPhaseTarget_SandboxRoute/Route_Blocked_Phase"],
            result["authorizedProtectedCompositionPaths"],
        )

    def test_protected_composition_paths_fail_closed_when_contract_does_not_support_them(self):
        contract = json.loads(json.dumps(CONTRACT))
        contract["existingSceneEnvironment"]["protectedCompositionPathsSupported"] = False
        result = self.evaluate(marker({
            "mode": "known",
            "tier": "existing-scene-composition",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": ["set-transform"],
            "protectedCompositionPaths": ["Systems/Phase"],
        }), contract=contract)
        self.assertEqual("unsupported", result["status"])
        self.assertIn({"kind": "protected-composition-path", "value": "Systems/Phase"}, result["unsupported"])

    def test_existing_scene_can_explicitly_authorize_derivative_auxiliary_lane(self):
        result = self.evaluate(marker({
            "mode": "known",
            "tier": "existing-scene-composition",
            "scene": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            "operations": ["set-transform", "instantiate-existing-prefab", "bake-navmesh"],
            "auxiliaryAuthoring": [{"tier": "prefab-derivative", "operations": ["create-prefab-derivative"]}],
        }))
        self.assertEqual("supported", result["status"])
        self.assertEqual([{"tier": "prefab-derivative", "operations": ["create-prefab-derivative"]}], result["authorizedAuxiliaryAuthoring"])

    def test_lower_tier_cannot_gain_derivative_auxiliary_lane(self):
        result = self.evaluate(marker({
            "mode": "known",
            "tier": "mechanical-structural",
            "operations": ["add-component"],
            "auxiliaryAuthoring": [{"tier": "prefab-derivative", "operations": ["create-prefab-derivative"]}],
        }))
        self.assertEqual("unsupported", result["status"])
        self.assertIn({"kind": "auxiliary-tier", "value": "prefab-derivative"}, result["unsupported"])

    def test_existing_scene_requires_exact_scene(self):
        result = self.evaluate(marker({
            "mode": "known",
            "tier": "existing-scene-composition",
            "operations": ["set-transform"],
        }))
        self.assertEqual("invalid", result["status"])
        self.assertFalse(result["supported"])

    def test_supported_new_scene_requirements_authorize_exact_tier(self):
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "new-scene-composition",
                    "operations": ["copy-scene", "set-transform", "reparent-object", "delete-object"],
                }
            )
        )
        self.assertEqual("supported", result["status"])
        self.assertTrue(result["authoringAuthorized"])
        self.assertEqual("new-scene-composition", result["authorizationTier"])


    def test_new_scene_copy_requires_project_creation_capability(self):
        contract = dict(CONTRACT)
        contract.pop("newSceneComposition", None)
        result = self.evaluate(
            marker(
                {
                    "mode": "known",
                    "tier": "new-scene-composition",
                    "operations": ["copy-scene", "set-transform"],
                }
            ),
            contract=contract,
        )
        self.assertEqual("unsupported", result["status"])
        self.assertIn({"kind": "operation", "value": "copy-scene"}, result["unsupported"])

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
