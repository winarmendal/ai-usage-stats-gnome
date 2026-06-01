import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';

import {ExtensionPreferences, gettext as _} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class CodexStatsPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage({
            title: _('Codex Stats'),
            icon_name: 'utilities-terminal-symbolic',
        });
        window.add(page);

        const dataGroup = new Adw.PreferencesGroup({
            title: _('Data'),
            description: _('Codex Stats reads local token_count and rate-limit metadata from Codex logs.'),
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
            subtitle: _('Displays 5h and weekly remaining percentages in the top bar.'),
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
