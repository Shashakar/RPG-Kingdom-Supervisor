#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="${RPGK_SYMPHONY_WORKSPACE_ROOT:-$HOME/code/rpg-kingdom-symphony-workspaces}"

usage() {
  cat >&2 <<'EOF'
Usage:
  visual-review-pr.sh --issue N --pr N --scene Assets/.../Scene.unity [--profile JSON_PATH] [--camera HIERARCHY_PATH] [--width PX] [--height PX]

Runs a one-shot Unity capture + independent visual review for an existing PR already at
symphony:human-review. It does not start an implementation worker, dispatch repairs, or merge.
EOF
}

issue=""
pr=""
scene=""
camera=""
profile=""
width="1920"
height="1080"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue) issue="${2:-}"; shift 2 ;;
    --pr) pr="${2:-}"; shift 2 ;;
    --scene) scene="${2:-}"; shift 2 ;;
    --camera) camera="${2:-}"; shift 2 ;;
    --profile) profile="${2:-}"; shift 2 ;;
    --width) width="${2:-}"; shift 2 ;;
    --height) height="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 64 ;;
  esac
done

[[ "$issue" =~ ^[0-9]+$ ]] || { echo "visual-review-pr: --issue must be a positive integer" >&2; exit 64; }
[[ "$pr" =~ ^[0-9]+$ ]] || { echo "visual-review-pr: --pr must be a positive integer" >&2; exit 64; }
[[ -n "$scene" && "$scene" == Assets/*.unity && "$scene" != *".."* ]] || {
  echo "visual-review-pr: --scene must be a normalized Assets/*.unity path" >&2
  exit 64
}
[[ "$width" =~ ^[0-9]+$ && "$height" =~ ^[0-9]+$ ]] || {
  echo "visual-review-pr: dimensions must be integers" >&2
  exit 64
}
(( width >= 320 && width <= 4096 && height >= 180 && height <= 4096 )) || {
  echo "visual-review-pr: dimensions are outside supported bounds" >&2
  exit 64
}
if [[ -n "$profile" ]]; then
  [[ -f "$profile" ]] || { echo "visual-review-pr: profile not found: $profile" >&2; exit 64; }
  jq -e '.views | type == "array" and length >= 1 and length <= 4' "$profile" >/dev/null || {
    echo "visual-review-pr: profile must contain 1 to 4 views" >&2
    exit 64
  }
fi
[[ -n "${SYMPHONY_GITHUB_TOKEN:-}" ]] || {
  echo "visual-review-pr: SYMPHONY_GITHUB_TOKEN is required" >&2
  exit 70
}

workspace="$WORKSPACE_ROOT/GH-$issue"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
STATUS_FILE="$STATE_ROOT/visual-reviews/active/GH-$issue.json"
[[ -d "$workspace" ]] || {
  echo "visual-review-pr: workspace missing: $workspace" >&2
  exit 74
}

update_status() {
  local phase="$1"
  local summary="$2"
  local current_index="${3:-}"
  local current_name="${4:-}"
  local completed_views="${5:-}"

  [[ -f "$STATUS_FILE" ]] || return 0
  python3 - "$STATUS_FILE" "$phase" "$summary" "$BASHPID" "$current_index" "$current_name" "$completed_views" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
try:
    value = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
if not isinstance(value, dict):
    raise SystemExit(0)

value["state"] = "active"
value["phase"] = sys.argv[2]
value["summary"] = sys.argv[3]
value["pid"] = int(sys.argv[4])
value["updatedAt"] = datetime.now(timezone.utc).isoformat()
if sys.argv[5]:
    value["currentViewIndex"] = int(sys.argv[5])
if sys.argv[6]:
    value["currentViewName"] = sys.argv[6]
elif sys.argv[2] == "independent_review":
    value["currentViewName"] = None
if sys.argv[7]:
    value["completedViews"] = int(sys.argv[7])

temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
temp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
os.replace(temp, path)
PY
}

capture_index=0
released=0
release_unity() {
  if (( released == 0 )); then
    (cd "$workspace" && bash "$ROOT/scripts/release-unity-resource.sh") >/dev/null 2>&1 || true
    released=1
  fi
}
review_succeeded=0
cleanup() {
  status=$?
  if (( review_succeeded == 0 && status != 0 )); then
    update_status "failed" "Visual review process exited with status $status." "" "" "$capture_index" || true
  fi
  release_unity
}
trap cleanup EXIT

update_status "acquiring_unity" "Acquiring the exclusive Unity resource." "" "" "0"
echo "visual-review-pr: acquiring Unity resource for GH-$issue"
(
  cd "$workspace"
  RPGK_UNITY_GUARD_DRY_RUN=1 \
    RPGK_UNITY_GUARD_SKIP_READINESS_SMOKE=1 \
    bash "$ROOT/scripts/unity-resource-guard.sh"
)

capture_one() {
  local name="$1"
  local camera_path="$2"
  local position="$3"
  local rotation="$4"
  local look_at="$5"
  local fov="$6"
  local view_width="$7"
  local view_height="$8"

  local args=(
    capture
    --project "$workspace"
    --scene "$scene"
    --width "$view_width"
    --height "$view_height"
    --view-name "$name"
    --fov "$fov"
  )
  [[ -n "$camera_path" ]] && args+=( --camera "$camera_path" )
  [[ -n "$position" ]] && args+=( --position "$position" )
  [[ -n "$rotation" ]] && args+=( --rotation "$rotation" )
  [[ -n "$look_at" ]] && args+=( --look-at "$look_at" )
  if (( capture_index > 0 )); then
    args+=( --reuse-stage-library )
  fi

  local view_index=$((capture_index + 1))
  local total_views
  total_views="$(python3 - "$STATUS_FILE" <<'PY'
import json, sys
from pathlib import Path
try:
    value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(int(value.get("totalViews") or 1))
except Exception:
    print(1)
PY
)"
  update_status "capturing" "Capturing view $view_index of $total_views: $name" "$view_index" "$name" "$capture_index"
  echo "visual-review-pr: capturing $scene view '$name'"
  bash "$ROOT/scripts/unity-runner.sh" "${args[@]}"
  capture_index=$((capture_index + 1))
  update_status "view_completed" "Completed capture $capture_index of $total_views: $name" "$capture_index" "$name" "$capture_index"
}

if [[ -n "$profile" ]]; then
  while IFS= read -r view; do
    name="$(jq -r '.name // "view"' <<<"$view")"
    camera_path="$(jq -r '.camera // empty' <<<"$view")"
    position="$(jq -r 'if .position then (.position | map(tostring) | join(",")) else "" end' <<<"$view")"
    rotation="$(jq -r 'if .rotation then (.rotation | map(tostring) | join(",")) else "" end' <<<"$view")"
    look_at="$(jq -r '.lookAt // empty' <<<"$view")"
    fov="$(jq -r '.fov // 60' <<<"$view")"
    view_width="$(jq -r '.width // empty' <<<"$view")"
    view_height="$(jq -r '.height // empty' <<<"$view")"
    [[ -n "$view_width" ]] || view_width="$width"
    [[ -n "$view_height" ]] || view_height="$height"
    [[ -n "$camera_path" ]] || camera_path="$camera"
    capture_one "$name" "$camera_path" "$position" "$rotation" "$look_at" "$fov" "$view_width" "$view_height"
  done < <(jq -c '.views[]' "$profile")
else
  capture_one "default" "$camera" "" "" "" "60" "$width" "$height"
fi

update_status "independent_review" "Running independent image-aware review for PR #$pr." "" "" "$capture_index"
echo "visual-review-pr: running independent visual review for PR #$pr"
python3 "$ROOT/scripts/review-orchestrator.py" --visual-review "$issue" "$pr"

update_status "review_completed" "Independent visual review completed for PR #$pr." "" "" "$capture_index"
review_succeeded=1
echo "visual-review-pr: visual review completed for GH-$issue / PR #$pr"
