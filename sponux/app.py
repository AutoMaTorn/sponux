"""GTK4 UI and application lifecycle for sponux."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, Gio, GLib  # noqa: E402

from . import config, indexer, placement, usage, userconfig  # noqa: E402
from .providers import apps as apps_provider  # noqa: E402
from .providers import base  # noqa: E402
from .providers import calc as calc_provider  # noqa: E402
from .providers import files as files_provider  # noqa: E402

PLACEHOLDER = "Search apps, files, or calculate…"

_KIND_LABEL = {"app": "App", "file": "File", "calc": "Calc",
               "openwith": "Open with", "remember": "Rule"}

# Hints shown under the results, built from the bindings actually in force —
# see _hints(). They used to be literals, which quietly became wrong the moment
# [keys] could change them.
_HINTS_OPEN_WITH = "↩ open with it   type to filter   Esc back"
# Shown while the query is empty, and gone the moment anything is typed. The
# prefixes below are the only part of the interface that has to be known in
# advance, so this is where they are taught.
_HINTS_EMPTY = "f: files   a: apps   = calculate   / or ~/ a path"

# A leading prefix narrows the search to one kind of result. Short, because
# they are typed ahead of every query that uses them.
_KIND_PREFIXES = {
    "f:": "file", "file:": "file",
    "a:": "app", "app:": "app",
    "c:": "calc", "=": "calc",
}
_KIND_ONLY = {"file": "files only", "app": "apps only",
              "calc": "calculator only"}

# Shown when the icon a result asked for is missing from the icon theme —
# without this, GTK renders the "broken image" glyph.
_KIND_FALLBACK_ICON = {
    "app": "application-x-executable",
    "openwith": "application-x-executable",
    "file": "text-x-generic",
    "calc": "accessories-calculator",
    "remember": "checkbox-symbolic",
}


def _split_kind_prefix(text):
    """Split a leading kind prefix off the query: ("file", "conf") for "f:conf".

    Longest first, so "file:" is not read as "f" followed by "ile:".
    """
    stripped = text.lstrip()
    for prefix in sorted(_KIND_PREFIXES, key=len, reverse=True):
        if stripped.startswith(prefix):
            return _KIND_PREFIXES[prefix], stripped[len(prefix):]
    return None, text


class SponuxWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="sponux")
        # [window] and [keys], re-read on every open by reset_and_present() so
        # that editing config.toml needs no restart.
        self.settings = userconfig.window_settings()
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(self.settings.width, -1)
        self.add_css_class("sponux")
        self._results = []

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("sponux-card")
        self.set_child(card)

        self.entry = Gtk.Entry()
        self.entry.add_css_class("sponux-search")
        self.entry.set_placeholder_text(PLACEHOLDER)
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("activate", self._activate_selected)
        card.append(self.entry)

        self.sep = Gtk.Box()
        self.sep.add_css_class("sponux-sep")
        card.append(self.sep)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_propagate_natural_height(True)
        self.scroller.set_max_content_height(420)
        card.append(self.scroller)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("sponux-results")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.scroller.set_child(self.listbox)

        self.hints = Gtk.Label(label="", xalign=0.0)
        self.hints.add_css_class("sponux-hints")
        card.append(self.hints)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        # Capture, so the launcher's own bindings win over the Entry's. Ctrl+C
        # in particular: grab_focus() selects the query text, and the Entry
        # would otherwise copy that instead of the selected file's path.
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(key)

        # Tell the WM this is a floating dialog *before* it is first mapped,
        # so tiling WMs don't tile the card. See placement.py.
        self.connect("realize", lambda _w: placement.prepare(self))

        # Connected unconditionally; whether it acts is decided in the handler,
        # so [window] hide_on_focus_loss can be changed without a restart.
        self.connect("notify::is-active", self._on_active_changed)

        # Only arm the focus-loss handler once the window has actually been
        # focused; it starts out inactive, and hiding on that would close the
        # window the instant it opens.
        self._was_active = False
        self._debounce_id = 0
        # Which kind of result the query's prefix restricts us to, if any.
        self._kind_filter = None
        # "search", or "open-with" while the list is showing applications to
        # open one particular file with.
        self._mode = "search"
        self._open_with = None
        self._suspended = None   # the search to come back to from open-with
        self._show_results(False)

    # ---- search -------------------------------------------------------

    def _on_changed(self, _entry):
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(self.settings.debounce,
                                             self._do_search)

    def _do_search(self):
        self._debounce_id = 0
        raw = self.entry.get_text()
        if self._mode == "open-with":
            self._populate(self._open_with_rows(raw))
            return GLib.SOURCE_REMOVE
        kind, query = _split_kind_prefix(raw)
        self._kind_filter = kind
        results = []
        limit = self.settings.max_results
        if query.strip():
            # Every provider is asked for the full list; the merge below keeps
            # the best of them, so one knob decides how many rows are shown.
            if kind in (None, "calc"):
                results += calc_provider.search(query)
            if kind in (None, "app"):
                results += apps_provider.search(query, limit)
            if kind in (None, "file"):
                results += files_provider.search(query, limit)
                results += files_provider.search_path(query, limit)
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[:limit]
        self._populate(results)
        return GLib.SOURCE_REMOVE

    def _populate(self, results):
        self._results = results
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt

        for res in results:
            self.listbox.append(self._make_row(res))

        self._show_results(bool(results))
        if results:
            first = self.listbox.get_row_at_index(0)
            if first is not None:
                self.listbox.select_row(first)
        self._update_hints()

    def _make_row(self, res):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("sponux-row")

        img = self._icon_image(res.icon, res.kind)
        box.append(img)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=res.title, xalign=0.0)
        title.add_css_class("sponux-title")
        title.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        text.append(title)
        if res.subtitle:
            sub = Gtk.Label(label=res.subtitle, xalign=0.0)
            sub.add_css_class("sponux-subtitle")
            sub.set_ellipsize(3)
            text.append(sub)
        box.append(text)

        badge = Gtk.Label(label=_KIND_LABEL.get(res.kind, ""))
        badge.add_css_class("sponux-badge")
        badge.set_valign(Gtk.Align.CENTER)
        box.append(badge)

        row.set_child(box)
        return row

    @staticmethod
    def _icon_image(icon, kind=""):
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display) if display else None
        img = None
        if isinstance(icon, str):
            if theme is None or theme.has_icon(icon):
                img = Gtk.Image.new_from_icon_name(icon)
        elif isinstance(icon, Gio.Icon):
            if theme is None or theme.has_gicon(icon):
                img = Gtk.Image.new_from_gicon(icon)
        if img is None:
            img = Gtk.Image.new_from_icon_name(
                _KIND_FALLBACK_ICON.get(kind, "application-x-executable")
            )
        img.set_pixel_size(32)
        return img

    def _show_results(self, visible):
        self.sep.set_visible(visible)
        self.scroller.set_visible(visible)

    def _selected(self):
        row = self.listbox.get_selected_row()
        if row is None:
            return None
        index = row.get_index()
        return self._results[index] if 0 <= index < len(self._results) else None

    def _update_hints(self):
        """Say what the modifiers do, for whatever is selected right now."""
        if self._mode == "open-with":
            self.hints.set_text(_HINTS_OPEN_WITH)
            self.hints.set_visible(True)
            return
        # Nothing typed yet: teach the prefixes instead of the modifiers, since
        # there is nothing selected for a modifier to act on.
        if not self.entry.get_text():
            self.hints.set_text(_HINTS_EMPTY)
            self.hints.set_visible(True)
            return
        result = self._selected()
        # A filter with no matches still has to say it is filtering, or an
        # empty card looks like the search is broken.
        self.hints.set_visible(result is not None or bool(self._kind_filter))
        parts = []
        if self._kind_filter:
            parts.append(_KIND_ONLY[self._kind_filter])
        if result is not None:
            parts.append(self._hints_for(result.kind))
        self.hints.set_text("   ·   ".join(parts))

    def _label(self, action):
        """The binding for an action, written the way a user would read it."""
        keyval, mods = self.settings.keys[action]
        return Gtk.accelerator_get_label(keyval, mods)

    def _hints_for(self, kind):
        """The keys that apply to this kind of result, as currently bound."""
        close = f"{self._label('close')} close"
        if kind == "file":
            # Enter is not configurable, so it stays the symbol it always was.
            return (f"↩ open   {self._label('reveal')} containing folder   "
                    f"{self._label('copy_path')} copy path   "
                    f"{self._label('open_with')} open with…")
        if kind == "calc":
            return f"↩ copy result   {close}"
        return f"↩ run   {close}"

    # ---- open with ----------------------------------------------------

    def _enter_open_with(self, result):
        """Show the applications registered for this file, instead of results.

        Deliberately a mode inside the same window rather than a dialog: the
        launcher is a keyboard tool, and Escape has to come straight back to
        the search that was under way.
        """
        apps = files_provider.apps_for(result.path, result.is_dir)
        if not apps:
            return False
        self._suspended = (self.entry.get_text(), self._results)
        self._mode = "open-with"
        self._open_with = {"result": result, "apps": apps, "remember": False}
        self.entry.set_placeholder_text(f"Open {result.title} with…")
        self.entry.set_text("")
        self._populate(self._open_with_rows(""))
        return True

    def _leave_open_with(self):
        text, results = self._suspended or ("", [])
        self._reset_mode()
        self.entry.set_text(text)
        self._populate(results)

    def _reset_mode(self):
        self._mode = "search"
        self._open_with = None
        self._suspended = None
        self._kind_filter = None
        self.entry.set_placeholder_text(PLACEHOLDER)

    def _open_with_rows(self, query):
        """The application list, plus the switch that turns a choice into a
        rule. Nothing is written to config.toml unless that switch is on."""
        state = self._open_with
        if state is None:
            return []
        result = state["result"]
        remember = state["remember"]
        table, key = userconfig.rule_for(result.path, result.is_dir)

        rows = [base.Result(
            title=f"Remember the choice for {key}",
            subtitle=("on — the application picked below is written to "
                      f"config.toml as [{table}] {key}"
                      if remember else
                      f"off — press Enter to also save it as [{table}] {key}"),
            icon=("checkbox-checked-symbolic" if remember
                  else "checkbox-symbolic"),
            action=self._toggle_remember,
            kind="remember",
        )]

        q = query.strip()
        for app in state["apps"]:
            name = app.get_display_name() or app.get_name() or ""
            if q and base.fuzzy_score(q, name) <= 0:
                continue
            command = files_provider.command_for(app)
            rows.append(base.Result(
                title=name,
                subtitle=(f'saves {key} = "{command}"' if remember
                          else command or app.get_description() or ""),
                icon=app.get_icon() or "application-x-executable",
                action=(lambda a=app, r=result, keep=remember:
                        self._activate_open_with(a, r, keep)),
                kind="openwith",
            ))
            # The tail of the list is every other installed application; it is
            # there to be filtered, not scrolled.
            if len(rows) > self.settings.max_results:
                break
        return rows

    def _toggle_remember(self):
        if self._open_with is not None:
            self._open_with["remember"] = not self._open_with["remember"]
            self._populate(self._open_with_rows(self.entry.get_text()))

    def _activate_open_with(self, app, result, remember):
        usage.record(result.usage_key)
        files_provider.open_with(app, result.path)
        if not remember:
            return
        command = files_provider.command_for(app)
        written = userconfig.remember_opener(result.path, result.is_dir, command)
        if written:
            print(f"sponux: wrote {written} to {userconfig.CONFIG_FILE}")

    # ---- activation & keys -------------------------------------------

    def _on_row_activated(self, _listbox, row):
        self._run(row.get_index())

    def _activate_selected(self, *_):
        row = self.listbox.get_selected_row()
        if row is not None:
            self._run(row.get_index())

    def _run(self, index):
        if not 0 <= index < len(self._results):
            return
        result = self._results[index]
        if result.kind == "remember":
            # A switch, not a result: the window has to stay where it is.
            if result.action is not None:
                result.action()
            return
        # Counted here rather than in the providers, so that everything the
        # user actually opens counts once, whatever opened it.
        usage.record(result.usage_key)
        self.hide_launcher()
        if result.action is not None:
            result.action()

    def _reveal_selected(self):
        result = self._selected()
        if result is None or not result.path:
            return False
        self.hide_launcher()
        files_provider.reveal(result.path)
        return True

    def _copy_selected_path(self):
        result = self._selected()
        if result is None or not result.path:
            return False
        base.copy_text(result.path)
        self.hide_launcher()
        return True

    def _open_with_selected(self):
        result = self._selected()
        if result is None or not result.path:
            return False
        return bool(self._enter_open_with(result))

    def _move_selection(self, delta):
        n = len(self._results)
        if n == 0:
            return
        row = self.listbox.get_selected_row()
        cur = row.get_index() if row is not None else -1
        nxt = max(0, min(n - 1, cur + delta))
        target = self.listbox.get_row_at_index(nxt)
        if target is not None:
            self.listbox.select_row(target)
            target.grab_focus()
            self.entry.grab_focus_without_selecting()
        self._update_hints()

    def _pressed_keyvals(self, keyval, keycode):
        """Every keyval a binding could reasonably mean by this keypress.

        A binding is written for a layout — `<Ctrl>c` names the Latin letter.
        With a second layout active the same physical key delivers something
        else entirely: under `us,ru`, C arrives as Cyrillic_es, and comparing
        keyvals alone silently kills every letter shortcut. So the physical key
        is resolved back through the keymap and its unshifted keyval in each
        layout counts as a match too.

        Costs 7 µs per keypress, measured.
        """
        found = {keyval, Gdk.keyval_to_lower(keyval)}
        display = self.get_display()
        if display is not None and keycode:
            ok, keys, keyvals = display.map_keycode(keycode)
            if ok:
                found.update(kv for key, kv in zip(keys, keyvals)
                             if key.level == 0)
        return found

    def _matches(self, action, keyvals, mods):
        """Is this keypress the binding configured for `action`?"""
        want_key, want_mods = self.settings.keys[action]
        return want_key in keyvals and mods == want_mods

    def _on_key(self, _controller, keyval, keycode, state):
        # Strip the locks GTK does not count as part of a shortcut, and treat
        # the keypad's Enter as Return, which is what people write in a binding.
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_KP_Enter:
            keyval = Gdk.KEY_Return
        keyvals = self._pressed_keyvals(keyval, keycode)

        if self._matches("close", keyvals, mods) or keyval == Gdk.KEY_Escape:
            # Escape closes whatever [keys] says, so that a typo in the config
            # cannot leave the launcher with no way out.
            #
            # In open-with, it backs out to the search it interrupted; only
            # then does it close the launcher.
            if self._mode == "open-with":
                self._leave_open_with()
            else:
                self.hide_launcher()
            return True
        if self._matches("quit", keyvals, mods):
            self.get_application().quit()
            return True
        if self._matches("copy_path", keyvals, mods):
            # Falls through to the Entry's own copy when no file is selected.
            if self._copy_selected_path():
                return True
            return False
        if self._matches("reveal", keyvals, mods):
            if self._reveal_selected():
                return True
        if self._matches("open_with", keyvals, mods):
            if self._open_with_selected():
                return True

        # Not configurable: these are what make it a launcher rather than a
        # keymap, and rebinding them is how you lock yourself out.
        if keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True
        if keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True
        if keyval == Gdk.KEY_Return:
            self._activate_selected()
            return True
        return False

    # ---- show / hide --------------------------------------------------

    def _on_active_changed(self, *_):
        if not self.settings.hide_on_focus_loss:
            return
        if self.is_active():
            self._was_active = True
        elif self._was_active:
            self._was_active = False
            self.hide_launcher()

    def hide_launcher(self):
        """The single way the window goes away, from Escape, focus loss,
        activating a result, or the toggle hotkey."""
        self._was_active = False
        self._reset_mode()
        self.set_visible(False)

    def reset_and_present(self):
        # Pick up config.toml before anything is drawn, so an edit applies the
        # next time the launcher opens rather than the next time it restarts.
        # settings() only re-reads when the file's mtime changed.
        self.settings = userconfig.window_settings()
        self.set_default_size(self.settings.width, -1)
        self._reset_mode()
        self.entry.set_text("")
        self._populate([])
        self.present()
        self.entry.grab_focus()
        # The WM chooses a position when it maps the window, so we can only
        # override it afterwards. Idle covers the common case; the follow-up
        # tick covers WMs that place the window a frame or two later.
        GLib.idle_add(self._center)
        GLib.timeout_add(40, self._center)

    def _center(self):
        placement.center(self)
        placement.take_input(self)
        return GLib.SOURCE_REMOVE


class SponuxApp(Gtk.Application):
    def __init__(self, start_hidden=False):
        super().__init__(application_id=config.APP_ID)
        self.window = None
        self._css_providers = []
        # --daemon: come up resident without showing anything. Set on the
        # first activation only, which is the one run() performs itself.
        self._start_hidden = start_hidden

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # stay resident so re-launch shows the window instantly
        self._load_css()
        indexer.start_background()

    def do_activate(self):
        if self.window is None:
            self.window = SponuxWindow(self)
            self.window.connect("close-request", self._on_close)
        if self._start_hidden:
            # Started at login. The window is built but never presented, so
            # the first real open has nothing left to construct.
            self._start_hidden = False
            return
        # Pick up edits to style.css without a restart — the daemon is
        # long-lived, so otherwise restyling it would mean killing it after
        # every change.
        if userconfig.style_changed():
            self._load_css()

        # Toggle: hide if already visible, otherwise show fresh.
        if self.window.get_visible():
            self.window.hide_launcher()
        else:
            self.window.reset_and_present()

    def _on_close(self, window):
        window.hide_launcher()
        return True  # keep the process alive as a daemon

    def _load_css(self):
        """Load the bundled stylesheet, then the user's on top of it."""
        display = Gdk.Display.get_default()
        if display is None:
            return
        for provider in self._css_providers:
            Gtk.StyleContext.remove_provider_for_display(display, provider)
        self._css_providers = []

        bundled = GLib.build_filenamev(
            [GLib.path_get_dirname(__file__), "style.css"]
        )
        self._add_css(display, bundled, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # The card is dark whatever the desktop is, so tell GTK as much: the
        # parts it draws itself — text cursor, selection, symbolic icons — take
        # their colours from this rather than from the CSS above.
        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings is not None:
            gtk_settings.set_property("gtk-application-prefer-dark-theme", True)

        # PRIORITY_USER outranks PRIORITY_APPLICATION, so the user's rules win
        # and they only need to write the ones they want to change — including
        # the variables the bundled sheet defines in :root.
        if userconfig.STYLE_FILE.exists():
            self._add_css(display, str(userconfig.STYLE_FILE),
                          Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def _add_css(self, display, path, priority):
        provider = Gtk.CssProvider()
        # A stylesheet the user is editing will sometimes be mid-edit and
        # invalid; say so on stderr instead of failing silently.
        provider.connect(
            "parsing-error",
            lambda _p, section, error: print(
                f"sponux: {path}:{section.get_start_location().lines + 1}: "
                f"{error.message}"
            ),
        )
        provider.load_from_path(path)
        Gtk.StyleContext.add_provider_for_display(display, provider, priority)
        self._css_providers.append(provider)


def main(argv=None, daemon=False):
    import sys
    # Set a clean WM_CLASS ("sponux") so tiling WMs (i3/sway) can target it
    # with floating rules; otherwise the class defaults to "__main__.py".
    GLib.set_prgname("sponux")
    GLib.set_application_name("sponux")
    if daemon and _daemon_running():
        return 0
    app = SponuxApp(start_hidden=daemon)
    return app.run(argv if argv is not None else sys.argv)


def _daemon_running():
    """Is a sponux daemon already on the session bus?

    --daemon must not go through GApplication to find out: registering and
    then bowing out forwards the activation to the running instance, which
    pops its window — the one thing an autostart entry must never do.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (config.APP_ID,)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 2000, None,
        )
        return reply.unpack()[0]
    except GLib.Error:
        return False  # no session bus: nothing can be running on it
