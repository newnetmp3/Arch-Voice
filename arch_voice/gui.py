"""GTK 4 / libadwaita front-end."""

from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, presets  # noqa: E402
from .client import DaemonError, call  # noqa: E402

APP_ID = "io.github.newnetmp3.ArchVoice"

CATEGORY_ICONS = {
    "Voice": "audio-input-microphone-symbolic",
    "Creatures": "face-devilish-symbolic",
    "Robotic": "applications-science-symbolic",
    "Comms": "call-start-symbolic",
    "Spaces": "weather-overcast-symbolic",
    "Fun": "face-smile-big-symbolic",
    "Utility": "preferences-system-symbolic",
}

CSS = """
.preset-card { padding: 12px 14px; border-radius: 12px; }
.preset-card .preset-title { font-weight: bold; }
.preset-card .preset-desc { font-size: smaller; opacity: 0.75; }
.preset-card.active {
    background-color: alpha(@accent_bg_color, 0.22);
    box-shadow: inset 0 0 0 2px @accent_bg_color;
}
.category-title { font-weight: 800; font-size: larger; margin-top: 12px; }
.value-label { font-feature-settings: "tnum"; min-width: 64px; }
"""


def run_async(fn, *args, done=None, error=None):
    """Run a (blocking) daemon call off the main thread."""
    def worker():
        try:
            result = fn(*args)
        except Exception as e:  # surfaced to the UI
            if error:
                GLib.idle_add(error, e)
            return
        if done:
            GLib.idle_add(done, result)
    threading.Thread(target=worker, daemon=True).start()


