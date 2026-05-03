#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "This script must run as root. Use sudo." >&2
  exit 1
fi

INSTALL_DIR="${TIMELAPSE_INSTALL_DIR:-/opt/timelapse}"
ENV_FILE=/etc/timelapse-server/server.env

if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "Git checkout not found at $INSTALL_DIR" >&2
  exit 1
fi

git -C "$INSTALL_DIR" pull --ff-only

python3 -m venv "$INSTALL_DIR/server/.venv"
"$INSTALL_DIR/server/.venv/bin/pip" install -r "$INSTALL_DIR/server/requirements.txt"

INSTALL_DIR="$INSTALL_DIR" python3 - "$INSTALL_DIR/server/systemd/timelapse-server.service" /etc/systemd/system/timelapse-server.service <<'PY'
import os
import sys

source_path, destination_path = sys.argv[1], sys.argv[2]
with open(source_path, "r", encoding="utf-8") as source:
    service = source.read().replace("/opt/timelapse", os.environ["INSTALL_DIR"])
with open(destination_path, "w", encoding="utf-8") as destination:
    destination.write(service)
PY
chmod 644 /etc/systemd/system/timelapse-server.service
systemctl daemon-reload
systemctl restart timelapse-server

if [ -f "$ENV_FILE" ]; then
  # shellcheck source=/dev/null
  . "$ENV_FILE"
fi

BIND_HOST="${TIMELAPSE_BIND_HOST:-127.0.0.1}"
PORT="${TIMELAPSE_PORT:-8080}"

echo "Updated timelapse server."
curl -fsS "http://$BIND_HOST:$PORT/api/health" || {
  echo
  echo "Health check failed. Check: journalctl -u timelapse-server -n 100" >&2
  exit 1
}
echo
