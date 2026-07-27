"""Where runtime problems go when nobody is watching a terminal.

Everything sponux has to say while it runs — a broken command in `[open]`, a
stylesheet it cannot parse, an index rebuild that failed — used to be a bare
`print()`. Started from `~/.config/i3/config` or from an autostart entry, a
launcher has no stdout and no stderr, so a user with a broken config saw only
that nothing happened. `--check` answers that question for the config as it
sits on disk; this is the other half, the part that only shows up while
running.

Every message goes through `note()` or `problem()` here. Both still write the
line to stderr, so running the daemon in a terminal looks exactly as it did.
Once the application has called `start_logging()` they are also appended,
timestamped, to `~/.local/state/sponux/sponux.log`, which `sponux --check`
reads back.

Two deliberate limits:

**Notifications are rare on purpose.** One is raised only where the user
pressed a key and nothing visible happened — a configured opener that would
not start, a config file that is being ignored whole. Background trouble (a
stylesheet saved mid-edit, an index update that failed) goes to the log and
stays there; a desktop notification on every file save is worse than the
problem it reports.

**The log is a tail, not an archive.** It rotates to `sponux.log.1` at 64 KiB,
so the pair is bounded, and a message repeating within a minute is counted and
folded into the next line rather than written again — an indexer failing in a
loop must not fill the disk.

Nothing here can raise: this is where failures are reported, so a failure of
its own has nowhere to go but stderr.
"""

import sys
import threading
import time

from . import config

# The log stays off until the application turns it on, so that --check,
# --which and --write-config — which read the same config and print the same
# complaints, with someone watching — leave no trace and pop no notifications.
_logging = False

# message -> (monotonic time it was written, how many were dropped since)
_recent = {}
_REPEAT_WINDOW = 60.0
_MAX_TRACKED = 256

# The indexer reports from its own thread; one lock covers the table and the
# file so two threads cannot interleave a line.
_lock = threading.Lock()


def start_logging():
    """Begin recording to the log file. Called once, by the application."""
    global _logging
    _logging = True


def note(message: str):
    """Something worth a record: what was written, what was skipped."""
    _emit(message, notify=False)


def problem(message: str, notify: bool = False):
    """Something failed.

    Pass `notify=True` only when the user is standing in front of the failure
    — they asked for something and it did not happen.
    """
    _emit(message, notify=notify)


def log_lines(limit: int = 0):
    """The log as a list of lines, newest last. Empty if there is none.

    `limit` keeps only the last N. For --check, and for the tests.
    """
    try:
        lines = config.LOG_FILE.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:] if limit else lines


def _emit(message: str, notify: bool):
    line = f"sponux: {message}"
    # stdout is block-buffered when it is a pipe, so a complaint made while
    # --check prints would otherwise land in the wrong place entirely.
    sys.stdout.flush()
    print(line, file=sys.stderr, flush=True)

    if not _logging:
        return

    with _lock:
        dropped = _accept(message)
        if dropped is None:
            return
        if dropped:
            line += f" (and {dropped} more like it in the last minute)"
        _append(line)

    if notify:
        _notify(message)


def _accept(message: str):
    """How many repeats to fold into this line, or None to drop it.

    Called with the lock held.
    """
    now = time.monotonic()
    written, dropped = _recent.get(message, (None, 0))
    if written is not None and now - written < _REPEAT_WINDOW:
        _recent[message] = (written, dropped + 1)
        return None
    if len(_recent) >= _MAX_TRACKED:
        _forget_old(now)
    _recent[message] = (now, 0)
    return dropped


def _forget_old(now: float):
    """Drop what can no longer suppress anything. Called with the lock held."""
    for message, (written, _) in list(_recent.items()):
        if now - written >= _REPEAT_WINDOW:
            del _recent[message]
    if len(_recent) >= _MAX_TRACKED:
        # Everything is younger than the window: a daemon being shouted at by
        # 256 distinct messages a minute. Start over rather than grow.
        _recent.clear()


def _append(line: str):
    """Add one timestamped line to the log. Called with the lock held."""
    global _logging
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LOG_FILE
        if path.exists() and path.stat().st_size >= config.MAX_LOG_BYTES:
            # One generation back, replacing whatever was there: the point is a
            # bounded pair of files, not history.
            path.replace(path.with_name(path.name + ".1"))
        with open(path, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError as exc:
        # Nowhere left to report this but stderr, and no reason to try the
        # same failing write on every later message.
        _logging = False
        print(f"sponux: cannot write {config.LOG_FILE}: {exc} — "
              "logging is off for this run", file=sys.stderr, flush=True)


def _notify(message: str):
    """Raise a desktop notification, if there is an application to raise it."""
    try:
        from gi.repository import Gio, GLib
    except ImportError:  # pragma: no cover - GI is a hard dependency of the app
        return
    app = Gio.Application.get_default()
    if app is None or not app.get_is_registered():
        # A CLI process, or one still starting: the log already has it.
        return
    notification = Gio.Notification.new("sponux")
    notification.set_body(message)
    try:
        # One id for all of them, so a second failure replaces the first on
        # screen instead of stacking up behind it.
        app.send_notification("sponux-problem", notification)
    except GLib.Error:
        pass
