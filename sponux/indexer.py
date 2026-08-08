"""Builds and maintains the SQLite filename index.

The index is a single flat table of filenames under the configured roots.
Search is a simple LIKE query (see providers/files.py).

Two mechanisms keep it current, because neither is sufficient alone:

* a **full rebuild**, on startup and every ``interval`` seconds, in a
  background thread — the only thing that can recover from watches that were
  never armed, events dropped when the kernel queue overflowed, or a change to
  the rules;
* **incremental updates** from ``Gio.FileMonitor`` (inotify), so a file created
  a second ago is findable a second ago rather than up to 15 minutes later.

Monitors have to be created where the GLib main loop runs, so arming them is
posted to the main thread; their callbacks only drop an event on a queue, and
the indexer thread does the SQLite work. With no main loop — ``--reindex`` from
the command line — the watching half simply never starts.

What gets indexed is configurable; see IndexRules and the ``[index]`` section
in ~/.config/sponux/config.toml.
"""

import fnmatch
import os
import queue
import sqlite3
import threading
import time

from . import config, report, userconfig

_lock = threading.Lock()      # serialises writers within this process
_events = queue.Queue()       # (kind, path, other_path), main thread -> worker
_monitors = {}                # directory path -> Gio.FileMonitor; main thread only
_watch_limit_hit = False
_file_limit_hit = False       # reported already; reset by a build under the limit

# Directories armed per main-loop iteration. Arming is a cheap inotify_add_watch,
# but a home directory can hold thousands of them and the main thread is drawing
# the launcher.
_ARM_CHUNK = 250


