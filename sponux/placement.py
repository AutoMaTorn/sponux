"""Place the launcher window centred, without needing any WM configuration.

A Spotlight-style launcher has to appear as a small floating card near the
middle of the screen. Tiling window managers (i3, sway, bspwm) would instead
tile it to fill a column, and stacking WMs apply their own placement policy.
Rather than asking every user to add a WM rule, we declare what this window
is through EWMH — a dialog, kept above, absent from taskbar and pager, which
every WM floats — and then position it ourselves with plain X11 requests.

Everything here is X11-only and best effort: under Wayland without
gtk4-layer-shell, or when libX11 is unavailable, the X11 functions do nothing
and the compositor places the window itself. With gtk4-layer-shell present,
setup() turns the window into an overlay layer surface instead, which is how
a Wayland compositor is talked into floating, centring and focusing it.
"""

import ctypes
import ctypes.util

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from . import config, report  # noqa: E402

try:
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11
except (ValueError, ImportError):  # pragma: no cover - non-X11 builds
    GdkX11 = None

_XA_ATOM = 4
_PROP_MODE_REPLACE = 0
_CW_OVERRIDE_REDIRECT = 1 << 9
_REVERT_TO_PARENT = 2
_CURRENT_TIME = 0


class _XSetWindowAttributes(ctypes.Structure):
    """Layout must match Xlib's exactly — we only ever set override_redirect."""

    _fields_ = [
        ("background_pixmap", ctypes.c_ulong),
        ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong),
        ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong),
    ]

_lib = None   # ctypes libX11 handle, or False once we know it is unusable
_dpy = None   # our own Display* connection (separate from GDK's)
_atoms = {}


# ---- Wayland, via gtk4-layer-shell ------------------------------------
#
# Wayland gives a plain toplevel no placement at all — sway would tile the
# launcher. gtk4-layer-shell speaks wlr-layer-shell for us: an overlay-layer
# surface is floated, placed and focusable by protocol. The library is an
# optional runtime dependency: absent, everything falls back to "the
# compositor decides", exactly the behaviour there was before.

_layer_shell = None      # the module, or False once known to be unusable
_layer_shell_noted = False


def _ls():
    """The Gtk4LayerShell module on a session that supports it, else None."""
    global _layer_shell
    if _layer_shell is None:
        _layer_shell = False
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell
        except (ValueError, ImportError):
            Gtk4LayerShell = None
        # is_supported() checks both that this is a Wayland session and that
        # the compositor speaks the layer-shell protocol.
        if Gtk4LayerShell is not None and Gtk4LayerShell.is_supported():
            _layer_shell = Gtk4LayerShell
    return _layer_shell or None


def setup(window):
    """Make the window a layer surface on Wayland. Call before ``realize``.

    Returns True when it did. On X11, or without gtk4-layer-shell, returns
    False and the X11 path in prepare()/center()/take_input() runs instead.
    """
    global _layer_shell_noted
    ls = _ls()
    if ls is None:
        display = Gdk.Display.get_default()
        if (display is not None and type(display).__name__ == "WaylandDisplay"
                and not _layer_shell_noted):
            _layer_shell_noted = True
            report.note("Wayland without gtk4-layer-shell: the compositor "
                        "places the window; install gtk4-layer-shell for "
                        "centring and focus")
        return False
    ls.init_for_window(window)
    ls.set_layer(window, ls.Layer.OVERLAY)
    ls.set_namespace(window, "sponux")
    # ON_DEMAND, not EXCLUSIVE: take the keyboard while the launcher is up,
    # but never hold it hostage if something goes wrong with the surface.
    ls.set_keyboard_mode(window, ls.KeyboardMode.ON_DEMAND)
    return True


def _on_layer_shell(window) -> bool:
    ls = _ls()
    return ls is not None and ls.is_layer_window(window)


def _place_layer(window, ls):
    """Position the layer surface per [window] position.

    Horizontal centring comes free (no left/right anchors). "center" anchors
    nothing, which the protocol reads as centred; "top" anchors the top edge
    with a margin of top_fraction of the output's height. Which output is the
    compositor's choice — Wayland will not say where the pointer is — and is
    in practice the focused one, which is where the user is looking.
    """
    settings = getattr(window, "settings", None)
    position = settings.position if settings else config.POSITION
    top_fraction = settings.top_fraction if settings else config.TOP_FRACTION
    if position == "center":
        ls.set_anchor(window, ls.Edge.TOP, False)
        return
    surface = window.get_surface()
    monitor = (window.get_display().get_monitor_at_surface(surface)
               if surface is not None else None)
    if monitor is None:
        monitors = list(window.get_display().get_monitors())
        monitor = monitors[0] if monitors else None
    if monitor is None:
        return
    ls.set_anchor(window, ls.Edge.TOP, True)
    ls.set_margin(window, ls.Edge.TOP,
                  int(monitor.get_geometry().height * top_fraction))


