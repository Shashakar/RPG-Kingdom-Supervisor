#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REMOTE="$TMP/remote.git"
SEED="$TMP/seed"
FAKE_SUPERVISOR="$TMP/fake-supervisor"
DIRECT_SYNC="$TMP/direct-sync.py"
mkdir -p "$FAKE_SUPERVISOR/scripts"

cat > "$DIRECT_SYNC" <<'PY'
#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import sys

module_path = Path(sys.argv[1])
workspace = Path(sys.argv[2])
branch = sys.argv[3]
spec = importlib.util.spec_from_file_location("rpgk_git_handoff_host_wrapper", module_path)
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load git-handoff-host-wrapper.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ok, message = module.sync_rearmed_branch(workspace, branch)
if not ok:
    print(message or "continuation workspace refresh blocked", file=sys.stderr)
    raise SystemExit(73)
print(f"refreshed {branch}")
PY
chmod +x "$DIRECT_SYNC"

cat > "$FAKE_SUPERVISOR/scripts/git-handoff.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "prepare" ]] || { echo "test adapter supports prepare only" >&2; exit 64; }
shift
branch=""
project="$PWD"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) branch="$2"; shift 2 ;;
    --project) project="$2"; shift 2 ;;
    *) echo "unexpected test adapter argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$branch" ]] || { echo "missing branch" >&2; exit 64; }
python3 "$RPGK_TEST_DIRECT_SYNC" \
  "$RPGK_TEST_REAL_SUPERVISOR_ROOT/scripts/git-handoff-host-wrapper.py" \
  "$project" \
  "$branch"
SH
chmod +x "$FAKE_SUPERVISOR/scripts/git-handoff.sh"

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
  (
    cd "$path"
    RPGK_SUPERVISOR_ROOT="$FAKE_SUPERVISOR" \
    RPGK_TEST_DIRECT_SYNC="$DIRECT_SYNC" \
    RPGK_TEST_REAL_SUPERVISOR_ROOT="$ROOT" \
      bash "$ROOT/scripts/refresh-rearmed-workspace.sh"
  )
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
  echo "successful clean refresh left a dirty worktree" >&2
  exit 1
}

# Dirty preserved source work is temporarily stashed, current main is integrated, and the exact
# dirty work is restored for the continued worker.
dirty_workspace="$(prepare_workspace GH-124 codex/test-refresh)"
printf 'local work\n' > "$dirty_workspace/local-only.txt"
dirty_head="$(git -C "$dirty_workspace" rev-parse HEAD)"
run_refresh "$dirty_workspace" >/dev/null
git -C "$dirty_workspace" merge-base --is-ancestor origin/main HEAD || {
  echo "dirty continuation did not absorb current origin/main" >&2
  exit 1
}
[[ "$(git -C "$dirty_workspace" rev-parse HEAD)" != "$dirty_head" ]] || {
  echo "dirty continuation did not advance onto current main" >&2
  exit 1
}
[[ "$(cat "$dirty_workspace/local-only.txt")" == "local work" ]] || {
  echo "dirty continuation source work was not restored exactly" >&2
  exit 1
}
[[ "$(git -C "$dirty_workspace" status --porcelain --untracked-files=all)" == "?? local-only.txt" ]] || {
  echo "dirty continuation did not preserve expected dirty file set" >&2
  git -C "$dirty_workspace" status --porcelain --untracked-files=all >&2
  exit 1
}

# Clean unpushed commits are durable continuation state and remain intact while current main is integrated.
local_workspace="$(prepare_workspace GH-125 codex/test-refresh)"
printf 'unpushed\n' > "$local_workspace/unpushed.txt"
git -C "$local_workspace" add unpushed.txt
git -C "$local_workspace" commit -qm "local only"
local_commit="$(git -C "$local_workspace" rev-parse HEAD)"
run_refresh "$local_workspace" >/dev/null
git -C "$local_workspace" merge-base --is-ancestor "$local_commit" HEAD || {
  echo "local continuation commit was not preserved" >&2
  exit 1
}
git -C "$local_workspace" merge-base --is-ancestor origin/main HEAD || {
  echo "locally-ahead continuation does not contain current main" >&2
  exit 1
}
[[ -f "$local_workspace/unpushed.txt" ]] || { echo "unpushed continuation work was discarded" >&2; exit 1; }
[[ -z "$(git -C "$local_workspace" status --porcelain --untracked-files=all)" ]] || {
  echo "locally-ahead continuation refresh left a dirty worktree" >&2
  exit 1
}

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
grep -Fq "conflicts with the continuation branch" "$TMP/conflict.err" || {
  echo "conflicting continuation failure was not explained" >&2
  exit 1
}
[[ "$(git -C "$conflict_workspace" rev-parse HEAD)" == "$conflict_head" ]] || { echo "conflicting continuation HEAD changed" >&2; exit 1; }
[[ ! -e "$conflict_workspace/.git/MERGE_HEAD" ]] || { echo "conflicting continuation left merge state behind" >&2; exit 1; }
[[ -z "$(git -C "$conflict_workspace" status --porcelain --untracked-files=all)" ]] || {
  echo "conflicting continuation did not restore a clean worktree" >&2
  exit 1
}

# If current main conflicts specifically with dirty preserved work, the sync must also fail closed
# and reconstruct the original dirty workspace exactly.
git -C "$SEED" switch -q main
printf 'dirty common\n' > "$SEED/dirty-conflict.txt"
git -C "$SEED" add dirty-conflict.txt
git -C "$SEED" commit -qm "dirty conflict base"
git -C "$SEED" push -q origin main
git -C "$SEED" switch -qc codex/dirty-conflict-refresh
git -C "$SEED" push -q -u origin codex/dirty-conflict-refresh
git -C "$SEED" switch -q main
printf 'upstream version\n' > "$SEED/dirty-conflict.txt"
git -C "$SEED" add dirty-conflict.txt
git -C "$SEED" commit -qm "dirty conflict upstream"
git -C "$SEED" push -q origin main

dirty_conflict_workspace="$(prepare_workspace GH-127 codex/dirty-conflict-refresh)"
printf 'preserved local version\n' > "$dirty_conflict_workspace/dirty-conflict.txt"
dirty_conflict_head="$(git -C "$dirty_conflict_workspace" rev-parse HEAD)"
set +e
run_refresh "$dirty_conflict_workspace" >"$TMP/dirty-conflict.out" 2>"$TMP/dirty-conflict.err"
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected dirty conflict to fail closed with 73, got $status" >&2; exit 1; }
grep -Fq "preserved uncommitted continuation work" "$TMP/dirty-conflict.err" || {
  echo "dirty conflict failure was not explained" >&2
  exit 1
}
[[ "$(git -C "$dirty_conflict_workspace" rev-parse HEAD)" == "$dirty_conflict_head" ]] || {
  echo "dirty conflict changed original HEAD" >&2
  exit 1
}
[[ "$(cat "$dirty_conflict_workspace/dirty-conflict.txt")" == "preserved local version" ]] || {
  echo "dirty conflict failed to restore preserved source content" >&2
  exit 1
}
[[ "$(git -C "$dirty_conflict_workspace" status --porcelain --untracked-files=all)" == " M dirty-conflict.txt" ]] || {
  echo "dirty conflict failed to restore original dirty state" >&2
  git -C "$dirty_conflict_workspace" status --porcelain --untracked-files=all >&2
  exit 1
}
[[ ! -e "$dirty_conflict_workspace/.git/MERGE_HEAD" ]] || {
  echo "dirty conflict left merge state behind" >&2
  exit 1
}

echo "rearmed-workspace-refresh-test: PASS"