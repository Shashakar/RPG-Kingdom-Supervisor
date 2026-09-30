#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-/home/dex/src/RPG-Kingdom-Supervisor}"
STATE_ROOT="${RPGK_SUPERVISOR_STATE_ROOT:-/home/dex/.local/state/rpg-kingdom-supervisor}"
STATUS_FILE="$STATE_ROOT/dashboard-update.json"
LOCK_FILE="$STATE_ROOT/dashboard-update.lock"
CLOUDFLARE_TASK="${RPGK_CLOUDFLARE_REFRESH_TASK:-RPG Kingdom Supervisor - Refresh Cloudflare}"

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

cd "$SUPERVISOR_ROOT"
previous="$(git rev-parse HEAD)"
write_status "running" "Fetching origin/main" "$previous" "$previous"

if [[ -n "$(git status --porcelain)" ]]; then
  write_status "failed" "Update refused because the Supervisor checkout has local changes" "$previous" "$previous"
  exit 20
fi
if [[ "$(git branch --show-current)" != "main" ]]; then
  write_status "failed" "Update refused because the Supervisor checkout is not on main" "$previous" "$previous"
  exit 21
fi

git fetch --prune origin main
git merge --ff-only origin/main
current="$(git rev-parse HEAD)"
write_status "restarting" "Code updated; restarting Supervisor services" "$previous" "$current"

systemctl restart rpg-kingdom-supervisor.service
systemctl restart rpg-kingdom-diagnostics.service

# Windows cloudflared can retain a stale WSL localhost route after the dashboard restarts.
# A pre-created elevated Windows Scheduled Task is the deliberately narrow privilege bridge.
if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -NonInteractive -Command "Start-ScheduledTask -TaskName '$CLOUDFLARE_TASK'" >/dev/null
else
  write_status "failed" "Supervisor restarted, but powershell.exe is unavailable so Cloudflared was not refreshed" "$previous" "$current"
  exit 22
fi

write_status "succeeded" "Updated Supervisor and restarted Supervisor, diagnostics, and Cloudflared" "$previous" "$current"
trap - EXIT