def connect(readonly: bool = False) -> sqlite3.Connection:
    """A connection to the index. Raises if a readonly one has nothing to open.

    A reader must never create the database, and must never set journal_mode:
    both take an exclusive lock, and doing either races the very first build.
    That race is real — it wedged a CI run. The searching side polled while the
    daemon was building, found no file yet, opened a *writable* connection of
    its own, and the two of them fought over converting a brand-new database to
    WAL until one lost with "database is locked". The build was the one that
    lost, so the index stayed empty until the next full rebuild an hour later.

    Callers of the readonly form already treat a missing index as "no results
    yet", which is exactly what it is.
    """
    if readonly:
        con = sqlite3.connect(
            f"file:{config.INDEX_DB}?mode=ro", uri=True, check_same_thread=False
        )
        con.execute("PRAGMA busy_timeout=3000")
        return con

    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.INDEX_DB, check_same_thread=False)
    # Two writers exist in practice — the daemon's indexer thread and a
    # `sponux --reindex` run alongside it — and SQLite's default is to fail
    # instantly rather than wait for the other one to finish.
    con.execute("PRAGMA busy_timeout=3000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _ensure_schema(con: sqlite3.Connection):
    con.execute(
        """CREATE TABLE IF NOT EXISTS files(
               path TEXT PRIMARY KEY,
               name TEXT,
               name_lower TEXT,
               is_dir INTEGER
           )"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_name_lower ON files(name_lower)")


# ---- what belongs in the index ----------------------------------------


def _expand(path) -> str:
    """~ and $VARS resolved, no trailing slash, so patterns compare literally."""
    return os.path.expanduser(os.path.expandvars(str(path))).rstrip("/") or "/"


def _seq(value):
    """A TOML value that should be a list of strings, or nothing usable."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


class IndexRules:
    """Which paths the index covers.

    Assembled from ``[index]`` in config.toml on top of the defaults in
    config.py. The point of the include/exclude pair is the common case of
    wanting *some* dotfiles: hidden directories are skipped wholesale by
    default (~/.cache alone would double the index), so ``include`` names the
    hidden trees worth having — ~/.config being the obvious one — without
    dragging in the rest.
    """

    def __init__(self, roots, skip, hidden_dirs, hidden_files, include,
                 exclude, max_files, interval, watch, max_watches,
                 follow_symlinks):
        self.roots = roots
        self.skip = skip
        self.hidden_dirs = hidden_dirs
        self.hidden_files = hidden_files
        self.include = include
        self.exclude = exclude
        self.max_files = max_files
        self.interval = interval
        self.watch = watch
        self.max_watches = max_watches
        self.follow_symlinks = follow_symlinks

    @classmethod
    def from_settings(cls) -> "IndexRules":
        section = userconfig.settings().get("index")
        if not isinstance(section, dict):
            section = {}

        def flag(key, default):
            value = section.get(key)
            return bool(value) if isinstance(value, bool) else default

        def number(key, default):
            value = section.get(key)
            return int(value) if isinstance(value, int) and value >= 0 else default

        roots = [_expand(r) for r in _seq(section.get("roots"))]
        skip = set(config.SKIP_DIRS)
        skip |= {s.strip("/") for s in _seq(section.get("skip"))}
        skip -= {s.strip("/") for s in _seq(section.get("unskip"))}

        hidden = section.get("hidden")
        hidden = bool(hidden) if isinstance(hidden, bool) else None

        return cls(
            roots=roots or [_expand(r) for r in config.INDEX_ROOTS],
            skip=skip,
            hidden_dirs=not config.SKIP_HIDDEN_DIRS if hidden is None else hidden,
            hidden_files=not config.SKIP_HIDDEN_FILES if hidden is None else hidden,
            include=[_expand(p) for p in _seq(section.get("include"))],
            exclude=[_expand(p) for p in _seq(section.get("exclude"))],
            max_files=number("max_files", config.MAX_FILES),
            interval=number("interval", config.REINDEX_INTERVAL),
            watch=flag("watch", config.WATCH_FILESYSTEM),
            max_watches=number("max_watches", config.MAX_WATCHES),
            follow_symlinks=flag("follow_symlinks", config.FOLLOW_SYMLINKS),
        )

    def key(self):
        """Everything that changes what is indexed — to notice config edits."""
        return (tuple(self.roots), tuple(sorted(self.skip)), self.hidden_dirs,
                self.hidden_files, tuple(self.include), tuple(self.exclude),
                self.max_files, self.interval, self.watch, self.max_watches,
                self.follow_symlinks)

    # Patterns are matched against the whole path with fnmatch, where "*" also
    # crosses directory separators — "~/Videos/*" therefore covers the whole
    # tree, which is what people mean by it.
    def _excluded(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pat) for pat in self.exclude)

    def _included(self, path: str) -> bool:
        """Inside one of the explicitly included trees."""
        for pat in self.include:
            if (path == pat or path.startswith(pat + "/")
                    or fnmatch.fnmatch(path, pat)
                    or fnmatch.fnmatch(path, pat + "/*")):
                return True
        return False

    def _toward_include(self, path: str) -> bool:
        """A parent of an included tree: ~/.config on the way to ~/.config/nvim.

        Such a directory is walked but not itself indexed — otherwise the
        include would be unreachable behind the hidden-directory rule.
        """
        prefix = path + "/"
        return any(pat.startswith(prefix) for pat in self.include)

    def dir_action(self, path: str, name: str, inside: bool = True):
        """(index this directory, descend into it).

        `inside` says whether the parent is part of an indexed tree. It is
        False while walking *through* a hidden directory to reach an include:
        ~/.local/share must be traversed to get to ~/.local/share/applications,
        but indexing everything else it holds is precisely what the user asked
        not to have. The returned "index" flag is the `inside` of the children.
        """
        if name in self.skip or self._excluded(path):
            return (False, False)
        if self._included(path):
            return (True, True)
        if not inside or (name.startswith(".") and not self.hidden_dirs):
            return (False, True) if self._toward_include(path) else (False, False)
        return (True, True)

    def file_ok(self, path: str, name: str, inside: bool = True) -> bool:
        if self._excluded(path):
            return False
        if not inside:
            return self._included(path)
        if name.startswith(".") and not self.hidden_files:
            return self._included(path)
        return True

    def walk_state(self, directory: str):
        """`inside` for a directory reached out of the blue, or None if the
        walk would never have got there at all.

        A filesystem event names a path with no context, so the decisions the
        walk would have made on the way down are replayed here.
        """
        root = max((r for r in self.roots
                    if directory == r or directory.startswith(r + "/")),
                   key=len, default=None)
        if root is None:
            return None
        inside = True
        path = root
        for name in directory[len(root):].strip("/").split("/"):
            if not name:
                continue
            path = f"{path}/{name}"
            index, descend = self.dir_action(path, name, inside)
            if not descend:
                return None
            inside = index
        return inside


def _iter_entries(roots, rules, dirs_out=None, inside=True):
    """Yield (path, name, name_lower, is_dir) tuples, pruning by the rules.

    Directories that were descended into are collected in `dirs_out` when
    given, so the watcher can arm exactly the tree that was walked. `inside`
    travels down the stack with each directory; see IndexRules.dir_action.
    """
    for root in roots:
        root = _expand(root)
        if not os.path.isdir(root):
            continue
        if dirs_out is not None:
            dirs_out.append(root)
        stack = [(root, inside)]
        # Every directory the walk has entered, by identity, so a link into a
        # tree already covered — including one pointing back at an ancestor —
        # is indexed as a directory but not descended a second time.
        seen = set()
        _visited_path(root, seen)
        while stack:
            current, within = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        name = entry.name
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            linked_dir = (not is_dir and rules.follow_symlinks
                                          and entry.is_symlink()
                                          and entry.is_dir())
                        except OSError:
                            continue
                        if is_dir or linked_dir:
                            index, descend = rules.dir_action(
                                entry.path, name, within
                            )
                            if (descend and rules.follow_symlinks
                                    and _visited(entry, seen)):
                                descend = False
                            if descend:
                                stack.append((entry.path, index))
                                if dirs_out is not None:
                                    dirs_out.append(entry.path)
                            if index:
                                yield (entry.path, name, name.lower(), 1)
                        elif rules.file_ok(entry.path, name, within):
                            yield (entry.path, name, name.lower(), 0)
            except (PermissionError, OSError):
                continue


def _visited(entry, seen) -> bool:
    """True if this directory is one the walk has already been through.

    ~/.config/i3 -> ~/dotfiles/.config/i3 is worth following; a link back up
    the tree is not, and without this the walk would not terminate.
    """
    try:
        st = entry.stat()  # follows the link
    except OSError:
        return True
    return _remember_inode(st, seen)


def _visited_path(path: str, seen) -> bool:
    try:
        return _remember_inode(os.stat(path), seen)
    except OSError:
        return True


def _remember_inode(st, seen) -> bool:
    key = (st.st_dev, st.st_ino)
    if key in seen:
        return True
    seen.add(key)
    return False


# ---- full rebuild -----------------------------------------------------


def build_index(roots=None, rules=None, dirs_out=None) -> int:
    """Rebuild the index from scratch. Returns the number of rows written."""
    global _file_limit_hit
    rules = rules or IndexRules.from_settings()
    roots = roots or rules.roots
    with _lock:
        con = connect()
        try:
            _ensure_schema(con)
            con.execute("DELETE FROM files")
            batch, n = [], 0
            for row in _iter_entries(roots, rules, dirs_out):
                batch.append(row)
                n += 1
                if len(batch) >= 2000:
                    con.executemany(
                        "INSERT OR IGNORE INTO files VALUES (?,?,?,?)", batch
                    )
                    batch.clear()
                if n >= rules.max_files:
                    break
            if batch:
                con.executemany(
                    "INSERT OR IGNORE INTO files VALUES (?,?,?,?)", batch
                )
            con.commit()
        finally:
            con.close()
    # A truncated build silently costs the user every result past the limit,
    # and the log is the only place that can be seen from. Report the
    # transition, not every hourly rebuild — the same reason _watch() speaks
    # once. A build that fits again resets the flag, so a later relapse is
    # reported too.
    if n >= rules.max_files:
        if not _file_limit_hit:
            _file_limit_hit = True
            report.note(f"index stopped at max_files = {rules.max_files}; "
                        f"everything past that is unfindable until the limit "
                        f"is raised in config.toml")
    else:
        _file_limit_hit = False
    return n


# ---- incremental updates ----------------------------------------------


def _gio():
    """(Gio, GLib), or (None, None) where there is no GTK stack to import."""
    try:
        from gi.repository import Gio, GLib
        return Gio, GLib
    except (ImportError, ValueError):  # pragma: no cover - non-GTK environment
        return None, None


_event_kinds = None


def _event_kind(event):
    """Map a GFileMonitorEvent to what it means for the index, or None.

    Only events that change a *name* matter: the index stores paths, so a file
    being written to does not affect it.
    """
    global _event_kinds
    if _event_kinds is None:
        Gio, _ = _gio()
        if Gio is None:
            return None
        ev = Gio.FileMonitorEvent
        _event_kinds = {}
        for name, kind in (("CREATED", "add"), ("MOVED_IN", "add"),
                           ("DELETED", "del"), ("MOVED_OUT", "del"),
                           ("RENAMED", "move"), ("MOVED", "move")):
            member = getattr(ev, name, None)
            if member is not None:
                _event_kinds[member] = kind
    return _event_kinds.get(event)


def _on_changed(_monitor, gfile, other, event):
    """Main-thread callback: do nothing but hand the event to the worker."""
    kind = _event_kind(event)
    if kind is None:
        return
    _events.put((kind, gfile.get_path(),
                 other.get_path() if other is not None else None))


def _watch(path: str, max_watches: int):
    """Arm one directory. Main thread only."""
    global _watch_limit_hit
    if path in _monitors:
        return
    if len(_monitors) >= max_watches:
        if not _watch_limit_hit:
            _watch_limit_hit = True
            report.note(f"watching only the first {max_watches} directories; "
                        f"the rest stay on the periodic rebuild")
        return
    Gio, _ = _gio()
    if Gio is None:
        return
    try:
        monitor = Gio.File.new_for_path(path).monitor_directory(
            Gio.FileMonitorFlags.WATCH_MOVES, None
        )
    except Exception:
        # A directory can disappear between the walk and here, and inotify
        # watches are a finite kernel resource; neither is worth failing over.
        return
    monitor.connect("changed", _on_changed)
    _monitors[path] = monitor


def _unwatch_tree(path: str):
    """Drop the monitors for a directory and everything under it. Main thread."""
    prefix = path + "/"
    for watched in [p for p in _monitors if p == path or p.startswith(prefix)]:
        monitor = _monitors.pop(watched)
        monitor.cancel()


def sync_watches(dirs, rules):
    """Arm watches for `dirs`, drop any that are no longer indexed.

    Called from the indexer thread after a full rebuild; the work is posted to
    the main loop, where the monitors have to live.
    """
    Gio, GLib = _gio()
    if Gio is None:
        return
    GLib.idle_add(_arm_chunks(list(dirs), rules.max_watches, prune=set(dirs)))


def _arm_chunks(pending, max_watches, prune=None):
    """An idle callback arming watches a chunk at a time until none are left.

    `prune` is the complete set of directories that should be watched; monitors
    outside it are cancelled first. Left None when adding to what is already
    armed, so a newly created subtree does not disturb the rest.
    """
    first = [True]

    def step():
        if first[0]:
            first[0] = False
            if prune is not None:
                for path in list(_monitors):
                    if path not in prune:
                        _monitors.pop(path).cancel()
        chunk, pending[:_ARM_CHUNK] = pending[:_ARM_CHUNK], []
        for path in chunk:
            _watch(path, max_watches)
        return bool(pending)

    return step


def _like_prefix(path: str) -> str:
    """A LIKE pattern matching everything under `path`, wildcards escaped."""
    escaped = (path.replace("\\", "\\\\")
                   .replace("%", "\\%")
                   .replace("_", "\\_"))
    return escaped + "/%"


def _remove_path(con, path: str, glib):
    con.execute("DELETE FROM files WHERE path = ?", (path,))
    con.execute("DELETE FROM files WHERE path LIKE ? ESCAPE '\\'",
                (_like_prefix(path),))
    if glib is not None:
        glib.idle_add(lambda: (_unwatch_tree(path), False)[1])


def _add_path(con, path: str, rules, glib):
    """Index a path that has just appeared, and the tree under it.

    A whole tree can arrive in one event — an unpacked archive, a git clone, a
    directory moved in from elsewhere — and inotify says nothing about what is
    inside it, so the subtree is walked here.
    """
    name = os.path.basename(path)
    if not name:
        return
    inside = rules.walk_state(os.path.dirname(path))
    if inside is None:
        return
    try:
        is_dir = os.path.isdir(path) and (rules.follow_symlinks
                                          or not os.path.islink(path))
    except OSError:
        return

    if not is_dir:
        if rules.file_ok(path, name, inside):
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                        (path, name, name.lower(), 0))
        return

    index, descend = rules.dir_action(path, name, inside)
    if index:
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                    (path, name, name.lower(), 1))
    if not descend:
        return
    dirs = []
    rows = list(_iter_entries([path], rules, dirs, inside=index))
    if rows:
        con.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", rows)
    if rules.watch and glib is not None:
        glib.idle_add(_arm_chunks(dirs, rules.max_watches))


