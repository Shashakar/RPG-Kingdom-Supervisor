#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

# shellcheck source=codex-permission-profile.sh
source "$SUPERVISOR_ROOT/scripts/codex-permission-profile.sh"

if ! command -v codex >/dev/null 2>&1; then
  echo "RPG Kingdom Codex permission probe: codex is not installed or not on PATH" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "RPG Kingdom Codex permission probe: git is not installed or not on PATH" >&2
  exit 1
fi

case "$(uname -s)" in
  Linux)
    sandbox_subcommand="linux"
    ;;
  Darwin)
    sandbox_subcommand="macos"
    ;;
  *)
    echo "RPG Kingdom Codex permission probe: unsupported host for model-free sandbox probe: $(uname -s)" >&2
    exit 1
    ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
repo="$tmp/repo"

git init -q "$repo"
printf 'probe\n' > "$repo/probe.txt"

if ! codex \
  "${RPGK_CODEX_PERMISSION_ARGS[@]}" \
  sandbox "$sandbox_subcommand" \
  --permission-profile "$RPGK_CODEX_PERMISSION_PROFILE" \
  -C "$repo" \
  -- \
  bash -lc 'git add probe.txt && git -c user.name="RPG Kingdom Supervisor" -c user.email="supervisor@local.invalid" commit -qm "permission probe" && test -z "$(git status --porcelain)"'; then
  echo "RPG Kingdom Codex permission probe: FAILED; the active Codex sandbox cannot write repository .git metadata" >&2
  exit 1
fi

echo "RPG Kingdom Codex permission probe: PASS"
