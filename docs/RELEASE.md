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

```bash
./scripts/install.sh
```

Then verify:

- old `codexbar@inled.es` is removed if present
- `codex-stats@winarmendal.local` can be enabled
- top-bar label shows Day, 5h, and Week
- popover tabs switch correctly
- preferences open and persist
- no prompt or response text appears in the UI

## Publish

Create a GitHub release from the tagged commit and attach the bundle from `dist/`.

