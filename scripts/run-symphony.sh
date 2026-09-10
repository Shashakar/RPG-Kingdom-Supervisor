#!/usr/bin/env bash
set -uo pipefail

SYMPHONY_ROOT="${SYMPHONY_ROOT:-$HOME/src/openai-symphony/elixir}"
SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
SECRETS_FILE="${RPGK_SECRETS_FILE:-$HOME/.config/rpg-kingdom-supervisor/secrets.env}"
USE_ALT_SCREEN="${RPGK_ALTERNATE_SCREEN:-1}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
WORKSPACE_ROOT="${RPGK_SYMPHONY_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"
BROKER_ROOT="$STATE_ROOT/unity-broker"
BROKER_STATUS="$BROKER_ROOT/status.json"
BROKER_PID_FILE="$BROKER_ROOT/pid"
BROKER_LOG="$BROKER_ROOT/broker.log"
BROKER_HOST_TIMEOUT_SECONDS="${RPGK_UNITY_BROKER_HOST_TIMEOUT_SECONDS:-1800}"
BROKER_KILL_GRACE_SECONDS="${RPGK_UNITY_BROKER_KILL_GRACE_SECONDS:-5}"
BROKER_STARTED=0
BROKER_PID=""
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

if ! command -v python3 >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: python3 and jq are required for the Unity host broker." >&2
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

  if ! bash "$SUPERVISOR_ROOT/scripts/codex-app-server-permission-probe.sh"; then
    echo "ERROR: Codex App Server did not select the Supervisor permission profile; Symphony will not start because its worker path is not proven to match the standalone sandbox probe." >&2
    exit 1
  fi
fi

cleanup_screen() {
  if (( ALT_SCREEN_ACTIVE == 1 )); then
    tput rmcup 2>/dev/null || true
    ALT_SCREEN_ACTIVE=0
  fi
}

stop_unity_broker() {
  if (( BROKER_STARTED == 1 )) && [[ -n "$BROKER_PID" ]]; then
    kill "$BROKER_PID" 2>/dev/null || true
    wait "$BROKER_PID" 2>/dev/null || true
    BROKER_STARTED=0
  fi
}

cleanup_all() {
  cleanup_screen
  stop_unity_broker
}
trap cleanup_all EXIT

start_unity_broker() {
  mkdir -p "$BROKER_ROOT" "$WORKSPACE_ROOT"

  if [[ -f "$BROKER_PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(tr -d '[:space:]' < "$BROKER_PID_FILE")"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
      if [[ -f "$BROKER_STATUS" ]] && jq -e \
        --arg root "$(cd "$WORKSPACE_ROOT" && pwd)" \
        '.protocolVersion == 1 and (.state == "ready" or .state == "running") and .workspaceRoot == $root' \
        "$BROKER_STATUS" >/dev/null 2>&1; then
        echo "RPG Kingdom Unity broker: reusing PID $existing_pid"
        return 0
      fi
      echo "ERROR: an incompatible Unity broker is already running as PID $existing_pid." >&2
      echo "Stop that broker before starting Symphony with this Supervisor checkout." >&2
      return 1
    fi
  fi

  python3 -u "$SUPERVISOR_ROOT/scripts/unity-host-broker.py" \
    --workspace-root "$WORKSPACE_ROOT" \
    --state-root "$STATE_ROOT" \
    --host-runner "$SUPERVISOR_ROOT/scripts/unity-runner-host.sh" \
    --command-timeout-seconds "$BROKER_HOST_TIMEOUT_SECONDS" \
    --kill-grace-seconds "$BROKER_KILL_GRACE_SECONDS" \
    >>"$BROKER_LOG" 2>&1 &
  BROKER_PID=$!
  BROKER_STARTED=1

  local attempt
  for attempt in $(seq 1 50); do
    if ! kill -0 "$BROKER_PID" 2>/dev/null; then
      echo "ERROR: Unity host broker exited during startup. Recent log:" >&2
      tail -n 40 "$BROKER_LOG" >&2 2>/dev/null || true
      return 1
    fi
    if [[ -f "$BROKER_STATUS" ]] && jq -e \
      --argjson pid "$BROKER_PID" \
      --arg root "$(cd "$WORKSPACE_ROOT" && pwd)" \
      '.protocolVersion == 1 and .state == "ready" and .pid == $pid and .workspaceRoot == $root' \
      "$BROKER_STATUS" >/dev/null 2>&1; then
      echo "RPG Kingdom Unity broker: ready (PID $BROKER_PID)"
      return 0
    fi
    sleep 0.1
  done

  echo "ERROR: Unity host broker did not become ready within 5 seconds. Recent log:" >&2
  tail -n 40 "$BROKER_LOG" >&2 2>/dev/null || true
  return 1
}

if ! start_unity_broker; then
  exit 1
fi

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

cleanup_all
trap - EXIT

if (( status != 0 && status != 130 )); then
  echo "Symphony exited with status: $status" >&2
fi

exit "$status"
