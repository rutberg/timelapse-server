#!/bin/sh
set -eu

ENV_FILE=/etc/timelapse-agent/headless.env
CONNECTION_FILE=/etc/NetworkManager/system-connections/timelapse-wifi.nmconnection

if [ ! -f "$ENV_FILE" ]; then
  exit 0
fi

. "$ENV_FILE"

: "${TIMELAPSE_WIFI_SSID:?TIMELAPSE_WIFI_SSID is required}"
: "${TIMELAPSE_WIFI_COUNTRY:=SE}"
: "${TIMELAPSE_WIFI_HIDDEN:=false}"

if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_wifi_country "$TIMELAPSE_WIFI_COUNTRY" || true
fi

install -d -m 700 /etc/NetworkManager/system-connections

case "$TIMELAPSE_WIFI_HIDDEN" in
  true|TRUE|1|yes|YES) hidden=true ;;
  *) hidden=false ;;
esac

uuid="$(cat /proc/sys/kernel/random/uuid)"
temp_file="${CONNECTION_FILE}.tmp"

{
  printf '[connection]\n'
  printf 'id=timelapse-wifi\n'
  printf 'uuid=%s\n' "$uuid"
  printf 'type=wifi\n'
  printf 'interface-name=wlan0\n'
  printf 'autoconnect=true\n\n'
  printf '[wifi]\n'
  printf 'mode=infrastructure\n'
  printf 'ssid=%s\n' "$TIMELAPSE_WIFI_SSID"
  printf 'hidden=%s\n\n' "$hidden"
  if [ -n "${TIMELAPSE_WIFI_PSK:-}" ]; then
    printf '[wifi-security]\n'
    printf 'key-mgmt=wpa-psk\n'
    printf 'psk=%s\n\n' "$TIMELAPSE_WIFI_PSK"
  fi
  printf '[ipv4]\n'
  printf 'method=auto\n\n'
  printf '[ipv6]\n'
  printf 'addr-gen-mode=default\n'
  printf 'method=auto\n\n'
  printf '[proxy]\n'
} > "$temp_file"

chmod 600 "$temp_file"
mv "$temp_file" "$CONNECTION_FILE"

if command -v nmcli >/dev/null 2>&1; then
  nmcli connection reload || true
  nmcli radio wifi on || true
fi

rm -f "$ENV_FILE"
systemctl disable timelapse-firstboot-network.service >/dev/null 2>&1 || true
