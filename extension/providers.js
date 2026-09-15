// Shared provider registry.
//
// Imported by BOTH extension.js (GNOME Shell process) and prefs.js (Adw/Gtk
// process), so this module must not import anything beyond gi://GLib — no St,
// no Gtk, no resource:///org/gnome/shell/*.

import GLib from 'gi://GLib';

export function expandHome(path) {
    if (path === '~')
        return GLib.get_home_dir();
    if (path.startsWith('~/'))
        return GLib.build_filenamev([GLib.get_home_dir(), path.slice(2)]);
    return path;
}

// One entry per usage provider.
//
//   id           helper --provider value, cache file suffix, panel-provider value
//   label        user-visible name
//   enabledKey   GSettings bool that hides the provider (null = always shown)
//   rootKey      GSettings string with the provider's data root
//   defaultRoot  schema default for rootKey (shown in Preferences subtitles)
//   gauges       'codex-freshest' (pick the freshest Codex windows) or a list of
//                payload limits.<key> names; an empty list means the provider has
//                no rate-limit data at all and only token totals are shown
//   extraGauges  additional limits.<key> rows, optionally gated on whenKey
//   icon         icons/<icon>.svg (dark theme) / icons/<icon>-light.svg (light)
export const PROVIDERS = [
    {
        id: 'codex',
        label: 'Codex',
        enabledKey: null,
        rootKey: 'log-root',
        defaultRoot: '~/.codex/sessions',
        gauges: 'codex-freshest',
        extraGauges: [],
        icon: 'codex-stats-symbolic',
    },
    {
        id: 'claude',
        label: 'Claude',
        enabledKey: 'claude-enabled',
        rootKey: 'claude-log-root',
        defaultRoot: '~/.claude/projects',
        gauges: ['primary', 'secondary'],
        extraGauges: [{key: 'fable_weekly', label: 'Fable', whenKey: 'claude-online-usage'}],
        icon: 'claude-symbolic',
    },
    {
        id: 'grok',
        label: 'Grok',
        enabledKey: 'grok-enabled',
        rootKey: 'grok-log-root',
        defaultRoot: '~/.grok',
        // Grok Build only publishes a weekly credit window; there is no 5h window.
        gauges: ['secondary'],
        extraGauges: [],
        icon: 'grok-symbolic',
    },
    {
        id: 'opencode',
        label: 'OpenCode',
        enabledKey: 'opencode-enabled',
        rootKey: 'opencode-log-root',
        defaultRoot: '~/.local/share/opencode',
        // OpenCode stores no rate-limit data locally.
        gauges: [],
        extraGauges: [],
        icon: 'opencode-symbolic',
    },
];

// Every key that changes which providers are visible or where their data lives.
// A change on any of these re-resolves the visible set and refreshes.
export const PROVIDER_KEYS = PROVIDERS
    .flatMap(provider => [provider.enabledKey, provider.rootKey])
    .filter(key => !!key);
