#!/usr/bin/env bash
# Builds static/css/app.css from styles/input.css using the
# standalone Tailwind CLI — no Node/npm anywhere in this project. Downloads
# the CLI binary for whatever platform it's running on (once; cached in
# bin/) the first time it's run. Same script runs locally and in the
# Docker build, so dev and prod always use the same Tailwind version.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="v4.3.3"
BIN_PATH="bin/tailwindcss"

if [ ! -x "$BIN_PATH" ]; then
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os-$arch" in
    Linux-x86_64)  asset="tailwindcss-linux-x64" ;;
    Linux-aarch64) asset="tailwindcss-linux-arm64" ;;
    Darwin-arm64)  asset="tailwindcss-macos-arm64" ;;
    Darwin-x86_64) asset="tailwindcss-macos-x64" ;;
    *)
      echo "Unsupported platform for the Tailwind standalone CLI: $os-$arch" >&2
      echo "Download one manually from https://github.com/tailwindlabs/tailwindcss/releases/tag/$VERSION and save it as $BIN_PATH" >&2
      exit 1
      ;;
  esac

  echo "Downloading Tailwind CLI ($asset $VERSION)..."
  curl -sLo "$BIN_PATH" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/$asset"
  chmod +x "$BIN_PATH"
fi

"$BIN_PATH" -i styles/input.css -o static/css/app.css --minify
echo "Built static/css/app.css"
