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

echo "RPG Kingdom router: GH-$issue_number -> $route_name ($model, effort=$reasoning_effort)" >&2

if [[ "${RPGK_ROUTER_DRY_RUN:-0}" == "1" ]]; then
  printf '%s\t%s\t%s\n' "$model" "$reasoning_effort" "$route_name"
  exit 0
fi

bash "$SUPERVISOR_ROOT/scripts/build-continuation-context-with-auth.sh"

unset SYMPHONY_GITHUB_TOKEN

# Implementation and independent review share one host-level Codex session slot. This preserves
# the current max-one-agent budget even though review is driven by a Supervisor sidecar.
mkdir -p "$(dirname "$CODEX_LOCK")"
exec 9>"$CODEX_LOCK"
flock 9

exec codex \
  --config shell_environment_policy.inherit=all \
  "${RPGK_CODEX_PERMISSION_ARGS[@]}" \
  --config "model=\"$model\"" \
  --config "model_reasoning_effort=$reasoning_effort" \
  app-server
