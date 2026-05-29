#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/package"
STAGE_DIR="${BUILD_DIR}/codex-stats@winarmendal.github.io"
DIST_DIR="${ROOT_DIR}/dist"
SCHEMA="schemas/org.gnome.shell.extensions.codex-stats.gschema.xml"

rm -rf "${BUILD_DIR}"
mkdir -p "${STAGE_DIR}/helper" "${STAGE_DIR}/icons" "${STAGE_DIR}/schemas" "${DIST_DIR}"
rm -f "${DIST_DIR}"/*.shell-extension.zip

cp "${ROOT_DIR}/extension/metadata.json" "${STAGE_DIR}/"
cp "${ROOT_DIR}/extension/extension.js" "${STAGE_DIR}/"
cp "${ROOT_DIR}/extension/prefs.js" "${STAGE_DIR}/"
cp "${ROOT_DIR}/extension/stylesheet.css" "${STAGE_DIR}/"
cp "${ROOT_DIR}/extension/icons/"*.svg "${STAGE_DIR}/icons/"
cp "${ROOT_DIR}/extension/schemas/org.gnome.shell.extensions.codex-stats.gschema.xml" "${STAGE_DIR}/schemas/"
cp "${ROOT_DIR}/helper/codex_stats_helper.py" "${STAGE_DIR}/helper/"
chmod +x "${STAGE_DIR}/helper/codex_stats_helper.py"

glib-compile-schemas "${STAGE_DIR}/schemas"

(
  cd "${STAGE_DIR}"
  gnome-extensions pack . \
    --force \
    --out-dir="${DIST_DIR}" \
    --schema="${SCHEMA}" \
    --extra-source="icons/codex-stats-symbolic.svg" \
    --extra-source="icons/codex-stats-symbolic-light.svg" \
    --extra-source="helper/codex_stats_helper.py"
)

echo "Packaged extension in ${DIST_DIR}"
