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

## Install From Source

```bash
git clone https://github.com/winarmendal/codex-stats-gnome.git
cd codex-stats-gnome
./scripts/install.sh
```

On GNOME Wayland, you may need to log out and back in before GNOME Shell indexes a newly installed local extension. After re-login:

```bash
gnome-extensions enable codex-stats@winarmendal.local
```

The install script removes the older `codexbar@inled.es` extension if it is present, then installs Codex Stats as `codex-stats@winarmendal.local`.

## Package

```bash
./scripts/package.sh
```

The extension bundle is written to `dist/`.

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
- [Privacy](docs/PRIVACY.md)
- [Release checklist](docs/RELEASE.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).

