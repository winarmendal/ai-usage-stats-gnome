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
- top-bar label shows Day, 5h, and Week
- popover tabs switch correctly
- preferences open and persist
- no prompt or response text appears in the UI

## Publish

Create a GitHub release from the tagged commit and attach only:

```text
dist/codex-stats@winarmendal.github.io.shell-extension.zip
```

Release notes should state that this is a manual GitHub zip install and is not yet listed on Extension Manager. Extension Manager support requires a later `extensions.gnome.org` review pass.
