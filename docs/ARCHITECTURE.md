# Architecture

Codex Stats is split into a lightweight GNOME Shell extension and a local Python helper.

## GNOME Extension

The extension lives in `extension/` and uses GNOME Shell ES modules:

- `PanelMenu.Button` renders the top-bar indicator.
- `PopupMenu` and `St` widgets render the popover.
- `Gio.Settings` stores refresh interval, log root, panel toggles, and cache usage.
- `GLib.Subprocess` runs the helper asynchronously so JSONL parsing does not block GNOME Shell.

The extension refreshes every 60 seconds by default.

Manual refresh runs once immediately and then polls a few more times over the next several seconds. Rate-limit percentages also use realtime account snapshots and live local metadata when available, because Codex can update its own usage display before the corresponding `token_count` event is appended to JSONL.

## Helper

The helper lives in `helper/codex_stats_helper.py`.

It scans `~/.codex/sessions/**/*.jsonl`, parses only `event_msg` events whose payload type is `token_count`, and emits one JSON object for the extension. For fresher 5-hour and weekly percentages, it can also read structured `codex.rate_limits` metadata events from `~/.codex/logs_2.sqlite` and ask the local Codex CLI for `account/rateLimits/read`.

The helper aggregates:

- today total tokens and hourly buckets
- 5-hour and weekly rate-limit metadata, selected independently from current reset windows and overlaid with realtime account/local `codex.rate_limits` events when available
- last 7 days
- current month by day
- last 3 months by month

## Cache

The helper stores parsed metadata in `~/.cache/codex-stats/cache.json`.

Cache keys include file path, size, and mtime. If a JSONL file changes, that file is parsed again. The cache stores token metadata only, not prompt or response text.

Rate-limit selection first prefers the realtime account snapshot from the local Codex CLI, then fresher live local `codex.rate_limits` events. If a live event does not include reset metadata, the helper keeps the reset/window from JSONL. Without live metadata, it ignores expired reset windows and uses the newest valid JSONL snapshot.

## Packaging

`scripts/package.sh` stages the extension, compiles schemas, and runs `gnome-extensions pack`. Generated bundles go to `dist/` and are not committed.
