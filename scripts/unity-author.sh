#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="${RPGK_SYMPHONY_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"
ACK_TIMEOUT_SECONDS="${RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS:-10}"
RUN_TIMEOUT_SECONDS="${RPGK_UNITY_BROKER_TIMEOUT_SECONDS:-3600}"

usage() {
  cat >&2 <<'EOF'
Usage:
  unity-author.sh apply --request PATH [--project PATH]

The request must be protocolVersion=1, tier="mechanical", contain one Assets/*.unity scene,
and contain one or more typed mechanical operations. The host and project-side executor perform
the final safety validation.
EOF
}

[[ $# -ge 1 ]] || { usage; exit 64; }
command="$1"
shift
[[ "$command" == "apply" ]] || { usage; exit 64; }

project="$PWD"
authoring_request=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) [[ $# -ge 2 ]] || exit 64; project="$2"; shift 2 ;;
    --request) [[ $# -ge 2 ]] || exit 64; authoring_request="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown Unity authoring argument: $1" >&2; usage; exit 64 ;;
  esac
done

command -v jq >/dev/null 2>&1 || { echo "RPG Kingdom Unity authoring: jq is required" >&2; exit 80; }
[[ -n "$authoring_request" && -f "$authoring_request" ]] || { echo "RPG Kingdom Unity authoring: --request must name an existing JSON file" >&2; exit 64; }

if ! jq -e '
  .protocolVersion == 1 and
  .tier == "mechanical" and
  (.scene | type == "string" and startswith("Assets/") and endswith(".unity") and (contains("..") | not)) and
  (.operations | type == "array" and length > 0)
' "$authoring_request" >/dev/null; then
  echo "RPG Kingdom Unity authoring: invalid Tier-1 mechanical request envelope" >&2
  exit 64
fi

project="$(cd "$project" && pwd)"
workspace_root="$(cd "$WORKSPACE_ROOT" && pwd)"
workspace_name="$(basename "$project")"
if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ || "$(dirname "$project")" != "$workspace_root" ]]; then
  echo "RPG Kingdom Unity authoring: project '$project' is not a Symphony GH issue workspace under '$workspace_root'" >&2
  exit 81
fi

broker_dir="$project/Logs/SymphonyUnity/.broker"
request_dir="$broker_dir/requests"
ack_dir="$broker_dir/acks"
response_dir="$broker_dir/responses"
history_request_dir="$broker_dir/history/requests"
mkdir -p "$request_dir" "$ack_dir" "$response_dir" "$history_request_dir"

request_id="${workspace_name}-author-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
request_path="$request_dir/$request_id.json"
ack_path="$ack_dir/$request_id.json"
response_path="$response_dir/$request_id.json"
history_request_path="$history_request_dir/$request_id.json"
requested_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

temp_history="$history_request_path.tmp.$$"
jq -cn \
  --arg requestId "$request_id" \
  --arg requestedAt "$requested_at" \
  --slurpfile authoring "$authoring_request" \
  '{protocolVersion:1,requestId:$requestId,operation:"author",testFilter:"",requestedAt:$requestedAt,authoring:$authoring[0]}' \
  > "$temp_history"
mv "$temp_history" "$history_request_path"
cp "$history_request_path" "$request_path.tmp.$$"
mv "$request_path.tmp.$$" "$request_path"

ack_deadline=$((SECONDS + ACK_TIMEOUT_SECONDS))
while [[ ! -f "$ack_path" && ! -f "$response_path" ]]; do
  if (( SECONDS >= ack_deadline )); then
    rm -f "$request_path" 2>/dev/null || true
    echo "RPG Kingdom Unity authoring: host broker did not acknowledge request within ${ACK_TIMEOUT_SECONDS}s" >&2
    exit 84
  fi
  sleep 0.2
done

run_deadline=$((SECONDS + RUN_TIMEOUT_SECONDS))
while [[ ! -f "$response_path" ]]; do
  if (( SECONDS >= run_deadline )); then
    echo "RPG Kingdom Unity authoring: host broker request '$request_id' exceeded ${RUN_TIMEOUT_SECONDS}s" >&2
    exit 85
  fi
  sleep 0.5
done

stdout="$(jq -r '.stdout // ""' "$response_path")"
stderr="$(jq -r '.stderr // ""' "$response_path")"
exit_code="$(jq -r '.exitCode // 86' "$response_path")"
[[ -z "$stdout" ]] || printf '%s\n' "$stdout"
[[ -z "$stderr" ]] || printf '%s\n' "$stderr" >&2

if [[ ! "$exit_code" =~ ^[0-9]+$ ]]; then
  echo "RPG Kingdom Unity authoring: broker returned an invalid exit code" >&2
  exit 86
fi
exit "$exit_code"