def _apply_events(batch, rules):
    """Apply a batch of filesystem events to the index. Indexer thread."""
    _, glib = _gio()
    with _lock:
        con = connect()
        try:
            _ensure_schema(con)
            for kind, path, other in batch:
                if path is None:
                    continue
                if kind == "add":
                    _add_path(con, path, rules, glib)
                elif kind == "del":
                    _remove_path(con, path, glib)
                elif kind == "move":
                    _remove_path(con, path, glib)
                    if other is not None:
                        _add_path(con, other, rules, glib)
            con.commit()
        finally:
            con.close()


def _drain(first, limit=500):
    """`first` plus whatever else is already queued — bursts arrive in bulk."""
    batch = [first]
    while len(batch) < limit:
        try:
            batch.append(_events.get_nowait())
        except queue.Empty:
            break
    return batch


# ---- the loop ---------------------------------------------------------


def start_background(reindex_interval=None):
    """Run the hybrid indexer in a daemon thread: rebuild, then watch."""

    def loop():
        while True:
            rules = IndexRules.from_settings()
            interval = (reindex_interval if reindex_interval is not None
                        else rules.interval)
            dirs = []
            try:
                build_index(rules=rules, dirs_out=dirs if rules.watch else None)
                if rules.watch:
                    sync_watches(dirs, rules)
            except Exception as exc:
                # No notification: nothing the user did is waiting on this,
                # and a rebuild that fails once a minute would notify as often.
                report.problem(f"indexing failed: {exc}")

            # Until the next full rebuild is due, keep the index current from
            # the watches. A rebuild also happens early if the rules changed.
            deadline = time.monotonic() + interval if interval > 0 else None
            while deadline is None or time.monotonic() < deadline:
                timeout = 1.0 if deadline is None else min(
                    1.0, deadline - time.monotonic()
                )
                try:
                    event = _events.get(timeout=max(timeout, 0.05))
                except queue.Empty:
                    if IndexRules.from_settings().key() != rules.key():
                        break
                    continue
                try:
                    _apply_events(_drain(event), rules)
                except Exception as exc:
                    report.problem(f"index update failed: {exc}")

    t = threading.Thread(target=loop, name="sponux-indexer", daemon=True)
    t.start()
    return t
