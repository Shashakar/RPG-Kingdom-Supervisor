#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
RPGK_REPO="${RPGK_REPO:-Shashakar/RPG-Kingdom}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
CODEX_LOCK="$STATE_ROOT/locks/codex-session.lock"

# shellcheck source=routing-policy.sh
source "$SUPERVISOR_ROOT/scripts/routing-policy.sh"
# shellcheck source=codex-permission-profile.sh
source "$SUPERVISOR_ROOT/scripts/codex-permission-profile.sh"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom router: expected Symphony workspace basename GH-<issue>, got '$workspace_name'" >&2
  exit 64
fi
issue_number="${BASH_REMATCH[1]}"

labels="$(gh issue view "$issue_number" --repo "$RPGK_REPO" --json labels --jq '.labels[].name')"

if ! route="$(rpgk_select_route "$labels")"; then
  echo "RPG Kingdom router: refusing to start Codex for GH-$issue_number because routing labels conflict" >&2
  exit 65
fi

IFS=$'\t' read -r model reasoning_effort route_name <<<"$route"
role="implementation"
if grep -Fxiq 'symphony:rework' <<<"$labels"; then
  role="repair"
elif grep -Fxiq 'completion:report-only' <<<"$labels"; then
  role="report-only"
fi

echo "RPG Kingdom router: GH-$issue_number -> $route_name ($model, effort=$reasoning_effort, role=$role)" >&2

if [[ "${RPGK_ROUTER_DRY_RUN:-0}" == "1" ]]; then
  printf '%s\t%s\t%s\n' "$model" "$reasoning_effort" "$route_name"
  exit 0
fi

bash "$SUPERVISOR_ROOT/scripts/build-continuation-context-with-auth.sh"

unset SYMPHONY_GITHUB_TOKEN GH_TOKEN GITHUB_TOKEN

# Implementation, repair, report-only work, and independent review currently share one host-level
# Codex session slot. Hold it while sampling quota and publishing the active worker record so
# telemetry cannot race a second Codex process.
mkdir -p "$(dirname "$CODEX_LOCK")"
exec 9>"$CODEX_LOCK"
flock 9

python3 "$SUPERVISOR_ROOT/scripts/codex-usage-snapshot.py" --write --quiet || true
if ! python3 "$SUPERVISOR_ROOT/scripts/supervisor_telemetry.py" worker-start \
  --role "$role" \
  --model "$model" \
  --effort "$reasoning_effort" \
  --route "$route_name" >/dev/null; then
  echo "RPG Kingdom router: refusing to start Codex because the durable worker telemetry record could not be established" >&2
  exit 70
fi

# exec preserves this shell PID in the active worker record, allowing the dashboard to distinguish
# a live worker from stale state without introducing a second wrapper process.
exec codex \
  --config shell_environment_policy.inherit=all \
  "${RPGK_CODEX_PERMISSION_ARGS[@]}" \
  --config "model=\"$model\"" \
  --config "model_reasoning_effort=$reasoning_effort" \
  app-server
