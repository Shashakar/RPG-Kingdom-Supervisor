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
[[ -d "$workspace" ]] || {
  echo "visual-review-pr: workspace missing: $workspace" >&2
  exit 74
}

released=0
release_unity() {
  if (( released == 0 )); then
    (cd "$workspace" && bash "$ROOT/scripts/release-unity-resource.sh") >/dev/null 2>&1 || true
    released=1
  fi
}
trap release_unity EXIT

echo "visual-review-pr: acquiring Unity resource for GH-$issue"
(
  cd "$workspace"
  RPGK_UNITY_GUARD_DRY_RUN=1 \
    RPGK_UNITY_GUARD_SKIP_READINESS_SMOKE=1 \
    bash "$ROOT/scripts/unity-resource-guard.sh"
)

capture_index=0
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

  echo "visual-review-pr: capturing $scene view '$name'"
  bash "$ROOT/scripts/unity-runner.sh" "${args[@]}"
  capture_index=$((capture_index + 1))
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

echo "visual-review-pr: running independent visual review for PR #$pr"
python3 "$ROOT/scripts/review-orchestrator.py" --visual-review "$issue" "$pr"

echo "visual-review-pr: visual review completed for GH-$issue / PR #$pr"
