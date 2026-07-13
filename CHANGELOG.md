# Changelog

## 0.4.0

- Added **Claude Cowork** (agent-mode) token usage to the Claude provider. Cowork token counts are now folded into the same Claude totals and history as Claude Code, so the Claude number reflects **Claude Code + Cowork combined** (Anthropic's Pro/Max limits are shared across both, so a Code-only figure under-reported real usage). Automatic whenever Claude tracking is enabled — no new setting.
- Disclosed new local read location: `~/.config/Claude/local-agent-mode-sessions/**/audit.jsonl`. Only files named `audit.jsonl` are read (the canonical per-session usage ledger), and only numeric `message.usage` counts + dedup identifiers are parsed. The nested Claude Code transcripts under that tree are **never opened**, so no prompt/response text is read — consistent with the existing privacy model (`docs/PRIVACY.md`).
- Chat usage (claude.ai / desktop chat) remains out of scope: it is not stored locally and no personal API exposes per-surface token counts; its consumption is already reflected in the account-wide 5-hour/weekly gauges when online mode is enabled.
- Renamed the online per-model weekly row from **Sonnet** to **Fable**: the popover now shows a **Fable** row (`fable_weekly`), sourced from the usage API's `seven_day_omelette` bucket (the API's current codename for the Fable model — there is no `seven_day_fable`). The Opus bucket is unchanged (still not shown separately — already counted in the All-models weekly gauge).
- Bumped extension metadata to version 9.

## 0.3.0

- Renamed the extension to **AI Usage Stats** (display name only). The UUID `codex-stats@winarmendal.github.io` and GSettings schema id `org.gnome.shell.extensions.codex-stats` are unchanged, so existing installs upgrade with no settings loss and no reinstall.
- Added **Claude Code** as a second provider alongside Codex, with a Codex/Claude selector in the popover; the active provider persists across sessions.
- Added Claude token history from `~/.claude/projects` JSONL `assistant` events, reading only `message.usage` numeric counts and deduplicating per request so totals are not inflated.
- Added an opt-in Claude statusLine capture wrapper (installed from Preferences → Claude → "Install") that supplies 5-hour and weekly rate-limit percentages, which Claude Code does not persist on its own. Fully reversible; chains any pre-existing statusLine command unchanged.
- Added an opt-in, off-by-default **online live limits** mode that reads live 5-hour/weekly and per-model **Sonnet** limits from your own Anthropic account usage endpoint, authenticated read-only with your local Claude login token. Only numeric usage fields are read; the token is never written, refreshed, logged, or forwarded anywhere else.
- Added a provider-aware panel icon: the Claude logomark shows when Claude is the active provider, theme-tinted for light and dark to match the Codex icon.
- Kept the Sonnet gauge stable under endpoint throttling: its row stays visible (showing `--` when momentarily unavailable) and the online throttle cache is kept independent of the token-history cache, so disabling the cache no longer blanks live limits on every rate-limited fetch.
- Bumped extension metadata to version 8.

## 0.2.2

- Used the local Codex CLI account rate-limit snapshot for fresher 5-hour and weekly percentages.
- Kept local JSONL `token_count` logs as the source for token burn and history.
- Added a Preferences toggle for realtime account limits.
- Preferred the newest valid rate-limit snapshot instead of the highest stale usage value.
- Documented the realtime/local-log privacy model and ignored compiled GSettings artifacts.

## 0.2.1

- Stabilized 5-hour and weekly rate-limit selection across concurrent Codex sessions.
- Ignored expired reset windows so stale pre-reset snapshots cannot override current usage.
- Added a follow-up manual refresh pass to catch delayed `token_count` writes after Codex UI updates.
- Bumped extension metadata to version 4.

## 0.2.0

- Refined the top-bar UX: icon-only by default, with an optional compact `5h x%  Week y%` usage display.
- Added a collapsed More Stats section for Day, Week, Month, and 3M history.
- Bundled Codex panel icons for dark and light GNOME themes.
- Updated popover styling to follow GNOME Shell theme colors and accent color.
- Packaged the icon assets in the GitHub Release zip.

## 0.1.0

- Initial public release.
- Added GNOME Shell 50 top-bar usage summary.
- Added local Python helper for Codex JSONL `token_count` aggregation.
- Added Day, Week, Month, and 3M history views.
- Added preferences, install/uninstall/package scripts, smoke tests, and public documentation.
- Changed public UUID to `codex-stats@winarmendal.github.io`.
