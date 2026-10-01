import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const XML = `<node><interface name="org.jarvis.WindowControl">
<method name="List"><arg type="s" direction="out"/></method>
<method name="Close"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="b" direction="out"/></method>
</interface></node>`;

export default class JarvisWindowControl extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._dbus.export(Gio.DBus.session, '/org/gnome/Shell/Extensions/Jarvis');
    }

    _windows() {
        return global.get_window_actors().map(actor => actor.meta_window)
            .filter(window => window && !window.is_override_redirect() &&
                !window.is_skip_taskbar() && window.get_pid() > 0 && window.can_close());
    }

    List() {
        const tracker = Shell.WindowTracker.get_default();
        return JSON.stringify(this._windows().map(window => {
            const app = tracker.get_window_app(window);
            return {id: String(window.get_stable_sequence()), pid: window.get_pid(),
                app_id: app?.get_id() ?? '', name: app?.get_name() ?? 'Aplicativo'};
        }));
    }

    Close(id, pid) {
        const window = this._windows().find(candidate =>
            String(candidate.get_stable_sequence()) === id && candidate.get_pid() === pid);
        if (!window)
            return false;
        window.delete(global.get_current_time());
        return true;
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
    }
}
