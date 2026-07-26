"""Visual check harness: open the real sponux window with a prefilled query
and screenshot it, without injecting keystrokes into the live desktop.

    python3 tools/visual.py /tmp/shots                    # the window alone
    python3 tools/visual.py --full docs/images  "kitty"   # the whole screen

`--full` captures the root window instead, so the launcher is shown on the
desktop it actually appears on — wallpaper, bar and all — at full resolution.
Cropping and scaling it for a README is a separate, deliberate step; this only
takes the picture.

Switch to an empty workspace first unless you want whatever is open to be in it,
and remember that a status bar shows things you may not want published.
"""
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from sponux import app as sponux_app, placement

ARGS = [a for a in sys.argv[1:] if a != "--full"]
FULL = "--full" in sys.argv[1:]

OUT = ARGS[0] if ARGS else "/tmp/sponux-shots"
os.makedirs(OUT, exist_ok=True)

# Extra queries can be given on the command line, one shot each. Write
# `name=query` to control the file name, which is also the only way to ask for
# the empty query — the state the launcher opens in:
#     python3 tools/visual.py /tmp/shots polybar "2+2" "empty=" "path=/etc/ho"
def _spec(arg):
    name, sep, query = arg.partition("=")
    return (name, query) if sep and name.isidentifier() else (arg, arg)


QUERIES = ([_spec(a) for a in ARGS[1:]]
           or [("apps", "fi"), ("files", "sponux"), ("calc", "2+2*10")])


class VisualApp(sponux_app.SponuxApp):
    def __init__(self):
        super().__init__()
        self.set_application_id("io.github.sponux.visualtest")
        self.queue = list(QUERIES)

    def do_startup(self):
        # Skip SponuxApp's indexer thread and hold(); just the styling.
        Gtk.Application.do_startup(self)
        self._load_css()

    def do_activate(self):
        if self.window is None:
            self.window = sponux_app.SponuxWindow(self)
        self.window.reset_and_present()
        GLib.timeout_add(600, self._next)

    def _next(self):
        if not self.queue:
            self.quit()
            return GLib.SOURCE_REMOVE
        name, query = self.queue.pop(0)
        self.window.entry.set_text(query)
        GLib.timeout_add(700, self._shot, name)
        return GLib.SOURCE_REMOVE

    def _shot(self, name):
        xid = placement._xid(self.window)
        path = f"{OUT}/shot-{name}.png"
        print(f"{name}: xid={xid} "
              f"size={self.window.get_width()}x{self.window.get_height()}")
        if FULL:
            # The launcher is override-redirect, so it is on screen but not a
            # child of anything worth capturing on its own; grab the root.
            subprocess.run(["maim", "--hidecursor", path], check=False)
        else:
            subprocess.run(["maim", "-i", str(xid), path], check=False)
        GLib.timeout_add(200, self._next)
        return GLib.SOURCE_REMOVE


GLib.set_prgname("sponux")
VisualApp().run([])
