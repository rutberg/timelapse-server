#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${TIMELAPSE_VENV:-$ROOT/.venv-dev}"
PYTHON="${PYTHON:-python3}"
APT_PACKAGES=(ca-certificates ffmpeg openssh-client)

install_apt_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "apt-get not found; skipping system package install."
    return
  fi

  if [ "$(id -u)" -eq 0 ]; then
    apt-get update
    env DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"
  elif command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"
  else
    echo "No root access or sudo; skipping system package install."
  fi
}

cd "$ROOT"

install_apt_packages

"$PYTHON" -m venv "$VENV"
PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 "$VENV/bin/pip" install -r requirements-dev.txt

mkdir -p "${TIMELAPSE_DATA_DIR:-$ROOT/dev-data}"

if [ "${CODEX_RUN_TESTS_DURING_SETUP:-0}" = "1" ]; then
  "$VENV/bin/python" -m pytest
fi

echo "Codex environment ready."
echo "Validate with: $VENV/bin/python -m pytest"
