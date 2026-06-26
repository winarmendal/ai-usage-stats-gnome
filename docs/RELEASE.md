# Release Checklist

Run this checklist before publishing a release.

## Validation

```bash
./scripts/smoke-test.sh
```

Confirm:

- helper unit tests pass
- helper emits valid JSON
- GSettings schema compiles
- extension bundle packages with `gnome-extensions pack`

## Manual GNOME Check

For the public release bundle:

```bash
./scripts/package.sh
gnome-extensions install --force dist/codex-stats@winarmendal.github.io.shell-extension.zip
gnome-extensions enable codex-stats@winarmendal.github.io
```

For a source-checkout install:

```bash
./scripts/install.sh
```

Then verify:

- `codex-stats@winarmendal.github.io` can be enabled
- top bar shows the bundled provider icon (Codex by default; the Claude logomark when Claude is the active provider)
- enabling compact panel usage shows 5h and Week percentages only
- panel icon and popover remain legible in GNOME light and dark mode
- More Stats expands and the popover tabs switch correctly
- enabling "Track Claude Code" shows the Codex/Claude selector, and switching to Claude swaps both the panel icon and the stats
- Claude 5h and Week show `--` until the statusLine capture wrapper is installed (Preferences → Claude → "Install")
- with "Fetch live limits online" enabled, the Sonnet row populates and stays visible (only briefly showing `--`, never disappearing)
- preferences open and persist
- no prompt, response text, or OAuth token appears in the UI or logs

## Publish

Create a GitHub release from the tagged commit and attach only:

```text
dist/codex-stats@winarmendal.github.io.shell-extension.zip
```

Release notes should state that this is a manual GitHub zip install and is not yet listed on Extension Manager. Extension Manager support requires a later `extensions.gnome.org` review pass.
