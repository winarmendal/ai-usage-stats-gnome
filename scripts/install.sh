#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="codex-stats@winarmendal.local"
OLD_UUID="codexbar@inled.es"
EXTENSIONS_DIR="${HOME}/.local/share/gnome-shell/extensions"
TARGET_DIR="${EXTENSIONS_DIR}/${UUID}"
OLD_DIR="${EXTENSIONS_DIR}/${OLD_UUID}"

if gnome-extensions info "${OLD_UUID}" >/dev/null 2>&1; then
  gnome-extensions disable "${OLD_UUID}" >/dev/null 2>&1 || true
  gnome-extensions uninstall "${OLD_UUID}" >/dev/null 2>&1 || true
fi
rm -rf "${OLD_DIR}"

rm -rf "${TARGET_DIR}"
mkdir -p "${TARGET_DIR}/helper" "${TARGET_DIR}/schemas"

cp "${ROOT_DIR}/extension/metadata.json" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/extension.js" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/prefs.js" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/stylesheet.css" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/schemas/org.gnome.shell.extensions.codex-stats.gschema.xml" "${TARGET_DIR}/schemas/"
cp "${ROOT_DIR}/helper/codex_stats_helper.py" "${TARGET_DIR}/helper/"
chmod +x "${TARGET_DIR}/helper/codex_stats_helper.py"

glib-compile-schemas "${TARGET_DIR}/schemas"

if gnome-extensions info "${UUID}" >/dev/null 2>&1; then
  gnome-extensions enable "${UUID}" >/dev/null 2>&1 || {
    echo "Installed ${UUID}. Enable it from GNOME Extensions or run: gnome-extensions enable ${UUID}" >&2
  }
else
  echo "Installed ${UUID}. GNOME Shell has not indexed it in this Wayland session yet; log out and back in, then enable it." >&2
fi

echo "Installed ${UUID} to ${TARGET_DIR}"
