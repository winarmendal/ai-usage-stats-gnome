import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';

import {ExtensionPreferences, gettext as _} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {PROVIDERS, expandHome} from './providers.js';

const WRAPPER_MARKER = 'claude_statusline_capture.py';

// Source/install.sh layout keeps the wrapper under helper/; `gnome-extensions
// pack` flattens extra sources to the extension root. Match _helperPath() in
// extension.js by checking the nested path first, then the flattened one.
function resolveWrapperPath(extensionPath) {
    const nested = GLib.build_filenamev([extensionPath, 'helper', WRAPPER_MARKER]);
    if (GLib.file_test(nested, GLib.FileTest.EXISTS))
        return nested;
    return GLib.build_filenamev([extensionPath, WRAPPER_MARKER]);
}

function claudeSettingsPath() {
    const configDir = GLib.getenv('CLAUDE_CONFIG_DIR') || GLib.build_filenamev([GLib.get_home_dir(), '.claude']);
    return GLib.build_filenamev([configDir, 'settings.json']);
}

// Returns the parsed object, {} when the file is absent, or null on a read/parse
// error (caller must abort rather than overwrite a file it could not understand).
function readJsonFile(path) {
    const file = Gio.File.new_for_path(path);
    if (!file.query_exists(null))
        return {};
    try {
        const [ok, contents] = file.load_contents(null);
        if (!ok)
            return null;
        const data = JSON.parse(new TextDecoder().decode(contents));
        return (data && typeof data === 'object') ? data : null;
    } catch (_error) {
        return null;
    }
}

function writeJsonFileAtomic(path, data) {
    const file = Gio.File.new_for_path(path);
    const parent = file.get_parent();
    if (parent && !parent.query_exists(null))
        parent.make_directory_with_parents(null);
    const bytes = new TextEncoder().encode(`${JSON.stringify(data, null, 2)}\n`);
    // make_backup=true writes via a temp file + rename (atomic) and keeps a ~ backup.
    file.replace_contents(bytes, null, true, Gio.FileCreateFlags.NONE, null);
}

