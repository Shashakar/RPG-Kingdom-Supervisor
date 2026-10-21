#!/usr/bin/env python3
"""Offline, filesystem-only security regression for Claude's visual Unity lane."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name: str, script: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = load("claude_visual_policy_security_test", "claude-visual-policy.py")
host = load("claude_visual_host_security_test", "claude-visual-host.py")
broker = load("visual_broker_security_test", "git-handoff-host.py")


def actor(name: str, role: str, path: str, x: float):
    return {"id": name, "role": role, "asset": path,
            "position": [x, 0, 0], "yaw": 0, "scale": 1}


def experiment(kind: str):
    objects = [
        actor("blink", "character", "Assets/AssetStoreLocal/Blink/Model.fbx", -2),
        actor("swordman", "character", "Assets/AssetStoreLocal/Swordman/Model.fbx", 2),
    ]
    if kind == "environment-cohesion":
        objects += [
            actor("house", "building", "Assets/AssetStoreLocal/Townsmith/House.prefab", 0),
            actor("tree", "nature", "Assets/AssetStoreLocal/Fantasy/Trees/Tree.prefab", 6),
        ]
    return {
        "kind": kind,
        "objects": objects,
        "shots": [{"id": "idle", "clip": "Assets/Kevin Iglesias/Motions/Idle.anim"
                   if kind == "character-comparison" else "",
                   "clipName": "", "seconds": 0.3}],
        "camera": {"position": [0, 2, -12], "lookAt": [0, 1, 0], "fov": 45},
    }


def manifest(number: int, kinds=("character-comparison", "environment-cohesion")):
    return {"protocolVersion": 1, "issue": number,
            "experiments": [experiment(x) for x in kinds]}


def grant(number: int, kinds=("character-comparison", "environment-cohesion")):
    return {"protocolVersion": 1, "issue": number, "phases": list(kinds),
            "allowLicensedScreenshots": True}


def png():
    return (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
            + (1280).to_bytes(4, "big") + (720).to_bytes(4, "big")
            + b"\x00" * 600)


class VisualSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wsroot = self.root / "workspaces"
        self.workspace = self.wsroot / "GH-17"
        self.workspace.mkdir(parents=True)
        self.state = self.root / "state"
        self.auth = grant(17)
        self.plan = manifest(17)

    def test_required_labels_and_exact_issue_authority(self):
        body = "<!-- rpgk-claude-visual-authority\n" + json.dumps(self.auth) + "\n-->"
        self.assertEqual(policy.authority(body, 17), self.auth)
        self.assertEqual(policy.preflight_labels(set(policy.LABELS), body, 17), self.auth)
        for labels in (set(policy.LABELS) - {"resource:unity-editor"},
                       set(policy.LABELS) | {"symphony:ready"},
                       set(policy.LABELS) | {"authoring:scene-existing-composition"}):
            with self.subTest(labels=labels), self.assertRaises(policy.VisualContractError):
                policy.preflight_labels(labels, body, 17)
        with self.assertRaises(policy.VisualContractError):
            policy.authority(body, 16)
        with self.assertRaises(policy.VisualContractError):
            policy.authority(body + body, 17)
        with self.assertRaises(policy.VisualContractError):
            policy.authority("", 17)
        unauthorised = dict(self.auth, allowLicensedScreenshots=False)
        with self.assertRaises(policy.VisualContractError):
            policy.authority("<!-- rpgk-claude-visual-authority\n" +
                             json.dumps(unauthorised) + "\n-->", 17)

    def test_plan_bounds_and_fail_closed_data(self):
        self.assertEqual(policy.validate_manifest(self.plan, self.auth, 17)["issue"], 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][0]["objects"][0]["asset"] = "Assets/Rekindle/../Hearthmere.unity"
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][1]["objects"][2]["asset"] = "Assets/ThirdParty/Vendor/Scene.prefab\n"
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][0]["objects"][1]["id"] = "blink"
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][1]["objects"].pop()
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][0]["shots"][0]["clip"] = ""
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][0]["camera"]["position"][0] = 9999999999
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][1]["shots"][0]["seconds"] = -1
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)
        bad = json.loads(json.dumps(self.plan))
        bad["experiments"][0]["shots"][0]["evil"] = "run-shell"
        with self.assertRaises(policy.VisualContractError):
            policy.validate_manifest(bad, self.auth, 17)

    def test_manifest_symlink_rejected(self):
        path = policy.manifest_path(self.workspace, 17)
        path.parent.mkdir(parents=True)
        path.symlink_to(self.root / "secret")
        with self.assertRaises(policy.VisualContractError):
            policy.read_manifest(self.workspace, self.auth, 17)

    def test_host_requires_three_independent_opt_ins_and_explicit_asset_roots(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(policy.VisualContractError):
                policy.require_host_opt_in()
        with mock.patch.dict(os.environ, {
            "RPGK_CLAUDE_VISUAL_UNITY_ENABLED": "1",
            "RPGK_REKINDLE_UNITY_READY": "1",
            "RPGK_CLAUDE_LICENSED_VISUAL_REVIEW_APPROVED": "1",
            "RPGK_CLAUDE_VISUAL_ASSET_PROJECT_WINDOWS": "C:\\OwnedAssets",
            "RPGK_CLAUDE_VISUAL_LICENSED_ROOTS": "../unsafe",
        }):
            with self.assertRaises(host.VisualHostError):
                host.host_ready()

    def test_unity_lease_exclusive_and_preserves_failed_ownership(self):
        with host.unity_lease(self.state, self.workspace):
            lock = self.state / "locks/unity-editor.lock"
            self.assertEqual((lock / "owner").read_text().strip(), "GH-17")
            with self.assertRaises(host.VisualHostError):
                with host.unity_lease(self.state, self.workspace):
                    pass
        self.assertFalse(lock.exists())
        with self.assertRaisesRegex(RuntimeError, "worker crashed"):
            with host.unity_lease(self.state, self.workspace):
                raise RuntimeError("worker crashed")
        self.assertTrue(lock.exists(), "unconfirmed Windows child must retain resource lock")

    def make_evidence(self):
        path = policy.manifest_path(self.workspace, 17)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.plan), encoding="utf-8")
        output = policy.evidence_root(self.workspace, 17)
        output.mkdir(parents=True)
        captures = [x["kind"] + "-" + shot["id"] + ".png"
                    for x in self.plan["experiments"] for shot in x["shots"]]
        for name in captures:
            (output / name).write_bytes(png())
        summary = {"issue": 17, "success": True, "captures": captures,
                   "sourceAssets": [], "editorVersion": "6000.3.10f1"}
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return path, output, captures

    def write_receipt(self, path, output, captures):
        receipt = {
            "protocolVersion": 1, "issue": 17,
            "workspace": str(self.workspace.resolve()),
            "runId": "GH-17-visual-abcdef012345",
            "branch": "claude/gh-17-implementation",
            "hostVerified": True, "stageOnly": True,
            "manifestSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "summarySha256": hashlib.sha256((output / "summary.json").read_bytes()).hexdigest(),
            "screenshots": {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                            for name in captures},
        }
        dest = self.state / "claude-visual" / "GH-17.json"
        dest.parent.mkdir(parents=True)
        dest.write_text(json.dumps(receipt), encoding="utf-8")
        return dest

    def verify_broker(self, run_id="GH-17-visual-abcdef012345", branch="claude/gh-17-implementation"):
        return broker.validate_claude_visual_evidence(
            self.workspace.resolve(), self.state, 17, branch, [run_id])

    def test_only_matching_host_receipt_satisfies_unity_required(self):
        path, output, captures = self.make_evidence()
        hashes = host.verify_evidence(output, self.plan)
        self.assertEqual(set(hashes), set(captures))
        self.write_receipt(path, output, captures)
        proof = self.verify_broker()
        self.assertEqual(proof[0]["kind"], "staged-unity-visual")
        with self.assertRaises(broker.HandoffError):
            self.verify_broker(branch="claude/unreviewed")
        with self.assertRaises(broker.HandoffError):
            self.verify_broker(run_id="GH-17-visual-000000000000")
        (output / captures[0]).write_bytes(png() + b"tampered")
        with self.assertRaises(broker.HandoffError):
            self.verify_broker()

    def test_changed_manifest_or_summary_never_passes_handoff(self):
        path, output, captures = self.make_evidence()
        self.write_receipt(path, output, captures)
        with self.assertRaises(broker.HandoffError):
            self.verify_broker(run_id="malicious-run-id")
        path.write_text(json.dumps(dict(self.plan, issue=9)), encoding="utf-8")
        with self.assertRaises(broker.HandoffError):
            self.verify_broker()
        path.write_text(json.dumps(self.plan), encoding="utf-8")
        (output / "summary.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(broker.HandoffError):
            self.verify_broker()

    def test_visual_scope_is_not_general_scene_authoring(self):
        dispatcher = load("claude_visual_dispatcher_security_test", "claude-dispatcher.py")
        safe = [
            "Docs/VisualExperiments/GH-17/request.json",
            "Docs/VisualExperiments/GH-17/review.md",
            "Docs/Evidence/VisualExperiments/GH-17/character-comparison-idle.png",
            "Docs/Evidence/VisualExperiments/GH-17/summary.json",
        ]
        dispatcher.enforce_visual_scope(safe, 17, self.workspace)
        for forbidden in ("Assets/Rekindle/Scenes/Hearthmere.unity",
                          "Assets/AssetStoreLocal/Purchased.fbx",
                          "Docs/Evidence/VisualExperiments/GH-18/shot.png",
                          "Docs/VisualExperiments/GH-17/hack.py",
                          "Docs/Evidence/VisualExperiments/GH-17/a/b.png"):
            with self.subTest(forbidden=forbidden), self.assertRaises(dispatcher.DispatchError):
                dispatcher.enforce_visual_scope([*safe, forbidden], 17, self.workspace)


if __name__ == "__main__":
    unittest.main()
