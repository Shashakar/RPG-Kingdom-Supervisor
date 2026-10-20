#!/usr/bin/env bash
# Source this from run-symphony.sh. Holds FD 7 for the entire Supervisor process.
# Intentionally shared by Rekindle and RPG Kingdom profiles on the same host.
rpgk_acquire_host_instance_lock() {
  local lock_file="$HOME/.local/state/rpg-kingdom-supervisor/host-instance.lock"
  mkdir -p "$(dirname "$lock_file")" || return 75
  exec 7>"$lock_file" || return 75
  if ! flock -n 7; then
    echo "ERROR: another Supervisor host instance is already active; stop the manual launcher or system service before starting another" >&2
    return 75
  fi
  # Child brokers may inherit FD 7 and preserve ownership until they exit;
  # an overlapping launch must not silently reuse brokers and spawn extra schedulers.
}
