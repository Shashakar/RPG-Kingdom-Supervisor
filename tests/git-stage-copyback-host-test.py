#!/usr/bin/env python3
"""Real-Git host approval tests for the managed stage-copyback operation."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/git-stage-copyback-host.py"
spec = importlib.util.spec_from_file_location("stage_copyback_host", SCRIPT)
assert spec and spec.loader
host = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = host
spec.loader.exec_module(host)

SCENE = "Assets/RPGKingdom/Generated/AgentBenchmarks/Proof.unity"
META = SCENE + ".meta"
FOLDER = "Assets/RPGKingdom/Generated/AgentBenchmarks.meta"


def git(ws, *args):
    return subprocess.check_output(["git", "-C", str(ws), *args], text=True).strip()


def write(root, rel, value):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding="utf-8")


def denied(fn, contains):
    try:
        fn()
    except (ValueError, host.stage_handoff.StageHandoffDenied,
            host.stage_handoff.proposal.AuditDenied) as exc:
        assert contains in str(exc), (contains, str(exc))
    else:
        raise AssertionError("copyback should be denied: " + contains)


def main():
    with tempfile.TemporaryDirectory(prefix="rpgk-service-copyback-") as td:
        root = Path(td)
        wsroot = root / "workspaces"
        ws = wsroot / "GH-700"
        stage = root / "pilot-one"
        state = root / "state"
        origin = root / "remote.git"
        ws.mkdir(parents=True)
        stage.mkdir()
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(ws)], check=True)
        git(ws, "remote", "add", "origin", str(origin))
        write(ws, ".gitignore", "Logs/\\n")
        write(ws, "Assets/base.txt", "baseline\\n")
        write(ws, "Assets/Settings/Mobile_RPAsset.asset", "old URP\\n")
        write(ws, "Packages/manifest.json", "{}\\n")
        write(ws, "ProjectSettings/ProjectVersion.txt", "Unity\\n")
        for directory in ("Assets", "Packages", "ProjectSettings"):
            (stage / directory).mkdir(exist_ok=True)
        for rel in ("Assets/base.txt", "Assets/Settings/Mobile_RPAsset.asset",
                    "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"):
            write(stage, rel, (ws / rel).read_text())
        git(ws, "add", ".")
        git(ws, "-c", "user.name=Test", "-c", "user.email=test@local.invalid",
            "commit", "-qm", "base")
        git(ws, "push", "-q", "-u", "origin", "main")
        base = git(ws, "rev-parse", "HEAD")
        git(ws, "switch", "-c", "codex/700-proof")
        # The host gets GitHub origin from its trusted game workspace. In this
        # offline test the Git object remote is deliberately a local fixture.
        git(ws, "remote", "set-url", "origin",
            "https://github.com/Shashakar/RPG-Kingdom.git")
        write(stage, SCENE, "real Unity scene\\n")
        write(stage, META, "fileFormatVersion: 2\\nguid: " + "a" * 32 + "\\n")
        write(stage, FOLDER, "fileFormatVersion: 2\\nguid: " + "b" * 32 + "\\n")
        write(stage, "Assets/Settings/Mobile_RPAsset.asset", "unrelated upgraded URP\\n")
        (stage / "session.json").write_text(json.dumps({
            "schemaVersion": 1, "sessionId": stage.name,
            "sourceProjectPath": "C:\\\\mock\\\\GH-700"
        }))
        approval = {
            "schemaVersion": 1, "mode": "host-reviewed-stage-copyback",
            "approved": True, "issue": "GH-700", "workspace": str(ws),
            "branch": "codex/700-proof", "sourceRevision": base,
            "stagePath": str(stage), "selectedHashes": {
                rel: host.stage_handoff.proposal.digest(stage / rel)
                for rel in (SCENE, META, FOLDER)
            }
        }
        file = state / "stage-copyback-approvals/GH-700.json"
        file.parent.mkdir(parents=True)
        def run():
            return host.copyback(ws, wsroot, state, "codex/700-proof", 700)

        mapped = subprocess.CompletedProcess(["wslpath"], 0, stdout=str(ws) + "\n")
        real_run = host.subprocess.run

        def mock_run(argv, **kwargs):
            if argv and argv[0] == "wslpath":
                return mapped
            return real_run(argv, **kwargs)

        with patch.object(host.subprocess, "run", side_effect=mock_run):
            denied(run, "missing host-owned")
            file.write_text(json.dumps(approval))
            approval["issue"] = "GH-701"
            file.write_text(json.dumps(approval))
            denied(run, "approval does not match")
            approval["issue"] = "GH-700"
            approval["selectedHashes"][SCENE] = "0" * 64
            file.write_text(json.dumps(approval))
            denied(run, "staged asset hashes changed")
            approval["selectedHashes"][SCENE] = host.stage_handoff.proposal.digest(stage / SCENE)
            file.write_text(json.dumps(approval))
            result = run()
            assert result["status"] == "completed"
            assert set(result["copiedPaths"]) == {SCENE, META, FOLDER}
            assert git(ws, "branch", "--show-current") == "codex/700-proof"
            assert git(ws, "rev-parse", "refs/heads/main") == base
            assert git(ws, "rev-parse", "HEAD") == base
            assert (ws / "Assets/Settings/Mobile_RPAsset.asset").read_text().splitlines() == ["old URP"]
            denied(run, "workspace is dirty")
        print("git-stage-copyback-host-test: PASS")


if __name__ == "__main__":
    main()
