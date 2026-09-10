#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="${RPGK_SYMPHONY_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"
ACK_TIMEOUT_SECONDS="${RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS:-10}"
RUN_TIMEOUT_SECONDS="${RPGK_UNITY_BROKER_TIMEOUT_SECONDS:-3600}"

usage() {
  cat >&2 <<'EOF'
Usage:
  unity-runner.sh health [--project PATH]
  unity-runner.sh editmode [--project PATH] [--filter FILTER]
  unity-runner.sh playmode [--project PATH] [--filter FILTER]

Environment overrides:
  RPGK_SYMPHONY_WORKSPACE_ROOT
  RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS
  RPGK_UNITY_BROKER_TIMEOUT_SECONDS
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 64
fi

command="$1"
shift
project="$PWD"
test_filter=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { echo "Missing value for --project" >&2; exit 64; }
      project="$2"
      shift 2
      ;;
    --filter)
      [[ $# -ge 2 ]] || { echo "Missing value for --filter" >&2; exit 64; }
      test_filter="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown Unity runner argument: $1" >&2
      usage
      exit 64
      ;;
  esac
done

case "$command" in
  health|editmode|playmode) ;;
  *)
    echo "Unknown Unity runner command: $command" >&2
    usage
    exit 64
    ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  echo "RPG Kingdom Unity runner: jq is required for broker requests" >&2
  exit 80
fi

project="$(cd "$project" && pwd)"
workspace_root="$(cd "$WORKSPACE_ROOT" && pwd)"
workspace_name="$(basename "$project")"

if [[ ! "$workspace_name" =~ ^GH-([0-9]+)$ || "$(dirname "$project")" != "$workspace_root" ]]; then
  echo "RPG Kingdom Unity runner: project '$project' is not a Symphony GH issue workspace under '$workspace_root'" >&2
  exit 81
fi

broker_dir="$project/Logs/SymphonyUnity/.broker"
request_dir="$broker_dir/requests"
ack_dir="$broker_dir/acks"
response_dir="$broker_dir/responses"
mkdir -p "$request_dir" "$ack_dir" "$response_dir"

request_id="${workspace_name}-${command}-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
request_path="$request_dir/$request_id.json"
ack_path="$ack_dir/$request_id.json"
response_path="$response_dir/$request_id.json"
temp_request="$request_path.tmp.$$"

jq -cn \
  --arg requestId "$request_id" \
  --arg operation "$command" \
  --arg testFilter "$test_filter" \
  '{protocolVersion:1,requestId:$requestId,operation:$operation,testFilter:$testFilter}' \
  > "$temp_request"
mv "$temp_request" "$request_path"

ack_deadline=$((SECONDS + ACK_TIMEOUT_SECONDS))
while [[ ! -f "$ack_path" && ! -f "$response_path" ]]; do
  if (( SECONDS >= ack_deadline )); then
    rm -f "$request_path" 2>/dev/null || true
    echo "RPG Kingdom Unity runner: host broker did not acknowledge the request within ${ACK_TIMEOUT_SECONDS}s; start Symphony through scripts/run-symphony.sh" >&2
    exit 84
  fi
  sleep 0.2
done

run_deadline=$((SECONDS + RUN_TIMEOUT_SECONDS))
while [[ ! -f "$response_path" ]]; do
  if (( SECONDS >= run_deadline )); then
    echo "RPG Kingdom Unity runner: host broker request '$request_id' exceeded ${RUN_TIMEOUT_SECONDS}s" >&2
    exit 85
  fi
  sleep 0.5
done

stdout="$(jq -r '.stdout // ""' "$response_path")"
stderr="$(jq -r '.stderr // ""' "$response_path")"
status="$(jq -r '.status // "failed"' "$response_path")"
exit_code="$(jq -r '.exitCode // 86' "$response_path")"

if [[ -n "$stdout" ]]; then
  printf '%s\n' "$stdout"
fi
if [[ -n "$stderr" ]]; then
  printf '%s\n' "$stderr" >&2
fi
if [[ "$status" == "NoTestsMatched" ]]; then
  echo "RPG Kingdom Unity runner: NoTestsMatched" >&2
fi

if [[ ! "$exit_code" =~ ^[0-9]+$ ]]; then
  echo "RPG Kingdom Unity runner: broker returned an invalid exit code" >&2
  exit 86
fi

exit "$exit_code"
