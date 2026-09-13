#!/usr/bin/env bash
# Role-aware Codex concurrency policy for RPG Kingdom Supervisor.
#
# This file maps worker roles onto two bounded host slots:
#   mutation: implementation, repair, report-only
#   review:   independent read-only review
#
# A separate per-issue lock prevents review from overlapping mutation of the
# same RPG Kingdom issue/workspace while still allowing unrelated issues to
# use one mutation and one review Codex process concurrently.

rpgk_codex_slot_for_role() {
  case "${1:-}" in
    implementation|repair|report-only)
      printf '%s\n' mutation
      ;;
    review)
      printf '%s\n' review
      ;;
    *)
      echo "RPG Kingdom concurrency policy: unsupported Codex role '${1:-}'" >&2
      return 64
      ;;
  esac
}

rpgk_codex_slot_lock_path() {
  local state_root="${1:?state root required}"
  local role="${2:?role required}"
  local slot
  slot="$(rpgk_codex_slot_for_role "$role")" || return
  printf '%s/locks/codex-%s.lock\n' "$state_root" "$slot"
}

rpgk_codex_issue_lock_path() {
  local state_root="${1:?state root required}"
  local identifier="${2:?issue identifier required}"
  if [[ ! "$identifier" =~ ^GH-[0-9]+$ ]]; then
    echo "RPG Kingdom concurrency policy: invalid issue identifier '$identifier'" >&2
    return 64
  fi
  printf '%s/locks/issues/%s.lock\n' "$state_root" "$identifier"
}

rpgk_codex_max_total_processes() {
  printf '%s\n' 2
}
