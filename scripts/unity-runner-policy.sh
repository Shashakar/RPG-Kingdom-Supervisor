#!/usr/bin/env bash

# Side-effect-free policy helpers for the Phase 4 Windows Unity runner.

rpgk_project_unity_version() {
  local project_root="${1:-.}"
  local version_file="$project_root/ProjectSettings/ProjectVersion.txt"

  if [[ ! -f "$version_file" ]]; then
    printf 'Unity ProjectVersion.txt not found at %s\n' "$version_file" >&2
    return 30
  fi

  local version
  version="$(sed -n 's/^m_EditorVersion:[[:space:]]*//p' "$version_file" | head -n 1 | tr -d '\r')"
  if [[ -z "$version" || ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+[abfp][0-9]+$ ]]; then
    printf 'Could not parse a supported Unity editor version from %s\n' "$version_file" >&2
    return 31
  fi

  printf '%s\n' "$version"
}

rpgk_default_unity_editor_windows() {
  local version="$1"
  printf 'C:\\Program Files\\Unity\\Hub\\Editor\\%s\\Editor\\Unity.exe\n' "$version"
}

rpgk_normalize_test_platform() {
  case "${1:-}" in
    editmode|EditMode|edit|Edit)
      printf 'EditMode\n'
      ;;
    playmode|PlayMode|play|Play)
      printf 'PlayMode\n'
      ;;
    *)
      printf 'Unsupported Unity test platform: %s\n' "${1:-<empty>}" >&2
      return 32
      ;;
  esac
}