# ---- raw X11 ----------------------------------------------------------


def _x11():
    """Return (libX11, Display*), or (None, None) if X11 is not usable."""
    global _lib, _dpy
    if _lib is None:
        _lib = False
        path = ctypes.util.find_library("X11")
        if path:
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                lib = None
            if lib is not None:
                _declare(lib)
                dpy = lib.XOpenDisplay(None)
                if dpy:
                    _lib, _dpy = lib, dpy
    return (_lib, _dpy) if _lib else (None, None)


def _declare(lib):
    """Set argtypes/restypes; without these, 64-bit pointers get truncated."""
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XChangeProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ]
    lib.XMoveWindow.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
    ]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XQueryPointer.restype = ctypes.c_int
    lib.XQueryPointer.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XChangeWindowAttributes.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(_XSetWindowAttributes),
    ]
    lib.XSetInputFocus.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
    ]


def _atom(name: str) -> int:
    if name not in _atoms:
        lib, dpy = _x11()
        _atoms[name] = lib.XInternAtom(dpy, name.encode(), False) if lib else 0
    return _atoms[name]


def _set_atom_property(xid: int, prop: str, values):
    lib, dpy = _x11()
    if not lib:
        return
    data = (ctypes.c_ulong * len(values))(*(_atom(v) for v in values))
    lib.XChangeProperty(
        dpy, xid, _atom(prop), _XA_ATOM, 32, _PROP_MODE_REPLACE,
        ctypes.byref(data), len(values),
    )
    lib.XFlush(dpy)


def _pointer_position():
    """Root-relative pointer position in device pixels, or None."""
    lib, dpy = _x11()
    if not lib:
        return None
    root = ctypes.c_ulong()
    child = ctypes.c_ulong()
    rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
    mask = ctypes.c_uint()
    ok = lib.XQueryPointer(
        dpy, lib.XDefaultRootWindow(dpy),
        ctypes.byref(root), ctypes.byref(child),
        ctypes.byref(rx), ctypes.byref(ry),
        ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask),
    )
    return (rx.value, ry.value) if ok else None


# ---- window helpers ---------------------------------------------------


def _xid(window):
    """The X11 window id backing a Gtk.Window, or 0 if there isn't one."""
    if GdkX11 is None:
        return 0
    surface = window.get_surface()
    if surface is None or not isinstance(surface, GdkX11.X11Surface):
        return 0
    return surface.get_xid()


def _device_rect(mon):
    """A monitor's geometry in device pixels, to compare against the pointer.

    GDK reports geometry in application pixels; XQueryPointer answers in device
    pixels. The X11 backend applies one scale factor to the whole root window,
    so scaling each monitor by its own factor lands on the same coordinates.
    """
    geo = mon.get_geometry()
    scale = mon.get_scale_factor() or 1
    return (geo.x * scale, geo.y * scale, geo.width * scale, geo.height * scale)


def _distance_to(rect, px, py):
    """Squared distance from a point to a rectangle; 0 when inside it."""
    x, y, w, h = rect
    dx = max(x - px, 0, px - (x + w - 1))
    dy = max(y - py, 0, py - (y + h - 1))
    return dx * dx + dy * dy


def _target_monitor(window):
    """The monitor holding the pointer — where the user is looking.

    With several monitors the launcher has to appear where the user's attention
    already is, which is where the pointer is, not on a fixed screen. When the
    pointer cannot be located (Wayland, no libX11, or a pointer parked on
    another X screen) we fall back to the first monitor, and when it sits in a
    gap between monitors — possible with mismatched heights or an odd RandR
    layout — we take the nearest one rather than guessing.
    """
    display = window.get_display()
    monitors = list(display.get_monitors())
    if not monitors:
        return None
    pos = _pointer_position()
    if pos is None:
        return monitors[0]
    px, py = pos
    return min(monitors, key=lambda m: _distance_to(_device_rect(m), px, py))


# ---- public API -------------------------------------------------------


