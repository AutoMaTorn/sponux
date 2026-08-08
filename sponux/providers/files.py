"""File provider — searches the SQLite filename index."""

import os
import shlex

from gi.repository import Gio, GLib

from . import apps as apps_provider
from .base import Result, fuzzy_score
from .. import config, indexer, report, usage, userconfig

# Rows pulled from SQLite per requested result. LIKE can only narrow the
# candidates; the ranking below decides which of them the user actually sees,
# so it needs more than `limit` rows to choose from.
CANDIDATE_FACTOR = 8

# Files compete with apps on one list. A tie goes to the app: launching one
# is the more common intent, and there are far fewer of them.
FILE_WEIGHT = 0.9

# Small nudge so a directory outranks a file that matched equally well.
DIR_BONUS = 2.0

_con = None


def _read_con():
    """A cached read-only connection to the index (lazy, main-thread)."""
    global _con
    if _con is None and config.INDEX_DB.exists():
        try:
            _con = indexer.connect(readonly=True)
        except Exception:
            _con = None
    return _con


def _open(path: str, is_dir: bool = False):
    """Open a path with the configured application, else the desktop default.

    Whichever application ends up doing it is counted as a use of that
    application, so the editor everything is opened with rises in the app
    search too. Counted here rather than in the window, which counts the file:
    this is the only place that knows which application it turned out to be.
    Both calls happen after the window is hidden, off the typing path.

    Counted through record_opener(), which is worth less than a launch by name:
    nobody named this application, a rule did, and the rule is one old decision
    being replayed by every file since.
    """
    argv = userconfig.opener_command(path, is_dir)
    if argv and _spawn(argv):
        app = app_for_command(argv[0])
        if app is not None:
            usage.record_opener(app)
        return
    try:
        uri = GLib.filename_to_uri(path, None)
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error:
        return
    default = Gio.AppInfo.get_default_for_type(content_type(path, is_dir), False)
    if default is not None:
        usage.record_opener(default)


def _spawn(argv) -> bool:
    """Launch a configured command. False if it couldn't start, so the caller
    can fall back rather than leaving the user with nothing happening."""
    try:
        Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
        return True
    except GLib.Error as exc:
        # Someone pressed Enter and nothing opened: exactly the case a
        # notification is for.
        report.problem(f"cannot run {argv[0]!r}: {exc.message}", notify=True)
        return False


def reveal(path: str):
    """Open the folder containing a path — its parent, or itself for a
    directory, since revealing a folder inside itself is what people mean."""
    _open(os.path.dirname(path) or "/", is_dir=True)


def content_type(path: str, is_dir: bool = False) -> str:
    """The mime type used to look up applications for a path."""
    if is_dir:
        return "inode/directory"
    guessed, _ = Gio.content_type_guess(path, None)
    return guessed or _UNKNOWN_CONTENT_TYPE


def apps_for(path: str, is_dir: bool = False):
    """Applications to offer for a path: what has been chosen here before,
    then the ones registered for the type, then the rest.

    Registration alone is not enough to build the list from. A file with no
    extension — ``~/.config/i3/config``, ``.bashrc`` — is
    ``application/octet-stream`` to GIO, and nothing on this system claims that
    type, so "open with" would be an empty list for precisely the files it is
    most wanted for. Every installed application follows the registered ones,
    unsorted noise at the bottom that typing filters away.

    Nothing here consults sponux's own [open] rules: the point of "open with"
    is to reach past them.
    """
    ctype = content_type(path, is_dir)
    registered = [a for a in Gio.AppInfo.get_all_for_type(ctype) if a.should_show()]
    default = Gio.AppInfo.get_default_for_type(ctype, False)
    if default is not None:
        registered = ([a for a in registered if a.get_id() == default.get_id()]
                      or [default]) + [a for a in registered
                                       if a.get_id() != default.get_id()]

    seen = {a.get_id() for a in registered}
    others = sorted(
        (a for a in apps_provider.installed() if a.get_id() not in seen),
        key=lambda a: (a.get_display_name() or "").lower(),
    )
    # What has been chosen here before comes first, across both groups; the
    # order built above survives as the tie-break for everything else. See
    # usage.order_by_usage().
    return usage.order_by_usage(registered + others, usage.key_for_appinfo)


