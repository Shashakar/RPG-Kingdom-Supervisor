#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
SYMPHONY_ROOT="${SYMPHONY_ROOT:-$HOME/src/openai-symphony/elixir}"
SYMPHONY_REPO_ROOT="$(cd "$SYMPHONY_ROOT/.." && pwd)"
PATCH_FILE="$SUPERVISOR_ROOT/patches/symphony-named-permissions.patch"
PIN="8001b52e3062495a16e520e4ceaf8f9de868c4d0"
LOCAL_BRANCH="rpgk/named-permissions"

if bash "$SUPERVISOR_ROOT/scripts/verify-symphony-permissions-patch.sh" >/dev/null 2>&1; then
  echo "RPG Kingdom Symphony permissions patch: already applied"
  exit 0
fi

if [[ ! -f "$PATCH_FILE" ]]; then
  echo "ERROR: Symphony compatibility patch not found: $PATCH_FILE" >&2
  exit 1
fi

if [[ -n "$(git -C "$SYMPHONY_REPO_ROOT" status --porcelain)" ]]; then
  echo "ERROR: Symphony checkout has uncommitted changes; refusing to apply the compatibility patch." >&2
  echo "Checkout: $SYMPHONY_REPO_ROOT" >&2
  exit 1
fi

current="$(git -C "$SYMPHONY_REPO_ROOT" rev-parse HEAD)"
if [[ "$current" != "$PIN" ]]; then
  echo "ERROR: Symphony checkout is not at the evaluated upstream pin." >&2
  echo "Expected: $PIN" >&2
  echo "Current:  $current" >&2
  echo "Review upstream changes before changing the pin or applying this patch." >&2
  exit 1
fi

if git -C "$SYMPHONY_REPO_ROOT" show-ref --verify --quiet "refs/heads/$LOCAL_BRANCH"; then
  echo "ERROR: local Symphony branch '$LOCAL_BRANCH' already exists but the patch is not active." >&2
  echo "Inspect that branch before retrying; this script will not overwrite it." >&2
  exit 1
fi

git -C "$SYMPHONY_REPO_ROOT" switch -c "$LOCAL_BRANCH"
git -C "$SYMPHONY_REPO_ROOT" apply --check "$PATCH_FILE"
git -C "$SYMPHONY_REPO_ROOT" apply "$PATCH_FILE"

cd "$SYMPHONY_ROOT"
mise exec -- mix format \
  lib/symphony_elixir/config.ex \
  lib/symphony_elixir/config/schema.ex \
  lib/symphony_elixir/codex/app_server.ex
mise exec -- mix test \
  test/symphony_elixir/workspace_and_config_test.exs \
  test/symphony_elixir/app_server_test.exs

git -C "$SYMPHONY_REPO_ROOT" diff --check
git -C "$SYMPHONY_REPO_ROOT" add \
  elixir/lib/symphony_elixir/config.ex \
  elixir/lib/symphony_elixir/config/schema.ex \
  elixir/lib/symphony_elixir/codex/app_server.ex

git -C "$SYMPHONY_REPO_ROOT" \
  -c user.name="RPG Kingdom Supervisor" \
  -c user.email="supervisor@local.invalid" \
  commit -m "Support named Codex permissions profiles"

bash "$SUPERVISOR_ROOT/scripts/verify-symphony-permissions-patch.sh"

echo "RPG Kingdom Symphony permissions patch: PASS"
