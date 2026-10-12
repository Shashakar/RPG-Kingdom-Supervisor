#!/usr/bin/env python3
"""C5 proposal-only stage inventory. No write, approval, or copyback capability.

Operator runs this against an issue workspace and its owned disposable stage.
This is a review artifact, NOT authorization to promote an asset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ISSUE = re.compile(r"GH-[1-9][0-9]*\Z")
GUID = re.compile(r"(?m)^\s*guid:\s*([0-9a-fA-F]{32})\s*$")
SAFE = re.compile(r"^[A-Za-z0-9_ .+@()/-]+$")
MAX_FILES = 250000
CHECK_ROOTS = ("Assets", "Packages", "ProjectSettings")
IGNORE = {"Library", "Logs", "Temp", "Obj", "Build", "Builds", ".git"}
PROOF = "Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity"
FOLDER_META = "Assets/RPGKingdom/Generated/AgentBenchmarks.meta"


class AuditDenied(Exception):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def inventory(root: Path) -> dict[str, dict]:
    result = {}
    for name in CHECK_ROOTS:
        base = root / name
        if not base.is_dir() or base.is_symlink():
            raise AuditDenied(f"missing, linked or invalid project root: {name}")
        for directory, dirs, filenames in os.walk(base, followlinks=False):
            current = Path(directory)
            if any((current / d).is_symlink() for d in dirs):
                raise AuditDenied("linked project directory")
            dirs[:] = sorted(d for d in dirs if d not in IGNORE)
            for filename in sorted(filenames):
                path = current / filename
                if path.is_symlink() or not path.is_file():
                    raise AuditDenied("linked or nonregular project file")
                relative = path.relative_to(root).as_posix()
                if (not SAFE.fullmatch(relative) or "//" in relative
                        or any(x in {".", "..", ""} for x in relative.split("/"))):
                    raise AuditDenied("unsafe project path")
                if len(result) >= MAX_FILES:
                    raise AuditDenied("project inventory too large")
                data = {"sha256": digest(path), "bytes": path.stat().st_size}
                if relative.endswith(".meta"):
                    # Read only sidecars; duplicate GUIDs are refused, not overwritten.
                    match = GUID.search(path.read_text(encoding="utf-8-sig", errors="replace"))
                    if not match:
                        raise AuditDenied(f"missing .meta GUID: {relative}")
                    data["guid"] = match.group(1).lower()
                result[relative] = data
    return result


def git(args: list[str], workspace: Path) -> str:
    process = subprocess.run(["git", "-C", str(workspace), *args],
                             capture_output=True, text=True, timeout=30, check=False)
    if process.returncode:
        raise AuditDenied(f"git {' '.join(args)} failed")
    return process.stdout.strip()


def validate_source(workspace: Path) -> str:
    if not ISSUE.fullmatch(workspace.name):
        raise AuditDenied("workspace name must be GH-N")
    head = git(["rev-parse", "HEAD"], workspace)
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise AuditDenied("invalid HEAD")
    if head != git(["rev-parse", "refs/remotes/origin/main"], workspace):
        raise AuditDenied("source HEAD not equal to origin/main")
    if git(["status", "--porcelain", "--untracked-files=all"], workspace):
        raise AuditDenied("source workspace is not clean")
    return head


def unique_guids(files: dict[str, dict], which: str) -> None:
    seen = {}
    for name, entry in files.items():
        guid = entry.get("guid")
        if guid:
            if guid in seen:
                raise AuditDenied(f"duplicate {which} GUID in {seen[guid]} and {name}")
            seen[guid] = name


def classify(path: str, change: str) -> tuple[str, str]:
    # This first slice NEVER sets approved or promotable=true.
    if path in {PROOF, PROOF + ".meta", FOLDER_META} and change == "added":
        return "candidate-for-human-review", "C4 generated proof scene / required metadata"
    if path == PROOF and change == "modified":
        return "candidate-for-human-review", "C4 proof scene modification (requires manual inspection)"
    if path.startswith("Packages/") or path.startswith("ProjectSettings/"):
        return "quarantined", "project configuration changed"
    return "quarantined", "not explicitly in C4 proof fixture scope"


def manifest(workspace: Path, stage: Path) -> dict:
    if workspace.resolve() == stage.resolve():
        raise AuditDenied("source and stage must be different directories")
    head = validate_source(workspace)
    source = inventory(workspace)
    staged = inventory(stage)
    unique_guids(source, "source")
    unique_guids(staged, "stage")
    original = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
    if original not in source or original not in staged:
        raise AuditDenied("original PlaytestScene missing in source or staged project")
    if source[original]["sha256"] != staged[original]["sha256"]:
        raise AuditDenied("original PlaytestScene modified in disposable stage")
    changes = []
    for path in sorted(source.keys() | staged.keys()):
        before, after = source.get(path), staged.get(path)
        if before == after:
            continue
        kind = "added" if before is None else "removed" if after is None else "modified"
        classification, reason = classify(path, kind)
        changes.append({"path": path, "change": kind, "classification": classification,
                        "reason": reason, "source": before, "stage": after,
                        "promotable": False})
    return {
        "schemaVersion": 1, "mode": "proposal-only", "issue": workspace.name,
        "sourceWorkspace": str(workspace.resolve()),
        "stageProject": str(stage.resolve()), "sourceRevision": head,
        "sourceSceneSha256": source.get("Assets/RPGKingdom/Scenes/PlaytestScene.unity", {}).get("sha256"),
        "stageSceneSha256": staged.get("Assets/RPGKingdom/Scenes/PlaytestScene.unity", {}).get("sha256"),
        "changes": changes, "counts": {
            "changed": len(changes),
            "reviewCandidates": sum(x["classification"] == "candidate-for-human-review" for x in changes),
            "quarantined": sum(x["classification"] == "quarantined" for x in changes)},
        "authorization": None, "copyback": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--stage", required=True, help="Operator-owned NTFS Unity stage mapped into WSL")
    args = parser.parse_args()
    try:
        print(json.dumps(manifest(Path(args.workspace), Path(args.stage)),
                         sort_keys=True, separators=(",", ":")))
        return 0
    except (AuditDenied, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"C5 proposal audit blocked: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
