# Codex Stats V1 PRD

## Summary

Codex Stats is a local-first GNOME Shell extension that shows Codex token usage in the top bar. It reads local Codex JSONL session logs, aggregates token-count metadata, and presents daily, 5-hour, weekly, and historical usage without calling OpenAI, ChatGPT, or any network API.

## Product Behavior

- Top bar shows a compact summary such as `Codex  Day 1.2M  5h 99%  Week 60%`.
- Click popover shows today's token burn, 5-hour remaining usage, weekly remaining usage, reset times, refresh and preferences buttons, and Day, Week, Month, and 3M history tabs.
- Historical stats show hourly burn for today, daily burn for the last 7 local days, daily burn for the current local month, and monthly totals for the last 3 calendar months.
- Missing logs or rate-limit data should show `--` and a concise status message instead of crashing.

## Identity And Installation

- GNOME Shell UUID: `codex-stats@winarmendal.github.io`.
- Install path: `~/.local/share/gnome-shell/extensions/codex-stats@winarmendal.github.io`.
- Codex Stats is a separate GNOME Shell extension identity. It does not replace, inspect, disable, uninstall, remove, or warn about other Codex-related extensions.
- The installer may clean up the pre-release Codex Stats UUID `codex-stats@winarmendal.local` because that was this project's previous local development identity.
- Public distribution uses GitHub Releases first. Users install the packaged `.shell-extension.zip` with `gnome-extensions install --force`.
- Extension Manager visibility requires `extensions.gnome.org` review and is deferred to a later release.

## Privacy And Data Source

- Parse only `event_msg` / `token_count` metadata events from `~/.codex/sessions/**/*.jsonl`.
- Sum `payload.info.last_token_usage.total_tokens`.
- Use only local logs as the source of truth.
- Never display or parse prompt text, assistant text, file contents, cookies, API tokens, or browser data.
- Do not call OpenAI, ChatGPT, or other network APIs in v1.

## Implementation Requirements

- Use GNOME Shell ES modules, `PanelMenu.Button`, `PopupMenu`, `St` widgets, and `Gio.Settings`.
- Run the Python stdlib helper asynchronously from the extension with `GLib.Subprocess`.
- Default refresh interval is 60 seconds.
- Helper cache path is `~/.cache/codex-stats/cache.json`.
- Cache parsed file mtime, size, and bucket totals so normal refreshes only parse changed or new JSONL files.
- Tolerate malformed or truncated JSONL lines and continue scanning.

## Test Plan

- Unit-test helper aggregation for daily, hourly, weekly, monthly, 3M, rate-limit, malformed JSON, empty log root, and cache invalidation behavior.
- Run `python -m unittest discover -s tests`.
- Verify helper `--json` prints valid JSON.
- Verify `glib-compile-schemas` succeeds.
- Verify `gnome-extensions pack` succeeds.
- Run `./scripts/smoke-test.sh` before release.
