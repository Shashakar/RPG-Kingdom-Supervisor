#!/usr/bin/env python3
"""Host-only Rekindle visual capture; Claude cannot invoke this script or Unity.

The model writes JSON, then the dispatcher verifies an issue-scoped contract,
takes the shared Unity-editor lease, and calls this fixed Windows-stage runner.
No prefab/scene/licensed source bytes are copied from the stage to Git.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import importlib.util

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("claude_visual_policy", HERE / "claude-visual-policy.py")
assert SPEC and SPEC.loader
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)

RUNNER = HERE / "windows" / "run-rekindle-visual.ps1"
PNG = b"\x89PNG\r\n\x1a\n"
VERSION_RE = re.compile(r"m_EditorVersion:\s*([0-9]+\.[0-9]+\.[0-9]+[abfp][0-9]+)")
RUN_RE = re.compile(r"GH-[1-9][0-9]*-visual-[a-f0-9]{12}")


class VisualHostError(RuntimeError):
    pass


def _safe_root(root: Path, workspace: Path) -> Path:
    if workspace.is_symlink() or not workspace.is_dir():
        raise VisualHostError("visual workspace must be a real directory")
    parent = workspace.resolve().parent
    if parent != root.resolve():
        raise VisualHostError("visual workspace outside configured root")
    return workspace.resolve()


def host_ready() -> tuple[str, str]:
    POLICY.require_host_opt_in()
    source = os.environ.get("RPGK_CLAUDE_VISUAL_ASSET_PROJECT_WINDOWS", "")
    roots = os.environ.get("RPGK_CLAUDE_VISUAL_LICENSED_ROOTS", "")
    if not source or not roots:
        raise VisualHostError("host must configure imported-assets project and exact approved asset roots")
    if not (RUNNER.is_file() and shutil.which("powershell.exe") and shutil.which("wslpath")):
        raise VisualHostError("reviewed Windows Unity visual runner/WSL bridge is unavailable")
    if not 1 <= len(roots.split(";")) <= 12:
        raise VisualHostError("visual asset root list outside bounds")
    for name in roots.split(";"):
        if (not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", name)
                or name in {".", "..", "Rekindle", "Settings", "ThirdParty", "__SupervisorVisualTrial"}):
            raise VisualHostError("unsafe approved asset root")
    return source, roots


@contextmanager
def unity_lease(state_root: Path, workspace: Path):
    """Do not reclaim another worker's lock or bypass uncertain ownership."""
    lock = state_root / "locks" / "unity-editor.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise VisualHostError("Unity Editor is already leased; leave Claude visual task queued") from exc
    owner, work = workspace.name, str(workspace.resolve())
    (lock / "owner").write_text(owner + "\n", encoding="utf-8")
    (lock / "workspace").write_text(work + "\n", encoding="utf-8")
    success = False
    try:
        yield
        success = True
    finally:
        # On timeout/crash/ambiguous Windows cleanup retain the lock, requiring
        # inspection. A later worker must never silently steal this resource.
        if success:
            if ((lock / "owner").read_text(encoding="utf-8").strip() != owner or
                (lock / "workspace").read_text(encoding="utf-8").strip() != work):
                raise VisualHostError("Unity lease changed ownership; retaining lock")
            quarantine = lock.with_name(lock.name + ".released." + uuid.uuid4().hex)
            lock.rename(quarantine)
            shutil.rmtree(quarantine)


def _winpath(path: Path) -> str:
    result = subprocess.run(["wslpath", "-w", str(path)], capture_output=True,
                            text=True, timeout=8, check=True)
    return result.stdout.strip()


def _unity_version(workspace: Path) -> str:
    file = workspace / "ProjectSettings" / "ProjectVersion.txt"
    value = file.read_text(encoding="utf-8")
    match = VERSION_RE.search(value)
    if not match:
        raise VisualHostError("Unity ProjectVersion is absent or unsupported")
    return match.group(1)


def _expected_names(manifest: dict) -> list[str]:
    return [f"{experiment['kind']}-{shot['id']}.png"
            for experiment in manifest["experiments"] for shot in experiment["shots"]]


