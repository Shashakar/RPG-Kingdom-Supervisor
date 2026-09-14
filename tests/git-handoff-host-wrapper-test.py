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


def init_repo(root: Path, name: str) -> tuple[Path, Path]:
    remote = root / f"{name}-remote.git"
    seed = root / f"{name}-seed"
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
    return remote, seed


def clone_workspace(root: Path, remote: Path, name: str) -> Path:
    workspace = root / name
    subprocess.run(["git", "clone", "-q", str(remote), str(workspace)], check=True)
    git(workspace, "config", "user.name", "Test User")
    git(workspace, "config", "user.email", "test@example.invalid")
    (workspace / ".git" / "info" / "exclude").write_text("/.symphony-attempt-complete\n", encoding="utf-8")
    (workspace / ".symphony-attempt-complete").touch()
    return workspace


def commit_file(repo: Path, path: str, contents: str, message: str) -> str:
    (repo / path).write_text(contents, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def assert_clean(workspace: Path) -> None:
    assert not git(workspace, "status", "--porcelain", "--untracked-files=all")
    assert not (workspace / ".git" / "MERGE_HEAD").exists()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rpgk-host-sync-test-") as temp:
        root = Path(temp)

        # A clean local-only branch is durable continuation state even if it was never pushed.
        remote, seed = init_repo(root, "local-only")
        workspace = clone_workspace(root, remote, "GH-91")
        git(workspace, "switch", "-c", "codex/gh-91-local")
        local_feature = commit_file(workspace, "feature.txt", "local feature\n", "local feature")
        main_update = commit_file(seed, "main-update.txt", "main update\n", "advance main")
        git(seed, "push", "origin", "main")

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-91-local")
        assert ok, message
        assert module.is_ancestor(workspace, local_feature, "HEAD")
        assert module.is_ancestor(workspace, "origin/main", "HEAD")
        assert (workspace / "main-update.txt").read_text(encoding="utf-8") == "main update\n"
        assert git(workspace, "rev-parse", "origin/main") == main_update
        assert not module.ref_exists(workspace, "refs/remotes/origin/codex/gh-91-local")
        assert_clean(workspace)

        # A clean local branch ahead of its remote feature branch preserves its unpushed commits.
        remote, seed = init_repo(root, "local-ahead")
        git(seed, "switch", "-c", "codex/gh-92-ahead")
        remote_feature = commit_file(seed, "feature.txt", "remote feature\n", "remote feature")
        git(seed, "push", "-u", "origin", "codex/gh-92-ahead")
        workspace = clone_workspace(root, remote, "GH-92")
        git(workspace, "switch", "codex/gh-92-ahead")
        local_feature = commit_file(workspace, "local.txt", "local continuation\n", "local continuation")
        git(seed, "switch", "main")
        commit_file(seed, "main-update.txt", "new main\n", "advance main")
        git(seed, "push", "origin", "main")

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-92-ahead")
        assert ok, message
        assert module.is_ancestor(workspace, remote_feature, "HEAD")
        assert module.is_ancestor(workspace, local_feature, "HEAD")
        assert module.is_ancestor(workspace, "origin/main", "HEAD")
        assert_clean(workspace)

        # A local branch behind its remote fast-forwards first, then absorbs current main.
        remote, seed = init_repo(root, "remote-ahead")
        git(seed, "switch", "-c", "codex/gh-93-behind")
        commit_file(seed, "feature.txt", "feature one\n", "feature one")
        git(seed, "push", "-u", "origin", "codex/gh-93-behind")
        workspace = clone_workspace(root, remote, "GH-93")
        git(workspace, "switch", "codex/gh-93-behind")
        remote_feature = commit_file(seed, "feature-two.txt", "feature two\n", "feature two")
        git(seed, "push", "origin", "codex/gh-93-behind")
        git(seed, "switch", "main")
        commit_file(seed, "main-update.txt", "new main\n", "advance main")
        git(seed, "push", "origin", "main")

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-93-behind")
        assert ok, message
        assert module.is_ancestor(workspace, remote_feature, "HEAD")
        assert module.is_ancestor(workspace, "origin/main", "HEAD")
        assert_clean(workspace)

        # True feature-branch divergence still fails closed and preserves the original local HEAD.
        remote, seed = init_repo(root, "diverged")
        git(seed, "switch", "-c", "codex/gh-94-diverged")
        commit_file(seed, "feature.txt", "shared\n", "shared feature")
        git(seed, "push", "-u", "origin", "codex/gh-94-diverged")
        workspace = clone_workspace(root, remote, "GH-94")
        git(workspace, "switch", "codex/gh-94-diverged")
        local_head = commit_file(workspace, "local.txt", "local\n", "local side")
        commit_file(seed, "remote.txt", "remote\n", "remote side")
        git(seed, "push", "origin", "codex/gh-94-diverged")

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-94-diverged")
        assert not ok
        assert message is not None and "diverged" in message
        assert git(workspace, "rev-parse", "HEAD") == local_head
        assert_clean(workspace)

        # Dirty source work is preserved through the host-owned continuation transaction.
        remote, seed = init_repo(root, "dirty")
        workspace = clone_workspace(root, remote, "GH-95")
        git(workspace, "switch", "-c", "codex/gh-95-dirty")
        (workspace / "local-edit.txt").write_text("keep me\n", encoding="utf-8")
        dirty_head = git(workspace, "rev-parse", "HEAD")
        main_update = commit_file(seed, "dependency.txt", "new dependency\n", "advance main")
        git(seed, "push", "origin", "main")

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-95-dirty")
        assert ok, message
        assert git(workspace, "rev-parse", "HEAD") != dirty_head
        assert module.is_ancestor(workspace, main_update, "HEAD")
        assert (workspace / "local-edit.txt").read_text(encoding="utf-8") == "keep me\n"
        assert git(workspace, "status", "--porcelain", "--untracked-files=all") == "?? local-edit.txt"
        assert not (workspace / ".git" / "MERGE_HEAD").exists()

        # A main merge conflict rolls all host mutations back to the exact original clean HEAD.
        remote, seed = init_repo(root, "conflict")
        commit_file(seed, "conflict.txt", "common\n", "conflict base")
        git(seed, "push", "origin", "main")
        workspace = clone_workspace(root, remote, "GH-96")
        git(workspace, "switch", "-c", "codex/gh-96-conflict")
        original_head = commit_file(workspace, "conflict.txt", "feature version\n", "feature conflict")
        commit_file(seed, "conflict.txt", "main version\n", "main conflict")
        git(seed, "push", "origin", "main")

        ok, message = module.sync_rearmed_branch(workspace, "codex/gh-96-conflict")
        assert not ok
        assert message is not None and "conflicts with the continuation branch" in message
        assert git(workspace, "rev-parse", "HEAD") == original_head
        assert (workspace / "conflict.txt").read_text(encoding="utf-8") == "feature version\n"
        assert_clean(workspace)

    print("git-handoff-host-wrapper-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
