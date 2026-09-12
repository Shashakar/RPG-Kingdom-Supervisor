#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <issue-number> [--json]" >&2
  exit 64
fi

if printf '%s\n' "$@" | grep -Fxq -- '--json'; then
  issue_json="$(python3 "$SUPERVISOR_ROOT/scripts/diagnostics.py" issue "$@")"
  quota_json="$(python3 "$SUPERVISOR_ROOT/scripts/quota-status.py" --json)"
  jq -n \
    --argjson issue "$issue_json" \
    --argjson quota "$quota_json" \
    '$issue + {currentQuota:$quota, quotaSemantics:"currentQuota is current global state and must not be confused with historical worker quotaBefore/quotaAfter samples"}'
  exit 0
fi

python3 "$SUPERVISOR_ROOT/scripts/diagnostics.py" issue "$@"
python3 "$SUPERVISOR_ROOT/scripts/quota-status.py"