def verify_evidence(output: Path, manifest: dict) -> dict:
    expected = _expected_names(manifest)
    summary_file = output / "summary.json"
    if not summary_file.is_file() or summary_file.is_symlink():
        raise VisualHostError("Unity visual summary missing")
    try:
        data = json.loads(summary_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise VisualHostError("Unity visual summary malformed") from exc
    if (data.get("success") is not True or data.get("issue") != manifest["issue"] or
        data.get("captures") != expected):
        raise VisualHostError("Unity preview did not attest the exact expected captures")
    actual = {p.name for p in output.iterdir()}
    if actual != set(expected) | {"summary.json"}:
        raise VisualHostError("unexpected visual evidence file; fail closed")
    fingerprints = {}
    for name in expected:
        file = output / name
        if file.is_symlink() or not file.is_file():
            raise VisualHostError("missing/symlinked visual screenshot")
        raw = file.read_bytes()
        if (len(raw) < 500 or raw[:8] != PNG or
            int.from_bytes(raw[16:20], "big") != 1280 or
            int.from_bytes(raw[20:24], "big") != 720):
            raise VisualHostError("Unity did not produce a valid 1280x720 PNG")
        fingerprints[name] = hashlib.sha256(raw).hexdigest()
    return fingerprints


def run_visual(workspace: Path, auth: dict, number: int, *, state_root: Path,
               workspace_root: Path, timeout: int = 2400) -> dict:
    workspace = _safe_root(workspace_root, workspace)
    source, roots = host_ready()
    manifest = POLICY.read_manifest(workspace, auth, number)
    output = POLICY.evidence_root(workspace, number)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents if p != workspace.parent):
        raise VisualHostError("visual output path contains a symlink")
    if output.exists() and any(output.iterdir()):
        raise VisualHostError("visual evidence output already exists; preserve for human inspection")
    output.mkdir(parents=True, exist_ok=True)
    request = POLICY.manifest_path(workspace, number)
    run_id = f"GH-{number}-visual-{hashlib.sha256(request.read_bytes()).hexdigest()[:12]}"
    if not RUN_RE.fullmatch(run_id):
        raise VisualHostError("visual run-id invalid")
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", _winpath(RUNNER), "-SourceProjectPath", _winpath(workspace),
        "-AssetProjectPath", source, "-ManifestPath", _winpath(request),
        "-OutputPath", _winpath(output), "-RunId", run_id,
        "-UnityVersion", _unity_version(workspace), "-LicensedRoots", roots,
    ]
    # Unity owner lock is held by the *caller* across validation and this command.
    # A timeout is ambiguous; retain the lease until the Windows child is checked.
    try:
        result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VisualHostError("Unity preview timed out; Windows process ownership needs inspection") from exc
    if result.returncode != 0:
        raise VisualHostError(
            f"staged Unity preview exited {result.returncode}: {(result.stderr or result.stdout)[-900:]}"
        )
    hashes = verify_evidence(output, manifest)
    manifest_sha = hashlib.sha256(request.read_bytes()).hexdigest()
    branch_result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=workspace, text=True, capture_output=True, check=True, timeout=8,
    )
    receipt = {
        "protocolVersion": 1,
        "issue": number,
        "runId": run_id,
        "workspace": str(workspace),
        "branch": branch_result.stdout.strip(),
        "manifestSha256": manifest_sha,
        "screenshots": hashes,
        "summarySha256": hashlib.sha256((output / "summary.json").read_bytes()).hexdigest(),
        "stageOnly": True,
        "hostVerified": True,
    }
    receipt_dir = state_root / "claude-visual"
    receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = receipt_dir / f"GH-{number}.json"
    if receipt_path.is_symlink():
        raise VisualHostError("visual host receipt path cannot be a symlink")
    tmp = receipt_dir / (f".GH-{number}-" + uuid.uuid4().hex)
    with tmp.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
        handle.write("\\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, receipt_path)
    return {"runId": run_id, "screenshots": hashes, "stageOnly": True,
            "manifestSha256": manifest_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--authority-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        auth = json.loads(args.authority_json.read_text(encoding="utf-8"))
        with unity_lease(args.state_root.resolve(), args.workspace.resolve()):
            result = run_visual(args.workspace, auth, args.issue,
                                state_root=args.state_root.resolve(),
                                workspace_root=args.workspace_root.resolve())
    except (VisualHostError, POLICY.VisualContractError, ValueError, OSError,
            subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 75
    print(json.dumps({"status": "completed", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
