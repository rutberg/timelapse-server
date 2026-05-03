#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo scripts/install-server.sh

Optional environment variables:
  TIMELAPSE_REPO_URL=https://github.com/rutberg/timelapse
  TIMELAPSE_INSTALL_DIR=/opt/timelapse
  TIMELAPSE_DATA_DIR=/srv/timelapse
  TIMELAPSE_BIND_HOST=192.168.68.52
  TIMELAPSE_PORT=8080
  TIMELAPSE_ALLOWED_NETWORKS=127.0.0.0/8,192.168.68.0/22

If TIMELAPSE_INSTALL_DIR does not already contain a Git checkout,
TIMELAPSE_REPO_URL is required so the installer can clone the project.
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "$EUID" -ne 0 ]; then
  echo "This script must run as root. Use sudo." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer expects Debian/Ubuntu with apt-get." >&2
  exit 1
fi

INSTALL_DIR="${TIMELAPSE_INSTALL_DIR:-/opt/timelapse}"
DATA_DIR="${TIMELAPSE_DATA_DIR:-/srv/timelapse}"
PORT="${TIMELAPSE_PORT:-8080}"
REPO_URL="${TIMELAPSE_REPO_URL:-}"

apt-get update
apt-get install -y ca-certificates curl ffmpeg git iproute2 python3 python3-venv

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  if [ -z "$REPO_URL" ]; then
    echo "No Git checkout found at $INSTALL_DIR." >&2
    echo "Set TIMELAPSE_REPO_URL or clone the repo there first." >&2
    exit 1
  fi
  install -d -m 755 "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

if [ ! -f "$INSTALL_DIR/server/app/main.py" ]; then
  echo "Server app not found at $INSTALL_DIR/server/app/main.py" >&2
  exit 1
fi

detect_bind_host() {
  local address
  address="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\./ { print; exit }')"
  if [ -n "$address" ]; then
    printf '%s\n' "$address"
    return
  fi
  ip -o -4 addr show scope global | awk '{split($4, parts, "/"); print parts[1]; exit}'
}

BIND_HOST="${TIMELAPSE_BIND_HOST:-$(detect_bind_host)}"
if [ -z "$BIND_HOST" ]; then
  echo "Could not detect bind host. Set TIMELAPSE_BIND_HOST." >&2
  exit 1
fi

detect_lan_network() {
  local bind_host="$1"
  local cidr
  cidr="$(ip -o -4 addr show scope global | awk -v host="$bind_host" '{split($4, parts, "/"); if (parts[1] == host) { print $4; exit }}')"
  if [ -z "$cidr" ]; then
    return
  fi
  CIDR="$cidr" python3 - <<'PY'
import ipaddress
import os

print(ipaddress.ip_interface(os.environ["CIDR"]).network)
PY
}

LAN_NETWORK="$(detect_lan_network "$BIND_HOST" || true)"
if [ -n "${TIMELAPSE_ALLOWED_NETWORKS:-}" ]; then
  ALLOWED_NETWORKS="$TIMELAPSE_ALLOWED_NETWORKS"
elif [ -n "$LAN_NETWORK" ]; then
  ALLOWED_NETWORKS="127.0.0.0/8,$LAN_NETWORK"
else
  ALLOWED_NETWORKS="127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
fi

install -d -m 755 "$DATA_DIR"
install -d -m 755 /etc/timelapse-server

cat > /etc/timelapse-server/server.env <<EOF
TIMELAPSE_DATA_DIR=$DATA_DIR
TIMELAPSE_ALLOWED_NETWORKS=$ALLOWED_NETWORKS
TIMELAPSE_BIND_HOST=$BIND_HOST
TIMELAPSE_PORT=$PORT
EOF
chmod 644 /etc/timelapse-server/server.env

python3 -m venv "$INSTALL_DIR/server/.venv"
"$INSTALL_DIR/server/.venv/bin/pip" install --upgrade pip
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
systemctl enable --now timelapse-server
systemctl restart timelapse-server

echo "Installed timelapse server."
echo "Service: timelapse-server"
echo "URL: http://$BIND_HOST:$PORT"
echo "Data directory: $DATA_DIR"
echo "Allowed networks: $ALLOWED_NETWORKS"

if command -v curl >/dev/null 2>&1; then
  sleep 1
  curl -fsS "http://$BIND_HOST:$PORT/api/health" || {
    echo
    echo "Health check failed. Check: journalctl -u timelapse-server -n 100" >&2
    exit 1
  }
  echo
fi
