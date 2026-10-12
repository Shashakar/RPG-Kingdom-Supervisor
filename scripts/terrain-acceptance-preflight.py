#!/usr/bin/env python3
"""Read-only preflight for GH-264 terrain acceptance on the installed WSL host.

No GitHub mutations, broker requests, Unity process changes, service restarts,
workspace cloning, or issue rearming. This is not Unity authoring validation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

SCENE = "Assets/RPGKingdom/Scenes/PlaytestScene.unity"
CAPABILITY = "Assets/RPGKingdom/Editor/SymphonyMechanicalSceneAuthoringCapabilities.json"
OPERATIONS = {"edit-terrain", "create-heightfield-ground", "bake-navmesh"}


def probe(command: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        p = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return p.returncode == 0, (p.stdout.strip() or p.stderr.strip())[:500]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:300]


def evaluate(state: Path, root: Path, supervisor: Path, *, issue: str = "GH-264") -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    check("supervisor-code", (supervisor / "scripts/run-symphony.sh").is_file(),
          "installed Supervisor start script must exist")
    ok, msg = probe(["systemctl", "--user", "is-active", "rpg-kingdom-supervisor.service"])
    if not ok:
        ok, msg = probe(["systemctl", "is-active", "rpg-kingdom-supervisor.service"])
    check("managed-service", ok, msg or "systemd unit is not active")
    for name in ("unity-broker", "unity-author-broker", "git-broker"):
        path = state / name / "status.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ready = data.get("protocolVersion") == 1 and data.get("state") == "ready" and data.get("activeRequest") is None
            pid = data.get("pid")
            alive = isinstance(pid, int) and pid > 0 and Path(f"/proc/{pid}").is_dir()
            check(name, ready and alive, f"state={data.get('state')} active={bool(data.get('activeRequest'))} pidAlive={alive}")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            check(name, False, f"status unavailable: {type(exc).__name__}")

    unity_lock = state / "locks/unity-editor.lock"
    owner = (unity_lock / "owner")
    check("unity-resource-free", not unity_lock.exists(),
          "lock absent" if not unity_lock.exists() else ("owned by " + (owner.read_text().strip() if owner.is_file() else "unknown")))

    paused = root / "GH-260"
    check("paused-gh260-untouched", True,
          f"read-only preflight does not modify {paused}")

    trial = root / issue
    if trial.exists():
        valid_git, top = probe(["git", "-C", str(trial), "rev-parse", "--show-toplevel"])
        check("trial-workspace", valid_git and top == str(trial.resolve()), "existing GH-264 workspace is a Git checkout" if valid_git else top)
        if valid_git:
            _, branch = probe(["git", "-C", str(trial), "branch", "--show-current"])
            _, current = probe(["git", "-C", str(trial), "rev-parse", "HEAD"])
            _, baseline = probe(["git", "-C", str(trial), "rev-parse", "origin/main"])
            _, dirty = probe(["git", "-C", str(trial), "status", "--porcelain", "--untracked-files=all"])
            acceptable = branch in {"", "main"} or branch.startswith("codex/264-")
            check("trial-clean-baseline", not dirty and current == baseline and acceptable,
                  f"branch={branch or 'detached'} clean={not bool(dirty)} baseline={current == baseline}")
    else:
        check("trial-workspace", True, "not created yet; normal issue dispatch should create it")

    ok, detail = probe(["git", "-C", str(supervisor), "status", "--porcelain"])
    check("supervisor-checkout", ok, "Git readable" if ok else detail)

    return {"issue": issue, "mode": "read-only", "ready": all(item["passed"] for item in checks), "checks": checks,
            "note": "Issue labels, scene geometry, and Unity test results require separate verification; this preflight does not arm dispatch."}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state-root", default=os.environ.get("RPGK_SUPERVISOR_STATE_ROOT",
                            str(Path.home() / ".local/state/rpg-kingdom-supervisor")))
    p.add_argument("--workspace-root", default=os.environ.get("RPGK_SYMPHONY_WORKSPACE_ROOT",
                            str(Path.home() / "code/rpg-kingdom-symphony-workspaces")))
    p.add_argument("--supervisor-root", default=os.environ.get("RPGK_SUPERVISOR_ROOT",
                            str(Path.home() / "src/RPG-Kingdom-Supervisor")))
    args = p.parse_args()
    outcome = evaluate(Path(args.state_root).expanduser(), Path(args.workspace_root).expanduser(),
                       Path(args.supervisor_root).expanduser())
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0 if outcome["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
