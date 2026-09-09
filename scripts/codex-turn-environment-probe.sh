#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

# shellcheck source=codex-permission-profile.sh
source "$SUPERVISOR_ROOT/scripts/codex-permission-profile.sh"

for tool in codex python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "RPG Kingdom Codex model-turn environment probe: $tool is not installed or not on PATH" >&2
    exit 1
  fi
done

export RPGK_CODEX_PERMISSION_PROFILE
export RPGK_CODEX_PERMISSION_PROFILE_TOML

exec python3 "$SUPERVISOR_ROOT/scripts/codex-turn-environment-probe.py" "$@"
