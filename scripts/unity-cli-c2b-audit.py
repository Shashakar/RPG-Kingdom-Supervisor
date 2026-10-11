#!/usr/bin/env python3
"""Stage C2b operator-only source/stage attestation and *dry-run* copyback audit.

The tool never launches Unity, executes C#, modifies either project or copies
any asset. It refuses to authorize promotion; a successful audit is evidence
for a later independently-reviewed copyback system, not permission to merge.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("unity_cli_readonly_c2b_dependency", BASE / "unity-cli-readonly.py")
assert spec and spec.loader
ro = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ro
spec.loader.exec_module(ro)

PROTOCOL = 1
FLAG = "RPGK_UNITY_C2B_AUDIT_ENABLED"
SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
CANDIDATE = "Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity"
ADDITIONS = frozenset((
    CANDIDATE, CANDIDATE + ".meta",
    "Assets/RPGKingdom/Generated.meta",
    "Assets/RPGKingdom/Generated/AgentBenchmarks.meta",
))
ROOTS = ("Assets", "Packages", "ProjectSettings")
PIPELINE_VERSION = "0.8.0-exp.1"
MAX_FILES = 250_000
MAX_BYTES = 150 * (1024 ** 3)


def deny(message: str) -> None:
    raise ro.Denied(message)


def sha_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def inventory(base: Path) -> tuple[dict[str, str], int]:
    """Hash all source project files; reject symlink/junction escapes.

    Only Assets, Packages and ProjectSettings are compared. Library, Logs and
    temporary Unity cache files are intentionally excluded.
    """
    files: dict[str, str] = {}
    seen = set()
    size = 0
    for root_name in ROOTS:
        root = base / root_name
        if not root.is_dir() or root.is_symlink():
            deny("project root missing or linked: " + root_name)
        for directory, dirs, names in os.walk(root, followlinks=False):
            current = Path(directory)
            for child in dirs:
                node = current / child
                if node.is_symlink() or not node.is_dir():
                    deny("symbolic link / reparse directory in project: " + str(node))
            dirs.sort()
            for name in sorted(names):
                file = current / name
                mode = file.lstat().st_mode
                if not stat.S_ISREG(mode) or file.is_symlink():
                    deny("non-regular asset or linked path: " + str(file))
                rel = file.relative_to(base).as_posix()
                folded = rel.casefold()
                if folded in seen:
                    deny("case-insensitive project path collision: " + rel)
                seen.add(folded)
                size += file.stat().st_size
                if size > MAX_BYTES or len(files) >= MAX_FILES:
                    deny("project snapshot exceeds bounded inventory limits")
                files[rel] = sha_file(file)
    return files, size


def clean_workspace(workspace: Path) -> None:
    check = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if check.returncode or check.stdout.strip():
        deny("workspace must have a clean Git index and working tree")


def canonical_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("cannot parse Unity package manifest/lock: " + str(path)) from exc
    if not isinstance(value, dict):
        deny("Unity manifest/lock was not a JSON object")
    return value


def compare_packages(workspace: Path, stage: Path, ws: dict, staged: dict) -> None:
    """Allow only Unity Pipeline's *known* package addition to manifest/lock."""
    for rel in ROOTS:
        if rel not in ROOTS:
            deny("impossible root mismatch")
    wskeys = set(ws)
    stkeys = set(staged)
    if wskeys != stkeys:
        deny("staged project content differs from workspace file list; use a fresh stage from this workspace")
    for rel, digest in ws.items():
        if staged[rel] == digest:
            continue
        if rel not in ("Packages/manifest.json", "Packages/packages-lock.json"):
            deny("staged baseline does not match clean issue workspace: " + rel)
        expected = canonical_json(workspace / rel)
        actual = canonical_json(stage / rel)
        dependencies = actual.get("dependencies")
        if not isinstance(dependencies, dict):
            deny("invalid staged Unity package dependencies")
        if rel == "Packages/manifest.json":
            if dependencies.pop("com.unity.pipeline", None) != PIPELINE_VERSION:
                deny("staged package change is not exactly the pinned Pipeline addition")
        else:
            if "com.unity.pipeline" not in dependencies:
                deny("unapproved packages-lock.json difference")
            dependencies.pop("com.unity.pipeline")
        if actual != expected:
            deny("unapproved staged Unity package/lock changes: " + rel)


def record_path(state: Path, issue: str) -> Path:
    if not ro.ISSUE.fullmatch(issue):
        deny("invalid issue")
    return state / "unity-cli-c2b-audit" / (issue + ".json")


