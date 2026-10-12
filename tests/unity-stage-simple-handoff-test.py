#!/usr/bin/env python3
"""Offline real-Git regression for minimal Unity stage -> feature branch handoff."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "simple_stage_handoff", BASE / "scripts/unity-stage-simple-handoff.py")
handoff = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = handoff
spec.loader.exec_module(handoff)
proof = handoff.proposal.PROOF
scene = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"


def cmd(cwd, *args):
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


class StageHandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ws = root / "GH-260"
        self.stage = root / "stage"
        self.origin = root / "origin.git"
        self.ws.mkdir()
        self.stage.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.ws)], check=True)
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)], check=True)
        cmd(self.ws, "remote", "add", "origin", str(self.origin))
        for path in (self.ws, self.stage):
            for name in handoff.proposal.CHECK_ROOTS:
                (path / name).mkdir()
        self.put(self.ws, scene, "original\n")
        self.put(self.ws, "Assets/Settings/Mobile_RPAsset.asset", "URP base\n")
        self.put(self.ws, "ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 6000.3.10f1\n")
        self.put(self.ws, "Packages/manifest.json", "{}\n")
        self.put(self.ws, ".gitignore", "Library/\nLogs/\nAssets/ignored.tmp\n")
        for rel in [scene, "Assets/Settings/Mobile_RPAsset.asset",
                    "ProjectSettings/ProjectVersion.txt", "Packages/manifest.json"]:
            self.put(self.stage, rel, (self.ws / rel).read_text())
        cmd(self.ws, "add", ".")
        cmd(self.ws, "-c", "user.name=test", "-c", "user.email=test@example.test",
            "commit", "-qm", "baseline")
        cmd(self.ws, "push", "-q", "-u", "origin", "main")
        self.base_rev = cmd(self.ws, "rev-parse", "HEAD")
        self.put(self.stage, scene, "modified approved terrain scene\n")
        self.put(self.stage, "Assets/Settings/Mobile_RPAsset.asset", "URP unrelated autochange\n")

    def put(self, base, rel, value):
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def proof_fixture(self):
        self.put(self.stage, proof, "created proof scene\n")
        self.put(self.stage, proof + ".meta", "fileFormatVersion: 2\nguid: " + "1" * 32 + "\n")
        self.put(self.stage, handoff.proposal.FOLDER_META,
                 "fileFormatVersion: 2\nguid: " + "2" * 32 + "\n")

    def test_plan_is_read_only_and_reports_all_exclusions(self):
        self.proof_fixture()
        plan = handoff.plan(self.ws, self.stage, [proof])
        self.assertFalse(plan["copyback"])
        self.assertEqual(cmd(self.ws, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(cmd(self.ws, "status", "--porcelain"), "")
        self.assertEqual(
            {x["path"] for x in plan["selected"]},
            {proof, proof + ".meta", handoff.proposal.FOLDER_META})
        self.assertIn(scene, {x["path"] for x in plan["excluded"]})
        self.assertIn("Assets/Settings/Mobile_RPAsset.asset",
                      {x["path"] for x in plan["excluded"]})

    def test_real_textmesh_pro_ampersand_paths_do_not_block_stage_preview(self):
        # Real game main includes Assets/TextMesh Pro/Resources/Fonts & Materials.
        # These unchanged Unity package files must not block plan/apply.
        path = "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset"
        self.put(self.ws, path, "original font data\n")
        self.put(self.stage, path, "original font data\n")
        self.put(self.ws, path + ".meta",
                 "fileFormatVersion: 2\nguid: " + "c" * 32 + "\n")
        self.put(self.stage, path + ".meta",
                 "fileFormatVersion: 2\nguid: " + "c" * 32 + "\n")
        cmd(self.ws, "add", ".")
        cmd(self.ws, "-c", "user.name=test", "-c", "user.email=test@example.test",
            "commit", "-qm", "existing font fixture")
        cmd(self.ws, "update-ref", "refs/remotes/origin/main",
            cmd(self.ws, "rev-parse", "HEAD"))
        output = handoff.plan(self.ws, self.stage, [scene])
        self.assertEqual([x["path"] for x in output["selected"]], [scene])
        self.assertNotIn(path, {x["path"] for x in output["excluded"]})
        self.assertIn("Assets/Settings/Mobile_RPAsset.asset",
                      {x["path"] for x in output["excluded"]})

    def test_apply_copies_scene_only_after_feature_branch_switch(self):
        result = handoff.apply(self.ws, self.stage, "codex/260-terrain-approach", [scene])
        self.assertEqual(result["status"], "copied-to-feature-branch")
        self.assertEqual(result["copiedPaths"], [scene])
        self.assertFalse(result["prCreated"])
        self.assertEqual(cmd(self.ws, "branch", "--show-current"), "codex/260-terrain-approach")
        self.assertEqual((self.ws / scene).read_text(), "modified approved terrain scene\n")
        self.assertEqual((self.ws / "Assets/Settings/Mobile_RPAsset.asset").read_text(), "URP base\n")
        self.assertEqual(cmd(self.ws, "rev-parse", "HEAD"), self.base_rev)
        self.assertEqual(cmd(self.ws, "rev-parse", "refs/heads/main"), self.base_rev)
        self.assertEqual(cmd(self.ws, "diff", "--name-only"), scene)
        self.assertEqual(cmd(self.ws, "status", "--porcelain").strip(), "M " + scene)

    def test_apply_does_not_need_live_github_network(self):
        cmd(self.ws, "remote", "set-url", "origin", "/definitely/not/a/reachable/git/remote")
        result = handoff.apply(self.ws, self.stage, "codex/260-offline-copy", [scene])
        self.assertEqual(result["status"], "copied-to-feature-branch")
        self.assertEqual(cmd(self.ws, "branch", "--show-current"), "codex/260-offline-copy")

    def test_new_scene_copies_meta_and_parent_meta_with_no_urp(self):
        self.proof_fixture()
        result = handoff.apply(self.ws, self.stage, "codex/261-stage-proof", [proof])
        self.assertEqual(set(result["copiedPaths"]), {
            proof, proof + ".meta", handoff.proposal.FOLDER_META})
        self.assertFalse((self.ws / "Assets/Settings/Mobile_RPAsset.asset").read_text() == "URP unrelated autochange\n")
        self.assertEqual(cmd(self.ws, "rev-parse", "refs/heads/main"), self.base_rev)

    def test_rejects_bad_selections_and_existing_branch(self):
        for path in ("../README.md", "Packages/manifest.json",
                     "Assets/../Packages/foo", "/tmp/outside", "Assets\\raw", "Assets/nope.unity"):
            with self.subTest(path=path), self.assertRaises(handoff.StageHandoffDenied):
                handoff.plan(self.ws, self.stage, [path])
        with self.assertRaises(handoff.StageHandoffDenied):
            handoff.apply(self.ws, self.stage, "main", [scene])
        cmd(self.ws, "branch", "codex/260-exists")
        with self.assertRaises(handoff.StageHandoffDenied):
            handoff.apply(self.ws, self.stage, "codex/260-exists", [scene])
        self.assertEqual(cmd(self.ws, "branch", "--show-current"), "main")

    def test_apply_requires_explicit_paths(self):
        with self.assertRaisesRegex(handoff.StageHandoffDenied, "explicit --path"):
            handoff.apply(self.ws, self.stage, "codex/260-empty", [])
        self.assertEqual(cmd(self.ws, "branch", "--show-current"), "main")

    def test_new_asset_without_metadata_is_denied(self):
        self.put(self.stage, "Assets/RPGKingdom/Generated/Orphan.asset", "new asset\n")
        with self.assertRaisesRegex(handoff.StageHandoffDenied, "missing its .meta"):
            handoff.apply(self.ws, self.stage, "codex/260-orphan", [
                "Assets/RPGKingdom/Generated/Orphan.asset"])
        self.assertEqual(cmd(self.ws, "branch", "--show-current"), "main")

    def test_dirty_main_and_symlinked_stage_asset_denied(self):
        self.put(self.ws, "Assets/extra.txt", "dirty\n")
        with self.assertRaises(handoff.proposal.AuditDenied):
            handoff.apply(self.ws, self.stage, "codex/260-dirty", [scene])
        (self.ws / "Assets/extra.txt").unlink()
        (self.stage / scene).unlink()
        try:
            (self.stage / scene).symlink_to(self.ws / scene)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaises(handoff.proposal.AuditDenied):
            handoff.apply(self.ws, self.stage, "codex/260-linked", [scene])


if __name__ == "__main__":
    unittest.main()
