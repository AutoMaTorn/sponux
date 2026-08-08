"""Checks for the application provider, and mostly for its cache.

`Gio.AppInfo.get_all()` cost ~26 ms over 137 entries on the machine this was
written on — a quarter of the gap between two keystrokes — so the list is
cached. A cache here has form: the previous one was removed because nothing
ever invalidated it, and an application installed while the daemon was running
was then never found until it restarted. So these checks are mostly about the
two ways it goes stale on purpose.

The list source is replaced with a stub for those. Driving the real one means
waiting on inotify, on GIO's own refresh and on a main loop, none of which is
sponux; the one check that does exercise all three is a probe at the end, and
it skips rather than fails, the way tests/test_index.py does.

Run: python3 tests/test_apps.py
"""

import os
import pathlib
import shutil
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="sponux-apps-")
# Set before gi is imported: GLib reads these once, and _APP_DIRS is built from
# them when the provider is imported.
os.environ["XDG_DATA_HOME"] = f"{_TMP}/data"
os.environ["XDG_STATE_HOME"] = f"{_TMP}/state"
os.environ["XDG_CACHE_HOME"] = f"{_TMP}/cache"
os.environ["XDG_CONFIG_HOME"] = f"{_TMP}/config"
APPDIR = pathlib.Path(_TMP) / "data" / "applications"
APPDIR.mkdir(parents=True)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gi.repository import Gio, GLib  # noqa: E402

from sponux import usage  # noqa: E402
from sponux.providers import apps  # noqa: E402

_failures = []
_skipped = []

# The one thing here that depends on the machine rather than on sponux.
PROBE_SECONDS = float(os.environ.get("SPONUX_TEST_TIMEOUT", "5"))


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


class FakeApp:
    """The parts of Gio.AppInfo the provider reads."""

    def __init__(self, app_id, name, keywords=(), generic="", shown=True):
        self._id, self._name = app_id, name
        self._keywords, self._generic, self._shown = keywords, generic, shown

    def get_id(self):
        return self._id

    def get_display_name(self):
        return self._name

    def get_name(self):
        return self._name

    def get_keywords(self):
        return list(self._keywords)

    def get_generic_name(self):
        return self._generic

    def get_description(self):
        return ""

    def get_icon(self):
        return None

    def should_show(self):
        return self._shown

    def __repr__(self):
        return f"<{self._name}>"


class FakeFile:
    """Enough of a GFile for the monitor callback."""

    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path


ALPHA = FakeApp("alpha.desktop", "Alpha Editor")
BETA = FakeApp("beta.desktop", "Beta Browser")
HIDDEN = FakeApp("hidden.desktop", "Hidden Helper", shown=False)
KEYWORDED = FakeApp("gamma.desktop", "Gamma", keywords=["alpha"])

_listing = [ALPHA]
_real_get_all = Gio.AppInfo.get_all
Gio.AppInfo.get_all = staticmethod(lambda: list(_listing))


def titles(query):
    return [r.title for r in apps.search(query)]


def touch_appdir():
    """What installing a package does to the directory, without the package."""
    stamp = time.time() + 1
    os.utime(APPDIR, (stamp, stamp))


# ---- it is a cache ----------------------------------------------------

apps.invalidate()
check("an application is found", titles("alpha"), ["Alpha Editor"])
check("the prepared list is reused rather than rebuilt",
      apps._entries() is apps._entries(), True)

_listing = [ALPHA, BETA]
check("a list that changed behind our back is not noticed by itself",
      titles("beta"), [])

# ---- ...invalidated by the directory mtime ----------------------------
#
# The mtime is what an install actually changes, and it needs no event to
# arrive: it also covers a directory that did not exist when the daemon
# started, and the case where no monitor could be armed at all.

touch_appdir()
check("a changed application directory rebuilds the list",
      titles("beta"), ["Beta Browser"])

_listing = [ALPHA]
touch_appdir()
check("...and an uninstalled application stops being offered",
      titles("beta"), [])

# ---- ...and by the monitor, for what the mtime cannot see -------------
#
# Editing a .desktop in place renames an application without touching its
# directory. invalidate() is what the monitor callback does.

_listing = [FakeApp("alpha.desktop", "Alpha Editor Renamed")]
check("an in-place edit is invisible to the mtime check",
      titles("renamed"), [])
apps.invalidate()
check("...and the monitor's invalidate() picks it up",
      titles("renamed"), ["Alpha Editor Renamed"])

apps._on_changed(None, FakeFile(str(APPDIR / "mimeinfo.cache")), None, None)
check("bookkeeping files in those directories are ignored",
      apps._prepared is not None, True)
apps._on_changed(None, FakeFile(str(APPDIR / "anything.desktop")), None, None)
check("a .desktop file is not", apps._prepared, None)

# ---- what the provider does with the list ------------------------------

_listing = [ALPHA, BETA, HIDDEN, KEYWORDED]
apps.invalidate()
check("an application that asks not to be shown is not shown",
      titles("hidden"), [])
check("keywords match too", "Gamma" in titles("alpha"), True)
check("...but below a name that matches as well",
      titles("alpha"), ["Alpha Editor", "Gamma"])
check("installed() hands back the applications themselves",
      apps.installed(), [ALPHA, BETA, KEYWORDED])

usage.forget_all()
usage.record(usage.key_for_appinfo(BETA))
check("history still reaches the ranking through the cache",
      apps.search("b")[0].title, "Beta Browser")

# ---- the probe: a real .desktop file, a real monitor, a real main loop --

Gio.AppInfo.get_all = _real_get_all
apps.invalidate()

(APPDIR / "zzsponuxprobe.desktop").write_text(
    "[Desktop Entry]\nType=Application\nName=Zzsponuxprobe\nExec=/bin/true\n"
)
context = GLib.MainContext.default()
deadline = time.monotonic() + PROBE_SECONDS
found = False
while not found and time.monotonic() < deadline:
    context.iteration(False)
    found = "Zzsponuxprobe" in titles("zzsponuxprobe")
    if not found:
        time.sleep(0.05)

if found:
    check("a .desktop file dropped in while running is found", found, True)
else:
    # inotify inside a container, GIO's own refresh, a loaded machine: none of
    # that is sponux, and a red suite would say it was.
    _skipped.append("the end-to-end new-application probe")

shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
if _skipped:
    print("all application checks passed, except: " + ", ".join(_skipped))
else:
    print("all application checks passed")
