# Architecture

Codex Stats is split into a lightweight GNOME Shell extension and a local Python helper.

## GNOME Extension

The extension lives in `extension/` and uses GNOME Shell ES modules:

- `PanelMenu.Button` renders the top-bar indicator.
- `PopupMenu` and `St` widgets render the popover.
- `Gio.Settings` stores refresh interval, log root, panel toggles, and cache usage.
- `GLib.Subprocess` runs the helper asynchronously so JSONL parsing does not block GNOME Shell.

The extension refreshes every 60 seconds by default.

## Helper

The helper lives in `helper/codex_stats_helper.py`.

It scans `~/.codex/sessions/**/*.jsonl`, parses only `event_msg` events whose payload type is `token_count`, and emits one JSON object for the extension.

The helper aggregates:

- today total tokens and hourly buckets
- latest 5-hour and weekly rate-limit metadata
- last 7 days
- current month by day
- last 3 months by month

## Cache

The helper stores parsed metadata in `~/.cache/codex-stats/cache.json`.

Cache keys include file path, size, and mtime. If a JSONL file changes, that file is parsed again. The cache stores token metadata only, not prompt or response text.

## Packaging

`scripts/package.sh` stages the extension, compiles schemas, and runs `gnome-extensions pack`. Generated bundles go to `dist/` and are not committed.

