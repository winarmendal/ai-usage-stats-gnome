# Codex Stats V1 PRD

## Summary

Codex Stats is a local-first GNOME Shell extension that shows Codex token usage in the top bar. It reads local Codex JSONL session logs, aggregates token-count metadata, optionally overlays live local rate-limit metadata, and can ask the local Codex CLI for realtime account rate-limit percentages.

## Product Behavior

- Top bar shows the Codex Stats icon by default, with an option to show compact remaining usage such as `5h 99%  Week 60%`.
- The panel icon is bundled with Codex Stats and must not depend on the Codex desktop app being installed.
- Panel and popover styling should follow the active GNOME Shell theme and remain legible in light and dark mode.
- Click popover shows today's token burn, 5-hour remaining usage, weekly remaining usage, reset times, refresh and preferences buttons, and a collapsed More Stats section.
- Weekly reset text includes the weekday/date plus time; short-window reset text includes the date when it is not today.
- Expanding More Stats reveals Day, Week, Month, and 3M history tabs.
- Historical stats show hourly burn for today, daily burn for the last 7 local days, daily burn for the current local month, and monthly totals for the last 3 calendar months.
- Missing logs or rate-limit data should show `--` and a concise status message instead of crashing.
- Rate-limit display should prefer the realtime Codex account snapshot when available, then fresh local `codex.rate_limits` metadata, then the newest valid JSONL rate-limit snapshot. Expired reset windows must not override current windows.

## Identity And Installation

- GNOME Shell UUID: `codex-stats@winarmendal.github.io`.
- Install path: `~/.local/share/gnome-shell/extensions/codex-stats@winarmendal.github.io`.
- Codex Stats is a separate GNOME Shell extension identity. It does not replace, inspect, disable, uninstall, remove, or warn about other Codex-related extensions.
- The installer may clean up the pre-release Codex Stats UUID `codex-stats@winarmendal.local` because that was this project's previous local development identity.
- Public distribution uses GitHub Releases first. Users install the packaged `.shell-extension.zip` with `gnome-extensions install --force`.
- Extension Manager visibility requires `extensions.gnome.org` review and is deferred to a later release.

## Privacy And Data Source

- Parse only `event_msg` / `token_count` metadata events from `~/.codex/sessions/**/*.jsonl` for token burn and historical charts.
- For more responsive 5-hour and weekly percentages, read only structured local `codex.rate_limits` events from `~/.codex/logs_2.sqlite` when present.
- When realtime account limits are enabled, call the local `codex app-server --listen stdio://` process and request only `account/rateLimits/read`.
- Sum `payload.info.last_token_usage.total_tokens`.
- Select 5-hour and weekly rate-limit status independently. Expired `resets_at` snapshots must not override a current window; if no current snapshot exists, roll the expired window forward and show it as freshly reset.
- Use local logs as the token burn and history source of truth. Realtime account limits are only for current 5-hour and weekly percentage display.
- Never display prompt text, assistant text, file contents, cookies, API tokens, or browser data. Live rate-limit parsing must accept only structured `codex.rate_limits` metadata and ignore free-form text.
- Codex Stats does not call OpenAI, ChatGPT, or other network APIs directly. Realtime account limit mode delegates to the local Codex CLI and can be disabled in preferences.

## Implementation Requirements

- Use GNOME Shell ES modules, `PanelMenu.Button`, `PopupMenu`, `St` widgets, and `Gio.Settings`.
- Run the Python stdlib helper asynchronously from the extension with `GLib.Subprocess`.
- Default refresh interval is 60 seconds.
- Realtime account limit mode defaults to enabled, with local log fallback if the Codex CLI is unavailable or times out.
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
