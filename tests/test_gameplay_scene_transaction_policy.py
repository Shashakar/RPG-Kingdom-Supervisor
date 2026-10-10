"""Tests for the host-owned, opt-in gameplay scene transaction policy."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "scope", Path(__file__).resolve().parents[1] / "scripts" / "gameplay_scene_transaction_policy.py"
)
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class GameplayTransactionTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(issue="GH-255", workspace="/work/GH-255", branch="codex/gh-255-opening")
        self.grant = dict(
            protocolVersion=1, issue="GH-255", workspace="/work/GH-255",
            branch="codex/gh-255-opening", tier="opening-encounter-composition",
            scene="Assets/RPGKingdom/Scenes/PlaytestScene.unity",
            allowedRoots=["World/TownArea", "Systems/Encounter_FirstApproach"],
            operations=["compose-authored-opening-encounter"],
        )
        self.request = dict(
            protocolVersion=1, tier="opening-encounter-composition",
            scene=self.grant["scene"],
            operations=[dict(kind="compose-authored-opening-encounter", openingSpawnYaw=0)],
        )

    def denied(self, mutate):
        mutate()
        with self.assertRaises(scope.AuthorizationError):
            scope.validate(self.grant, self.request, **self.context)

    def test_valid_scoped_request(self):
        result = scope.validate(self.grant, self.request, **self.context)
        self.assertEqual(result["operation"], "compose-authored-opening-encounter")
        self.assertEqual(result["allowedRoots"], self.grant["allowedRoots"])

    def test_wrong_issue(self): self.denied(lambda: self.grant.update(issue="GH-256"))
    def test_wrong_workspace(self): self.denied(lambda: self.grant.update(workspace="/work/GH-999"))
    def test_wrong_branch(self): self.denied(lambda: self.grant.update(branch="main"))
    def test_wrong_scene(self): self.denied(lambda: self.request.update(scene="Assets/Other.unity"))
    def test_traversal_scene(self): self.denied(lambda: self.grant.update(scene="Assets/../Other.unity"))
    def test_unsupported_operation(self): self.denied(lambda: self.request["operations"][0].update(kind="delete-object"))
    def test_multiple_operations(self): self.denied(lambda: self.request["operations"].append(dict(kind="compose-authored-opening-encounter")))
    def test_arbitrary_editor_command(self): self.denied(lambda: self.request.update(executeMethod="Dangerous.Entry"))
    def test_arbitrary_field_write(self): self.denied(lambda: self.request["operations"][0].update(propertyPath="enemyState.characterState"))
    def test_self_authorized_scope(self): self.denied(lambda: self.request.update(protectedCompositionPaths=["World"]))
    def test_invalid_root(self): self.denied(lambda: self.grant.update(allowedRoots=["World/../Systems"]))
    def test_empty_roots(self): self.denied(lambda: self.grant.update(allowedRoots=[]))
    def test_unknown_grant_operation(self): self.denied(lambda: self.grant.update(operations=["execute-script"]))
    def test_missing_grant_binding(self): self.denied(lambda: self.grant.pop("issue"))


if __name__ == "__main__":
    unittest.main()
