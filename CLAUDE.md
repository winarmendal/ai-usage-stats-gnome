# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

AI Usage Stats is a local-first GNOME Shell extension that shows Codex and Claude Code token usage and rate-limit percentages in the top bar. The display name is "AI Usage Stats" (`metadata.json` `name`). The UUID (`codex-stats@winarmendal.github.io`) and GSettings schema id (`org.gnome.shell.extensions.codex-stats`) are unchanged from earlier releases. It is split into a GJS Shell extension (`extension/`) and a standalone Python aggregator (`helper/codex_stats_helper.py`).

## Commands

```bash
./scripts/smoke-test.sh          # full local check: helper unit tests + live helper JSON + schema compile + packaging
python -m unittest discover -s tests                         # helper unit tests only
python -m unittest tests.test_helper.HelperTests.test_daily_hourly_and_limits   # a single test
./helper/codex_stats_helper.py --json | python -m json.tool              # inspect real Codex aggregation output
./helper/codex_stats_helper.py --provider claude --json | python -m json.tool   # inspect real Claude aggregation output
./helper/codex_stats_helper.py --provider claude --claude-online --json | python -m json.tool   # opt-in: live limits + per-model Fable/Opus via Anthropic usage API
./scripts/install.sh             # install this checkout to ~/.local/share/gnome-shell/extensions (then re-login + `gnome-extensions enable`)
./scripts/package.sh             # build the release zip into dist/ (removes stale *.shell-extension.zip first)
./scripts/uninstall.sh [--purge-cache]
```

`smoke-test.sh` is the CI gate (`.github/workflows/ci.yml`) and what to run before every PR. There is no JS test suite or linter; `extension.js`/`prefs.js` are validated only by packaging + manual GNOME Shell testing.

## Architecture

**Two processes, one JSON contract.** The extension never parses logs itself. On each refresh `extension.js::_runHelper()` spawns the helper via `Gio.Subprocess` (async + `Gio.Cancellable`, so GNOME Shell never blocks) and parses a single JSON object from stdout. Heavy parsing belongs in the helper; keep the extension a thin async UI shell (see `CONTRIBUTING.md`).

**Multi-provider model.** One helper binary, one `--provider {codex|claude}` flag (default `codex`). The shared aggregation core (bucketing, rate-limit window selection, cache I/O) is provider-agnostic; each provider supplies a source adapter. Per-provider cache files: `cache-codex.json`, `cache-claude.json`.

**GSettings → helper args is a deliberate subset.** The extension passes `--json --provider <…> --log-root <…> --cache-file <…>` plus `--no-cache` (when `cache-enabled` is false), `--no-account-limits` (when `account-limits-enabled` is false), and for Claude `--limits-file <…>` plus `--claude-online` (when `claude-online-usage` is true). New GSettings keys: `active-provider`, `claude-enabled`, `claude-log-root` (default `~/.claude/projects`), `claude-limits-file` (default `~/.cache/codex-stats/claude-limits.json`), `claude-online-usage` (default false; opt-in online live limits + per-model Fable/Opus). The helper's other flags — `--live-log-db`, `--codex-bin`, `--no-live-limits`, `--claude-credentials-file`, `--claude-online-cache`, `--cowork-log-root`, `--now` — are standalone/test-only and not wired to settings (the Cowork root defaults inside the helper and is scanned automatically for the Claude provider). The GSettings schema (`extension/schemas/org.gnome.shell.extensions.codex-stats.gschema.xml`) is the source of truth for keys; `_runHelper`/`_onSettingsChanged` wiring is the source of truth for which keys actually affect behavior.

**Codex rate-limit data sources, in precedence order:**
1. **Realtime account snapshot** — asks the local Codex CLI (`codex app-server --listen stdio://`, `account/rateLimits/read`) — `collect_account_limit_snapshots`.
2. **Live local metadata** — `codex.rate_limits` events read from `~/.codex/logs_2.sqlite` — `collect_live_limit_snapshots`.
3. **JSONL fallback** — `token_count` events under `~/.codex/sessions/**/*.jsonl` — `collect_events` → `select_limit_snapshot`.

`merge_live_limit_snapshot` overlays the fresher sources onto the JSONL selection. Expired reset windows from JSONL are ignored unless live metadata supplies a fresher window (`roll_reset_forward`, `select_limit_snapshot`).

**Claude token source.** `assistant` events under `~/.claude/projects/**/*.jsonl`; only `message.usage` numeric fields are read. Deduplication is required: Claude writes one JSONL line per streaming iteration, so lines are grouped by `sessionId+requestId` (fallback: `message.id`) and only the last write per group is counted — without this, totals inflate roughly 3×.

