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
  unity-runner.sh capture [--project PATH] --scene ASSET_PATH [--camera HIERARCHY_PATH] [--view-name NAME] [--position X,Y,Z] [--rotation X,Y,Z | --look-at HIERARCHY_PATH] [--fov DEGREES] [--reuse-stage-library] [--width PX] [--height PX]

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
scene_path=""
camera_path=""
view_name=""
camera_position=""
camera_rotation=""
look_at_path=""
field_of_view="60"
reuse_stage_library="false"
capture_width="1920"
capture_height="1080"

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
    --scene)
      [[ $# -ge 2 ]] || { echo "Missing value for --scene" >&2; exit 64; }
      scene_path="$2"
      shift 2
      ;;
    --camera)
      [[ $# -ge 2 ]] || { echo "Missing value for --camera" >&2; exit 64; }
      camera_path="$2"
      shift 2
      ;;
    --view-name) view_name="$2"; shift 2 ;;
    --position) camera_position="$2"; shift 2 ;;
    --rotation) camera_rotation="$2"; shift 2 ;;
    --look-at) look_at_path="$2"; shift 2 ;;
    --fov) field_of_view="$2"; shift 2 ;;
    --reuse-stage-library) reuse_stage_library="true"; shift ;;
    --width)
      [[ $# -ge 2 ]] || { echo "Missing value for --width" >&2; exit 64; }
      capture_width="$2"
      shift 2
      ;;
    --height)
      [[ $# -ge 2 ]] || { echo "Missing value for --height" >&2; exit 64; }
      capture_height="$2"
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
  health|editmode|playmode|capture) ;;
  *)
    echo "Unknown Unity runner command: $command" >&2
    usage
    exit 64
    ;;
esac

if [[ "$command" == "capture" ]]; then
  [[ -n "$scene_path" ]] || { echo "RPG Kingdom Unity runner: capture requires --scene" >&2; exit 64; }
  [[ "$scene_path" == Assets/*.unity ]] || { echo "RPG Kingdom Unity runner: --scene must be an Assets/*.unity path" >&2; exit 64; }
  [[ "$capture_width" =~ ^[0-9]+$ && "$capture_height" =~ ^[0-9]+$ ]] || { echo "RPG Kingdom Unity runner: capture dimensions must be integers" >&2; exit 64; }
  [[ -z "$camera_rotation" || -z "$look_at_path" ]] || { echo "RPG Kingdom Unity runner: use --rotation or --look-at, not both" >&2; exit 64; }
  [[ "$field_of_view" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "RPG Kingdom Unity runner: --fov must be numeric" >&2; exit 64; }
  (( capture_width >= 320 && capture_width <= 4096 && capture_height >= 180 && capture_height <= 4096 )) || {
    echo "RPG Kingdom Unity runner: capture dimensions are outside the supported bounds" >&2
    exit 64
  }
elif [[ -n "$scene_path" || -n "$camera_path" ]]; then
  echo "RPG Kingdom Unity runner: --scene/--camera are valid only for capture" >&2
  exit 64
fi

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
history_request_dir="$broker_dir/history/requests"
mkdir -p "$request_dir" "$ack_dir" "$response_dir" "$history_request_dir"

request_id="${workspace_name}-${command}-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
request_path="$request_dir/$request_id.json"
ack_path="$ack_dir/$request_id.json"
response_path="$response_dir/$request_id.json"
history_request_path="$history_request_dir/$request_id.json"
temp_history="$history_request_path.tmp.$$"
temp_request="$request_path.tmp.$$"
requested_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Keep the immutable request envelope beside broker responses. The transient request
# is consumed by the broker, while this copy lets diagnostics reconstruct filters and
# timing even when Unity fails before summary.json/results.xml are produced.
jq -cn \
  --arg requestId "$request_id" \
  --arg operation "$command" \
  --arg testFilter "$test_filter" \
  --arg scenePath "$scene_path" \
  --arg cameraPath "$camera_path" \
  --arg viewName "$view_name" \
  --arg cameraPosition "$camera_position" \
  --arg cameraRotation "$camera_rotation" \
  --arg lookAtPath "$look_at_path" \
  --arg fieldOfView "$field_of_view" \
  --argjson reuseStageLibrary "$reuse_stage_library" \
  --argjson captureWidth "$capture_width" \
  --argjson captureHeight "$capture_height" \
  --arg requestedAt "$requested_at" \
  --argjson clientPid "$$" \
  '{protocolVersion:1,requestId:$requestId,operation:$operation,testFilter:$testFilter,scenePath:$scenePath,cameraPath:$cameraPath,viewName:$viewName,cameraPosition:$cameraPosition,cameraRotation:$cameraRotation,lookAtPath:$lookAtPath,fieldOfView:($fieldOfView|tonumber),reuseStageLibrary:$reuseStageLibrary,captureWidth:$captureWidth,captureHeight:$captureHeight,requestedAt:$requestedAt,clientPid:$clientPid}' \
  > "$temp_history"
mv "$temp_history" "$history_request_path"
cp "$history_request_path" "$temp_request"
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

case "$status" in
  NoTestsMatched)
    echo "RPG Kingdom Unity runner: NoTestsMatched" >&2
    ;;
  HostBusy)
    echo "RPG Kingdom Unity runner: HostBusy" >&2
    ;;
  Stalled)
    echo "RPG Kingdom Unity runner: Stalled" >&2
    ;;
  StallRecoveryBlocked)
    echo "RPG Kingdom Unity runner: StallRecoveryBlocked" >&2
    ;;
  ClientAbandoned)
    echo "RPG Kingdom Unity runner: ClientAbandoned" >&2
    ;;
  ClientAbandonRecoveryBlocked)
    echo "RPG Kingdom Unity runner: ClientAbandonRecoveryBlocked" >&2
    ;;
  TimedOut)
    echo "RPG Kingdom Unity runner: TimedOut" >&2
    ;;
  StaleRequest)
    echo "RPG Kingdom Unity runner: StaleRequest" >&2
    ;;
  BrokerStopped)
    echo "RPG Kingdom Unity runner: BrokerStopped" >&2
    ;;
esac

if [[ ! "$exit_code" =~ ^[0-9]+$ ]]; then
  echo "RPG Kingdom Unity runner: broker returned an invalid exit code" >&2
  exit 86
fi

exit "$exit_code"