def command_for(app) -> str:
    """The command line to write into config.toml for an application.

    Taken from the .desktop Exec line with the field codes (%f, %U, …) removed
    — those are placeholders the desktop fills in, and sponux appends the path
    itself. Keeping the rest means "kitty -e nvim" survives as written.
    """
    cmdline = app.get_commandline() or ""
    if cmdline:
        try:
            argv = [a for a in shlex.split(cmdline) if not a.startswith("%")]
        except ValueError:
            argv = []
        if argv:
            return shlex.join(argv)
    return app.get_executable() or ""


def app_for_command(program: str, apps=None):
    """The installed application a configured opener runs, if it is one.

    `[open]` rules are command lines — "code", "kitty -e nvim" — and nothing in
    them names a .desktop entry, so the executable is the only link back to the
    application list. The reverse of command_for(), and lossier: it is used to
    decide whose history to credit, never to decide what runs.

    Only an unambiguous answer counts. Several entries can run the same binary
    (an application and its URL handler, a wrapper and the thing it wraps), and
    crediting an arbitrary one of them means the ranking quietly learns the
    wrong application. `should_show()` settles most of it, since the hidden
    halves of those pairs are hidden precisely because they are not the entry
    anyone means; an id equal to the command settles the rest.
    """
    name = os.path.basename(program)
    if not name:
        return None
    if apps is None:
        apps = apps_provider.installed()
    matches = [a for a in apps if a.should_show()
               and os.path.basename(a.get_executable() or "") == name]
    if len(matches) == 1:
        return matches[0]
    exact = [a for a in matches if (a.get_id() or "") == f"{name}.desktop"]
    return exact[0] if len(exact) == 1 else None


def open_with(app, path: str) -> bool:
    """Open one path with a chosen application, the way the desktop would."""
    try:
        return app.launch_uris([GLib.filename_to_uri(path, None)], None)
    except GLib.Error as exc:
        report.problem(f"cannot open {path} with "
                       f"{app.get_display_name()}: {exc.message}", notify=True)
        return False


# What GIO answers when it cannot identify a file (typically one with no
# extension). Its Adwaita icon is near-white and all but disappears on the
# light card, so we use the generic text icon instead — just as accurate for
# an unidentified file, and actually visible.
_UNKNOWN_CONTENT_TYPE = "application/octet-stream"


def _icon_for(path: str, is_dir: bool):
    if is_dir:
        return "folder"
    content_type, _ = Gio.content_type_guess(path, None)
    if content_type and content_type != _UNKNOWN_CONTENT_TYPE:
        icon = Gio.content_type_get_icon(content_type)
        if icon is not None:
            return icon
    return "text-x-generic"


def _result_for(path: str, is_dir: bool, score: float, name: str = ""):
    """One file result. Shared so an indexed hit and a typed path are the same
    kind of thing to the window — same actions, same badge, same counting."""
    home = os.path.expanduser("~")
    shown = path.replace(home, "~", 1) if path.startswith(home) else path
    return Result(
        title=name or os.path.basename(path) or path,
        subtitle=shown,
        icon=_icon_for(path, is_dir),
        score=score,
        action=lambda p=path, d=is_dir: _open(p, d),
        kind="file",
        path=path,
        is_dir=is_dir,
        usage_key=usage.key_for_file(path),
    )


# A query starting like this is a path being typed, not a search term.
# Deliberately only absolute and ~: "./x" would be relative to the daemon's
# working directory, which is wherever it happened to be started and means
# nothing to the person typing.
_PATH_PREFIXES = ("/", "~/")

# Typed paths sit above every index result. fuzzy_score() tops out at 110 and
# frecency can add 25, so this band cannot be reached from below: when the user
# has named a file outright there is nothing left to guess at.
PATH_EXACT_SCORE = 400.0
PATH_COMPLETION_SCORE = 300.0


def is_path_query(query: str) -> bool:
    q = query.strip()
    return q == "~" or q.startswith(_PATH_PREFIXES)


