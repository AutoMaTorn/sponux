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
MAX_FILES = 200_000

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

# Max results shown per provider.
MAX_RESULTS = 8

# Rank things you actually open above things that merely match as well. The
# weight is the most a result can gain from its history; fuzzy_score() runs
# 0-110, and its steps between match qualities are 15-30, so 25 lets history
# settle near-ties and lose to a clearly better match. See usage.py.
FRECENCY = True
FRECENCY_WEIGHT = 25.0

# Entries kept in the usage database; the least recently used go first.
MAX_USAGE_ENTRIES = 5000

# Hide the window as soon as it loses focus — i.e. clicking outside it, or
# switching to another window, dismisses the launcher (rofi's click-to-exit).
HIDE_ON_FOCUS_LOSS = True

# Bypass the window manager entirely (X11 override-redirect), the way rofi
# does by default. The launcher then never becomes the WM's focused window,
# so a tiling WM leaves its focus highlight on whatever you were working in.
# Requires claiming input focus ourselves; see placement.take_input().
UNMANAGED_WINDOW = True
