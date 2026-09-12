#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
SYMPHONY_ROOT="${SYMPHONY_ROOT:-$HOME/src/openai-symphony/elixir}"
SYMPHONY_REPO_ROOT="$(cd "$SYMPHONY_ROOT/.." && pwd)"
PERMISSIONS_TRANSFORM="$SUPERVISOR_ROOT/scripts/patch-symphony-named-permissions.py"
USAGE_LIMIT_TRANSFORM="$SUPERVISOR_ROOT/scripts/patch-symphony-usage-limit.py"
CONTINUATION_TRANSFORM="$SUPERVISOR_ROOT/scripts/patch-symphony-continuation-policy.py"
PIN="8001b52e3062495a16e520e4ceaf8f9de868c4d0"
LOCAL_BRANCH="rpgk/named-permissions"

if bash "$SUPERVISOR_ROOT/scripts/verify-symphony-permissions-patch.sh" >/dev/null 2>&1; then
  echo "RPG Kingdom Symphony compatibility patches: already applied"
  exit 0
fi

for transform in "$PERMISSIONS_TRANSFORM" "$USAGE_LIMIT_TRANSFORM" "$CONTINUATION_TRANSFORM"; do
  if [[ ! -f "$transform" ]]; then
    echo "ERROR: Symphony compatibility transform not found: $transform" >&2
    exit 1
  fi
done

if [[ -n "$(git -C "$SYMPHONY_REPO_ROOT" status --porcelain)" ]]; then
  echo "ERROR: Symphony checkout has uncommitted changes; refusing to apply the compatibility patch." >&2
  echo "Checkout: $SYMPHONY_REPO_ROOT" >&2
  exit 1
fi

current="$(git -C "$SYMPHONY_REPO_ROOT" rev-parse HEAD)"
current_branch="$(git -C "$SYMPHONY_REPO_ROOT" branch --show-current)"

# The compatibility branch is generated from the evaluated pin. When the
# Supervisor tightens the transform, safely rebuild that dedicated local branch
# from the pin instead of requiring the operator to delete the old generated
# commit manually. Never reset an unrelated branch.
if [[ "$current_branch" == "$LOCAL_BRANCH" ]]; then
  if ! git -C "$SYMPHONY_REPO_ROOT" merge-base --is-ancestor "$PIN" "$current" 2>/dev/null; then
    echo "ERROR: local Symphony branch '$LOCAL_BRANCH' is not based on the evaluated upstream pin." >&2
    echo "Expected ancestor: $PIN" >&2
    echo "Current:           $current" >&2
    exit 1
  fi
  if [[ "$current" != "$PIN" ]]; then
    echo "RPG Kingdom Symphony compatibility patch: rebuilding $LOCAL_BRANCH from evaluated pin"
    git -C "$SYMPHONY_REPO_ROOT" reset --hard "$PIN"
    current="$PIN"
  fi
elif [[ "$current" != "$PIN" ]]; then
  echo "ERROR: Symphony checkout is not at the evaluated upstream pin or generated compatibility branch." >&2
  echo "Expected pin: $PIN" >&2
  echo "Current:      $current" >&2
  echo "Branch:       ${current_branch:-<detached>}" >&2
  echo "Review upstream changes before changing the pin or applying this patch." >&2
  exit 1
fi

if [[ "$current_branch" != "$LOCAL_BRANCH" ]]; then
  if git -C "$SYMPHONY_REPO_ROOT" show-ref --verify --quiet "refs/heads/$LOCAL_BRANCH"; then
    local_head="$(git -C "$SYMPHONY_REPO_ROOT" rev-parse "$LOCAL_BRANCH")"
    if ! git -C "$SYMPHONY_REPO_ROOT" merge-base --is-ancestor "$PIN" "$local_head" 2>/dev/null; then
      echo "ERROR: local Symphony branch '$LOCAL_BRANCH' exists but is not based on the evaluated upstream pin." >&2
      echo "Expected ancestor: $PIN" >&2
      echo "Current:           $local_head" >&2
      exit 1
    fi
    git -C "$SYMPHONY_REPO_ROOT" switch "$LOCAL_BRANCH"
    if [[ "$local_head" != "$PIN" ]]; then
      echo "RPG Kingdom Symphony compatibility patch: rebuilding existing $LOCAL_BRANCH from evaluated pin"
      git -C "$SYMPHONY_REPO_ROOT" reset --hard "$PIN"
    fi
  else
    git -C "$SYMPHONY_REPO_ROOT" switch -c "$LOCAL_BRANCH"
  fi
fi

python3 "$PERMISSIONS_TRANSFORM" "$SYMPHONY_ROOT"
python3 "$USAGE_LIMIT_TRANSFORM" "$SYMPHONY_ROOT"
python3 "$CONTINUATION_TRANSFORM" "$SYMPHONY_ROOT"

cd "$SYMPHONY_ROOT"
mise exec -- mix format \
  lib/symphony_elixir/agent_runner.ex \
  lib/symphony_elixir/config.ex \
  lib/symphony_elixir/config/schema.ex \
  lib/symphony_elixir/codex/app_server.ex
mise exec -- mix test \
  test/symphony_elixir/workspace_and_config_test.exs \
  test/symphony_elixir/app_server_test.exs \
  test/symphony_elixir/core_test.exs

git -C "$SYMPHONY_REPO_ROOT" diff --check
git -C "$SYMPHONY_REPO_ROOT" add \
  elixir/lib/symphony_elixir/agent_runner.ex \
  elixir/lib/symphony_elixir/config.ex \
  elixir/lib/symphony_elixir/config/schema.ex \
  elixir/lib/symphony_elixir/codex/app_server.ex

git -C "$SYMPHONY_REPO_ROOT" \
  -c user.name="RPG Kingdom Supervisor" \
  -c user.email="supervisor@local.invalid" \
  commit -m "Support RPG Kingdom Codex compatibility policy"

bash "$SUPERVISOR_ROOT/scripts/verify-symphony-permissions-patch.sh"

echo "RPG Kingdom Symphony compatibility patches: PASS"