class PresetCard(Gtk.Button):
    def __init__(self, preset: presets.Preset):
        super().__init__()
        self.preset = preset
        self.add_css_class("card")
        self.add_css_class("preset-card")
        self.set_tooltip_text(preset.description)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        icon = Gtk.Image.new_from_icon_name(
            preset.icon if preset.icon != "audio-input-microphone-symbolic"
            else CATEGORY_ICONS.get(preset.category, preset.icon))
        icon.set_pixel_size(24)
        box.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=preset.name, xalign=0)
        title.add_css_class("preset-title")
        desc = Gtk.Label(label=preset.description, xalign=0, wrap=True, max_width_chars=28,
                         lines=2, ellipsize=3)
        desc.add_css_class("preset-desc")
        text.append(title)
        text.append(desc)
        box.append(text)
        self.set_child(box)
        self.set_size_request(250, -1)

    def set_active(self, active: bool):
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")

    def matches(self, query: str) -> bool:
        q = query.lower()
        p = self.preset
        return not q or q in p.name.lower() or q in p.description.lower() or q in p.category.lower()


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Arch Voice")
        self.set_default_size(1100, 720)
        self.set_size_request(360, 480)
        self.status: dict = {}
        self.cards: dict[str, PresetCard] = {}
        self.sections: list[tuple[Gtk.Widget, Gtk.FlowBox]] = []
        self._param_timers: dict[str, int] = {}
        self._updating = False
        self._sources: list[dict] = []

        self.query = ""
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END,
                                          min_sidebar_width=320, max_sidebar_width=400)
        self.split.set_content(self._build_presets())
        self.split.set_sidebar(self._build_sidebar())

        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.set_content(self.split)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.refresh(initial=True)
        GLib.timeout_add_seconds(3, self._poll)

    # -- construction ------------------------------------------------------

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Arch Voice", subtitle="Starting…")
        header.set_title_widget(self.title)

        self.search = Gtk.SearchEntry(placeholder_text="Search presets")
        self.search.connect("search-changed", self._on_search)
        header.pack_start(self.search)

        menu = Gio.Menu()
        menu.append("High-quality pitch shifting", "win.hq")
        menu.append("Reset preset parameters", "win.reset")
        menu.append("Stop voice changer", "win.stop")
        menu.append("About Arch Voice", "win.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                  tooltip_text="Menu")
        header.pack_end(menu_btn)

        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic",
                                            tooltip_text="Show settings")
        self.sidebar_btn.bind_property("active", self.split, "show-sidebar",
                                       GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_end(self.sidebar_btn)
        self.sidebar_btn.set_active(True)

        self.monitor_btn = Gtk.ToggleButton(icon_name="audio-headphones-symbolic",
                                            tooltip_text="Hear yourself (use headphones!)")
        self.monitor_btn.connect("toggled", self._on_monitor)
        header.pack_end(self.monitor_btn)

        self.hq_action = Gio.SimpleAction.new_stateful("hq", None, GLib.Variant.new_boolean(False))
        self.hq_action.connect("activate", self._on_hq)
        self.add_action(self.hq_action)
        for name, cb in (("reset", self._on_reset), ("stop", self._on_stop),
                         ("about", self._on_about)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
        return header

    def _build_presets(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        for cat, items in presets.by_category().items():
            head = Gtk.Box(spacing=8)
            head.append(Gtk.Image.new_from_icon_name(CATEGORY_ICONS[cat]))
            lbl = Gtk.Label(label=cat, xalign=0)
            lbl.add_css_class("category-title")
            head.append(lbl)
            head.set_margin_top(8)
            box.append(head)

            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               column_spacing=10, row_spacing=10, min_children_per_line=1,
                               max_children_per_line=6)
            for pr in items:
                card = PresetCard(pr)
                card.connect("clicked", self._on_preset_clicked)
                self.cards[pr.id] = card
                flow.append(card)
            flow.set_filter_func(lambda child: child.get_child().matches(self.query))
            box.append(flow)
            self.sections.append((head, flow))

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(box)
        return scroller

    def _build_sidebar(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        self.active_group = Adw.PreferencesGroup(title="Active preset")
        self.active_row = Adw.ActionRow(title="—", subtitle="")
        self.active_row.add_prefix(Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic"))
        self.active_group.add(self.active_row)
        page.add(self.active_group)

        self.params_group = Adw.PreferencesGroup(title="Adjust")
        self.param_rows: list[Gtk.Widget] = []
        page.add(self.params_group)

        io = Adw.PreferencesGroup(title="Microphone")
        self.input_model = Gtk.StringList()
        self.input_row = Adw.ComboRow(title="Input device", model=self.input_model)
        self.input_row.connect("notify::selected", self._on_input_selected)
        io.add(self.input_row)
        hint = Adw.ActionRow(
            title="Use “Arch Voice Mic” in your apps",
            subtitle="Pick it as the microphone in Discord, OBS, games or your browser.")
        hint.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        io.add(hint)
        page.add(io)
        return page

    # -- state sync --------------------------------------------------------

    def refresh(self, initial: bool = False):
        def fetch():
            st = call("status")
            srcs = call("sources")
            return st, srcs
        if initial:
            self.title.set_subtitle("Starting voice changer…")
        run_async(fetch, done=self._apply_status, error=self._on_error)

    def _poll(self):
        run_async(lambda: call("status", autostart=False), done=self._apply_status_only,
                  error=lambda e: None)
        return True

    def _apply_status(self, result):
        st, srcs = result
        self._sources = srcs
        self._updating = True
        try:
            self.input_model.splice(0, self.input_model.get_n_items(),
                                    ["System default"] + [s["description"] for s in srcs])
            names = [s["name"] for s in srcs]
            idx = names.index(st["input"]) + 1 if st.get("input") in names else 0
            self.input_row.set_selected(idx)
        finally:
            self._updating = False
        self._apply_status_only(st, force=True)

    def _apply_status_only(self, st, force: bool = False):
        old = self.status
        self.status = st
        preset_id = st.get("preset")
        pr = presets.PRESETS.get(preset_id or "")
        self.title.set_subtitle(f"Active: {pr.name}" if pr else "Not running")
        for pid, card in self.cards.items():
            card.set_active(pid == preset_id)
        self._updating = True
        try:
            self.monitor_btn.set_active(bool(st.get("monitor")))
            self.hq_action.set_state(GLib.Variant.new_boolean(st.get("pitch_quality") == "hq"))
        finally:
            self._updating = False
        if force or old.get("preset") != preset_id or (
                old.get("values") != st.get("values") and not self._param_timers):
            self._rebuild_params(pr, st.get("values") or {})

    def _rebuild_params(self, pr, values):
        for row in self.param_rows:
            self.params_group.remove(row)
        self.param_rows = []
        if not pr:
            self.active_row.set_title("—")
            self.active_row.set_subtitle("")
            return
        self.active_row.set_title(pr.name)
        self.active_row.set_subtitle(pr.description)
        for prm in pr.all_params():
            row = Adw.PreferencesRow(activatable=False)
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                           margin_start=12, margin_end=12, margin_top=8, margin_bottom=4)
            top = Gtk.Box(spacing=6)
            top.append(Gtk.Label(label=prm.label, xalign=0, hexpand=True))
            adj = Gtk.Adjustment(value=values.get(prm.key, prm.default), lower=prm.min,
                                 upper=prm.max, step_increment=prm.step,
                                 page_increment=prm.step * 5)
            scale = Gtk.Scale(adjustment=adj, digits=prm.digits, draw_value=False, hexpand=True)
            if prm.min < prm.default < prm.max:
                scale.add_mark(prm.default, Gtk.PositionType.BOTTOM, None)
            value_lbl = Gtk.Label(xalign=1)
            value_lbl.add_css_class("value-label")
            value_lbl.add_css_class("dim-label")
            top.append(value_lbl)
            body.append(top)
            body.append(scale)
            row.set_child(body)

            def fmt(adj=adj, prm=prm, lbl=value_lbl):
                lbl.set_text(f"{adj.get_value():.{prm.digits}f}{prm.unit}")
            fmt()
            adj.connect("value-changed", lambda a, prm=prm, fmt=fmt: (fmt(), self._on_param(prm, a)))
            self.params_group.add(row)
            self.param_rows.append(row)

    # -- handlers ----------------------------------------------------------

    def _on_error(self, e):
        self.title.set_subtitle("Error")
        self.toast(str(e), timeout=8)

    def toast(self, text: str, timeout: int = 3):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def _on_search(self, entry):
        query = self.query = entry.get_text()
        for head, flow in self.sections:
            flow.invalidate_filter()
            visible = any(c.get_child().matches(query) for c in _children(flow))
            head.set_visible(visible)
            flow.set_visible(visible)

    def _on_preset_clicked(self, card: PresetCard):
        pr = card.preset
        for pid, c in self.cards.items():
            c.set_active(pid == pr.id)
        self.title.set_subtitle(f"Switching to {pr.name}…")
        run_async(lambda: call("apply", preset=pr.id, values={}),
                  done=lambda st: self._apply_status_only(st, force=True),
                  error=self._on_error)

    def _on_param(self, prm, adj):
        if self._updating:
            return
        old = self._param_timers.pop(prm.key, None)
        if old:
            GLib.source_remove(old)

        def fire():
            self._param_timers.pop(prm.key, None)
            run_async(lambda: call("set", key=prm.key, value=adj.get_value()),
                      error=self._on_error)
            return False
        self._param_timers[prm.key] = GLib.timeout_add(120, fire)

    def _on_input_selected(self, row, _pspec):
        if self._updating:
            return
        idx = row.get_selected()
        name = None if idx == 0 or idx > len(self._sources) else self._sources[idx - 1]["name"]
        run_async(lambda: call("input", name=name),
                  done=lambda st: self.toast("Microphone changed"), error=self._on_error)

    def _on_monitor(self, btn):
        if self._updating:
            return
        on = btn.get_active()
        run_async(lambda: call("monitor", on=on),
                  done=lambda st: self.toast("Monitoring on — use headphones" if on
                                             else "Monitoring off"),
                  error=self._on_error)

    def _on_hq(self, action, _param):
        new = not action.get_state().get_boolean()
        action.set_state(GLib.Variant.new_boolean(new))
        run_async(lambda: call("quality", value="hq" if new else "fast"),
                  done=lambda st: self.toast("High-quality pitch shifting " +
                                             ("on" if new else "off")),
                  error=self._on_error)

    def _on_reset(self, *_):
        run_async(lambda: call("reset"), done=lambda st: self._apply_status_only(st, force=True),
                  error=self._on_error)

    def _on_stop(self, *_):
        def done(_):
            self.title.set_subtitle("Stopped")
            for c in self.cards.values():
                c.set_active(False)
            self.toast("Voice changer stopped. Pick a preset to start again.")
            self.status = {}
        run_async(lambda: call("quit", autostart=False), done=done, error=self._on_error)

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="Arch Voice", application_icon="audio-input-microphone",
            version=__version__, developer_name="Arch Voice contributors",
            license_type=Gtk.License.MIT_X11,
            comments=f"Real-time voice changer for PipeWire with {len(presets.PRESETS)} presets.",
            website="https://github.com/newnetmp3/Arch-Voice")
        about.present(self)


def _children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        child = child.get_next_sibling()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


def main() -> int:
    try:
        return App().run([sys.argv[0]])
    except DaemonError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
