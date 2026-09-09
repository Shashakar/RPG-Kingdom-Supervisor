#!/usr/bin/env bash

# Side-effect-free Phase 3 Unity scheduling policy.
# Input labels are newline-delimited, exactly as returned by GitHub.
#
# Prints: <resource-mode>\t<validation-mode>
#   resource-mode: none | unity-editor
#   validation-mode: none | optional | required
# Returns non-zero for conflicting or incomplete policy.

rpgk_unity_has_label() {
  local needle="$1"
  local labels="$2"
  grep -Fxiq -- "$needle" <<<"$labels"
}

rpgk_select_unity_policy() {
  local labels="${1:-}"

  local required=0
  local optional=0
  local unity_resource=0

  rpgk_unity_has_label "validation:unity-required" "$labels" && required=1
  rpgk_unity_has_label "validation:unity-optional" "$labels" && optional=1
  rpgk_unity_has_label "resource:unity-editor" "$labels" && unity_resource=1

  if (( required == 1 && optional == 1 )); then
    printf 'conflicting Unity validation labels\n' >&2
    return 20
  fi

  if (( required == 1 && unity_resource == 0 )); then
    printf 'validation:unity-required requires resource:unity-editor\n' >&2
    return 21
  fi

  local resource_mode="none"
  local validation_mode="none"

  (( unity_resource == 1 )) && resource_mode="unity-editor"
  (( optional == 1 )) && validation_mode="optional"
  (( required == 1 )) && validation_mode="required"

  printf '%s\t%s\n' "$resource_mode" "$validation_mode"
}
