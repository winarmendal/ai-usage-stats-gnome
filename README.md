# AI Usage Stats

![CI](https://github.com/winarmendal/codex-stats-gnome/actions/workflows/ci.yml/badge.svg)

AI Usage Stats is a local-first GNOME Shell extension that shows Codex and Claude Code token usage and rate-limit percentages in the top bar.

For Codex it reads `token_count` metadata events from local JSONL session logs under `~/.codex/sessions`, uses local `codex.rate_limits` metadata from `~/.codex/logs_2.sqlite`, and can ask the local Codex CLI for the current account rate-limit snapshot for fresher 5-hour and weekly percentages. For Claude it reads `assistant` events from local JSONL transcripts under `~/.claude/projects` and uses an opt-in statusLine capture wrapper for 5-hour and weekly rate-limit percentages. An optional, off-by-default online mode can additionally read live 5-hour/weekly and per-model (Sonnet/Opus) limits directly from your own Anthropic account. Neither provider's prompts, assistant messages, file contents, browser data, or cookies are parsed or displayed; the opt-in online mode reads your local Claude login token read-only, solely to authenticate that one request (see `docs/PRIVACY.md`).

## Features

- Top-bar icon with active-provider label, optional compact usage: `5h 99%  Week 60%`
- Provider selector (Codex / Claude) at the top of the popover when Claude tracking is enabled; choice persists across sessions
- Bundled panel icon, so CLI-only users do not need the Codex or Claude desktop app icon installed
- Theme-aware GNOME Shell styling for light and dark mode
- Click popover with today, 5-hour remaining, and weekly remaining stats for the active provider
- Collapsed More Stats section with Day, Week, Month, and 3M token history views
- Preferences for refresh interval, log roots, realtime account limits, compact panel usage, cache usage, and Claude provider toggle
- Opt-in Claude statusLine capture wrapper (installed from Preferences → Claude → Install) that supplies 5-hour and weekly rate-limit data; Claude's gauges show `--` until it is installed
- Optional online live limits (Preferences → Claude, off by default): fetches live 5-hour/weekly and per-model Sonnet/Opus limits from your Anthropic account, fresh even with no Claude session open; see `docs/PRIVACY.md`
- Python stdlib helper with cache-aware JSONL parsing and realtime rate-limit metadata
- Privacy-focused model that displays only token and rate-limit metadata, for both providers

## Requirements

- GNOME Shell 50
- GJS 1.88 or compatible GNOME 50 runtime
- Python 3.10+
- `glib-compile-schemas`
- `gnome-extensions`

This project is currently built and tested for GNOME Shell 50. Wider shell-version support should be validated before changing `metadata.json`.

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
git clone https://github.com/winarmendal/codex-stats-gnome.git
cd codex-stats-gnome
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
