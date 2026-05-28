#!/usr/bin/env bash
set -euo pipefail

UUID="codex-stats@winarmendal.github.io"
TARGET_DIR="${HOME}/.local/share/gnome-shell/extensions/${UUID}"

gnome-extensions disable "${UUID}" >/dev/null 2>&1 || true
gnome-extensions uninstall "${UUID}" >/dev/null 2>&1 || true
rm -rf "${TARGET_DIR}"

if [[ "${1:-}" == "--purge-cache" ]]; then
  rm -rf "${HOME}/.cache/codex-stats"
fi

echo "Uninstalled ${UUID}"
