"""Paths and tunables for sponux."""

import os
from pathlib import Path

APP_ID = "io.github.sponux"


def _xdg(env: str, default: str) -> Path:
    v = os.environ.get(env)
    return Path(v) if v else Path.home() / default


CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "sponux"
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "sponux"
# What you have opened is not a cache: it cannot be recomputed, and clearing
# ~/.cache is a thing people do. It lives in the state directory instead.
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "sponux"
INDEX_DB = CACHE_DIR / "index.db"
USAGE_DB = STATE_DIR / "usage.db"
# Where runtime problems land when there is no terminal to print them to; see
# report.py. A tail rather than an archive: at this size it rotates one
# generation, so the two files together are bounded.
LOG_FILE = STATE_DIR / "sponux.log"
MAX_LOG_BYTES = 64 * 1024
# How many opens carry the "bind a key" hint before the window goes back to
# teaching the prefixes. Three rather than one: the first open is often spent
# looking at the window itself, and this costs nothing to get wrong twice.
FIRST_RUN_FILE = STATE_DIR / "first-run"
FIRST_RUN_HINTS = 3
# Written and removed by `sponux --autostart`; nothing else touches it, and
# it is not created by installing sponux.
AUTOSTART_FILE = _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "sponux.desktop"

# Defaults for the file index. Every one of these can be overridden per user
# in the [index] section of ~/.config/sponux/config.toml — see indexer.py.

# Root(s) that the file indexer walks.
INDEX_ROOTS = [str(Path.home())]

# Directory names skipped while indexing (noise / heavy trees).
# "build" and "dist" are here for the same reason as "target": a staging tree
# is a copy of files that already exist elsewhere, so indexing it means every
# search for one of them returns the original and two or three shadows of it.
# `[index] unskip` takes a name back off this list if you do want one of them.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".cache", ".venv", "venv",
    ".mozilla", ".npm", ".cargo", ".rustup", "site-packages", ".gradle",
    ".m2", "target", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    "Trash",
}

# Follow symlinked directories. Dotfiles are usually kept in one repository
# and symlinked into place (~/.config/i3 -> ~/dotfiles/.config/i3); without
# this, such a config directory is indexed as a single file and nothing inside
# it can be found. Loops are prevented by identity, not by refusing to look.
FOLLOW_SYMLINKS = True

# Skip hidden directories entirely (name starts with ".").
SKIP_HIDDEN_DIRS = True
# Skip hidden files (name starts with ".").
SKIP_HIDDEN_FILES = True

# Safety cap so a pathological home dir can't blow up the index.
#
# This bounds correctness (what is findable), not comfort. Comfort runs out
# first: `WHERE name_lower LIKE '%q%'` cannot use the index and the ORDER BY
# sorts every match, so a keystroke costs, measured on this machine —
#
#     1 000 rows   0.4 ms      50 000 rows   13 ms
#    10 000 rows   2.3 ms     100 000 rows   25 ms
#                             200 000 rows   51 ms
#
# — and the search runs on the GTK main thread, where the gap between
# keystrokes is about 60 ms. So this cap sits well past the point where typing
# stops being smooth; it is deliberately generous, and --check reports the
# latency separately (see SEARCH_SLOW_ROWS) rather than lowering it for
# everyone.
MAX_FILES = 200_000

# Where --check starts saying the index has grown into the latency above.
# ~10 ms is the most a keystroke can cost without being felt, which lands here.
SEARCH_SLOW_ROWS = 40_000

# How often (seconds) the resident daemon rebuilds the index in full. This is
# the safety net under the watches below — it is what recovers from events the
# kernel dropped — not the main way the index stays current. 0 disables it.
REINDEX_INTERVAL = 900  # 15 minutes

# Watch the indexed directories with Gio.FileMonitor (inotify) so that changes
# reach the index in the second they happen instead of at the next rebuild.
WATCH_FILESYSTEM = True

# Ceiling on the number of watched directories. One inotify watch costs about
# a kilobyte of kernel memory and counts against
# /proc/sys/fs/inotify/max_user_watches, which the whole session shares; past
# this many directories the periodic rebuild covers the rest.
MAX_WATCHES = 8192

# --- the window -------------------------------------------------------
#
# Defaults for the [window] and [keys] sections of config.toml; the sections
# only override. Read through userconfig.window_settings(), which re-reads them
# whenever the file changes, so editing config.toml takes effect the next time
# the launcher is opened.

# How many results the list shows at once, across all providers. Each provider
# is asked for this many and the merge keeps the best.
MAX_RESULTS = 9

# Card width in pixels. Its height follows the number of results.
WIDTH = 640

# How long typing has to pause before the search runs, in milliseconds. Long
# enough that a fast typist searches once instead of once per letter, short
# enough not to feel laggy.
DEBOUNCE_MS = 60

# Where the card sits: "top" puts it TOP_FRACTION of the way down the monitor,
# the way Spotlight and rofi do; "center" centres it vertically.
#
# Sitting a little above the middle reads better than dead centre, and leaves
# room for the results list to grow downwards without the card ever moving.
POSITION = "top"
TOP_FRACTION = 0.22

# Hide the window as soon as it loses focus — i.e. clicking outside it, or
# switching to another window, dismisses the launcher (rofi's click-to-exit).
HIDE_ON_FOCUS_LOSS = True

# What the modifier keys do, as GTK accelerator strings — the same syntax as
# `gtk-accel` and i3's `bindsym`. Overridable per action in [keys].
# The arrows, Enter and typing itself are deliberately not configurable: they
# are what makes it a launcher rather than a keymap.
KEYS = {
    "reveal": "<Ctrl>Return",       # open the folder containing the file
    "copy_path": "<Ctrl>c",         # copy the selected file's path
    "open_with": "<Shift>Return",   # choose an application for this file
    # Forget what the ranking learned about the selected result. Shift+Delete
    # because that is what browsers have bound to "drop this suggestion from
    # my history" for twenty years, and this is the same gesture.
    "forget": "<Shift>Delete",
    "close": "Escape",              # hide the window
    "quit": "<Ctrl>q",              # stop the daemon
}

# Rank things you actually open above things that merely match as well. The
# weight is the most a result can gain from its history; fuzzy_score() runs
# 0-110, and its steps between match qualities are 15-30, so 25 lets history
# settle near-ties and lose to a clearly better match. See usage.py.
FRECENCY = True
FRECENCY_WEIGHT = 25.0

# What one *indirect* open is worth against a deliberate one: an application
# credited because an [open] rule or the desktop default ran it, rather than
# because someone named it. Half, so two automatic opens weigh as much as one
# deliberate launch — the rule fired because of a decision made once, and every
# file since has been repeating it. 1.0 counts them alike; 0 stops counting
# them. See usage.record_opener().
FRECENCY_INDIRECT = 0.5

# Entries kept in the usage database; the least recently used go first.
MAX_USAGE_ENTRIES = 5000

# Bypass the window manager entirely (X11 override-redirect), the way rofi
# does by default. The launcher then never becomes the WM's focused window,
# so a tiling WM leaves its focus highlight on whatever you were working in.
# Requires claiming input focus ourselves; see placement.take_input().
#
# Deliberately NOT in config.toml, unlike everything above. It is applied once,
# when the X11 window is created, so it could not be changed without a restart
# while every other setting takes effect on the next open — and it is a knob for
# making the launcher behave under an unusual WM, not a daily preference.
UNMANAGED_WINDOW = True
