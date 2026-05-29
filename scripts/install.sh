#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="codex-stats@winarmendal.github.io"
LEGACY_UUID="codex-stats@winarmendal.local"
EXTENSIONS_DIR="${HOME}/.local/share/gnome-shell/extensions"
TARGET_DIR="${EXTENSIONS_DIR}/${UUID}"
LEGACY_DIR="${EXTENSIONS_DIR}/${LEGACY_UUID}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--help]

Installs Codex Stats from this source checkout as ${UUID}.

Codex Stats uses a GNOME Shell extension UUID in the form
extension-id@namespace. The namespace is tied to the public project owner
rather than a local machine name.

For public installs, prefer the packaged GitHub Release zip:
  gnome-extensions install --force codex-stats@winarmendal.github.io.shell-extension.zip
  gnome-extensions enable codex-stats@winarmendal.github.io

Options:
  -h, --help  Show this help text.
EOF
}

while (($#)); do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if gnome-extensions info "${LEGACY_UUID}" >/dev/null 2>&1 || [[ -d "${LEGACY_DIR}" ]]; then
  gnome-extensions disable "${LEGACY_UUID}" >/dev/null 2>&1 || true
  gnome-extensions uninstall "${LEGACY_UUID}" >/dev/null 2>&1 || true
  rm -rf "${LEGACY_DIR}"
fi

rm -rf "${TARGET_DIR}"
mkdir -p "${TARGET_DIR}/helper" "${TARGET_DIR}/icons" "${TARGET_DIR}/schemas"

cp "${ROOT_DIR}/extension/metadata.json" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/extension.js" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/prefs.js" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/stylesheet.css" "${TARGET_DIR}/"
cp "${ROOT_DIR}/extension/icons/"*.svg "${TARGET_DIR}/icons/"
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