def search_path(query: str, limit: int = config.MAX_RESULTS):
    """Offer the path the user is typing, and completions of its last component.

    This is the way out of the index. The index covers what `[index]` lets it
    cover, so /usr/lib, /etc and any excluded tree are unreachable through
    search however precisely they are named — and making the index cover
    everything is not the answer, since that is what makes results useless.
    Typing the path needs no index at all.
    """
    raw = query.strip()
    if not is_path_query(raw):
        return []
    path = os.path.expanduser(raw)

    results = []
    seen = set()
    if os.path.exists(path):
        full = os.path.normpath(path)
        results.append(_result_for(full, os.path.isdir(path), PATH_EXACT_SCORE))
        seen.add(full)

    # "/etc/ho" completes to /etc/hosts; "/etc/" lists /etc, because
    # os.path.split leaves an empty last component and everything matches it.
    parent, stem = os.path.split(path)
    for i, entry in enumerate(_complete(parent, stem, limit)):
        full = os.path.normpath(entry.path)
        if full in seen:
            continue
        seen.add(full)
        try:
            is_dir = entry.is_dir()
        except OSError:          # a dangling symlink, or no permission
            is_dir = False
        # Descending, so the order chosen in _complete() survives the sort the
        # window does across all providers.
        results.append(_result_for(full, is_dir, PATH_COMPLETION_SCORE - i,
                                   name=entry.name))
    return results[:limit]


def _complete(parent: str, stem: str, limit: int):
    """Directory entries under `parent` whose name starts with `stem`."""
    if not os.path.isdir(parent):
        return []
    low = stem.lower()
    out = []
    try:
        with os.scandir(parent) as it:
            for entry in it:
                # Hidden entries only once a dot has been typed — the rule a
                # shell uses, and the one the index defaults to.
                if entry.name.startswith(".") and not stem.startswith("."):
                    continue
                if low and not entry.name.lower().startswith(low):
                    continue
                out.append(entry)
    except OSError:              # unreadable directory: offer what we can
        return []

    def sort_key(entry):
        try:
            is_dir = entry.is_dir()
        except OSError:
            is_dir = False
        return (not is_dir, entry.name.lower())

    out.sort(key=sort_key)
    return out[:limit]


def search(query: str, limit: int = config.MAX_RESULTS):
    q = query.strip().lower()
    if not q:
        return []
    con = _read_con()
    if con is None:
        return []

    # One character matches half the home directory, so it is not worth
    # oversampling for the ranking below: a single-character query asks for no
    # more rows than it will show. The apps provider answers those queries too;
    # files sitting them out looked like "no such file" rather than "keep
    # typing".
    #
    # This bounds the ranking, not the search. The query below cannot use
    # idx_name_lower — a leading '%' rules it out, and the ORDER BY then sorts
    # every match before LIMIT applies — so SQLite reads the whole table either
    # way. Measured: 51 ms for a three-character query over 200 000 rows
    # against 55 ms for a one-character one, the smaller limit notwithstanding.
    # That is the size where this stops being free, and it is what
    # config.SEARCH_SLOW_ROWS has --check warn about.
    candidates = limit * CANDIDATE_FACTOR if len(q) > 1 else limit
    like = f"%{q}%"
    prefix = f"{q}%"
    try:
        rows = con.execute(
            """SELECT path, name, is_dir FROM files
               WHERE name_lower LIKE ?
               ORDER BY (name_lower LIKE ?) DESC, is_dir DESC, length(name) ASC
               LIMIT ?""",
            (like, prefix, candidates),
        ).fetchall()
    except Exception:
        return []

    results = []
    for path, name, is_dir in rows:
        is_dir = bool(is_dir)
        score = fuzzy_score(q, name)
        if score <= 0:
            continue
        if is_dir:
            score += DIR_BONUS
        # The history bonus is added after the file weighting, not scaled by
        # it: it is measured on the same scale for apps and files, so that
        # "the thing you keep opening" means the same in both.
        bonus = usage.bonus(usage.key_for_file(path))
        results.append(
            _result_for(path, is_dir, score * FILE_WEIGHT + bonus, name=name)
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
