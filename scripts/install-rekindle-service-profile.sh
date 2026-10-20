#!/usr/bin/env bash
set -euo pipefail

# Install only; restarting a live Supervisor while another foreground instance
# owns Unity/Git resources is an explicit operator decision.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" != "--apply" || $# -ne 1 ]]; then
  echo "Usage: bash scripts/install-rekindle-service-profile.sh --apply" >&2
  echo "Installs systemd SYSTEM-unit drop-ins; never restarts a service." >&2
  exit 64
fi
[[ "$(id -un)" == "dex" ]] || { echo "Run this as dex (not via sudo bash)." >&2; exit 73; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 73; }
command -v sudo >/dev/null || { echo "sudo is required" >&2; exit 73; }

check_unit() {
  local unit="$1" expected="$2" owner actual_exec
  owner="$(systemctl show "$unit" -p User --value)"
  actual_exec="$(systemctl show "$unit" -p ExecStart --value)"
  [[ "$owner" == dex ]] || { echo "Refusing $unit: expected User=dex, got $owner" >&2; return 73; }
  [[ "$actual_exec" == *"$expected"* ]] || { echo "Refusing $unit: ExecStart does not contain $expected" >&2; return 73; }
}
check_unit rpg-kingdom-supervisor.service "/home/dex/src/RPG-Kingdom-Supervisor/scripts/run-symphony.sh"
check_unit rpg-kingdom-diagnostics.service "/home/dex/src/RPG-Kingdom-Supervisor/scripts/"

for unit in rpg-kingdom-supervisor rpg-kingdom-diagnostics; do
  source="$ROOT/systemd/$unit.service.d/90-rekindle-target.conf"
  target="/etc/systemd/system/$unit.service.d/90-rekindle-target.conf"
  [[ -f "$source" ]] || { echo "Missing $source" >&2; exit 73; }
  sudo install -D -m 0644 "$source" "$target"
  echo "Installed $target"
done
sudo systemctl daemon-reload
echo "Profile overrides installed. SERVICES WERE NOT RESTARTED."
echo "Stop any manual run first; then restart Supervisor and diagnostics and verify status."