**Claude Cowork source.** The Claude provider also folds in Claude Cowork (agent-mode) usage: the helper scans `~/.config/Claude/local-agent-mode-sessions/**/audit.jsonl` (`collect_claude_events` takes a `cowork_root`) and reads `assistant` `message.usage` from the canonical per-session ledgers only. The scan is **name-scoped to `audit.jsonl`** — the same tree nests full Claude Code transcripts (`local_*/.claude/projects/**/*.jsonl`) that would double-count (~2.4×) and expose prompt text if globbed. Field names are snake_case (`session_id`/`request_id`, `_audit_timestamp`); `extract_claude_row`/`claude_group_key` handle both the camelCase (Code) and snake_case (Cowork) variants via first-present lookups. Cowork folds into the same `claude` provider totals/`cache-claude.json`; automatic when the Claude provider is active (helper defaults the root; `--cowork-log-root` overrides). Chat usage is intentionally not tracked (not stored locally; no personal API — see `docs/PRIVACY.md`).

**Claude rate-limit source.** Vanilla Claude Code does not persist subscription rate limits to disk. The opt-in statusLine capture wrapper (`helper/claude_statusline_capture.py`), installed from Preferences → Claude → "Install", writes numeric rate-limit/cost/context-window fields plus a `captured_at` timestamp to `~/.cache/codex-stats/claude-limits.json`. Until installed, Claude's 5h/weekly gauges show `--`; token history works without it. The wrapper is fully reversible and edits `~/.claude/settings.json` atomically, chaining any pre-existing statusLine command unchanged.

**Claude online live limits (opt-in).** When `claude-online-usage` is on, the extension adds `--claude-online` and the helper calls Anthropic's usage endpoint (`GET https://api.anthropic.com/api/oauth/usage`, header `anthropic-beta: oauth-2025-04-20`, Bearer token read **read-only** from `~/.claude/.credentials.json` — never written or refreshed). This is the live source behind Claude Code's `/usage`; it supplies fresh 5h/Week plus per-model weekly buckets surfaced as `limits.fable_weekly` / `limits.opus_weekly` (window 10080 min). Precedence for Claude limits: online → statusline capture → HUD fallback → `--`. Throttled (60s TTL, 429 → 5m backoff) via `~/.cache/codex-stats/claude-online.json`; the response is whitelisted to numeric `utilization`/`resets_at` before caching. Core fn `collect_claude_online_snapshots` takes an injectable `opener` so tests never hit the network.

**Naming gotcha:** internally `primary` = the 5-hour window (300 min) and `secondary` = the weekly window (10080 min) — `DEFAULT_LIMIT_WINDOWS`. The UI/popover labels these "5h" and "Week".

**Cache.** Parsed token metadata is cached per-provider at `~/.cache/codex-stats/cache-<provider>.json`, keyed by file path + size + mtime; a changed JSONL file is re-parsed. The cache stores token/limit metadata only — never prompt, response, or file content. **The cache is also a ledger:** entries whose source file has disappeared are kept (flagged `missing`, counted in `status.files_retained`) and keep contributing to history, because Claude Code prunes transcripts after `cleanupPeriodDays` (30 by default) and history must never shrink (`retain_missing_cache_entries`). `--no-cache` and `uninstall.sh --purge-cache` are the only ways to drop retained history. Bump `SCHEMA_VERSION` in the helper if the cached shape changes — but note a bump wipes retained history, so prefer additive fields.

**Determinism for tests.** Aggregation/selection take an injected `now`; tests pin time via `--now <ISO-8601>` so rate-limit-window logic is reproducible.

## Constraints

- **Local-first, privacy-bound.** No network calls for usage data **by default**; the sole exception is the explicitly opt-in `claude-online-usage` mode (off by default), which GETs Anthropic's own usage endpoint with the user's local Claude token to read *their own* live limits (see `docs/PRIVACY.md`). The helper must never parse or emit prompts, assistant messages, file contents, or cookies, and must never store, log, or forward the OAuth token anywhere except as the `Authorization` header of that one opt-in request — only token counts and rate-limit metadata are emitted, for both providers. Claude JSONL transcripts contain prompt/response text; the parser reads only `message.usage` numerics and dedup identifiers. The statusLine wrapper whitelists only numeric fields. This is the project's core promise (`docs/PRIVACY.md`).
- **Helper is Python 3.10+ stdlib only** — no third-party dependencies; it runs standalone and inside the packaged extension.
- **Targets GNOME Shell 50 only** (`metadata.json` `shell-version: ["50"]`). Validate on the target shell version before broadening it.
- **Releases:** bump `version` in `extension/metadata.json`, then `./scripts/package.sh`. Both install and package require compiled schemas (`glib-compile-schemas`).
- **Don't commit** generated artifacts: `build/`, `dist/`, `*.shell-extension.zip`, `.omx/` (see `.gitignore`).
