#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
TOKEN="${SYMPHONY_GITHUB_TOKEN:-}"

# Symphony may intentionally omit its tracker token from the Codex command environment even though
# the host has authenticated GitHub CLI access. Acquire a token only inside this short-lived wrapper,
# pass it to the context builder subprocess, and never export it back to the parent/router process.
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
fi

if [[ -z "$TOKEN" ]]; then
  echo "RPG Kingdom continuation context: no host GitHub credential is available" >&2
  exit 73
fi

SYMPHONY_GITHUB_TOKEN="$TOKEN" \
  bash "$SUPERVISOR_ROOT/scripts/build-continuation-context.sh"
