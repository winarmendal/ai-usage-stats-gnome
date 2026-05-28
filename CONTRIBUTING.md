# Contributing

Thanks for improving Codex Stats.

## Local Checks

Before opening a PR, run:

```bash
./scripts/smoke-test.sh
```

This runs helper unit tests, validates the GSettings schema, checks live helper JSON output, and verifies GNOME extension packaging.

## Development Notes

- Keep the extension local-first. Do not add network calls for usage data.
- Keep prompt, response, file, cookie, and API-token parsing out of the helper.
- Prefer small GNOME Shell UI changes that are safe to run inside the shell process.
- Put heavy parsing in `helper/codex_stats_helper.py`, not in `extension/extension.js`.
- Do not commit generated extension bundles, build directories, or `.omx` state.

## Compatibility

V1 targets GNOME Shell 50. If you broaden `shell-version`, test on that GNOME Shell version before submitting the change.

