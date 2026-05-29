import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const VIEWS = ['day', 'week', 'month', 'three_months'];
const BAR_WIDTH = 170;
const MAX_SERIES_HEIGHT = 330;
const SERIES_ROW_HEIGHT = 22;
const INTERFACE_SCHEMA = 'org.gnome.desktop.interface';
const USER_THEME_SCHEMA = 'org.gnome.shell.extensions.user-theme';
const PANEL_ICON_DARK_THEME_PATHS = [
    ['icons', 'codex-stats-symbolic.svg'],
    ['codex-stats-symbolic.svg'],
];
const PANEL_ICON_LIGHT_THEME_PATHS = [
    ['icons', 'codex-stats-symbolic-light.svg'],
    ['codex-stats-symbolic-light.svg'],
];
const VIEW_LABELS = {
    day: 'Day',
    week: 'Week',
    month: 'Month',
    three_months: '3M',
};

export default class CodexStatsExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._interfaceSettings = this._settingsForSchema(INTERFACE_SCHEMA);
        this._userThemeSettings = this._settingsForSchema(USER_THEME_SCHEMA);
        this._signals = [];
        this._themeSignals = [];
        this._timeoutId = null;
        this._activeView = 'day';
        this._statsExpanded = false;
        this._data = null;
        this._loading = false;
        this._cancellable = new Gio.Cancellable();

        this._indicator = new PanelMenu.Button(0.0, this.metadata.name, false);
        this._indicator.add_style_class_name('codex-stats-panel-button');

        this._panelBox = new St.BoxLayout({
            style_class: 'codex-stats-panel-box',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelIcon = new St.Icon({
            gicon: this._panelGIcon(),
            icon_size: 14,
            style_class: 'codex-stats-panel-icon',
        });
        this._panelLabel = new St.Label({
            text: 'Codex --',
            style_class: 'codex-stats-panel-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._panelBox.add_child(this._panelIcon);
        this._panelBox.add_child(this._panelLabel);
        this._indicator.add_child(this._panelBox);

        this._indicator.menu.box.add_style_class_name('codex-stats-popup');
        this._buildMenu();
        Main.panel.addToStatusArea(this.uuid, this._indicator);

        for (const key of ['refresh-interval', 'log-root', 'panel-show-usage', 'cache-enabled'])
            this._signals.push(this._settings.connect(`changed::${key}`, () => this._onSettingsChanged()));
        this._connectThemeSignal(this._interfaceSettings, 'changed::color-scheme');
        this._connectThemeSignal(this._interfaceSettings, 'changed::gtk-theme');
        this._connectThemeSignal(this._userThemeSettings, 'changed::name');

        this._onSettingsChanged();
    }

    disable() {
        this._cancellable?.cancel();
        this._cancellable = null;

        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }

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
        this._panelIcon = null;
        this._panelLabel = null;
        this._statsToggleButton = null;
        this._statsToggleLabel = null;
        this._statsToggleIcon = null;
        this._contentBox = null;
        this._tabsBox = null;
    }

    _onSettingsChanged() {
        this._setupTimeout();
        this._refreshData();
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
            text: _('Codex Stats'),
            style_class: 'codex-stats-title',
        });
        this._subtitleLabel = new St.Label({
            text: _('Local Codex usage'),
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

        this._tabsBox = new St.BoxLayout({
            style_class: 'codex-stats-tabs',
        });
        this._indicator.menu.box.add_child(this._tabsBox);

        this._contentBox = new St.BoxLayout({
            style_class: 'codex-stats-content',
            vertical: true,
        });
        this._indicator.menu.box.add_child(this._contentBox);

        this._renderTabs();
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

    async _refreshData(force = false) {
        if (this._loading && !force)
            return;

        this._loading = true;
        this._subtitleLabel?.set_text(_('Refreshing...'));
        this._updatePanel();

        try {
            const payload = await this._runHelper();
            this._data = payload;
        } catch (error) {
            logError(error, 'Codex Stats: helper refresh failed');
            this._data = {
                status: {
                    ok: false,
                    message: error.message || String(error),
                    files_scanned: 0,
                },
                today: {total_tokens: 0, hourly: []},
                limits: {
                    primary: {label: '5h', remaining_percent: null, used_percent: null, resets_at: null},
                    secondary: {label: 'Week', remaining_percent: null, used_percent: null, resets_at: null},
                },
                history: {week: [], month: [], three_months: []},
            };
        } finally {
            this._loading = false;
            this._updatePanel();
            this._updateMenu();
        }
    }

    _runHelper() {
        return new Promise((resolve, reject) => {
            const python = GLib.find_program_in_path('python3') || GLib.find_program_in_path('python') || '/usr/bin/python';
            const helperPath = this._helperPath();
            const cacheFile = GLib.build_filenamev([GLib.get_user_cache_dir(), 'codex-stats', 'cache.json']);
            const argv = [
                python,
                helperPath,
                '--json',
                '--log-root',
                this._settings.get_string('log-root'),
                '--cache-file',
                cacheFile,
            ];
            if (!this._settings.get_boolean('cache-enabled'))
                argv.push('--no-cache');

            const proc = Gio.Subprocess.new(
                argv,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );

            proc.communicate_utf8_async(null, this._cancellable, (subprocess, result) => {
                try {
                    const [, stdout, stderr] = subprocess.communicate_utf8_finish(result);
                    if (this._cancellable?.is_cancelled())
                        return;

                    const trimmed = (stdout || '').trim();
                    if (!trimmed) {
                        reject(new Error((stderr || 'Helper produced no output').trim()));
                        return;
                    }
                    resolve(JSON.parse(trimmed));
                } catch (error) {
                    reject(error);
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

    _panelGIcon() {
        const paths = this._prefersDarkTheme()
            ? PANEL_ICON_DARK_THEME_PATHS
            : PANEL_ICON_LIGHT_THEME_PATHS;
        for (const relativePath of paths) {
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

    _updatePanelIcon() {
        if (this._panelIcon)
            this._panelIcon.gicon = this._panelGIcon();
    }

    _settingsForSchema(schemaId) {
        if (Gio.SettingsSchemaSource.get_default()?.lookup(schemaId, true))
            return new Gio.Settings({schema_id: schemaId});
        return null;
    }

    _connectThemeSignal(settings, signalName) {
        if (settings)
            this._themeSignals.push([settings, settings.connect(signalName, () => this._updatePanelIcon())]);
    }

    _updatePanel() {
        if (!this._panelLabel)
            return;

        const showUsage = this._settings.get_boolean('panel-show-usage');
        this._panelLabel.visible = showUsage;
        if (!showUsage)
            return;

        const primary = this._data?.limits?.primary || {};
        const secondary = this._data?.limits?.secondary || {};
        this._panelLabel.set_text(`${primary.label || '5h'} ${this._formatPercent(primary.remaining_percent)}  ${secondary.label || 'Week'} ${this._formatPercent(secondary.remaining_percent)}`);
    }

    _updateMenu() {
        if (!this._summaryBox || !this._contentBox)
            return;

        this._summaryBox.destroy_all_children();
        this._contentBox.destroy_all_children();

        const data = this._data;
        if (!data) {
            this._summaryBox.add_child(this._label(_('Loading local Codex usage...'), 'codex-stats-muted'));
            return;
        }

        const status = data.status || {};
        const generated = data.generated_at ? this._formatTime(data.generated_at) : '--';
        this._subtitleLabel?.set_text(status.ok === false ? _('Needs attention') : _('Updated %s').format(generated));

        this._summaryBox.add_child(this._metricRow(_('Today'), this._formatTokens(data?.today?.total_tokens), _('tokens burned')));
        this._summaryBox.add_child(this._metricRow(
            data?.limits?.primary?.label || _('5h'),
            this._formatPercent(data?.limits?.primary?.remaining_percent),
            this._resetText(data?.limits?.primary?.resets_at)
        ));
        this._summaryBox.add_child(this._metricRow(
            data?.limits?.secondary?.label || _('Week'),
            this._formatPercent(data?.limits?.secondary?.remaining_percent),
            this._resetText(data?.limits?.secondary?.resets_at)
        ));

        if (status.message)
            this._summaryBox.add_child(this._label(status.message, status.ok === false ? 'codex-stats-error' : 'codex-stats-muted'));

        this._updateStatsDisclosure();
    }

    _updateStatsDisclosure() {
        if (!this._tabsBox || !this._contentBox)
            return;

        if (this._statsToggleLabel)
            this._statsToggleLabel.set_text(this._statsExpanded ? _('Less Stats') : _('More Stats'));
        if (this._statsToggleIcon)
            this._statsToggleIcon.set_icon_name(this._statsExpanded ? 'pan-down-symbolic' : 'pan-end-symbolic');

        this._tabsBox.visible = this._statsExpanded;
        this._contentBox.visible = this._statsExpanded;
        this._contentBox.destroy_all_children();

        if (this._statsExpanded)
            this._renderView();
    }

    _renderTabs() {
        if (!this._tabsBox)
            return;
        this._tabsBox.destroy_all_children();
        for (const view of VIEWS) {
            const button = new St.Button({
                label: VIEW_LABELS[view],
                style_class: view === this._activeView ? 'button codex-stats-tab codex-stats-tab-active' : 'button codex-stats-tab',
                can_focus: true,
                reactive: true,
                track_hover: true,
            });
            button.connect('clicked', () => {
                this._activeView = view;
                this._renderTabs();
                this._updateStatsDisclosure();
            });
            this._tabsBox.add_child(button);
        }
    }

    _renderView() {
        if (!this._contentBox)
            return;
        this._contentBox.destroy_all_children();

        const data = this._data;
        if (!data)
            return;

        if (this._activeView === 'day') {
            this._contentBox.add_child(this._sectionTitle(_('Today by hour')));
            this._renderRows(this._hourlyRows(data.today?.hourly || [], data.generated_at));
            return;
        }

        const series = this._activeView === 'three_months'
            ? data.history?.three_months || []
            : data.history?.[this._activeView] || [];
        this._contentBox.add_child(this._sectionTitle(VIEW_LABELS[this._activeView]));
        this._renderRows(this._objectRows(series));
    }

    _renderRows(rows) {
        if (!rows.length) {
            this._contentBox.add_child(this._label(_('No local usage in this range.'), 'codex-stats-muted'));
            return;
        }

        const max = Math.max(1, ...rows.map(row => row.value || 0));
        const scrollView = new St.ScrollView({
            style_class: 'codex-stats-series-scroll vfade',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            x_expand: true,
        });
        scrollView.set_height(Math.min(MAX_SERIES_HEIGHT, Math.max(72, rows.length * SERIES_ROW_HEIGHT)));

        const seriesBox = new St.BoxLayout({
            style_class: 'codex-stats-series',
            vertical: true,
            x_expand: true,
        });
        scrollView.set_child(seriesBox);
        this._contentBox.add_child(scrollView);

        for (const row of rows)
            seriesBox.add_child(this._barRow(row.label, row.value, max, row.muted));
    }

    _hourlyRows(values, generatedAt) {
        const limit = this._hourLimit(values, generatedAt);
        const rows = [];
        let zeroStart = null;

        const flushZeros = end => {
            if (zeroStart === null)
                return;
            rows.push({
                label: zeroStart === end ? this._hourLabel(zeroStart) : `${String(zeroStart).padStart(2, '0')}-${String(end).padStart(2, '0')}`,
                value: 0,
                muted: true,
            });
            zeroStart = null;
        };

        for (let index = 0; index < limit; index++) {
            const value = Math.max(0, Number(values[index] || 0));
            if (value === 0) {
                if (zeroStart === null)
                    zeroStart = index;
                continue;
            }

            flushZeros(index - 1);
            rows.push({
                label: this._hourLabel(index),
                value,
                muted: false,
            });
        }

        flushZeros(limit - 1);
        return rows;
    }

    _hourLimit(values, generatedAt) {
        if (!values.length)
            return 0;

        const generated = generatedAt ? new Date(generatedAt) : new Date();
        if (Number.isNaN(generated.getTime()))
            return values.length;

        return Math.min(values.length, generated.getHours() + 1);
    }

    _hourLabel(hour) {
        return `${String(hour).padStart(2, '0')}:00`;
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

    _resetText(value) {
        if (!value)
            return _('reset --');
        return _('reset %s').format(this._formatTime(value));
    }

    _formatTime(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime()))
            return '--';
        return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    }
}
