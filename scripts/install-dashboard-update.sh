#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
USER_NAME="${SUDO_USER:-$USER}"
UNIT=/etc/systemd/system/rpg-kingdom-dashboard-update.service
SUDOERS=/etc/sudoers.d/rpg-kingdom-dashboard-update

if [[ ! -f "$SUPERVISOR_ROOT/scripts/dashboard-update-host.sh" ]]; then
  echo "ERROR: dashboard-update-host.sh not found under $SUPERVISOR_ROOT" >&2
  exit 2
fi

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=RPG Kingdom Supervisor dashboard update
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$SUPERVISOR_ROOT
Environment=HOME=/home/$USER_NAME
ExecStart=/bin/bash $SUPERVISOR_ROOT/scripts/dashboard-update-host.sh
EOF

sudo tee "$SUDOERS" >/dev/null <<EOF
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start rpg-kingdom-dashboard-update.service
EOF
sudo chmod 0440 "$SUDOERS"
sudo visudo -cf "$SUDOERS" >/dev/null
sudo systemctl daemon-reload

echo "Installed WSL dashboard update privilege bridge."
echo "Next, run scripts/windows/install-cloudflared-refresh-task.ps1 once from an elevated Windows PowerShell."