def digest_map(mapping: dict[str, str]) -> str:
    canonical = json.dumps(mapping, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def load_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ro.Denied("missing/invalid C2b baseline receipt") from exc
    if (not isinstance(record, dict) or record.get("schemaVersion") != PROTOCOL
            or record.get("status") != "baseline-attested"
            or not isinstance(record.get("files"), dict)
            or record.get("fingerprint") != digest_map(record["files"])):
        deny("baseline receipt invalid or tampered")
    return record


def baseline(workspace: Path, revision: str, state: Path, stage_windows: str) -> dict:
    clean_workspace(workspace)
    stage, receipt = ro.stage_info(stage_windows)
    if not (stage / SCENE).is_file():
        deny("stage does not contain expected scene")
    source_files, source_bytes = inventory(workspace)
    staged_files, staged_bytes = inventory(stage)
    compare_packages(workspace, stage, source_files, staged_files)
    if CANDIDATE in staged_files:
        deny("benchmark output already exists; require fresh source stage for C2b baseline")
    record = {
        "schemaVersion": PROTOCOL, "status": "baseline-attested",
        "issue": workspace.name, "workspace": str(workspace),
        "workspaceRevision": revision, "stagePath": receipt["stagePath"],
        "unityPid": receipt["unityPid"], "scenePath": SCENE,
        "targetScene": CANDIDATE, "fileCount": len(staged_files),
        "sourceBytes": source_bytes, "stageBytes": staged_bytes,
        "fingerprint": digest_map(staged_files), "files": staged_files,
    }
    ro.write_grant(record_path(state, workspace.name), record)
    return {
        "success": True, "operation": "c2b-baseline",
        "issue": workspace.name, "fileCount": len(staged_files),
        "workspaceRevision": revision, "fingerprint": record["fingerprint"],
        "readyForCopyback": False, "stageOnly": True,
    }


def audit(workspace: Path, revision: str, state: Path) -> dict:
    clean_workspace(workspace)
    record = load_record(record_path(state, workspace.name))
    if (record.get("workspace") != str(workspace)
            or record.get("workspaceRevision") != revision
            or record.get("issue") != workspace.name
            or record.get("scenePath") != SCENE
            or record.get("targetScene") != CANDIDATE):
        deny("issue, workspace revision or asset path changed since baseline")
    stage, receipt = ro.stage_info(record["stagePath"])
    if receipt["unityPid"] != record["unityPid"]:
        deny("Editor PID differs from attested baseline")

    before = record["files"]
    now, byte_count = inventory(stage)
    removed = sorted(set(before) - set(now))
    modified = sorted(k for k in set(before) & set(now) if before[k] != now[k])
    added = sorted(set(now) - set(before))
    if removed or modified:
        deny("existing stage assets changed (no copyback preview): " +
             ", ".join((removed + modified)[:8]))
    unexpected = sorted(set(added) - ADDITIONS)
    if unexpected:
        deny("unexpected stage additions (no copyback preview): " + ", ".join(unexpected[:8]))
    if CANDIDATE not in added:
        deny("no new benchmark scene; refusing empty or replayed audit")
    scene = stage / CANDIDATE
    if scene.is_symlink() or not scene.is_file():
        deny("generated scene is not a regular file")
    # A bounded fixture check, NOT a replacement for Editor test/geometry checks.
    with scene.open("rb") as handle:
        head = handle.read(8 * 1024 * 1024).decode("utf-8-sig", errors="replace")
    if ("SupervisorCliBenchmarkRoot" not in head
            or "SupervisorCliBenchmarkCube" not in head):
        deny("generated scene does not contain fixed benchmark objects")
    return {
        "success": True,
        "operation": "c2b-copyback-audit",
        "issue": workspace.name,
        "workspaceRevision": revision,
        "fileCountBefore": len(before), "fileCountAfter": len(now),
        "addedPaths": added,
        "addedHashes": {rel: now[rel] for rel in added},
        "targetScene": CANDIDATE, "targetSha256": now[CANDIDATE],
        "unchangedExistingFiles": True,
        "dryRun": True, "copyback": False, "readyForCopyback": False,
        "blockers": [
            "OS-enforced Windows Editor identity and filesystem isolation unverified",
            "No formal Unity EditMode/PlayMode and visual/geometric validation",
            "No human-reviewed issue-bound asset promotion approval",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Operator-only stage provenance and dry-run copyback audit")
    parser.add_argument("action", choices=("baseline", "audit", "revoke"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--session-windows", default="")
    parser.add_argument("--state-root", default=os.environ.get(
        "RPGK_SUPERVISOR_STATE_ROOT", str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    args = parser.parse_args()
    try:
        if os.environ.get(FLAG) != "1":
            deny("C2b operator audit is disabled by default")
        workspace, revision = ro.workspace_info(args.workspace)
        state = Path(args.state_root).expanduser().resolve()
        receipt = record_path(state, workspace.name)
        if args.action == "revoke":
            receipt.unlink(missing_ok=True)
            result = {"success": True, "operation": "c2b-revoke",
                      "issue": workspace.name, "readyForCopyback": False}
        elif args.action == "baseline":
            if not args.session_windows:
                deny("baseline requires --session-windows from operator")
            result = baseline(workspace, revision, state, args.session_windows)
        else:
            if args.session_windows:
                deny("audit must use the pinned stage from baseline")
            result = audit(workspace, revision, state)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (ro.Denied, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Unity C2b attestation: blocked: " + str(exc), file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
