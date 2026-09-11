#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "git-handoff-host-wrapper.py"
spec = importlib.util.spec_from_file_location("rpgk_git_handoff_host_wrapper", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load git-handoff-host-wrapper.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-host-sync-test-") as temp:
        root = Path(temp)
        remote = root / "remote.git"
        seed = root / "seed"
        workspace = root / "GH-98"

        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
        git(seed, "config", "user.name", "Test User")
        git(seed, "config", "user.email", "test@example.invalid")
        (seed / "base.txt").write_text("base\n", encoding="utf-8")
        git(seed, "add", "base.txt")
        git(seed, "commit", "-m", "base")
        git(seed, "remote", "add", "origin", str(remote))
        git(seed, "push", "-u", "origin", "main")
        git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

        git(seed, "switch", "-c", "codex/gh-98-test")
        (seed / "feature.txt").write_text("old feature\n", encoding="utf-8")
        git(seed, "add", "feature.txt")
        git(seed, "commit", "-m", "feature")
        git(seed, "push", "-u", "origin", "codex/gh-98-test")

        # Clone the old continuation state before remote/main advances.
        subprocess.run(["git", "clone", "-q", str(remote), str(workspace)], check=True)
        git(workspace, "config", "user.name", "Test User")
        git(workspace, "config", "user.email", "test@example.invalid")
        git(workspace, "switch", "codex/gh-98-test")
        old_head = git(workspace, "rev-parse", "HEAD")
        (workspace / ".git" / "info" / "exclude").write_text("/.symphony-attempt-complete\n", encoding="utf-8")
        (workspace / ".symphony-attempt-complete").touch()

        # Simulate PR #102 landing on main, followed by a host-created durable branch merge whose
        # tree is current main. The local issue checkout remains on the old feature head.
        git(seed, "switch", "main")
        (seed / "restored-asset.txt").write_text("restored\n", encoding="utf-8")
        git(seed, "add", "restored-asset.txt")
        git(seed, "commit", "-m", "restore assets")
        git(seed, "push", "origin", "main")

        git(seed, "switch", "codex/gh-98-test")
        git(seed, "merge", "--no-edit", "main")
        git(seed, "push", "origin", "codex/gh-98-test")
        remote_head = git(seed, "rev-parse", "HEAD")
        assert old_head != remote_head

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-98-test")
        assert ok, message
        assert git(workspace, "rev-parse", "HEAD") == remote_head
        assert (workspace / "restored-asset.txt").read_text(encoding="utf-8") == "restored\n"
        assert not git(workspace, "status", "--porcelain", "--untracked-files=all")

        # Dirty source work is preserved rather than discarded by host refresh.
        (workspace / "local-edit.txt").write_text("keep me\n", encoding="utf-8")
        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-98-test")
        assert not ok
        assert message is not None and "uncommitted source changes" in message
        assert (workspace / "local-edit.txt").is_file()
        (workspace / "local-edit.txt").unlink()

        # Clean local-only commits also fail closed rather than being rewritten.
        (workspace / "local-commit.txt").write_text("local\n", encoding="utf-8")
        git(workspace, "add", "local-commit.txt")
        git(workspace, "commit", "-m", "local only")
        local_only_head = git(workspace, "rev-parse", "HEAD")
        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-98-test")
        assert not ok
        assert message is not None and "local commits not present" in message
        assert git(workspace, "rev-parse", "HEAD") == local_only_head

    print("git-handoff-host-wrapper-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
