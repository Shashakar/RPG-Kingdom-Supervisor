#!/usr/bin/env python3
"""C5g scratch-only Windows/NTFS copy-integrity lab. NO live path arguments.

This intentionally creates fake Unity assets in a fresh temp directory.
A stable hash-matched *copy* is not an immutable, access-controlled snapshot.
No protected Windows service, source copyback or authorization is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile
from typing import Callable

ROOTS = ("Assets", "Packages", "ProjectSettings")
MAX_FILES = 150
MAX_BYTES = 32 * 1024 * 1024


class SnapshotLabDenied(Exception):
    pass


def reject_reparse(path: Path) -> None:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or (hasattr(os.path, "isjunction") and os.path.isjunction(path))):
        raise SnapshotLabDenied("linked/reparse/junction entry forbidden")


def checksum(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as data:
        for block in iter(lambda: data.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def inventory(root: Path) -> dict[str, dict]:
    if not root.is_dir():
        raise SnapshotLabDenied("snapshot input root missing")
    reject_reparse(root)
    files: dict[str, dict] = {}
    total = 0
    for part in ROOTS:
        base = root / part
        reject_reparse(base)
        if not base.is_dir():
            raise SnapshotLabDenied("incomplete Unity project roots")
        for current, dirs, names in os.walk(base, followlinks=False):
            here = Path(current)
            reject_reparse(here)
            for entry in dirs:
                reject_reparse(here / entry)
            dirs.sort()
            for filename in sorted(names):
                path = here / filename
                reject_reparse(path)
                if not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
                    raise SnapshotLabDenied("nonregular asset entry")
                relative = path.relative_to(root).as_posix()
                if any(part in {"", ".", ".."} for part in relative.split("/")):
                    raise SnapshotLabDenied("unsafe project asset path")
                total += path.stat().st_size
                if len(files) >= MAX_FILES or total > MAX_BYTES:
                    raise SnapshotLabDenied("scratch inventory size limit reached")
                folded = relative.casefold()
                if any(k.casefold() == folded for k in files):
                    raise SnapshotLabDenied("case insensitive collision")
                files[relative] = {"sha256": checksum(path), "bytes": path.stat().st_size}
    return files


def copy_scratch(stage: Path, snapshots: Path,
                 *, after_copy: Callable[[], None] | None = None) -> tuple[Path, dict]:
    # This API is intentionally bounded and used ONLY with private temporary
    # synthetic fixtures, never a real stage or host-owned snapshot destination.
    root = snapshots / ("scratch-" + secrets.token_hex(12))
    if stage.resolve() == snapshots.resolve():
        raise SnapshotLabDenied("source and destination are identical")
    before = inventory(stage)
    root.mkdir(mode=0o700)
    try:
        for folder in ROOTS:
            (root / folder).mkdir()
        for rel, metadata in sorted(before.items()):
            src, dest = stage / rel, root / rel
            reject_reparse(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as inp, dest.open("xb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            if checksum(dest) != metadata["sha256"]:
                raise SnapshotLabDenied("copied snapshot hash mismatch")
        if after_copy is not None:
            after_copy()
        if inventory(stage) != before:
            raise SnapshotLabDenied("source mutated during scratch snapshot copy")
        if inventory(root) != before:
            raise SnapshotLabDenied("scratch snapshot differs from source")
        return root, before
    except BaseException:
        # This is a private, bounded scratch lab. Production recovery must
        # use an owner-scoped durable journal and protected Windows handles.
        shutil.rmtree(root)
        raise


def write_asset(root: Path, relative: str, value: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def run_lab() -> dict:
    with tempfile.TemporaryDirectory(prefix="rpgk-c5g-fake-stage-") as tmp:
        base = Path(tmp)
        stage = base / "fake-stage"
        stage.mkdir()
        for folder in ROOTS:
            (stage / folder).mkdir()
        scene = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
        proof = "Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity"
        write_asset(stage, scene, "fixture original scene\n")
        write_asset(stage, proof, "fixture generated scene\n")
        write_asset(stage, proof + ".meta", "guid: " + "a" * 32 + "\n")
        write_asset(stage, "Assets/Settings/Mobile_RPAsset.asset", "quarantined fixture URP delta\n")
        write_asset(stage, "Packages/manifest.json", '{"dependencies":{}}\n')
        write_asset(stage, "ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 6000.3.10f1\n")
        dest = base / "fake-service-snapshots"
        dest.mkdir()
        snapshot, expected = copy_scratch(stage, dest)
        snapshot_hash = checksum(snapshot / proof)
        # Source edits after copying must NOT rewrite the copied bytes.
        write_asset(stage, proof, "updated after copying\n")
        if checksum(snapshot / proof) != snapshot_hash:
            raise SnapshotLabDenied("scratch snapshot changed with source edits")
        # A concurrent mutation must be rejected during snapshot construction.
        try:
            copy_scratch(stage, dest,
                         after_copy=lambda: write_asset(stage, scene, "mutated during copy\n"))
        except SnapshotLabDenied:
            pass
        else:
            raise SnapshotLabDenied("concurrent mutation was accepted")
        if len(list(dest.iterdir())) != 1:
            raise SnapshotLabDenied("failed snapshot left extra scratch files")
        return {
            "status": "scratch-copy-integrity-only",
            "platform": os.name, "fileCount": len(expected),
            "copiedHashesVerified": True, "postCopySourceMutationIsolated": True,
            "concurrentMutationDetected": True,
            "realStageUsed": False, "windowsServiceDeployed": False,
            "stageFrozen": False, "immutableSnapshotVerified": False,
            "independentHumanAuthorization": False,
            "promotionAuthorized": False, "nonceConsumed": False,
            "copyback": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # No path, identity, stage, destination or approval args.
    try:
        print(json.dumps(run_lab(), sort_keys=True))
        return 0
    except (SnapshotLabDenied, OSError, ValueError) as exc:
        print(f"C5g scratch copy denied: {exc}", file=__import__("sys").stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
