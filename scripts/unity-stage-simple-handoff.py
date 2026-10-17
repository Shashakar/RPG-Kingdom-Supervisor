#!/usr/bin/env python3
"""Operator-run minimal Unity stage -> codex feature branch handoff.

No Windows service, new approval authority, nonce, or worker broker operation.
'plan' reads only. 'apply' copies explicitly chosen Assets files and their
changed Unity .meta companions into a NEW feature branch; normal existing
git-handoff.sh handles tests/commit/push/PR after this tool returns.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("simple_stage_proposal", HERE / "unity-stage-proposal.py")
assert spec and spec.loader
proposal = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proposal
spec.loader.exec_module(proposal)

BRANCH = re.compile(r"codex/[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


class StageHandoffDenied(Exception):
    pass


def git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(workspace), *args], text=True,
                            capture_output=True, timeout=120, check=False)
    if check and result.returncode:
        raise StageHandoffDenied("Git operation failed: " + " ".join(args) +
                                 ": " + (result.stderr.strip() or result.stdout.strip())[:500])
    return result


def checked_path(value: str) -> str:
    if (not isinstance(value, str) or not value.startswith("Assets/")
            or "\\" in value or not proposal.SAFE.fullmatch(value)
            or any(p in ("", ".", "..") for p in value.split("/"))
            or value.endswith("/")):
        raise StageHandoffDenied("only canonical relative Assets/ file paths are supported")
    return value


def checked_branch(value: str) -> str:
    if (not BRANCH.fullmatch(value) or ".." in value or "//" in value
            or value.endswith("/") or value.endswith(".lock")):
        raise StageHandoffDenied("use a new codex/* feature branch name")
    return value


def file_is_plain(root: Path, rel: str) -> bool:
    """Source and destination must not traverse linked directories/files."""
    path = root
    for name in PurePosixPath(rel).parts:
        path = path / name
        if path.is_symlink():
            return False
    return True


def changed_entries(workspace: Path, stage: Path) -> tuple[str, dict[str, dict], list[dict]]:
    workspace = workspace.resolve(strict=True)
    stage = stage.resolve(strict=True)
    if (workspace == stage or workspace in stage.parents or stage in workspace.parents
            or not (stage / "Assets").is_dir()):
        raise StageHandoffDenied("stage and source must be distinct Unity projects")
    revision = proposal.validate_source(workspace)
    current_branch = git(workspace, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if current_branch not in ("", "main"):
        raise StageHandoffDenied("start from the clean issue main workspace, not an existing feature branch")
    source = proposal.inventory(workspace)
    staged = proposal.inventory(stage)
    proposal.unique_guids(source, "source")
    proposal.unique_guids(staged, "stage")
    changed: list[dict] = []
    for rel in sorted(source.keys() | staged.keys()):
        before, after = source.get(rel), staged.get(rel)
        if before == after:
            continue
        changed.append({
            "path": rel, "change": "added" if before is None else "removed" if after is None else "modified",
            "source": before, "stage": after,
            "selectable": rel.startswith("Assets/") and after is not None,
        })
    return revision, staged, changed


def choose(paths: list[str], staged: dict[str, dict], changes: list[dict]) -> list[dict]:
    by_path = {row["path"]: row for row in changes}
    selected: set[str] = set()
    if not paths:
        raise StageHandoffDenied("apply requires at least one explicit --path")
    for raw in paths:
        rel = checked_path(raw)
        row = by_path.get(rel)
        if row is None or not row["selectable"]:
            raise StageHandoffDenied(f"selected file is not a changed staged Assets file: {rel}")
        selected.add(rel)
        if rel.endswith(".meta"):
            continue
        # New/modified Unity assets require their associated .meta where it
        # differs; unchanged existing sidecars stay on the Git baseline.
        sidecar = rel + ".meta"
        if row["change"] == "added" and sidecar not in staged:
            raise StageHandoffDenied(f"new Unity asset is missing its .meta: {rel}")
        if sidecar in by_path and by_path[sidecar]["selectable"]:
            selected.add(sidecar)
        folder = PurePosixPath(rel).parent
        while str(folder) not in (".", "Assets"):
            meta = str(folder) + ".meta"
            if meta in by_path and by_path[meta]["selectable"]:
                selected.add(meta)
            folder = folder.parent
    return [by_path[path] for path in sorted(selected)]


def plan(workspace: Path, stage: Path, selected_paths: list[str]) -> dict:
    revision, staged, changed = changed_entries(workspace, stage)
    selected = choose(selected_paths, staged, changed) if selected_paths else []
    chosen = {row["path"] for row in selected}
    return {
        "status": "review-stage-diff", "issue": workspace.name,
        "sourceRevision": revision, "stage": str(stage),
        "selected": selected,
        "excluded": [x for x in changed if x["path"] not in chosen],
        "copyback": False,
    }


def apply(workspace: Path, stage: Path, branch: str, selected_paths: list[str]) -> dict:
    branch = checked_branch(branch)
    if not selected_paths:
        raise StageHandoffDenied("apply requires at least one explicit --path")
    # Build the precise selection while issue workspace is still clean/main.
    transaction = plan(workspace, stage, selected_paths)
    entries = transaction["selected"]
    if git(workspace, "show-ref", "--verify", "--quiet", "refs/heads/" + branch,
           check=False).returncode == 0:
        raise StageHandoffDenied("local feature branch already exists")
    if git(workspace, "show-ref", "--verify", "--quiet", "refs/remotes/origin/" + branch,
           check=False).returncode == 0:
        raise StageHandoffDenied("remote-tracking feature branch already exists")
    # No network dependency here. The existing Git host handoff checks the
    # live remote when publishing and refuses non-fast-forward pushes.
    for entry in entries:
        rel = entry["path"]
        if not file_is_plain(stage, rel) or not file_is_plain(workspace, rel):
            raise StageHandoffDenied("linked source or destination file path: " + rel)
        # Untracked ignored assets would disappear from the normal Git handoff.
        tracked = git(workspace, "ls-files", "--error-unmatch", "--", rel,
                      check=False).returncode == 0
        ignored = git(workspace, "check-ignore", "-q", "--", rel,
                      check=False).returncode == 0
        if ignored and not tracked:
            raise StageHandoffDenied("selected asset would be ignored by Git: " + rel)

    # Ordinary disposable snapshot in /tmp, not protected or immutable; it
    # merely avoids copying moving files while changing Git branches.
    with tempfile.TemporaryDirectory(prefix="rpgk-stage-handoff-") as temp:
        snapshot = Path(temp)
        for entry in entries:
            rel = entry["path"]
            dest = snapshot / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with (stage / rel).open("rb") as src, dest.open("xb") as out:
                shutil.copyfileobj(src, out)
            if proposal.digest(dest) != entry["stage"]["sha256"]:
                raise StageHandoffDenied("stage asset changed while preparing copy: " + rel)
        if proposal.validate_source(workspace) != transaction["sourceRevision"]:
            raise StageHandoffDenied("source main advanced before branch creation")
        # No write to main: create/switch branch BEFORE copying any Unity file.
        git(workspace, "switch", "-c", branch)
        copied: list[str] = []
        for entry in entries:
            rel = entry["path"]
            destination = workspace / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot / rel, destination)
            if proposal.digest(destination) != entry["stage"]["sha256"]:
                raise StageHandoffDenied("destination checksum mismatch on feature branch: " + rel)
            copied.append(rel)
    return {
        "status": "copied-to-feature-branch",
        "issue": transaction["issue"], "branch": branch,
        "sourceRevision": transaction["sourceRevision"],
        "copiedPaths": copied,
        "excludedPaths": [x["path"] for x in transaction["excluded"]],
        "next": "run required Unity validation, then existing git-handoff.sh handoff to commit/push/open PR",
        "commit": False, "push": False, "prCreated": False, "merged": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--branch", help="new codex/* branch (required for apply)")
    parser.add_argument("--path", action="append", default=[],
                        help="explicit changed Assets file (repeat as needed)")
    args = parser.parse_args()
    try:
        if args.action == "apply":
            if not args.branch:
                raise StageHandoffDenied("apply requires --branch")
            result = apply(args.workspace, args.stage, args.branch, args.path)
        else:
            if args.branch:
                raise StageHandoffDenied("plan does not create branches; omit --branch")
            result = plan(args.workspace, args.stage, args.path)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (StageHandoffDenied, proposal.AuditDenied, OSError, ValueError,
            subprocess.SubprocessError) as exc:
        print(f"Unity simple stage handoff blocked: {exc}", file=sys.stderr)
        return 82


if __name__ == "__main__":
    raise SystemExit(main())
