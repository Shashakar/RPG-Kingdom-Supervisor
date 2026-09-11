#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

exec python3 "$SUPERVISOR_ROOT/scripts/supervisor_dashboard.py" "$@"
