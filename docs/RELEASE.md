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
- top bar shows the placeholder "Select provider" until a provider is picked in Preferences → Providers → "Top bar provider"; picking Codex/Claude/Grok/OpenCode swaps both the panel icon and the label
- enabling compact panel usage shows the top-bar provider's remaining percentages, or its token total (OpenCode) when it has no rate limits
- panel icon and popover remain legible in GNOME light and dark mode, for all four provider icons
- popover shows one block per enabled, present provider at once (no tabs); toggling a "Track <Provider>" switch off, or renaming its data root away, removes that block on the next refresh
- More Stats expands to one 7-day history list per visible provider in a single scroll area
- Grok's block shows a Week gauge only (5h reads `--`/is absent); OpenCode's block shows a token total with no gauges
- Claude 5h and Week show `--` until the statusLine capture wrapper is installed (Preferences → Claude → "Install")
- with "Fetch live limits online" enabled, the Fable row populates and stays visible (only briefly showing `--`, never disappearing)
- `gnome-extensions disable` then `enable` (no re-login) leaves the popover showing real data: no per-provider "helper refresh cancelled" rows and no "Needs attention" subtitle
- preferences open and persist, including the new Grok and OpenCode data-directory fields
- no prompt, response text, or OAuth token appears in the UI or logs

## Publish

Create a GitHub release from the tagged commit and attach only:

```text
dist/codex-stats@winarmendal.github.io.shell-extension.zip
```

Release notes should state that this is a manual GitHub zip install and is not yet listed on Extension Manager. Extension Manager support requires a later `extensions.gnome.org` review pass.
