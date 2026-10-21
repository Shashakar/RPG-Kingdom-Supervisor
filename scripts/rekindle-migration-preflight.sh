#!/usr/bin/env bash
set -euo pipefail
[[ "${RPGK_TARGET_PROFILE:-}" == "rekindle" ]] || exit 0
[[ "${RPGK_REPO:-}" == "Shashakar/Rekindle-Claude" ]] || { echo "Rekindle preflight: wrong target repository" >&2; exit 73; }
[[ "${RPGK_BASE_BRANCH:-}" == "rekindle-vertical-slice" ]] || { echo "Rekindle preflight: wrong integration base" >&2; exit 73; }
[[ "$(basename "$PWD")" =~ ^GH-[0-9]+$ ]] || { echo "Rekindle preflight: not GH workspace" >&2; exit 73; }
[[ -f AGENTS.md && -f Docs/ARCHITECTURE.md ]] || { echo "Rekindle preflight: repository contracts missing; merge agent-contract PR" >&2; exit 73; }
git ls-tree --name-only HEAD AGENTS.md | grep -Fxq AGENTS.md || { echo "Rekindle preflight: AGENTS.md is not tracked" >&2; exit 73; }
case "$(git remote get-url origin)" in
  https://github.com/Shashakar/Rekindle-Claude|https://github.com/Shashakar/Rekindle-Claude.git|git@github.com:Shashakar/Rekindle-Claude.git) ;;
  *) echo "Rekindle preflight: wrong origin" >&2; exit 73 ;;
esac
if [[ "${RPGK_REKINDLE_UNITY_READY:-0}" != "1" ]]; then
  issue="$(basename "$PWD")"
  labels="$(gh issue view "${issue#GH-}" --repo "$RPGK_REPO" --json labels --jq '.labels[].name')"
  if grep -Eqi '^(resource:unity-editor|validation:unity-required|authoring:.*)$' <<<"$labels"; then
    # Grant only the exact separately reviewed visual lane, never generic Unity.
    visual_labels=(
      agent:claude work:visual-unity resource:unity-editor
      validation:unity-required authoring:scene-new-composition
    )
    visual_ok=1
    for label in "${visual_labels[@]}"; do
      grep -Fxiq "$label" <<<"$labels" || visual_ok=0
    done
    if grep -Eqi '^(agent:codex|agent:auto|symphony:ready|validation:unity-optional|authoring:scene-(mechanical|structural|existing-composition))$' <<<"$labels"; then
      visual_ok=0
    fi
    if [[ "$visual_ok" != 1 ||
          "${RPGK_CLAUDE_VISUAL_HOST_READY:-0}" != 1 ||
          "${RPGK_CLAUDE_VISUAL_UNITY_ENABLED:-0}" != 1 ||
          "${RPGK_CLAUDE_LICENSED_VISUAL_REVIEW_APPROVED:-0}" != 1 ]]; then
      echo "Rekindle preflight: Unity work blocked; stage-only Claude visual approval required" >&2
      exit 74
    fi
  fi
fi
