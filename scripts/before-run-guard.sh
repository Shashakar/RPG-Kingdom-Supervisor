#!/usr/bin/env bash
set -euo pipefail

MARKER="${RPGK_ATTEMPT_MARKER:-.symphony-attempt-complete}"

if [[ -e "$MARKER" ]]; then
  echo "RPG Kingdom budget guard: this dispatch has already consumed one Symphony worker lifetime; refusing to launch another Codex App Server session" >&2
  echo "RPG Kingdom budget guard: inspect the prior run, then explicitly rearm the issue before retrying" >&2
  exit 73
fi

exit 0
