#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REMOTE="$TMP/remote.git"
SEED="$TMP/seed"

git init --bare -q "$REMOTE"
git init -q "$SEED"
git -C "$SEED" config user.name "Test User"
git -C "$SEED" config user.email "test@example.invalid"
printf 'base\n' > "$SEED/base.txt"
git -C "$SEED" add base.txt
git -C "$SEED" commit -qm "base"
git -C "$SEED" branch -M main
git -C "$SEED" remote add origin "$REMOTE"
git -C "$SEED" push -q -u origin main
git -C "$REMOTE" symbolic-ref HEAD refs/heads/main

git -C "$SEED" switch -qc codex/test-refresh
printf 'feature\n' > "$SEED/feature.txt"
git -C "$SEED" add feature.txt
git -C "$SEED" commit -qm "feature"
git -C "$SEED" push -q -u origin codex/test-refresh

git -C "$SEED" switch -q main
printf 'main update\n' > "$SEED/main-update.txt"
git -C "$SEED" add main-update.txt
git -C "$SEED" commit -qm "advance main"
git -C "$SEED" push -q origin main

prepare_workspace() {
  local name="$1"
  local branch="$2"
  local path="$TMP/$name"
  git clone -q "$REMOTE" "$path"
  git -C "$path" config user.name "Test User"
  git -C "$path" config user.email "test@example.invalid"
  git -C "$path" switch -q "$branch"
  printf '/.symphony-attempt-complete\n' >> "$path/.git/info/exclude"
  touch "$path/.symphony-attempt-complete"
  printf '%s\n' "$path"
}

run_refresh() {
  local path="$1"
  (cd "$path" && RPGK_EXPECTED_ORIGIN_URL="$REMOTE" bash "$ROOT/scripts/refresh-rearmed-workspace.sh")
}

# A clean continuation branch that is durable on origin should absorb current main on the host.
workspace="$(prepare_workspace GH-123 codex/test-refresh)"
run_refresh "$workspace" >/dev/null
git -C "$workspace" merge-base --is-ancestor origin/main HEAD || {
  echo "refreshed continuation does not contain current origin/main" >&2
  exit 1
}
git -C "$workspace" merge-base --is-ancestor origin/codex/test-refresh HEAD || {
  echo "refresh rewrote rather than extended the durable feature branch" >&2
  exit 1
}
[[ -f "$workspace/main-update.txt" ]] || { echo "main update was not materialized in refreshed workspace" >&2; exit 1; }
[[ -z "$(git -C "$workspace" status --porcelain --untracked-files=all)" ]] || {
  echo "successful refresh left a dirty worktree" >&2
  exit 1
}

# Uncommitted source work from a halted worker is never discarded automatically.
dirty_workspace="$(prepare_workspace GH-124 codex/test-refresh)"
printf 'local work\n' > "$dirty_workspace/local-only.txt"
dirty_head="$(git -C "$dirty_workspace" rev-parse HEAD)"
set +e
run_refresh "$dirty_workspace" >"$TMP/dirty.out" 2>"$TMP/dirty.err"
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected dirty continuation to fail closed with 73, got $status" >&2; exit 1; }
grep -Fq "uncommitted source changes" "$TMP/dirty.err" || { echo "dirty continuation failure was not explained" >&2; exit 1; }
[[ "$(git -C "$dirty_workspace" rev-parse HEAD)" == "$dirty_head" ]] || { echo "dirty continuation HEAD changed" >&2; exit 1; }
[[ -f "$dirty_workspace/local-only.txt" ]] || { echo "dirty continuation source work was discarded" >&2; exit 1; }

# Clean but unpushed commits are also preserved rather than implicitly rewritten against main.
local_workspace="$(prepare_workspace GH-125 codex/test-refresh)"
printf 'unpushed\n' > "$local_workspace/unpushed.txt"
git -C "$local_workspace" add unpushed.txt
git -C "$local_workspace" commit -qm "local only"
local_head="$(git -C "$local_workspace" rev-parse HEAD)"
set +e
run_refresh "$local_workspace" >"$TMP/local.out" 2>"$TMP/local.err"
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected unpushed continuation to fail closed with 73, got $status" >&2; exit 1; }
grep -Fq "local commits not present" "$TMP/local.err" || { echo "unpushed continuation failure was not explained" >&2; exit 1; }
[[ "$(git -C "$local_workspace" rev-parse HEAD)" == "$local_head" ]] || { echo "unpushed continuation HEAD changed" >&2; exit 1; }

# A stale durable PR branch that conflicts with newly merged main must halt before Codex and leave
# no half-merged index/worktree behind.
git -C "$SEED" switch -q main
printf 'common base\n' > "$SEED/conflict.txt"
git -C "$SEED" add conflict.txt
git -C "$SEED" commit -qm "conflict base"
git -C "$SEED" push -q origin main
git -C "$SEED" switch -qc codex/conflict-refresh
printf 'feature version\n' > "$SEED/conflict.txt"
git -C "$SEED" add conflict.txt
git -C "$SEED" commit -qm "feature conflict"
git -C "$SEED" push -q -u origin codex/conflict-refresh
git -C "$SEED" switch -q main
printf 'main version\n' > "$SEED/conflict.txt"
git -C "$SEED" add conflict.txt
git -C "$SEED" commit -qm "main conflict"
git -C "$SEED" push -q origin main

conflict_workspace="$(prepare_workspace GH-126 codex/conflict-refresh)"
conflict_head="$(git -C "$conflict_workspace" rev-parse HEAD)"
set +e
run_refresh "$conflict_workspace" >"$TMP/conflict.out" 2>"$TMP/conflict.err"
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected conflicting continuation to fail closed with 73, got $status" >&2; exit 1; }
grep -Fq "conflicts with the durable continuation branch" "$TMP/conflict.err" || {
  echo "conflicting continuation failure was not explained" >&2
  exit 1
}
[[ "$(git -C "$conflict_workspace" rev-parse HEAD)" == "$conflict_head" ]] || { echo "conflicting continuation HEAD changed" >&2; exit 1; }
[[ ! -e "$conflict_workspace/.git/MERGE_HEAD" ]] || { echo "conflicting continuation left merge state behind" >&2; exit 1; }
[[ -z "$(git -C "$conflict_workspace" status --porcelain --untracked-files=all)" ]] || {
  echo "conflicting continuation did not restore a clean worktree" >&2
  exit 1
}

echo "rearmed-workspace-refresh-test: PASS"
