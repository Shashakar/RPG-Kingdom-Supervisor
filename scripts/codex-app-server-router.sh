#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
RPGK_REPO="${RPGK_REPO:-Shashakar/RPG-Kingdom}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"

# shellcheck source=routing-policy.sh
source "$SUPERVISOR_ROOT/scripts/routing-policy.sh"
# shellcheck source=codex-permission-profile.sh
source "$SUPERVISOR_ROOT/scripts/codex-permission-profile.sh"
# shellcheck source=codex-concurrency-policy.sh
source "$SUPERVISOR_ROOT/scripts/codex-concurrency-policy.sh"

workspace_name="$(basename "$PWD")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ ]]; then
  echo "RPG Kingdom router: expected Symphony workspace basename GH-<issue>, got '$workspace_name'" >&2
  exit 64
fi
issue_number="${BASH_REMATCH[1]}"
identifier="GH-$issue_number"

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

# Select only pre-approved capabilities that are already available on the host. This is
# deliberately separate from routing: model selection remains unchanged if Graphify is absent.
mkdir -p "$STATE_ROOT/capabilities"
capabilities_file="$STATE_ROOT/capabilities/$identifier.json"
capability_args_file="$(mktemp)"
trap 'rm -f -- "$capability_args_file"' EXIT
labels_json="$(printf '%s\n' "$labels" | jq -R -s 'split("\n") | map(select(length > 0))')"
if ! python3 "$SUPERVISOR_ROOT/scripts/codex-capability-policy.py" route \
  --route "$route_name" \
  --issue "$identifier" \
  --workspace "$PWD" \
  --labels-json "$labels_json" \
  --args-file "$capability_args_file" \
  --state-file "$capabilities_file"; then
  echo "RPG Kingdom router: refusing to start Codex because capability selection failed" >&2
  exit 70
fi

# Keep first-party procedural guidance independent from MCP/tool selection. This augments the same
# capability snapshot before worker-start so durable worker telemetry contains the final skill set.
if ! python3 "$SUPERVISOR_ROOT/scripts/first-party-skill-policy.py" \
  --labels-json "$labels_json" \
  --state-file "$capabilities_file" >/dev/null; then
  echo "RPG Kingdom router: refusing to start Codex because first-party skill selection failed" >&2
  exit 70
fi

capability_args=()
if [[ -s "$capability_args_file" ]]; then
  mapfile -d '' -t capability_args < "$capability_args_file"
fi

# Implementation, repair, and report-only work share exactly one mutation slot. Independent
# read-only review owns a separate slot, so unrelated implementation + review work may overlap.
# The per-issue lock remains held for the full Codex lifetime and prevents a reviewer from reading
# an issue while that same workspace is still being mutated.
slot_lock="$(rpgk_codex_slot_lock_path "$STATE_ROOT" "$role")"
issue_lock="$(rpgk_codex_issue_lock_path "$STATE_ROOT" "$identifier")"
mkdir -p "$(dirname "$slot_lock")" "$(dirname "$issue_lock")"
exec 9>"$slot_lock"
flock 9
exec 8>"$issue_lock"
flock 8

python3 "$SUPERVISOR_ROOT/scripts/codex-usage-snapshot.py" --write --quiet || true
if ! python3 "$SUPERVISOR_ROOT/scripts/supervisor_telemetry.py" worker-start \
  --role "$role" \
  --model "$model" \
  --effort "$reasoning_effort" \
  --route "$route_name" \
  --capabilities-file "$capabilities_file" \
  --pid "$$" >/dev/null; then
  echo "RPG Kingdom router: refusing to start Codex because the durable worker telemetry record could not be established" >&2
  exit 70
fi

# exec preserves this shell PID in the active worker record, allowing the dashboard to distinguish
# a live worker from stale state without introducing a second wrapper process. Open flock file
# descriptors are inherited by Codex, so both the mutation slot and issue ownership remain held.
exec codex \
  --config shell_environment_policy.inherit=all \
  "${RPGK_CODEX_PERMISSION_ARGS[@]}" \
  --config "model=\"$model\"" \
  --config "model_reasoning_effort=$reasoning_effort" \
  "${capability_args[@]}" \
  app-server
