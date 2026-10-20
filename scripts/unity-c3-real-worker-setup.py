#!/usr/bin/env python3
"""Operator-only setup for a *real* Symphony GH-N Unity stage-only report task.

prepare: create/validate a clean game-main GH-N workspace.
grant: verify a NEW Stage B Unity Editor comes from that workspace, load the
       approved scene if needed, and issue a same-account Stage C3 grant.

No real worker dispatch or GitHub label mutation. No source file copyback.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("c3_real_issue_readonly", ROOT / "unity-cli-readonly.py")
assert SPEC and SPEC.loader
ro = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ro
SPEC.loader.exec_module(ro)

REPO = "https://github.com/Shashakar/RPG-Kingdom.git"
SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
TARGET = "Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity"


def deny(message: str) -> None:
    raise ro.Denied(message)


def call(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def revision(workspace: Path) -> str:
    check = call(["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"])
    if check.returncode or not re.fullmatch(r"[0-9a-f]{40}", check.stdout.strip()):
        deny("game workspace has no valid Git HEAD")
    return check.stdout.strip()


def verify_game_workspace(workspace: Path, *, fetch: bool) -> str:
    if not workspace.is_dir() or workspace.is_symlink():
        deny("missing or linked GH-N game workspace")
    for root in ("Assets", "Packages", "ProjectSettings"):
        if not (workspace / root).is_dir():
            deny("game checkout is missing " + root)
    origin = call(["git", "-C", str(workspace), "remote", "get-url", "origin"])
    if origin.returncode or origin.stdout.strip().removesuffix(".git").casefold() != REPO.removesuffix(".git").casefold():
        deny("workspace origin does not match RPG Kingdom")
    if fetch:
        update = call(["git", "-C", str(workspace), "fetch", "origin", "main"], timeout=120)
        if update.returncode:
            deny("cannot update game origin/main; " + update.stderr.strip()[:160])
    main = call(["git", "-C", str(workspace), "rev-parse", "--verify", "refs/remotes/origin/main"])
    if main.returncode or revision(workspace) != main.stdout.strip():
        deny("game workspace must be at the latest origin/main commit")
    status = call(["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"])
    if status.returncode or status.stdout.strip():
        deny("game workspace contains tracked or untracked changes")
    return revision(workspace)


def source_windows_path(workspace: Path) -> str:
    p = call(["wslpath", "-w", str(workspace)], timeout=10)
    if p.returncode or not p.stdout.strip():
        deny("cannot convert workspace to Windows path")
    return ntpath.normcase(ntpath.normpath(p.stdout.strip()))


def sha(file: Path) -> str:
    h = hashlib.sha256()
    with file.open("rb") as stream:
        for part in iter(lambda: stream.read(1048576), b""):
            h.update(part)
    return h.hexdigest()


def valid_stage(workspace: Path, stage_windows: str) -> tuple[Path, dict]:
    stage, receipt = ro.stage_info(stage_windows)
    recorded = receipt.get("sourceProjectPath")
    if not isinstance(recorded, str) or ntpath.normcase(ntpath.normpath(recorded)) != source_windows_path(workspace):
        deny("Stage B receipt is not bound to this exact GH-N game workspace; create a NEW stage from this workspace")
    for rel in (SCENE, "ProjectSettings/ProjectVersion.txt"):
        source = workspace / rel
        target = stage / rel
        if (not source.is_file() or not target.is_file()
                or source.is_symlink() or target.is_symlink()
                or sha(source) != sha(target)):
            deny("fresh Windows stage differs from issue workspace: " + rel)
    if (stage / TARGET).exists() or (stage / (TARGET + ".meta")).exists():
        deny("test target already exists in stage; create a fresh stage before testing")
    return stage, receipt


def grant(workspace: Path, stage_windows: str, state: Path) -> dict:
    stage, receipt = valid_stage(workspace, stage_windows)
    exe = ro.cli_path()
    ro.verify_pipeline(exe, stage, receipt)
    response = ro.cli_json(exe, stage, [
        "command", "get_scene_hierarchy", "--project-path", receipt["stagePath"], "--format", "json"
    ])
    active = response.get("data", {}).get("result", {})
    if active.get("scenePath") != SCENE:
        # This is trusted, operator-initiated fixed Editor code. The new stage
        # may initially open an empty scene; do not modify the game checkout.
        code = 'return UnityEditor.SceneManagement.EditorSceneManager.OpenScene("Assets/RPGKingdom/Scenes/PlaytestScene.unity").path;'
        ro.cli_json(exe, stage, [
            "command", "eval", "--code", code,
            "--project-path", receipt["stagePath"], "--format", "json"
        ])
        response = ro.cli_json(exe, stage, [
            "command", "get_scene_hierarchy", "--project-path", receipt["stagePath"], "--format", "json"
        ])
    summary = ro.compact_scene(response, SCENE)
    if summary["sceneDirty"]:
        deny("source scene is dirty")
    if sha(workspace / SCENE) != sha(stage / SCENE):
        deny("source scene unexpectedly changed during Editor open")
    env = os.environ.copy()
    env["RPGK_UNITY_STAGED_CODE_ENABLED"] = "1"
    output = subprocess.run(
        [sys.executable, str(ROOT / "unity-cli-staged-code.py"), "grant",
         "--workspace", str(workspace), "--state-root", str(state),
         "--session-windows", stage_windows, "--scene", SCENE],
        capture_output=True, text=True, timeout=120, env=env, check=False
    )
    if output.returncode != 0:
        deny("operator grant failed: " + output.stderr.strip()[:350])
    return {
        "status": "ready-to-arm",
        "issue": workspace.name,
        "workspace": str(workspace),
        "revision": revision(workspace),
        "stage": receipt["stagePath"],
        "editorPid": receipt["unityPid"],
        "activeScene": summary["scenePath"],
        "sourceObjectCount": summary["gameObjectCount"],
        "originalSceneSha256": sha(stage / SCENE),
        "copyback": False,
        "action": "Operator must start Supervisor with C3 environment and only then add symphony:ready to the issue",
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Prepare real issue for same-account Unity stage authoring")
    p.add_argument("action", choices=("prepare", "grant"))
    p.add_argument("--issue", required=True, help="GH-N issue identifier, e.g. GH-261")
    p.add_argument("--session-windows", default="")
    p.add_argument("--workspace-root", default=os.environ.get(
        "RPGK_SYMPHONY_WORKSPACE_ROOT", str(Path.home() / "code/rpg-kingdom-symphony-workspaces")))
    p.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    args = p.parse_args()
    try:
        if not ro.ISSUE.fullmatch(args.issue):
            deny("issue identifier must be GH-N")
        root = Path(args.workspace_root).expanduser().resolve()
        workspace = root / args.issue
        if args.action == "prepare" and not workspace.exists():
            root.mkdir(parents=True, exist_ok=True)
            result = call(["git", "clone", "--branch", "main", "--single-branch",
                           REPO, str(workspace)], timeout=180)
            if result.returncode:
                deny("Git clone failed: " + result.stderr.strip()[:300])
        head = verify_game_workspace(workspace, fetch=True)
        if args.action == "prepare":
            if args.session_windows:
                deny("prepare must not select an existing Windows stage")
            output = {
                "status": "workspace-prepared", "issue": args.issue,
                "workspace": str(workspace), "revision": head,
                "next": "Close the existing pilot Editor, then run unity-cli-stage.sh start --source against this workspace",
            }
        else:
            if not args.session_windows:
                deny("grant requires a freshly created Stage B --session-windows")
            state = Path(args.state_root).expanduser().resolve()
            output = grant(workspace, args.session_windows, state)
        print(json.dumps(output, separators=(",", ":")))
        return 0
    except (ro.Denied, OSError, subprocess.SubprocessError, ValueError) as exc:
        print("Real Unity worker setup: blocked: " + str(exc), file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
