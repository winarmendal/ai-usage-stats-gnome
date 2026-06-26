# Architecture

AI Usage Stats is split into a lightweight GNOME Shell extension and a local Python helper that supports multiple providers.

## GNOME Extension

The extension lives in `extension/` and uses GNOME Shell ES modules:

- `PanelMenu.Button` renders the top-bar indicator, labelled with the active provider.
- `PopupMenu` and `St` widgets render the popover, including a provider selector (Codex / Claude) at the top when Claude tracking is enabled.
- `Gio.Settings` stores refresh interval, log roots, panel toggles, cache usage, and the active provider. New keys: `active-provider`, `claude-enabled`, `claude-log-root` (default `~/.claude/projects`), `claude-limits-file` (default `~/.cache/codex-stats/claude-limits.json`), and `claude-online-usage` (opt-in online live limits, default off).
- `GLib.Subprocess` runs the helper asynchronously so JSONL parsing does not block GNOME Shell.

The extension refreshes every 60 seconds by default.

Manual refresh runs once immediately and then polls a few more times over the next several seconds. On each refresh the helper is invoked with `--provider <active-provider>`, so switching providers updates all displayed stats on the next cycle.

## Provider Model

One helper, one `--provider` flag. The shared aggregation core (bucketing, rate-limit window selection, cache I/O) is provider-agnostic. Each provider supplies a source adapter that yields normalised token events and rate-limit snapshots. The per-provider cache file is `cache-<provider>.json` (e.g. `cache-codex.json`, `cache-claude.json`).

## Helper — Codex Provider

The helper scans `~/.codex/sessions/**/*.jsonl`, parses only `event_msg` events whose payload type is `token_count`, and emits one JSON object for the extension. For fresher 5-hour and weekly percentages, it can also read structured `codex.rate_limits` metadata events from `~/.codex/logs_2.sqlite` and ask the local Codex CLI for `account/rateLimits/read`.

Three rate-limit data sources, in precedence order:

1. **Realtime account snapshot** — `codex app-server --listen stdio://`, `account/rateLimits/read` — `collect_account_limit_snapshots`.
2. **Live local metadata** — `codex.rate_limits` events from `~/.codex/logs_2.sqlite` — `collect_live_limit_snapshots`.
3. **JSONL fallback** — `token_count` events under `~/.codex/sessions/**/*.jsonl` — `collect_events` → `select_limit_snapshot`.

## Helper — Claude Provider

The helper scans `~/.claude/projects/**/*.jsonl` and parses `assistant` events, reading only `message.usage` numerics (input, output, cache_creation, cache_read tokens).

**Deduplication.** Claude appends one JSONL line per streaming iteration for the same API response. Without deduplication, token totals would inflate roughly 3×. The helper groups lines by `sessionId + requestId` (falling back to `message.id`) and keeps only the last write per group before aggregating.

**Rate-limit data source.** Vanilla Claude Code does not persist subscription rate limits to disk. Rate-limit percentages come from a separate capture file (`~/.cache/codex-stats/claude-limits.json`) written by the opt-in statusLine capture wrapper. If the file is absent, the 5-hour and weekly gauges show `--`. Token history works without the capture file.

As a fallback, if an OMC HUD cache file is present and carries a recent timestamp, the helper may use it as an alternative source for rate-limit metadata.

**Online live limits (opt-in).** When `claude-online-usage` is enabled, the helper additionally calls Anthropic's subscription usage endpoint (`GET https://api.anthropic.com/api/oauth/usage` — the same source as Claude Code's `/usage`), authenticated with the local OAuth token read **read-only** from `~/.claude/.credentials.json`. This yields live 5-hour/weekly percentages and, uniquely, per-model weekly buckets (`seven_day_sonnet` / `seven_day_opus`) surfaced as the `sonnet_weekly` / `opus_weekly` limit keys. The online source takes precedence over the capture file for the 5-hour/weekly windows; it is throttled with a short TTL plus HTTP 429 backoff (cache `~/.cache/codex-stats/claude-online.json`), never writes the credentials file, and never refreshes the token. Disabled by default to preserve local-first; see `docs/PRIVACY.md`.

## Claude StatusLine Capture Wrapper

`helper/claude_statusline_capture.py` is an opt-in wrapper installed from Preferences → Claude → "Install". It writes only numeric `rate_limits`, `cost`, and `context_window` fields plus a `captured_at` timestamp to the capture file — no prompts, no responses, no tokens-in-flight content. Any pre-existing `statusLine` command in `~/.claude/settings.json` is chained byte-for-byte so existing tooling is unaffected. The install and uninstall operations edit `~/.claude/settings.json` atomically and are fully reversible.

## Aggregation (both providers)

The helper aggregates for any provider:

- today total tokens and hourly buckets
- 5-hour and weekly rate-limit metadata, selected from current reset windows and overlaid with fresher sources when available
- last 7 days
- current month by day
- last 3 months by month

## Cache

The helper stores parsed metadata in `~/.cache/codex-stats/cache-<provider>.json`.

Cache keys include file path, size, and mtime. If a JSONL file changes, that file is parsed again. The cache stores token metadata only, not prompt or response text.

## Packaging

`scripts/package.sh` stages the extension, compiles schemas, and runs `gnome-extensions pack`. Generated bundles go to `dist/` and are not committed.
