import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

import {PROVIDERS, PROVIDER_KEYS, expandHome} from './providers.js';

const BAR_WIDTH = 170;
const MAX_SERIES_HEIGHT = 330;
const SERIES_ROW_HEIGHT = 22;
const MANUAL_REFRESH_DELAYS_SECONDS = [2, 5, 10];
const INTERFACE_SCHEMA = 'org.gnome.desktop.interface';
const USER_THEME_SCHEMA = 'org.gnome.shell.extensions.user-theme';
// Naming gotcha: internally `primary` is the 5-hour window and `secondary` the
// weekly one. These are the labels used when the helper payload omits its own.
const GAUGE_FALLBACK_LABELS = {
    primary: '5h',
    secondary: 'Week',
};
// Settings that change helper arguments or panel formatting but not which
// providers are visible (those live in PROVIDER_KEYS).
const REFRESH_KEYS = [
    'refresh-interval',
    'panel-show-usage',
    'cache-enabled',
    'account-limits-enabled',
    'claude-limits-file',
    'claude-online-usage',
];

export default class CodexStatsExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._interfaceSettings = this._settingsForSchema(INTERFACE_SCHEMA);
        this._userThemeSettings = this._settingsForSchema(USER_THEME_SCHEMA);
        this._signals = [];
        this._themeSignals = [];
        this._timeoutId = null;
        this._followupRefreshIds = [];
        // Monotonic for the whole object lifetime: GNOME Shell reuses the
        // instance across disable()/enable(), and resetting it here would let a
        // cancelled refresh from the previous cycle match the new cycle's serial
        // and write its cancellation fallbacks into the fresh UI.
        this._refreshSerial ??= 0;
        this._statsExpanded = false;
        // Helper payloads keyed by provider id; {} until the first refresh lands.
        this._data = {};
        this._visible = [];
        this._loading = false;
        this._panelIconKey = null;
        this._cancellable = new Gio.Cancellable();

        this._resolveVisibleProviders();

        this._indicator = new PanelMenu.Button(0.0, this.metadata.name, false);
        this._indicator.add_style_class_name('codex-stats-panel-button');

        this._panelBox = new St.BoxLayout({
            style_class: 'codex-stats-panel-box',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelIcon = new St.Icon({
            icon_size: 14,
            style_class: 'codex-stats-panel-icon',
        });
        this._panelLabel = new St.Label({
            text: '--',
            style_class: 'codex-stats-panel-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelBox.add_child(this._panelIcon);
        this._panelBox.add_child(this._panelLabel);
        this._indicator.add_child(this._panelBox);
        this._updatePanelIcon();

        this._indicator.menu.box.add_style_class_name('codex-stats-popup');
        this._buildMenu();
        Main.panel.addToStatusArea(this.uuid, this._indicator);

        this._signals.push(this._settings.connect('changed::panel-provider', () => this._updatePanel()));
        for (const key of PROVIDER_KEYS)
            this._signals.push(this._settings.connect(`changed::${key}`, () => this._onProviderChanged()));
        for (const key of REFRESH_KEYS)
            this._signals.push(this._settings.connect(`changed::${key}`, () => this._onSettingsChanged()));
        this._connectThemeSignal(this._interfaceSettings, 'changed::color-scheme');
        this._connectThemeSignal(this._interfaceSettings, 'changed::gtk-theme');
        this._connectThemeSignal(this._userThemeSettings, 'changed::name');

        this._onSettingsChanged();
    }

    disable() {
        // Bump the serial before cancelling so any in-flight _refreshData() sees
        // itself as stale and touches neither the settings nor the destroyed UI.
        this._refreshSerial++;
        this._cancellable?.cancel();
        this._cancellable = null;

        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        this._clearFollowupRefreshes();

        if (this._settings) {
            for (const id of this._signals)
                this._settings.disconnect(id);
        }
        this._signals = [];
        this._settings = null;

        for (const [settings, id] of this._themeSignals)
            settings.disconnect(id);
        this._themeSignals = [];
        this._interfaceSettings = null;
        this._userThemeSettings = null;

        this._indicator?.destroy();
        this._indicator = null;
        this._panelBox = null;
        this._panelIcon = null;
        this._panelLabel = null;
        this._panelIconKey = null;
        this._titleLabel = null;
        this._subtitleLabel = null;
        this._summaryBox = null;
        this._statsToggleButton = null;
        this._statsToggleLabel = null;
        this._statsToggleIcon = null;
        this._historyScroll = null;
        this._historyBox = null;

        this._data = {};
        this._visible = [];
        this._loading = false;
    }

    _onSettingsChanged() {
        this._setupTimeout();
        this._refreshData();
        this._updatePanel();
    }

    _onProviderChanged() {
        this._data = {};
        this._resolveVisibleProviders();
        this._setupTimeout();
        // Force: a refresh may already be in flight for the old provider set, and
        // a non-forced call would be dropped, leaving "Loading" until the next tick.
        this._refreshData(true);
        this._updatePanel();
    }

    _setupTimeout() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }

        const interval = Math.max(10, this._settings.get_int('refresh-interval'));
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, interval, () => {
            this._refreshData();
            return GLib.SOURCE_CONTINUE;
        });
    }

    // Codex is always tracked. The others show up only when explicitly enabled
    // AND their data root exists, so users never see empty blocks for tools they
    // do not have installed.
    _resolveVisibleProviders() {
        if (!this._settings) {
            this._visible = [];
            return this._visible;
        }

        this._visible = PROVIDERS.filter(provider => {
            if (!provider.enabledKey)
                return true;
            if (!this._settings.get_boolean(provider.enabledKey))
                return false;
            const root = expandHome(this._settings.get_string(provider.rootKey) || provider.defaultRoot);
            return GLib.file_test(root, GLib.FileTest.EXISTS);
        });
        return this._visible;
    }

    // The provider whose gauges go in the top bar. null until the user picks one
    // in Preferences, or when the picked provider is not currently visible.
    _panelProvider() {
        const id = this._settings?.get_string('panel-provider') || '';
        if (!id)
            return null;
        return this._visible.find(provider => provider.id === id) || null;
    }

    _buildMenu() {
        this._indicator.menu.box.destroy_all_children();

        const header = new St.BoxLayout({
            style_class: 'codex-stats-header',
            x_expand: true,
        });
        const titleBox = new St.BoxLayout({
            vertical: true,
            x_expand: true,
        });
        this._titleLabel = new St.Label({
            text: this.metadata.name,
            style_class: 'codex-stats-title',
        });
        this._subtitleLabel = new St.Label({
            text: _('Local usage'),
            style_class: 'codex-stats-subtitle',
        });
        titleBox.add_child(this._titleLabel);
        titleBox.add_child(this._subtitleLabel);
        header.add_child(titleBox);

        const refreshButton = this._iconButton('view-refresh-symbolic', _('Refresh'));
        refreshButton.connect('clicked', () => this._refreshData(true));
        header.add_child(refreshButton);

        const settingsButton = this._iconButton('preferences-system-symbolic', _('Preferences'));
        settingsButton.connect('clicked', () => {
            this.openPreferences();
            this._indicator.menu.close();
        });
        header.add_child(settingsButton);

        this._indicator.menu.box.add_child(header);

        this._summaryBox = new St.BoxLayout({
            style_class: 'codex-stats-summary',
            vertical: true,
        });
        this._indicator.menu.box.add_child(this._summaryBox);

        this._statsToggleButton = new St.Button({
            style_class: 'button codex-stats-more-button',
            can_focus: true,
            reactive: true,
            track_hover: true,
            accessible_name: _('More Stats'),
        });
        const statsToggleContent = new St.BoxLayout({
            style_class: 'codex-stats-more-content',
            x_expand: true,
        });
        this._statsToggleLabel = new St.Label({
            text: _('More Stats'),
            style_class: 'codex-stats-more-label',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._statsToggleIcon = new St.Icon({
            icon_name: 'pan-end-symbolic',
            icon_size: 16,
            style_class: 'codex-stats-more-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        statsToggleContent.add_child(this._statsToggleLabel);
        statsToggleContent.add_child(this._statsToggleIcon);
        this._statsToggleButton.set_child(statsToggleContent);
        this._statsToggleButton.connect('clicked', () => {
            this._statsExpanded = !this._statsExpanded;
            this._updateStatsDisclosure();
        });
        this._indicator.menu.box.add_child(this._statsToggleButton);

        // One scroll area for every provider's 7-day history.
        this._historyScroll = new St.ScrollView({
            style_class: 'codex-stats-history-scroll vfade',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
        });
        this._historyBox = new St.BoxLayout({
            style_class: 'codex-stats-history',
            vertical: true,
            x_expand: true,
        });
        this._historyScroll.set_child(this._historyBox);
        this._indicator.menu.box.add_child(this._historyScroll);

        this._updateStatsDisclosure();
        this._updateMenu();
    }

    _iconButton(iconName, accessibleName) {
        return new St.Button({
            child: new St.Icon({icon_name: iconName, icon_size: 16}),
            style_class: 'icon-button codex-stats-icon-button',
            can_focus: true,
            reactive: true,
            track_hover: true,
            accessible_name: accessibleName,
            y_align: Clutter.ActorAlign.CENTER,
        });
    }

    async _refreshData(force = false, followup = false) {
        if (!this._settings)
            return;
        if (this._loading && !force)
            return;

        if (force && !followup)
            this._clearFollowupRefreshes();

        // A provider's root can appear or disappear between ticks, so re-resolve
        // before every spawn round.
        const providers = this._resolveVisibleProviders().slice();

        const serial = ++this._refreshSerial;
        this._loading = true;

        try {
            this._subtitleLabel?.set_text(_('Refreshing...'));
            this._updatePanel();
            // allSettled (not all): one broken provider must not blank the others.
            const results = await Promise.allSettled(providers.map(provider => this._runHelper(provider)));
            if (serial !== this._refreshSerial || !this._settings)
                return;

            const data = {};
            providers.forEach((provider, index) => {
                const result = results[index];
                if (result.status === 'fulfilled') {
                    data[provider.id] = result.value;
                    return;
                }
                const error = result.reason instanceof Error ? result.reason : new Error(String(result.reason));
                logError(error, `AI Usage Stats: ${provider.id} helper refresh failed`);
                data[provider.id] = this._fallbackPayload(error);
            });
            this._data = data;
        } finally {
            if (serial === this._refreshSerial) {
                this._loading = false;
                if (this._settings) {
                    this._updatePanel();
                    this._updateMenu();
                    if (force && !followup)
                        this._scheduleFollowupRefreshes();
                }
            }
        }
    }

    _fallbackPayload(error) {
        return {
            status: {
                ok: false,
                message: error?.message || String(error),
                files_scanned: 0,
            },
            today: {total_tokens: 0, hourly: []},
            limits: {
                primary: {label: '5h', remaining_percent: null, used_percent: null, resets_at: null},
                secondary: {label: 'Week', remaining_percent: null, used_percent: null, resets_at: null},
            },
            history: {week: [], month: [], three_months: []},
        };
    }

    _clearFollowupRefreshes() {
        for (const id of this._followupRefreshIds || [])
            GLib.source_remove(id);
        this._followupRefreshIds = [];
    }

    _scheduleFollowupRefreshes() {
        this._clearFollowupRefreshes();

        for (const delay of MANUAL_REFRESH_DELAYS_SECONDS) {
            const id = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, delay, () => {
                this._followupRefreshIds = this._followupRefreshIds.filter(existingId => existingId !== id);
                this._refreshData(true, true);
                return GLib.SOURCE_REMOVE;
            });
            this._followupRefreshIds.push(id);
        }
    }

    _helperArgv(provider) {
        const python = GLib.find_program_in_path('python3') || GLib.find_program_in_path('python') || '/usr/bin/python';
        const cacheFile = GLib.build_filenamev([GLib.get_user_cache_dir(), 'codex-stats', `cache-${provider.id}.json`]);
        const argv = [
            python,
            this._helperPath(),
            '--json',
            '--provider',
            provider.id,
            '--cache-file',
            cacheFile,
            '--log-root',
            this._settings.get_string(provider.rootKey) || provider.defaultRoot,
        ];

        if (!this._settings.get_boolean('cache-enabled'))
            argv.push('--no-cache');

        if (provider.id === 'codex' && !this._settings.get_boolean('account-limits-enabled'))
            argv.push('--no-account-limits');

        if (provider.id === 'claude') {
            const limitsFile = this._settings.get_string('claude-limits-file');
            if (limitsFile)
                argv.push('--limits-file', limitsFile);
            if (this._settings.get_boolean('claude-online-usage'))
                argv.push('--claude-online');
        }

        return argv;
    }

    _runHelper(provider) {
        return new Promise((resolve, reject) => {
            const argv = this._helperArgv(provider);
            // Hold a local reference: disable() nulls this._cancellable while the
            // callback may still be pending.
            const cancellable = this._cancellable;

            const proc = Gio.Subprocess.new(
                argv,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );

            proc.communicate_utf8_async(null, cancellable, (subprocess, result) => {
                try {
                    const [, stdout, stderr] = subprocess.communicate_utf8_finish(result);
                    // Always settle — Promise.allSettled in _refreshData waits for
                    // every provider, so a silent return would hang the refresh.
                    if (cancellable?.is_cancelled()) {
                        reject(new Error(`${provider.id} helper refresh cancelled`));
                        return;
                    }

                    const trimmed = (stdout || '').trim();
                    if (!trimmed) {
                        reject(new Error((stderr || 'Helper produced no output').trim()));
                        return;
                    }
                    resolve(JSON.parse(trimmed));
                } catch (error) {
                    reject(error instanceof Error ? error : new Error(String(error)));
                }
            });
        });
    }

    _helperPath() {
        const nested = GLib.build_filenamev([this.path, 'helper', 'codex_stats_helper.py']);
        if (GLib.file_test(nested, GLib.FileTest.EXISTS))
            return nested;
        return GLib.build_filenamev([this.path, 'codex_stats_helper.py']);
    }

    // Per-provider icon. Each provider ships a dark-theme (light fill) and a
    // light-theme (dark fill) variant; `gnome-extensions pack` flattens extra
    // sources to the extension root, so check icons/ first then the flat path.
    _providerGIcon(provider) {
        if (!provider)
            return Gio.ThemedIcon.new('utilities-terminal-symbolic');

        const stem = this._prefersDarkTheme() ? provider.icon : `${provider.icon}-light`;
        for (const relativePath of [['icons', `${stem}.svg`], [`${stem}.svg`]]) {
            const iconPath = GLib.build_filenamev([this.path, ...relativePath]);
            if (GLib.file_test(iconPath, GLib.FileTest.EXISTS))
                return Gio.FileIcon.new(Gio.File.new_for_path(iconPath));
        }
        return Gio.ThemedIcon.new('utilities-terminal-symbolic');
    }

    _prefersDarkTheme() {
        const colorScheme = this._interfaceSettings?.get_string('color-scheme') || '';
        if (colorScheme.includes('dark'))
            return true;
        if (colorScheme.includes('light'))
            return false;

        return [
            this._interfaceSettings?.get_string('gtk-theme') || '',
            this._userThemeSettings?.get_string('name') || '',
        ].some(themeName => themeName.toLowerCase().includes('dark'));
    }

    // St.Icon compares gicons by pointer, so reassigning an equivalent FileIcon
    // reloads the texture. Only swap when the provider or theme actually changed.
    _updatePanelIcon() {
        if (!this._panelIcon)
            return;

        const provider = this._panelProvider();
        const key = `${provider?.id || 'none'}:${this._prefersDarkTheme() ? 'dark' : 'light'}`;
        if (key === this._panelIconKey)
            return;

        this._panelIcon.gicon = this._providerGIcon(provider);
        this._panelIconKey = key;
    }

    _onThemeChanged() {
        this._panelIconKey = null;
        this._updatePanelIcon();
        this._updateMenu();
    }

    _settingsForSchema(schemaId) {
        if (Gio.SettingsSchemaSource.get_default()?.lookup(schemaId, true))
            return new Gio.Settings({schema_id: schemaId});
        return null;
    }

    _connectThemeSignal(settings, signalName) {
        if (settings)
            this._themeSignals.push([settings, settings.connect(signalName, () => this._onThemeChanged())]);
    }

    // Codex publishes several windows but only the freshest observation is
    // trustworthy, so keep the limits sharing the newest observed_at.
    _codexLimits(payload) {
        const candidates = [
            payload?.limits?.primary,
            payload?.limits?.secondary,
        ].filter(limit => typeof limit?.remaining_percent === 'number' &&
            Number.isFinite(limit.remaining_percent));
        const observed = candidates.map(limit => ({
            limit,
            time: typeof limit.observed_at === 'string' && limit.observed_at.trim()
                ? Date.parse(limit.observed_at)
                : Number.NaN,
        }));
        const parseable = observed.filter(candidate => Number.isFinite(candidate.time));

        if (!parseable.length)
            return candidates;

        const newest = Math.max(...parseable.map(candidate => candidate.time));
        return parseable
            .filter(candidate => candidate.time === newest)
            .map(candidate => candidate.limit);
    }

    // A provider with no declared gauges (OpenCode) has no rate-limit data at
    // all; one whose gauges simply have no value yet still shows "--" rows.
    _providerHasGauges(provider) {
        if (provider.gauges === 'codex-freshest')
            return true;
        if (Array.isArray(provider.gauges) && provider.gauges.length)
            return true;
        return !!(provider.extraGauges || []).length;
    }

    _gaugeRows(provider, payload) {
        const limits = payload?.limits || {};
        const rows = [];

        if (provider.gauges === 'codex-freshest') {
            for (const limit of this._codexLimits(payload)) {
                rows.push({
                    label: limit.label || '',
                    percent: limit.remaining_percent,
                    resetsAt: limit.resets_at,
                    includeDate: limit.label === 'Week',
                });
            }
        } else {
            for (const key of provider.gauges || []) {
                const limit = limits[key] || {};
                rows.push({
                    label: limit.label || GAUGE_FALLBACK_LABELS[key] || key,
                    percent: limit.remaining_percent,
                    resetsAt: limit.resets_at,
                    includeDate: key === 'secondary',
                });
            }
        }

        // Extra buckets (Claude's Fable weekly) are rendered on every refresh
        // while their gate is on, so the row never flickers in and out; the value
        // shows "--" when the source is momentarily unavailable.
        for (const extra of provider.extraGauges || []) {
            if (extra.whenKey && !this._settings.get_boolean(extra.whenKey))
                continue;
            const limit = limits[extra.key] || {};
            rows.push({
                label: extra.label,
                percent: limit.remaining_percent,
                resetsAt: limit.resets_at,
                includeDate: true,
            });
        }

        return rows;
    }

    _updatePanel() {
        if (!this._panelLabel || !this._settings)
            return;

        this._updatePanelIcon();

        const showUsage = this._settings.get_boolean('panel-show-usage');
        this._panelLabel.visible = showUsage;
        if (!showUsage)
            return;

        const provider = this._panelProvider();
        this._panelLabel.remove_style_class_name('codex-stats-panel-label-placeholder');

        if (!provider) {
            this._panelLabel.add_style_class_name('codex-stats-panel-label-placeholder');
            this._panelLabel.set_text(_('Select provider'));
            return;
        }

        const payload = this._data[provider.id];
        if (!this._providerHasGauges(provider)) {
            this._panelLabel.set_text(`${_('Today')} ${this._formatTokens(payload?.today?.total_tokens)}`);
            return;
        }

        const rows = this._gaugeRows(provider, payload);
        if (!rows.length) {
            this._panelLabel.set_text('--');
            return;
        }

        this._panelLabel.set_text(rows
            .map(row => `${row.label} ${this._formatPercent(row.percent)}`.trim())
            .join('  '));
    }

    _updateMenu() {
        if (!this._summaryBox)
            return;

        this._summaryBox.destroy_all_children();

        if (!this._visible.length) {
            this._subtitleLabel?.set_text(_('Local usage'));
            this._summaryBox.add_child(this._label(
                _('No provider data found. Enable a provider in Preferences.'),
                'codex-stats-muted'
            ));
            this._updateStatsDisclosure();
            return;
        }

        const payloads = this._visible
            .map(provider => this._data[provider.id])
            .filter(payload => !!payload);

        if (!payloads.length) {
            this._summaryBox.add_child(this._label(_('Loading local usage...'), 'codex-stats-muted'));
            this._updateStatsDisclosure();
            return;
        }

        if (payloads.some(payload => payload?.status?.ok === false)) {
            this._subtitleLabel?.set_text(_('Needs attention'));
        } else {
            const stamps = payloads
                .map(payload => (payload.generated_at ? Date.parse(payload.generated_at) : Number.NaN))
                .filter(stamp => Number.isFinite(stamp));
            const generated = stamps.length ? this._formatTime(new Date(Math.max(...stamps))) : '--';
            this._subtitleLabel?.set_text(_('Updated %s').format(generated));
        }

        let rendered = 0;
        for (const provider of this._visible) {
            const payload = this._data[provider.id];
            if (!payload)
                continue;
            this._summaryBox.add_child(this._providerBlock(provider, payload, rendered > 0));
            rendered++;
        }

        this._updateStatsDisclosure();
    }

    _providerBlock(provider, payload, divider) {
        const box = new St.BoxLayout({
            style_class: divider
                ? 'codex-stats-provider codex-stats-provider-divider'
                : 'codex-stats-provider',
            vertical: true,
            x_expand: true,
        });

        const header = new St.BoxLayout({
            style_class: 'codex-stats-provider-header',
            x_expand: true,
        });
        header.add_child(new St.Icon({
            gicon: this._providerGIcon(provider),
            icon_size: 16,
            style_class: 'codex-stats-provider-icon',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        header.add_child(new St.Label({
            text: provider.label,
            style_class: 'codex-stats-provider-name',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        header.add_child(new St.Label({
            text: _('Today'),
            style_class: 'codex-stats-provider-today-label',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        header.add_child(new St.Label({
            text: this._formatTokens(payload?.today?.total_tokens),
            style_class: 'codex-stats-provider-today-value',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        box.add_child(header);

        for (const row of this._gaugeRows(provider, payload)) {
            box.add_child(this._metricRow(
                row.label,
                this._formatPercent(row.percent),
                this._resetText(row.resetsAt, row.includeDate)
            ));
        }

        const status = payload?.status || {};
        if (status.message)
            box.add_child(this._label(status.message, status.ok === false ? 'codex-stats-error' : 'codex-stats-muted'));

        return box;
    }

    _updateStatsDisclosure() {
        if (!this._historyScroll || !this._historyBox)
            return;

        if (this._statsToggleLabel)
            this._statsToggleLabel.set_text(this._statsExpanded ? _('Less Stats') : _('More Stats'));
        if (this._statsToggleIcon)
            this._statsToggleIcon.set_icon_name(this._statsExpanded ? 'pan-down-symbolic' : 'pan-end-symbolic');

        this._historyScroll.visible = this._statsExpanded;
        this._historyBox.destroy_all_children();

        if (this._statsExpanded)
            this._renderHistory();
    }

    _renderHistory() {
        let rows = 0;

        if (!this._visible.length) {
            this._historyBox.add_child(this._label(
                _('No provider data found. Enable a provider in Preferences.'),
                'codex-stats-muted'
            ));
            rows = 1;
        } else {
            for (const provider of this._visible) {
                const payload = this._data[provider.id];
                const series = this._objectRows(payload?.history?.week || []);
                this._historyBox.add_child(this._sectionTitle(_('%s — last 7 days').format(provider.label)));
                this._renderRows(series, this._historyBox);
                rows += 1 + Math.max(1, series.length);
            }
        }

        this._historyScroll.set_height(Math.min(MAX_SERIES_HEIGHT, Math.max(72, rows * SERIES_ROW_HEIGHT)));
    }

    _renderRows(rows, target) {
        if (!rows.length) {
            target.add_child(this._label(_('No local usage in this range.'), 'codex-stats-muted'));
            return;
        }

        const max = Math.max(1, ...rows.map(row => row.value || 0));
        const seriesBox = new St.BoxLayout({
            style_class: 'codex-stats-series',
            vertical: true,
            x_expand: true,
        });
        target.add_child(seriesBox);

        for (const row of rows)
            seriesBox.add_child(this._barRow(row.label, row.value, max, row.muted));
    }

    _objectRows(items) {
        return items.map(item => ({
            label: item.label || item.date || item.month || '--',
            value: Math.max(0, Number(item.total_tokens || 0)),
            muted: false,
        }));
    }

    _barRow(label, value, max, muted = false) {
        const row = new St.BoxLayout({
            style_class: muted ? 'codex-stats-bar-row codex-stats-bar-row-muted' : 'codex-stats-bar-row',
            x_expand: true,
        });
        row.add_child(new St.Label({
            text: label,
            style_class: 'codex-stats-bar-label',
            x_align: Clutter.ActorAlign.START,
        }));

        const barWrap = new St.BoxLayout({
            style_class: 'codex-stats-bar-wrap',
            x_expand: true,
        });
        const fill = new St.Widget({
            style_class: 'codex-stats-bar-fill',
            x_expand: false,
        });
        const fillWidth = Math.round(BAR_WIDTH * Math.max(0, value) / max);
        fill.set_width(value > 0 ? Math.max(2, fillWidth) : 0);
        barWrap.add_child(fill);
        row.add_child(barWrap);

        row.add_child(new St.Label({
            text: this._formatTokens(value),
            style_class: 'codex-stats-bar-value',
            x_align: Clutter.ActorAlign.END,
        }));
        return row;
    }

    _metricRow(label, value, detail) {
        const row = new St.BoxLayout({
            style_class: 'codex-stats-metric-row',
            x_expand: true,
        });
        row.add_child(new St.Label({
            text: label,
            style_class: 'codex-stats-metric-label',
        }));
        row.add_child(new St.Label({
            text: value,
            style_class: 'codex-stats-metric-value',
            x_expand: true,
            x_align: Clutter.ActorAlign.END,
        }));
        row.add_child(new St.Label({
            text: detail || '',
            style_class: 'codex-stats-metric-detail',
            x_align: Clutter.ActorAlign.END,
        }));
        return row;
    }

    _sectionTitle(text) {
        return new St.Label({
            text,
            style_class: 'codex-stats-section-title',
        });
    }

    _label(text, styleClass = '') {
        const label = new St.Label({
            text,
            style_class: styleClass,
        });
        label.clutter_text.line_wrap = true;
        return label;
    }

    _formatTokens(value) {
        if (value === undefined || value === null || Number.isNaN(Number(value)))
            return '--';
        const number = Number(value);
        const abs = Math.abs(number);
        if (abs >= 1_000_000_000)
            return `${this._trim(number / 1_000_000_000)}B`;
        if (abs >= 1_000_000)
            return `${this._trim(number / 1_000_000)}M`;
        if (abs >= 1_000)
            return `${this._trim(number / 1_000)}K`;
        return String(Math.round(number));
    }

    _trim(value) {
        const rounded = value.toFixed(1);
        return rounded.endsWith('.0') ? rounded.slice(0, -2) : rounded;
    }

    _formatPercent(value) {
        if (value === undefined || value === null || Number.isNaN(Number(value)))
            return '--';
        return `${Math.round(Number(value))}%`;
    }

    _resetText(value, includeDate = false) {
        if (!value)
            return _('reset --');
        const date = new Date(value);
        if (Number.isNaN(date.getTime()))
            return _('reset --');

        const showDate = includeDate || !this._sameLocalDate(date, new Date());
        const resetAt = showDate
            ? `${this._formatDate(date)} ${this._formatTime(date)}`
            : this._formatTime(date);
        return _('reset %s').format(resetAt);
    }

    _sameLocalDate(left, right) {
        return left.getFullYear() === right.getFullYear() &&
            left.getMonth() === right.getMonth() &&
            left.getDate() === right.getDate();
    }

    _formatDate(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime()))
            return '--';
        return date.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
    }

    _formatTime(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime()))
            return '--';
        return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    }
}