export default class CodexStatsPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const wrapperPath = resolveWrapperPath(this.path);

        const page = new Adw.PreferencesPage({
            title: this.metadata.name,
            icon_name: 'utilities-terminal-symbolic',
        });
        window.add(page);

        // --- Providers -------------------------------------------------------
        const providerGroup = new Adw.PreferencesGroup({
            title: _('Providers'),
            description: _('The popover shows every tracked provider at once. Pick which one the top bar shows.'),
        });
        page.add(providerGroup);

        // Index 0 is the "nothing picked yet" placeholder, so the top bar never
        // claims to show a provider the user did not choose.
        const panelProviderIds = ['', ...PROVIDERS.map(provider => provider.id)];
        const panelProviderModel = new Gtk.StringList();
        panelProviderModel.append(_('Not selected'));
        // Provider labels are product names, so they are not run through gettext.
        for (const provider of PROVIDERS)
            panelProviderModel.append(provider.label);
        const panelProviderRow = new Adw.ComboRow({
            title: _('Top bar provider'),
            subtitle: _('Whose gauges appear next to the panel icon.'),
            model: panelProviderModel,
        });
        panelProviderRow.selected = Math.max(0, panelProviderIds.indexOf(settings.get_string('panel-provider')));
        panelProviderRow.connect('notify::selected', () => {
            settings.set_string('panel-provider', panelProviderIds[panelProviderRow.selected] || '');
        });
        settings.connect('changed::panel-provider', () => {
            const index = panelProviderIds.indexOf(settings.get_string('panel-provider'));
            if (index >= 0 && index !== panelProviderRow.selected)
                panelProviderRow.selected = index;
        });
        providerGroup.add(panelProviderRow);

        for (const provider of PROVIDERS) {
            if (!provider.enabledKey)
                continue;
            const trackRow = new Adw.SwitchRow({
                title: _('Track %s').format(provider.label),
                subtitle: _('Shown when %s exists.').format(provider.defaultRoot),
            });
            settings.bind(provider.enabledKey, trackRow, 'active', Gio.SettingsBindFlags.DEFAULT);
            providerGroup.add(trackRow);
        }

        // --- Claude ----------------------------------------------------------
        const claudeGroup = new Adw.PreferencesGroup({
            title: _('Claude'),
            description: _('Token usage is read locally. Live 5h and weekly limits need the statusLine capture installed below; until then those gauges show “--”.'),
        });
        page.add(claudeGroup);

        const claudeRootRow = new Adw.EntryRow({
            title: _('Claude sessions root'),
            text: settings.get_string('claude-log-root'),
        });
        claudeRootRow.connect('changed', () => settings.set_string('claude-log-root', claudeRootRow.get_text()));
        claudeGroup.add(claudeRootRow);

        const claudeLimitsRow = new Adw.EntryRow({
            title: _('Limits capture file'),
            text: settings.get_string('claude-limits-file'),
        });
        claudeLimitsRow.connect('changed', () => settings.set_string('claude-limits-file', claudeLimitsRow.get_text()));
        claudeGroup.add(claudeLimitsRow);

        const captureRow = new Adw.ActionRow({
            title: _('Claude statusLine capture'),
            subtitle: _('Writes only rate-limit/cost numbers to the capture file. Chains any existing statusLine; reversible.'),
        });
        const captureButton = new Gtk.Button({valign: Gtk.Align.CENTER});
        captureRow.add_suffix(captureButton);
        claudeGroup.add(captureRow);

        const currentStatusLineCommand = () => {
            const data = readJsonFile(claudeSettingsPath());
            if (data && data.statusLine && typeof data.statusLine.command === 'string')
                return data.statusLine.command;
            return '';
        };
        const refreshCaptureButton = () => {
            captureButton.label = currentStatusLineCommand().includes(WRAPPER_MARKER) ? _('Uninstall') : _('Install');
        };
        refreshCaptureButton();

        captureButton.connect('clicked', () => {
            const path = claudeSettingsPath();
            const data = readJsonFile(path);
            if (data === null) {
                window.add_toast(new Adw.Toast({title: _('Could not parse ~/.claude/settings.json — aborted.')}));
                return;
            }
            const current = (data.statusLine && typeof data.statusLine.command === 'string') ? data.statusLine.command : '';
            try {
                if (current.includes(WRAPPER_MARKER)) {
                    // Reverse: restore the chained prior command, else drop statusLine.
                    const marker = ' -- ';
                    const index = current.indexOf(marker);
                    if (index >= 0)
                        data.statusLine = {type: 'command', command: current.slice(index + marker.length)};
                    else
                        delete data.statusLine;
                    writeJsonFileAtomic(path, data);
                    window.add_toast(new Adw.Toast({title: _('Capture removed.')}));
                } else {
                    const captureFile = expandHome(settings.get_string('claude-limits-file'));
                    let command = `python3 "${wrapperPath}" --capture "${captureFile}"`;
                    if (current)
                        command = `${command} -- ${current}`;
                    data.statusLine = {type: 'command', command};
                    writeJsonFileAtomic(path, data);
                    window.add_toast(new Adw.Toast({title: _('Installed — start a Claude session to capture limits.')}));
                }
                refreshCaptureButton();
            } catch (error) {
                window.add_toast(new Adw.Toast({title: _('Write failed: %s').format(error.message || String(error))}));
            }
        });

        const onlineRow = new Adw.SwitchRow({
            title: _('Fetch live limits online'),
            subtitle: _('Opt-in: makes a network request to the Anthropic usage API using your local Claude login. Adds live 5h/weekly plus per-model Fable/Opus limits, fresh even with no Claude session open. Only numeric usage is read; the token is never stored or sent elsewhere. Off keeps the extension fully local.'),
        });
        settings.bind('claude-online-usage', onlineRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        claudeGroup.add(onlineRow);

        // --- Codex -----------------------------------------------------------
        const dataGroup = new Adw.PreferencesGroup({
            title: _('Codex'),
            description: _('Codex usage is read from local token_count and rate-limit metadata.'),
        });
        page.add(dataGroup);

        const logRootRow = new Adw.EntryRow({
            title: _('Codex log root'),
            text: settings.get_string('log-root'),
        });
        logRootRow.connect('changed', () => {
            settings.set_string('log-root', logRootRow.get_text());
        });
        dataGroup.add(logRootRow);

        const cacheRow = new Adw.SwitchRow({
            title: _('Cache parsed metadata'),
            subtitle: _('Speeds up refreshes without storing prompts or message text.'),
        });
        settings.bind('cache-enabled', cacheRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        dataGroup.add(cacheRow);

        const accountLimitsRow = new Adw.SwitchRow({
            title: _('Realtime account limits'),
            subtitle: _('Uses the local Codex CLI to refresh 5h and weekly percentages when available.'),
        });
        settings.bind('account-limits-enabled', accountLimitsRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        dataGroup.add(accountLimitsRow);

        // --- Grok ------------------------------------------------------------
        const grokGroup = new Adw.PreferencesGroup({
            title: _('Grok'),
            description: _('Only per-turn token counts and the weekly credit percentage are read; prompt and chat files under this root are never opened.'),
        });
        page.add(grokGroup);

        const grokRootRow = new Adw.EntryRow({
            title: _('Grok home directory'),
            text: settings.get_string('grok-log-root'),
        });
        grokRootRow.connect('changed', () => settings.set_string('grok-log-root', grokRootRow.get_text()));
        grokGroup.add(grokRootRow);

        // --- OpenCode --------------------------------------------------------
        const openCodeGroup = new Adw.PreferencesGroup({
            title: _('OpenCode'),
            description: _('Only numeric token counts are read from the local OpenCode database; message text is never queried. OpenCode publishes no rate limits, so only token history is shown.'),
        });
        page.add(openCodeGroup);

        const openCodeRootRow = new Adw.EntryRow({
            title: _('OpenCode data directory'),
            text: settings.get_string('opencode-log-root'),
        });
        openCodeRootRow.connect('changed', () => settings.set_string('opencode-log-root', openCodeRootRow.get_text()));
        openCodeGroup.add(openCodeRootRow);

        const refreshGroup = new Adw.PreferencesGroup({
            title: _('Refresh'),
        });
        page.add(refreshGroup);

        const refreshRow = new Adw.SpinRow({
            title: _('Refresh interval'),
            subtitle: _('Seconds between helper refreshes.'),
            adjustment: new Gtk.Adjustment({
                lower: 10,
                upper: 3600,
                step_increment: 5,
                page_increment: 60,
                value: settings.get_int('refresh-interval'),
            }),
        });
        settings.bind('refresh-interval', refreshRow.adjustment, 'value', Gio.SettingsBindFlags.DEFAULT);
        refreshGroup.add(refreshRow);

        const panelGroup = new Adw.PreferencesGroup({
            title: _('Top Bar'),
        });
        page.add(panelGroup);

        const panelUsageRow = new Adw.SwitchRow({
            title: _('Show usage next to icon'),
            subtitle: _('Displays the top bar provider’s remaining percentages, or its token total when it has no rate limits.'),
        });
        settings.bind('panel-show-usage', panelUsageRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        panelGroup.add(panelUsageRow);

        const privacyGroup = new Adw.PreferencesGroup({
            title: _('Privacy'),
        });
        page.add(privacyGroup);

        const privacyRow = new Adw.ActionRow({
            title: _('Local metadata only'),
            subtitle: _('No prompt, response, file, cookie, or API token display.'),
        });
        privacyRow.add_prefix(new Gtk.Image({icon_name: 'changes-prevent-symbolic'}));
        privacyGroup.add(privacyRow);
    }
}
