#!/usr/bin/env bash
set -euo pipefail

MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"
EXPECTED_OWNER="${RPGK_REPO_OWNER:-Shashakar}"
EXPECTED_REPO="${RPGK_REPO_NAME:-RPG-Kingdom}"

fail() {
  echo "RPG Kingdom continuation refresh: $*" >&2
  exit 73
}

# Fresh worker lifetimes do not need continuation refresh. A successful before-run guard only
# reaches this script with an existing marker when an explicit one-shot rearm was just consumed.
[[ -e "$MARKER" ]] || exit 0

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "current directory is not a Git worktree"

workspace_name="$(basename "$PWD")"
[[ "$workspace_name" =~ ^GH-[0-9]+$ ]] || fail "workspace '$workspace_name' is not a GH issue workspace"

origin_url="$(git remote get-url origin 2>/dev/null || true)"
case "$origin_url" in
  "https://github.com/$EXPECTED_OWNER/$EXPECTED_REPO.git"|"https://github.com/$EXPECTED_OWNER/$EXPECTED_REPO"|"git@github.com:$EXPECTED_OWNER/$EXPECTED_REPO.git") ;;
  *) fail "origin '$origin_url' is not the expected $EXPECTED_OWNER/$EXPECTED_REPO repository" ;;
esac

current_branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ -n "$current_branch" ]] || fail "rearmed workspace is detached; refusing to refresh ambiguous Git state"

# Do not destroy implementation state from a prior halted attempt. Supervisor runtime files are
# locally ignored, so any status output here represents repository work that needs human/worker
# review rather than disposable orchestration state.
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  fail "workspace has uncommitted source changes; preserving prior work instead of refreshing from main"
fi

# Fetching and integrating remote state is host-owned preflight work. Codex itself does not need
# writable Git metadata or network access for this operation.
git fetch --prune origin >/dev/null 2>&1 || fail "failed to fetch remote refs from origin"
git show-ref --verify --quiet refs/remotes/origin/main || fail "origin/main is unavailable after fetch"

remote_branch="refs/remotes/origin/$current_branch"
if git show-ref --verify --quiet "$remote_branch"; then
  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "$remote_branch")"

  if [[ "$local_head" != "$remote_head" ]]; then
    if git merge-base --is-ancestor "$local_head" "$remote_head"; then
      git merge --ff-only "$remote_branch" >/dev/null 2>&1 || fail "failed to fast-forward local '$current_branch' to its remote branch"
    elif git merge-base --is-ancestor "$remote_head" "$local_head"; then
      fail "workspace contains local commits not present on origin/$current_branch; preserving unpushed work"
    else
      fail "workspace branch '$current_branch' diverged from origin/$current_branch; preserving state for review"
    fi
  fi
elif [[ "$current_branch" != "main" ]]; then
  fail "workspace branch '$current_branch' has no remote counterpart; preserving local-only continuation state"
fi

# If the checked-out branch already contains current main, only LFS hydration remains.
if ! git merge-base --is-ancestor refs/remotes/origin/main HEAD; then
  if [[ "$current_branch" == "main" ]]; then
    git merge --ff-only refs/remotes/origin/main >/dev/null 2>&1 || fail "local main cannot fast-forward to origin/main"
  else
    merge_log="$(mktemp)"
    if ! git merge --no-edit refs/remotes/origin/main >"$merge_log" 2>&1; then
      git merge --abort >/dev/null 2>&1 || true
      cat "$merge_log" >&2
      rm -f "$merge_log"
      fail "current main conflicts with the durable continuation branch; resolve/supersede the stale PR before another worker run"
    fi
    rm -f "$merge_log"
    echo "RPG Kingdom continuation refresh: merged current origin/main into $current_branch"
  fi
fi

# Asset migrations can add Git LFS-backed files after a workspace was originally created. Hydrate
# current-main LFS objects before Unity preflight so the worker never receives pointer-only assets.
if [[ -f .gitattributes ]] && grep -Eq '(^|[[:space:]])filter=lfs([[:space:]]|$)' .gitattributes; then
  command -v git-lfs >/dev/null 2>&1 || git lfs version >/dev/null 2>&1 || fail "Git LFS is required by the refreshed repository but is unavailable"
  git lfs fetch origin main >/dev/null 2>&1 || fail "failed to fetch Git LFS objects for origin/main"
  if git show-ref --verify --quiet "$remote_branch"; then
    git lfs fetch origin "$current_branch" >/dev/null 2>&1 || fail "failed to fetch Git LFS objects for origin/$current_branch"
  fi
  git lfs checkout >/dev/null 2>&1 || fail "failed to hydrate Git LFS assets in the refreshed workspace"
fi

# Refresh must leave a clean tree. A merge commit is allowed and remains a fast-forwardable
# descendant of the existing remote feature branch; final push still goes through git-handoff.
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  fail "workspace refresh left unexpected source changes"
fi

main_sha="$(git rev-parse refs/remotes/origin/main)"
head_sha="$(git rev-parse HEAD)"
echo "RPG Kingdom continuation refresh: workspace ready on $current_branch at $head_sha with origin/main $main_sha"
