#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <issue-number> [--json]" >&2
  exit 64
fi

exec python3 "$SUPERVISOR_ROOT/scripts/diagnostics.py" issue "$@"
