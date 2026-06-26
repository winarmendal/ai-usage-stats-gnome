#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_SCHEMA_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_SCHEMA_DIR}"' EXIT

python -m unittest discover -s "${ROOT_DIR}/tests"
"${ROOT_DIR}/helper/codex_stats_helper.py" --json | python -m json.tool >/dev/null
"${ROOT_DIR}/helper/codex_stats_helper.py" --provider claude --json | python -m json.tool >/dev/null

cp "${ROOT_DIR}/extension/schemas/org.gnome.shell.extensions.codex-stats.gschema.xml" "${TMP_SCHEMA_DIR}/"
glib-compile-schemas "${TMP_SCHEMA_DIR}"

"${ROOT_DIR}/scripts/package.sh" >/dev/null

echo "Smoke tests passed"

