#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-/home/dex/src/RPG-Kingdom-Supervisor}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-/home/dex/.local/state/rpg-kingdom-supervisor}"
STATUS_FILE="$STATE_ROOT/dashboard-update.json"
LOCK_FILE="$STATE_ROOT/dashboard-update.lock"
CLOUDFLARE_TASK="${RPGK_CLOUDFLARE_REFRESH_TASK:-RPG Kingdom Supervisor - Refresh Cloudflare}"
git_cmd() {
  git -C "$SUPERVISOR_ROOT" "$@"
}

mkdir -p "$STATE_ROOT"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

write_status() {
  local state="$1" message="$2" previous="${3:-}" current="${4:-}"
  python3 - "$STATUS_FILE" "$state" "$message" "$previous" "$current" <<'PY'
import json, pathlib, sys
from datetime import datetime, timezone
path, state, message, previous, current = sys.argv[1:]
payload = {
    "state": state,
    "message": message,
    "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "previousHead": previous or None,
    "currentHead": current or None,
}
tmp = pathlib.Path(path + ".tmp")
tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

previous=""
current=""
trap 'rc=$?; if (( rc != 0 )); then write_status "failed" "Update failed with exit code $rc" "$previous" "$current"; fi' EXIT

previous="$(git_cmd rev-parse HEAD)"
write_status "running" "Fetching origin/main" "$previous" "$previous"

if [[ -n "$(git_cmd status --porcelain)" ]]; then
  write_status "failed" "Update refused because the Supervisor checkout has local changes" "$previous" "$previous"
  exit 20
fi
if [[ "$(git_cmd branch --show-current)" != "main" ]]; then
  write_status "failed" "Update refused because the Supervisor checkout is not on main" "$previous" "$previous"
  exit 21
fi

git_cmd fetch --prune origin main
git_cmd merge --ff-only origin/main
current="$(git_cmd rev-parse HEAD)"
write_status "restarting" "Code updated; restarting Supervisor services" "$previous" "$current"

sudo -n /usr/bin/systemctl restart rpg-kingdom-supervisor.service
sudo -n /usr/bin/systemctl restart rpg-kingdom-diagnostics.service

# Windows cloudflared can retain a stale WSL localhost route after the dashboard restarts.
# A pre-created elevated Windows Scheduled Task is the deliberately narrow privilege bridge.
POWERSHELL_EXE="${RPGK_POWERSHELL_EXE:-/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe}"
if [[ -x "$POWERSHELL_EXE" ]]; then
  if [[ "$CLOUDFLARE_TASK" == *"'"* ]]; then
    write_status "failed" "Cloudflared refresh task name contains an unsupported quote" "$previous" "$current"
    exit 22
  fi
  # Start-ScheduledTask is asynchronous. Wait for this specific invocation to
  # acquire a new LastRunTime, finish, and report exit code 0 before declaring
  # the deployment complete.
  if ! "$POWERSHELL_EXE" -NoProfile -NonInteractive -Command "
    \$ErrorActionPreference = 'Stop'
    \$taskName = '$CLOUDFLARE_TASK'
    \$before = (Get-ScheduledTaskInfo -TaskName \$taskName).LastRunTime
    Start-ScheduledTask -TaskName \$taskName
    \$deadline = (Get-Date).AddSeconds(60)
    do {
      Start-Sleep -Milliseconds 500
      \$task = Get-ScheduledTask -TaskName \$taskName
      \$info = Get-ScheduledTaskInfo -TaskName \$taskName
      \$ran = \$info.LastRunTime -gt \$before
      if (\$ran -and \$task.State -ne 'Running') {
        if (\$info.LastTaskResult -ne 0) { throw \"Cloudflared refresh task failed with result \$(\$info.LastTaskResult)\" }
        exit 0
      }
    } while ((Get-Date) -lt \$deadline)
    throw 'Timed out waiting for Cloudflared refresh task to complete'
  " >/dev/null; then
    write_status "failed" "Supervisor restarted, but the Cloudflared refresh task did not complete successfully" "$previous" "$current"
    exit 22
  fi
else
  write_status "failed" "Supervisor restarted, but Windows PowerShell was not found at $POWERSHELL_EXE so Cloudflared was not refreshed" "$previous" "$current"
  exit 22
fi

write_status "succeeded" "Updated Supervisor and restarted Supervisor, diagnostics, and Cloudflared" "$previous" "$current"
trap - EXIT
