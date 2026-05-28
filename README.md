# Codex Stats

![CI](https://github.com/winarmendal/codex-stats-gnome/actions/workflows/ci.yml/badge.svg)

Codex Stats is a local-first GNOME Shell extension that shows Codex token usage in the top bar.

It reads only `token_count` metadata events from local Codex JSONL session logs under `~/.codex/sessions`. It does not read prompts, assistant messages, file contents, browser data, cookies, API tokens, or network APIs.

## Features

- Top-bar usage summary: `Codex  Day 1.2M  5h 99%  Week 60%`
- Click popover with today, 5-hour remaining, and weekly remaining stats
- Day, Week, Month, and 3M token history views
- Preferences for refresh interval, log root, panel sections, and cache usage
- Python stdlib helper with cache-aware JSONL parsing
- Local-only privacy model with no OpenAI or ChatGPT API calls

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

Codex Stats is not listed on Extension Manager yet. Extension Manager visibility requires publishing through `extensions.gnome.org`, which is planned for a later release.

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

Codex Stats installs as `codex-stats@winarmendal.github.io`. It does not inspect, disable, uninstall, or remove other Codex-related extensions.

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
