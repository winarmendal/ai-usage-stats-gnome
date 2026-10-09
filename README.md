# AI Usage Stats

![CI](https://github.com/winarmendal/ai-usage-stats-gnome/actions/workflows/ci.yml/badge.svg)

AI Usage Stats is a local-first GNOME Shell extension that shows Codex, Claude (Claude Code + Claude Cowork), Grok Build, and OpenCode token usage and rate-limit percentages in the top bar.

For Codex it reads `token_count` metadata events from local JSONL session logs under `~/.codex/sessions`, uses local `codex.rate_limits` metadata from `~/.codex/logs_2.sqlite`, and can ask the local Codex CLI for the current account rate-limit snapshot for fresher 5-hour and weekly percentages. For Claude it reads `assistant` events from local JSONL transcripts under `~/.claude/projects`, folds in **Claude Cowork** (agent-mode) usage from `~/.config/Claude/local-agent-mode-sessions/**/audit.jsonl` so the Claude figure reflects Claude Code + Cowork combined, and uses an opt-in statusLine capture wrapper for 5-hour and weekly rate-limit percentages. An optional, off-by-default online mode can additionally read live 5-hour/weekly and per-model (Fable/Opus) limits directly from your own Anthropic account. For Grok Build it reads per-turn token counts from `~/.grok/sessions/**/usage.json` and the weekly credit percentage from the tail of `~/.grok/logs/unified.jsonl` (billing lines only; no 5-hour window exists). For OpenCode it reads assistant message token counts from the local `~/.local/share/opencode` SQLite database (numeric `tokens.*` fields only; no rate-limit data exists on disk). No provider's prompts, assistant messages, file contents, browser data, or cookies are parsed or displayed; the opt-in Claude online mode reads your local Claude login token read-only, solely to authenticate that one request (see `docs/PRIVACY.md`).

## Features

- Top-bar icon and label for one explicitly chosen provider (Preferences → Providers → "Top bar provider"); until a provider is picked, the panel shows a placeholder
- Popover lists every enabled, present provider at once — no tabs. Each provider gets its own block with today's tokens and its rate-limit gauges (Codex: freshest 5h/Week; Claude: 5h/Week plus an optional Fable row; Grok: Week only; OpenCode: tokens only, no gauges)
- Providers show up automatically once their data folder exists (Grok: `~/.grok`, OpenCode: `~/.local/share/opencode`); a "Track <Provider>" switch in Preferences can still hide one
- Claude usage includes **Claude Cowork** (agent mode): Cowork token counts fold into the Claude totals automatically (only `audit.jsonl` ledgers are read; nested transcripts are never opened)
- Bundled panel icons for all four providers, so CLI-only users do not need any provider's desktop app icon installed
- Theme-aware GNOME Shell styling for light and dark mode
- Collapsed More Stats section with one 7-day token history list per visible provider, in a single scroll area
- Preferences for refresh interval, per-provider log/data roots, realtime account limits, compact panel usage, cache usage, and per-provider tracking toggles
- Opt-in Claude statusLine capture wrapper (installed from Preferences → Claude → Install) that supplies 5-hour and weekly rate-limit data; Claude's gauges show `--` until it is installed
- Optional online live limits (Preferences → Claude, off by default): fetches live 5-hour/weekly and per-model Fable/Opus limits from your Anthropic account, fresh even with no Claude session open; see `docs/PRIVACY.md`
- Python stdlib helper with cache-aware JSONL/SQLite parsing and realtime rate-limit metadata
- Privacy-focused model that displays only token and rate-limit metadata, for every provider

## Requirements

- GNOME Shell 50 or 51
- GJS 1.88+ (GNOME 50) or 1.90 (GNOME 51)
- Python 3.10+
- `glib-compile-schemas`
- `gnome-extensions`

This project is currently built and tested for GNOME Shell 50 and 51. Wider shell-version support should be validated before changing `metadata.json`.

## Install From GitHub Release

Download `codex-stats@winarmendal.github.io.shell-extension.zip` from the latest GitHub Release, then install it:

```bash
gnome-extensions install --force codex-stats@winarmendal.github.io.shell-extension.zip
gnome-extensions enable codex-stats@winarmendal.github.io
```

On GNOME Wayland, you may need to log out and back in if GNOME Shell has not indexed the newly installed extension UUID yet. After re-login, run the enable command again.

The extension UUID and GSettings schema id (`org.gnome.shell.extensions.codex-stats`) are unchanged from earlier releases — existing installs upgrade non-breaking with no settings loss and no reinstall required.

AI Usage Stats is not listed on Extension Manager yet. Extension Manager visibility requires publishing through `extensions.gnome.org`, which is planned for a later release.

## Install From Source

Source installs are intended for development and local testing:

```bash
git clone https://github.com/winarmendal/ai-usage-stats-gnome.git
cd ai-usage-stats-gnome
./scripts/install.sh
```

After re-login, enable the source install with:

```bash
gnome-extensions enable codex-stats@winarmendal.github.io
```

The extension installs as `codex-stats@winarmendal.github.io`. It does not inspect, disable, uninstall, or remove other Codex- or Claude-related extensions.

## Package

```bash
./scripts/package.sh
```

The extension bundle is written to `dist/`. Existing `*.shell-extension.zip` files in `dist/` are removed first so stale UUID bundles do not get attached to releases by mistake.

## Development

Run the complete local smoke suite:

```bash
./scripts/smoke-test.sh
```

Run just helper tests:

```bash
python -m unittest discover -s tests
```

Check current local Codex stats:

```bash
./helper/codex_stats_helper.py --json | python -m json.tool
```

Check current local Claude stats:

```bash
./helper/codex_stats_helper.py --provider claude --json | python -m json.tool
```

Check current local Grok Build stats:

```bash
./helper/codex_stats_helper.py --provider grok --json | python -m json.tool
```

Check current local OpenCode stats:

```bash
./helper/codex_stats_helper.py --provider opencode --json | python -m json.tool
```

## Uninstall

```bash
./scripts/uninstall.sh
```

To also remove the local helper cache:

```bash
./scripts/uninstall.sh --purge-cache
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Product requirements](docs/PRD.md)
- [Privacy](docs/PRIVACY.md)
- [Release checklist](docs/RELEASE.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).
