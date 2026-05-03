#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo image/patch-raspios-image.sh --image raspios-lite.img --config image/headless.env [--output timelapse.img]

Creates a fully headless Raspberry Pi OS Lite image by installing:
  - timelapse capture agent
  - systemd agent service
  - first-boot Wi-Fi configuration service
  - baked server URL and camera ID

The input image must be an uncompressed .img file.
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_PATH=""
CONFIG_PATH=""
OUTPUT_PATH=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --image)
      IMAGE_PATH="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "This script must run as root because it mounts image partitions." >&2
  exit 1
fi

if [ -z "$IMAGE_PATH" ] || [ -z "$CONFIG_PATH" ]; then
  usage >&2
  exit 2
fi

if [ ! -f "$IMAGE_PATH" ]; then
  echo "Image not found: $IMAGE_PATH" >&2
  exit 1
fi

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Config not found: $CONFIG_PATH" >&2
  exit 1
fi

for command in losetup mount umount python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 1
  fi
done

if [ -n "$OUTPUT_PATH" ]; then
  cp "$IMAGE_PATH" "$OUTPUT_PATH"
  IMAGE_PATH="$OUTPUT_PATH"
fi

set -a
# shellcheck source=/dev/null
. "$CONFIG_PATH"
set +a

: "${TIMELAPSE_CAMERA_ID:=tomatoes-zero-w}"
: "${TIMELAPSE_SERVER_URL:?TIMELAPSE_SERVER_URL is required}"
: "${TIMELAPSE_WIFI_SSID:?TIMELAPSE_WIFI_SSID is required}"
: "${TIMELAPSE_WIFI_COUNTRY:=SE}"
: "${TIMELAPSE_WIFI_HIDDEN:=false}"
: "${TIMELAPSE_CONFIG_POLL_SECONDS:=60}"
: "${TIMELAPSE_WORK_DIR:=/var/lib/timelapse-agent}"
: "${TIMELAPSE_ENABLE_SSH:=false}"

ROOT_MOUNT="$(mktemp -d)"
BOOT_MOUNT="$(mktemp -d)"
LOOP_DEVICE=""

cleanup() {
  set +e
  if mountpoint -q "$BOOT_MOUNT"; then
    umount "$BOOT_MOUNT"
  fi
  if mountpoint -q "$ROOT_MOUNT"; then
    umount "$ROOT_MOUNT"
  fi
  if [ -n "$LOOP_DEVICE" ]; then
    losetup -d "$LOOP_DEVICE"
  fi
  rmdir "$BOOT_MOUNT" "$ROOT_MOUNT" 2>/dev/null || true
}
trap cleanup EXIT

LOOP_DEVICE="$(losetup --find --show --partscan "$IMAGE_PATH")"
sleep 1

if [ -b "${LOOP_DEVICE}p1" ]; then
  BOOT_PART="${LOOP_DEVICE}p1"
  ROOT_PART="${LOOP_DEVICE}p2"
else
  BOOT_PART="${LOOP_DEVICE}1"
  ROOT_PART="${LOOP_DEVICE}2"
fi

if [ ! -b "$BOOT_PART" ] || [ ! -b "$ROOT_PART" ]; then
  echo "Could not find boot/root partitions for $LOOP_DEVICE" >&2
  exit 1
fi

mount "$ROOT_PART" "$ROOT_MOUNT"
mount "$BOOT_PART" "$BOOT_MOUNT"

install -d -m 755 "$ROOT_MOUNT/opt/timelapse-agent"
install -m 755 "$REPO_ROOT/agent/timelapse_agent.py" "$ROOT_MOUNT/opt/timelapse-agent/timelapse_agent.py"
install -D -m 755 "$REPO_ROOT/agent/timelapse_firstboot_network.sh" "$ROOT_MOUNT/usr/local/sbin/timelapse-firstboot-network"

install -D -m 644 "$REPO_ROOT/agent/systemd/timelapse-agent.service" "$ROOT_MOUNT/etc/systemd/system/timelapse-agent.service"
install -D -m 644 "$REPO_ROOT/agent/systemd/timelapse-firstboot-network.service" "$ROOT_MOUNT/etc/systemd/system/timelapse-firstboot-network.service"

install -d -m 755 "$ROOT_MOUNT/etc/systemd/system/multi-user.target.wants"
ln -sf ../timelapse-agent.service "$ROOT_MOUNT/etc/systemd/system/multi-user.target.wants/timelapse-agent.service"

install -d -m 755 "$ROOT_MOUNT/etc/systemd/system/sysinit.target.wants"
ln -sf ../timelapse-firstboot-network.service "$ROOT_MOUNT/etc/systemd/system/sysinit.target.wants/timelapse-firstboot-network.service"

install -d -m 700 "$ROOT_MOUNT/etc/timelapse-agent"

python3 - "$ROOT_MOUNT/etc/timelapse-agent/config.json" <<'PY'
import json
import os
import sys

config = {
    "camera_id": os.environ["TIMELAPSE_CAMERA_ID"],
    "server_url": os.environ["TIMELAPSE_SERVER_URL"],
    "config_poll_seconds": int(os.environ["TIMELAPSE_CONFIG_POLL_SECONDS"]),
    "work_dir": os.environ["TIMELAPSE_WORK_DIR"],
}

with open(sys.argv[1], "w", encoding="utf-8") as output:
    json.dump(config, output, indent=2)
    output.write("\n")
PY

python3 - "$ROOT_MOUNT/etc/timelapse-agent/headless.env" <<'PY'
import os
import shlex
import sys

keys = [
    "TIMELAPSE_WIFI_SSID",
    "TIMELAPSE_WIFI_PSK",
    "TIMELAPSE_WIFI_COUNTRY",
    "TIMELAPSE_WIFI_HIDDEN",
]

with open(sys.argv[1], "w", encoding="utf-8") as output:
    for key in keys:
        value = os.environ.get(key, "")
        output.write(f"{key}={shlex.quote(value)}\n")
PY

chmod 600 "$ROOT_MOUNT/etc/timelapse-agent/config.json"
chmod 600 "$ROOT_MOUNT/etc/timelapse-agent/headless.env"

case "$TIMELAPSE_ENABLE_SSH" in
  true|TRUE|1|yes|YES)
    touch "$BOOT_MOUNT/ssh"
    install -d -m 755 "$ROOT_MOUNT/etc/systemd/system/multi-user.target.wants"
    if [ -f "$ROOT_MOUNT/lib/systemd/system/ssh.service" ]; then
      ln -sf /lib/systemd/system/ssh.service "$ROOT_MOUNT/etc/systemd/system/multi-user.target.wants/ssh.service"
    fi
    if [ -n "${TIMELAPSE_USER:-}" ] && [ -n "${TIMELAPSE_PASSWORD_HASH:-}" ]; then
      printf '%s:%s\n' "$TIMELAPSE_USER" "$TIMELAPSE_PASSWORD_HASH" > "$BOOT_MOUNT/userconf.txt"
      chmod 600 "$BOOT_MOUNT/userconf.txt"
    fi
    ;;
esac

sync

echo "Patched image: $IMAGE_PATH"
echo "Camera ID: $TIMELAPSE_CAMERA_ID"
echo "Server URL: $TIMELAPSE_SERVER_URL"
echo "Wi-Fi SSID: $TIMELAPSE_WIFI_SSID"