def prepare(window):
    """Declare the window's role to the WM. Call once, on ``realize``.

    These properties are read when the window is mapped, so they must be set
    before the first ``present()`` — that is what makes tiling WMs float the
    card instead of tiling it.
    """
    surface = window.get_surface()
    if surface is None:
        return
    xid = _xid(window)
    if not xid:
        return
    if config.UNMANAGED_WINDOW:
        _set_override_redirect(xid)

    # Dialogs are floated by every tiling WM and skipped by window cyclers.
    # Harmless (and ignored) when the window is override-redirect, but it
    # keeps the window sane for anyone who turns UNMANAGED_WINDOW off.
    _set_atom_property(xid, "_NET_WM_WINDOW_TYPE", ["_NET_WM_WINDOW_TYPE_DIALOG"])
    # These write _NET_WM_STATE themselves, so they have to run before we do
    # — otherwise they replace the property and drop the "above" state.
    surface.set_skip_taskbar_hint(True)
    surface.set_skip_pager_hint(True)
    _set_atom_property(xid, "_NET_WM_STATE", [
        "_NET_WM_STATE_ABOVE",
        "_NET_WM_STATE_SKIP_TASKBAR",
        "_NET_WM_STATE_SKIP_PAGER",
    ])


def _set_override_redirect(xid: int):
    """Take the window out of the WM's hands, the way rofi's window is.

    An override-redirect window is never managed, focused or decorated by the
    window manager, so a tiling WM keeps its focus highlight on whatever you
    were working in. The price is that we must claim keyboard input ourselves
    — see take_input().
    """
    lib, dpy = _x11()
    if not lib:
        return
    attrs = _XSetWindowAttributes()
    attrs.override_redirect = 1
    lib.XChangeWindowAttributes(
        dpy, xid, _CW_OVERRIDE_REDIRECT, ctypes.byref(attrs)
    )
    lib.XFlush(dpy)


def take_input(window):
    """Claim keyboard input for an unmanaged window. No-op for a managed one.

    Nothing hands focus to an override-redirect window — that is the window
    manager's job, and we opted out of it — so we set the input focus
    ourselves. From there GTK receives key events exactly as usual.

    rofi additionally grabs the keyboard, but that is not reproducible here:
    an X grab delivers events to the *connection* that asked for it, and ours
    is not the connection GDK reads, so grabbing would silently swallow every
    keystroke. Setting the focus is enough — verified under i3, which does not
    take it back.
    """
    if not config.UNMANAGED_WINDOW:
        return
    xid = _xid(window)
    lib, dpy = _x11()
    if not xid or not lib:
        return
    lib.XSetInputFocus(dpy, xid, _REVERT_TO_PARENT, _CURRENT_TIME)
    lib.XFlush(dpy)


def center(window):
    """Move the window to the top-centre of the monitor under the pointer.

    Safe to call on every show; the WM's own placement runs at map time, so
    this has to happen after the window is mapped in order to win.
    """
    ls = _ls()
    if ls is not None and ls.is_layer_window(window):
        _place_layer(window, ls)
        return
    xid = _xid(window)
    if not xid:
        return
    mon = _target_monitor(window)
    if mon is None:
        return
    lib, dpy = _x11()
    if not lib:
        return

    scale = mon.get_scale_factor() or 1
    settings = getattr(window, "settings", None)
    x, y = card_position(
        mon.get_geometry(),
        window.get_width() or window.get_default_size()[0],
        window.get_height(),
        position=settings.position if settings else None,
        top_fraction=settings.top_fraction if settings else None,
    )
    lib.XMoveWindow(dpy, xid, x * scale, y * scale)
    lib.XFlush(dpy)


def card_position(geo, width, height, position=None, top_fraction=None):
    """Top-left of the card on a monitor, in that monitor's coordinates.

    `position` is "top" — the Spotlight placement, `top_fraction` of the way
    down — or "center", which centres the card vertically on the monitor.
    Both default to [window] in config.py.

    Kept free of X11 and GDK so the arithmetic can be checked directly; see
    tests/test_placement.py.
    """
    if position is None:
        position = config.POSITION
    if top_fraction is None:
        top_fraction = config.TOP_FRACTION

    x = geo.x + (geo.width - width) // 2
    if position == "center":
        # Centring uses the card's real height, which is 0 until the window has
        # been allocated; on that first pass fall back to the top placement so
        # the card does not flash at the very top of the screen.
        y = (geo.y + (geo.height - height) // 2 if height
             else geo.y + int(geo.height * top_fraction))
    else:
        y = geo.y + int(geo.height * top_fraction)
    # Never let the card hang off the monitor it was placed on: a portrait or
    # small monitor can be narrower than the card, and a short one shorter than
    # the offset plus a full results list. Height is 0 until allocation.
    x = max(geo.x, x)
    if height:
        y = max(geo.y, min(y, geo.y + geo.height - height))
    return x, y
