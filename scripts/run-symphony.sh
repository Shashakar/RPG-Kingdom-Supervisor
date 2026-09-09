#!/usr/bin/env bash
set -uo pipefail

SYMPHONY_ROOT="${SYMPHONY_ROOT:-$HOME/src/openai-symphony/elixir}"
SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
SECRETS_FILE="${RPGK_SECRETS_FILE:-$HOME/.config/rpg-kingdom-supervisor/secrets.env}"
USE_ALT_SCREEN="${RPGK_ALTERNATE_SCREEN:-1}"
ALT_SCREEN_ACTIVE=0

if [[ -f "$SECRETS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
fi

if [[ -z "${SYMPHONY_GITHUB_TOKEN:-}" ]]; then
  echo "ERROR: SYMPHONY_GITHUB_TOKEN is not set." >&2
  echo "Expected secrets file: $SECRETS_FILE" >&2
  exit 1
fi

if ! command -v mise >/dev/null 2>&1; then
  echo "ERROR: mise is not installed or not on PATH." >&2
  exit 1
fi

if [[ ! -d "$SYMPHONY_ROOT" ]]; then
  echo "ERROR: Symphony root does not exist: $SYMPHONY_ROOT" >&2
  exit 1
fi

if [[ ! -f "$SUPERVISOR_ROOT/WORKFLOW.md" ]]; then
  echo "ERROR: Supervisor workflow not found: $SUPERVISOR_ROOT/WORKFLOW.md" >&2
  exit 1
fi

if ! bash "$SUPERVISOR_ROOT/scripts/verify-symphony-permissions-patch.sh"; then
  echo "ERROR: the pinned Symphony checkout does not support named Codex permission profiles." >&2
  echo "Run: bash $SUPERVISOR_ROOT/scripts/apply-symphony-permissions-patch.sh" >&2
  exit 1
fi

if [[ "${RPGK_SKIP_CODEX_PERMISSION_PROBE:-0}" != "1" ]]; then
  if ! bash "$SUPERVISOR_ROOT/scripts/codex-git-write-probe.sh"; then
    echo "ERROR: Codex Git-write permission probe failed; Symphony will not start because workers could not complete normal branch/commit/push workflows." >&2
    exit 1
  fi
fi

cleanup_screen() {
  if (( ALT_SCREEN_ACTIVE == 1 )); then
    tput rmcup 2>/dev/null || true
    ALT_SCREEN_ACTIVE=0
  fi
}

trap cleanup_screen EXIT

if [[ "$USE_ALT_SCREEN" == "1" && -t 1 && "${TERM:-dumb}" != "dumb" ]] && command -v tput >/dev/null 2>&1; then
  if tput smcup 2>/dev/null; then
    ALT_SCREEN_ACTIVE=1
  fi
fi

cd "$SYMPHONY_ROOT" || exit 1

mise exec -- ./bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  "$SUPERVISOR_ROOT/WORKFLOW.md"
status=$?

cleanup_screen
trap - EXIT

if (( status != 0 && status != 130 )); then
  echo "Symphony exited with status: $status" >&2
fi

exit "$status"
