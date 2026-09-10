#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

# shellcheck source=codex-permission-profile.sh
source "$SUPERVISOR_ROOT/scripts/codex-permission-profile.sh"

if ! command -v codex >/dev/null 2>&1; then
  echo "RPG Kingdom Codex App Server permission probe: codex is not installed or not on PATH" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "RPG Kingdom Codex App Server permission probe: python3 is not installed or not on PATH" >&2
  exit 1
fi

export RPGK_CODEX_PERMISSION_PROFILE
export RPGK_CODEX_PERMISSION_PROFILE_TOML

exec python3 "$SUPERVISOR_ROOT/scripts/codex-app-server-permission-probe.py"
