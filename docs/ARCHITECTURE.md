# Architecture

AI Usage Stats is split into a lightweight GNOME Shell extension and a local Python helper that supports multiple providers.

## GNOME Extension

The extension lives in `extension/` and uses GNOME Shell ES modules:

- `extension/providers.js` is a shared registry module (imports only `gi://GLib`) used by both `extension.js` and `prefs.js`, so the two processes never disagree on provider ids, labels, GSettings keys, or icon names. It exports `expandHome()` and the `PROVIDERS` array: one entry per provider with `{id, label, enabledKey, rootKey, defaultRoot, gauges, extraGauges, icon}`. `gauges` is either `'codex-freshest'` (pick whichever of Codex's 5h/Week windows is freshest) or a list of `limits.<key>` names to render (an empty list means the provider has no rate-limit data and only a token total is shown, as with OpenCode). It also exports `PROVIDER_KEYS`, the flat list of every `enabledKey`/`rootKey` — a change to any of these re-resolves which providers are visible.
- `PanelMenu.Button` renders the top-bar indicator, labelled with whichever provider is picked as the top-bar provider (empty until chosen).
- `PopupMenu` and `St` widgets render the popover: one block per currently visible provider (icon, name, today's tokens, its gauges), stacked with dividers — no tabs.
- `Gio.Settings` stores refresh interval, per-provider log/data roots, panel toggles, cache usage, and the top-bar provider choice. Keys: `panel-provider` (replaces the old `active-provider`), `claude-enabled` (default true), `claude-log-root` (default `~/.claude/projects`), `claude-limits-file` (default `~/.cache/codex-stats/claude-limits.json`), `claude-online-usage` (opt-in online live limits, default off), `grok-enabled` (default true), `grok-log-root` (default `~/.grok`), `opencode-enabled` (default true), `opencode-log-root` (default `~/.local/share/opencode`).
- `GLib.Subprocess` runs the helper asynchronously so JSONL/SQLite parsing does not block GNOME Shell.

The extension refreshes every 60 seconds by default.

Manual refresh runs once immediately and then polls a few more times over the next several seconds. Each refresh resolves the visible provider set (a provider other than Codex is visible only when its `enabledKey` switch is on and its `rootKey` directory exists), then runs the helper for every visible provider concurrently (`Promise.allSettled`), tagged with a serial so a stale, still-in-flight refresh from before a settings change is dropped instead of overwriting fresher data.

## Provider Model

One helper, one `--provider` flag. The shared aggregation core (bucketing, rate-limit window selection, cache I/O) is provider-agnostic. Each provider supplies a source adapter that yields normalised token events and rate-limit snapshots. The per-provider cache file is `cache-<provider>.json` (`cache-codex.json`, `cache-claude.json`, `cache-grok.json`, `cache-opencode.json`).

**GSettings → helper args.** For every provider the extension passes `--json --provider <id> --cache-file <cache-<id>.json> --log-root <get_string(rootKey) or defaultRoot>`, plus `--no-cache` when the cache switch is off. Codex additionally gets `--no-account-limits` when realtime account limits are off; Claude additionally gets `--limits-file <claude-limits-file>` and, when online usage is on, `--claude-online`. Grok and OpenCode take no extra flags — they have no per-provider settings beyond their root and enabled switch.

## Helper — Codex Provider

The helper scans `~/.codex/sessions/**/*.jsonl`, parses only `event_msg` events whose payload type is `token_count`, and emits one JSON object for the extension. For fresher 5-hour and weekly percentages, it can also read structured `codex.rate_limits` metadata events from `~/.codex/logs_2.sqlite` and ask the local Codex CLI for `account/rateLimits/read`.

Three rate-limit data sources, in precedence order:

1. **Realtime account snapshot** — `codex app-server --listen stdio://`, `account/rateLimits/read` — `collect_account_limit_snapshots`.
2. **Live local metadata** — `codex.rate_limits` events from `~/.codex/logs_2.sqlite` — `collect_live_limit_snapshots`.
3. **JSONL fallback** — `token_count` events under `~/.codex/sessions/**/*.jsonl` — `collect_events` → `select_limit_snapshot`.

## Helper — Claude Provider

The helper scans `~/.claude/projects/**/*.jsonl` and parses `assistant` events, reading only `message.usage` numerics (input, output, cache_creation, cache_read tokens).

**Deduplication.** Claude appends one JSONL line per streaming iteration for the same API response. Without deduplication, token totals would inflate roughly 3×. The helper groups lines by `sessionId + requestId` (falling back to `message.id`) and keeps only the last write per group before aggregating.

**Cowork (agent mode).** The helper also scans `~/.config/Claude/local-agent-mode-sessions/**/audit.jsonl` (Claude Cowork's per-session audit ledgers) and folds their `assistant` `message.usage` token counts into the same Claude aggregation, so the Claude provider reflects Claude Code + Cowork combined. The scan is **name-scoped to `audit.jsonl`**: that tree also nests full Claude Code transcripts (`local_*/.claude/projects/**/*.jsonl`) which would double-count (~2.4×) and expose prompt text if globbed with `*.jsonl` — they are never opened. The shared dedup and extractor accept both field-name variants (snake_case `session_id`/`request_id` + `_audit_timestamp` for Cowork, camelCase `sessionId`/`requestId` + `timestamp` for Claude Code). The Cowork root defaults inside the helper and is automatic for the Claude provider; `--cowork-log-root` overrides it for standalone/test use.

**Rate-limit data source.** Vanilla Claude Code does not persist subscription rate limits to disk. Rate-limit percentages come from a separate capture file (`~/.cache/codex-stats/claude-limits.json`) written by the opt-in statusLine capture wrapper. If the file is absent, the 5-hour and weekly gauges show `--`. Token history works without the capture file.

As a fallback, if an OMC HUD cache file is present and carries a recent timestamp, the helper may use it as an alternative source for rate-limit metadata.

**Online live limits (opt-in).** When `claude-online-usage` is enabled, the helper additionally calls Anthropic's subscription usage endpoint (`GET https://api.anthropic.com/api/oauth/usage` — the same source as Claude Code's `/usage`), authenticated with the local OAuth token read **read-only** from `~/.claude/.credentials.json`. This yields live 5-hour/weekly percentages and, uniquely, per-model weekly buckets (`seven_day_omelette` — the API's current codename for the Fable model — and `seven_day_opus`) surfaced as the `fable_weekly` / `opus_weekly` limit keys. The online source takes precedence over the capture file for the 5-hour/weekly windows; it is throttled with a short TTL plus HTTP 429 backoff (cache `~/.cache/codex-stats/claude-online.json`), never writes the credentials file, and never refreshes the token. Disabled by default to preserve local-first; see `docs/PRIVACY.md`.

## Helper — Grok Provider

The helper scans `~/.grok/sessions/**/usage.json` and reads only each turn's `endedAt` and `totalTokens`; the file's `session` totals are ignored because resuming/forking a session copies the parent's history into them, which would double count. A turn's `endedAt + totalTokens` pair is treated as its stable identity, so a forked session that replays its parent's turns verbatim counts each turn once. Sibling files in the same session directory (`chat_history.jsonl`, `updates.jsonl`, `system_prompt.txt`, `terminal/`) hold prompt/tool-output text and are never opened — the scan is name-scoped to `usage.json`.

**Weekly rate limit.** Grok Build has no 5-hour window, only a weekly credit percentage. The helper tails (last 1 MiB of) `~/.grok/logs/unified.jsonl`, decodes only lines containing the exact marker `billing: fetched credits config`, and from a matching line reads only `msg`, `ts`, `ctx.config.creditUsagePercent`, and `ctx.config.currentPeriod.end`. The newest snapshot within the last 24 hours wins; an expired period is treated as 0% used, mirroring the Codex expired-window rule. Nothing from this log is cached. The panel/popover render this as a `Week` gauge only; `5h` always shows `--` for Grok.

## Helper — OpenCode Provider

The helper opens the local OpenCode SQLite database (`opencode.db` or `opencode-prod.db`, whichever has the newer mtime) **read-only**, via `file:<path>?mode=ro`, falling back to `?immutable=1` if the directory is not writable, and treating a locked/busy database as a soft failure that serves cached rows instead of blocking the panel. It reads assistant-role rows from whichever of the `message` (OpenCode 1.x) and `session_message` (2.x) tables exist, merging both when an upgraded install has both. From each row's JSON blob it reads only `role`, `tokens.input`, `tokens.output`, `tokens.reasoning`, `tokens.cache.read`, `tokens.cache.write`, and `time.created` (stable across the row being rewritten mid-stream, unlike `time.completed`). The `part` table, which holds message text, is never queried.

**Incremental ledger cache.** `cache-opencode.json` stores `{schema_version, db, hwm, rows}`: `hwm` is the highest `time_updated` value scanned so far, and each refresh queries only rows at or above it. Rows are keyed by message id and never removed, so history survives OpenCode pruning old sessions or the database path changing (a different `db` value resets `hwm` to rescan, but keeps already-cached rows). OpenCode publishes no rate-limit data, so this provider shows a token total only, no gauges.

## Claude StatusLine Capture Wrapper

`helper/claude_statusline_capture.py` is an opt-in wrapper installed from Preferences → Claude → "Install". It writes only numeric `rate_limits`, `cost`, and `context_window` fields plus a `captured_at` timestamp to the capture file — no prompts, no responses, no tokens-in-flight content. Any pre-existing `statusLine` command in `~/.claude/settings.json` is chained byte-for-byte so existing tooling is unaffected. The install and uninstall operations edit `~/.claude/settings.json` atomically and are fully reversible.

## Aggregation (every provider)

The helper aggregates for any provider:

- today total tokens and hourly buckets
- rate-limit metadata where the provider has any (5-hour and weekly for Codex/Claude, weekly-only for Grok, none for OpenCode), selected from current reset windows and overlaid with fresher sources when available
- last 7 days
- current month by day
- last 3 months by month

The popover's More Stats section renders one 7-day history list per visible provider in a single scroll area (no per-window tabs).

## Cache

The helper stores parsed metadata in `~/.cache/codex-stats/cache-<provider>.json`. Codex and Claude cache keys include file path, size, and mtime; a changed JSONL file is re-parsed. OpenCode's cache is instead an incremental ledger keyed by message id with a `hwm` watermark (see above). In every case the cache stores numeric token/limit metadata only, never prompt or response text.

## Packaging

`scripts/package.sh` and `scripts/install.sh` stage the extension (including `providers.js` and all eight per-theme provider icons), compile schemas, and (package.sh only) run `gnome-extensions pack` with `--extra-source` for `providers.js` and each icon — `gnome-extensions pack` flattens extra sources to the extension root, which is why the extension resolves an icon at `icons/<stem>.svg` first and falls back to the flattened `<stem>.svg`. Generated bundles go to `dist/` and are not committed.
