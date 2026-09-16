#!/bin/bash
# Install the systemd service for System Monitor.
# Auto-fills User= and paths from the current user & directory.
# Usage: sudo ./install-service.sh [--port 9090]
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
PORT="9090"

for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-9090}" ;;
  esac
done

UNIT_PATH="/etc/systemd/system/system-monitor.service"

cat > "$UNIT_PATH" <<EOF
[Unit]
Description=System Monitor - Realtime Dashboard
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/app.py --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
Environment=PORT=$PORT

StandardOutput=journal
StandardError=journal
SyslogIdentifier=system-monitor

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now system-monitor
systemctl status system-monitor --no-pager
echo ""
echo "Logs: journalctl -u system-monitor -f"
echo "Dashboard: http://localhost:$PORT/"
