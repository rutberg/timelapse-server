#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/build-release.sh [--out DIR]

Reads agent/VERSION, packages agent/timelapse_agent.py and VERSION into
DIR/timelapse-agent-<version>.tar.gz with a sidecar .sha256 file.

Defaults:
  DIR = dist
USAGE
}

OUT_DIR="dist"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --out) OUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/agent/VERSION"

if [ ! -f "$VERSION_FILE" ]; then
  echo "Missing $VERSION_FILE" >&2
  exit 1
fi

VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [ -z "$VERSION" ]; then
  echo "VERSION file is empty" >&2
  exit 1
fi

if ! [[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Invalid VERSION: $VERSION" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

PKG_DIR="$STAGE_DIR/timelapse-agent-$VERSION"
mkdir -p "$PKG_DIR"
cp "$REPO_ROOT/agent/timelapse_agent.py" "$PKG_DIR/"
cp "$VERSION_FILE" "$PKG_DIR/VERSION"

TARBALL="$OUT_DIR/timelapse-agent-$VERSION.tar.gz"
tar -C "$STAGE_DIR" -czf "$TARBALL" "timelapse-agent-$VERSION"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$TARBALL" | awk '{print $1}' > "$TARBALL.sha256"
else
  shasum -a 256 "$TARBALL" | awk '{print $1}' > "$TARBALL.sha256"
fi

echo "Built $TARBALL"
echo "SHA256: $(cat "$TARBALL.sha256")"
