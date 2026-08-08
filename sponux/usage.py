"""What has been opened, and how long ago — the launcher's memory.

A launcher that ranks purely on how well the text matches is wrong about the
same thing every day: of two files called "config", the one you edit every
morning and the one you have never opened score identically. This module keeps
a hit count and a last-used time per thing, and turns them into a bonus that
the providers add to their match score.

The rules it follows, in order of importance:

* **Recency outweighs volume.** Something opened ten times last year says less
  about now than something opened twice today, so the count is compressed
  logarithmically and then scaled by how recent the last use was.
* **History never beats a clearly better match.** The bonus is capped at
  ``config.FRECENCY_WEIGHT``; fuzzy_score()'s steps between an exact, prefix,
  word-start and substring match are wider than that at the top end, so
  history decides near-ties rather than overruling the query.
* **It is cheap.** The table is small enough to hold in memory, so a search
  does dictionary lookups, not queries.
"""

import fnmatch
import math
import sqlite3
import time

from . import config, report, userconfig

_cache = None      # key -> (hits, last_used); None until first loaded
_con = None

# Multipliers on the hit count by age of the last use. Deliberately a step
# function rather than an exponential: the steps are explainable ("used it
# today", "used it this week") and the exact curve does not matter.
_DECAY = (
    (3600, 1.0),        # within the hour
    (86400, 0.8),       # today
    (7 * 86400, 0.5),   # this week
    (30 * 86400, 0.3),  # this month
)
_DECAY_OLD = 0.15


def key_for_file(path: str) -> str:
    return f"file:{path}"


def key_for_app(app_id: str) -> str:
    return f"app:{app_id}"


def key_for_appinfo(app) -> str:
    """The key for a Gio.AppInfo, wherever it was reached from.

    Everything that can end up launching an application has to arrive at the
    same string — the provider that searches them, the "open with" list, the
    `[open]` rules — or a use counted in one place is invisible in the others.
    That failure is silent: the ranking simply never changes, and there is
    nothing in the log to say why. The id is the identity ("code.desktop"); the
    display name is the fallback for an entry that has none.
    """
    name = app.get_display_name() or app.get_name() or ""
    return key_for_app(app.get_id() or name)


def _connect():
    global _con
    if _con is None:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        _con = sqlite3.connect(config.USAGE_DB, check_same_thread=False)
        _con.execute("PRAGMA busy_timeout=3000")
        _con.execute(
            """CREATE TABLE IF NOT EXISTS usage(
                   key TEXT PRIMARY KEY,
                   hits INTEGER NOT NULL DEFAULT 0,
                   last REAL NOT NULL
               )"""
        )
        _con.commit()
    return _con


def _settings():
    section = userconfig.settings().get("rank")
    if not isinstance(section, dict):
        section = {}
    enabled = section.get("frecency")
    weight = section.get("weight")
    return (
        bool(enabled) if isinstance(enabled, bool) else config.FRECENCY,
        float(weight) if isinstance(weight, (int, float)) and weight >= 0
        else config.FRECENCY_WEIGHT,
    )


def indirect_weight() -> float:
    """What one indirect open is worth, from `[rank] indirect`.

    Read per call rather than cached because userconfig.settings() re-reads
    config.toml on mtime, and this is on the open path, not the typing path.
    """
    section = userconfig.settings().get("rank")
    if not isinstance(section, dict):
        section = {}
    value = section.get("indirect")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return config.FRECENCY_INDIRECT


def _table():
    """The whole usage table, in memory. Loaded once, updated in place."""
    global _cache
    if _cache is None:
        _cache = {}
        try:
            rows = _connect().execute("SELECT key, hits, last FROM usage")
            _cache = {key: (hits, last) for key, hits, last in rows}
        except sqlite3.Error as exc:
            # A launcher that cannot rank is fine; one that will not open is
            # not. Logged, but nothing to interrupt anyone about.
            report.problem(f"cannot read {config.USAGE_DB}: {exc}")
    return _cache


def record(key: str, now: float = None, weight: float = 1.0):
    """Count one use of `key`. Called when something is actually opened.

    `weight` is how much of a use this one is worth: 1.0 for something the user
    asked for by name, less for evidence that is weaker than that — see
    record_opener(). Fractions need no migration. The column is declared
    INTEGER, but SQLite's affinity keeps a value it cannot store losslessly as
    a REAL, so 3 + 0.5 is 3.5 in the same column, and every reader of it already
    treats hits as a number rather than a count of anything.
    """
    if not key or weight <= 0:
        return
    now = time.time() if now is None else now
    table = _table()
    hits = table.get(key, (0, 0.0))[0] + weight
    table[key] = (hits, now)
    try:
        con = _connect()
        # The increment comes from the row on disk (`hits + excluded.hits`),
        # never from the number computed above: the cache can be a moment
        # behind another process, and adding to a stale total would quietly
        # overwrite that process's uses.
        con.execute(
            """INSERT INTO usage(key, hits, last) VALUES(?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET hits = hits + excluded.hits,
                                              last = excluded.last""",
            (key, weight, now),
        )
        con.commit()
        if len(table) > config.MAX_USAGE_ENTRIES:
            _prune(con)
    except sqlite3.Error as exc:
        report.problem(f"cannot record use of {key}: {exc}")


def record_app(app, now: float = None, weight: float = 1.0) -> str:
    """Count one use of an application; return the key it was counted under.

    Called wherever an application actually runs something, not only where one
    was searched for: opening a project folder with an editor is a use of that
    editor, and until it was counted the launcher kept ranking the editor as if
    it had never been touched.
    """
    key = key_for_appinfo(app)
    record(key, now, weight)
    return key


def record_opener(app, now: float = None) -> str:
    """Count one *indirect* use — an application that opened something because
    a rule or the desktop said so, rather than because it was named.

    Worth less than a deliberate launch, and the reason is not only saturation.
    Choosing an application from the "open with" list, or typing its name and
    pressing Enter, is someone saying which application they want. A `[open]`
    rule firing says only that a file was opened; the application in it was
    chosen once, months ago, and every file since has been repeating that one
    decision. Weaker evidence, counted as less of it.

    The size of "less" is `[rank] indirect`, 0.5 by default: two automatic
    opens then weigh exactly as much as one deliberate launch, which is a
    sentence anyone can check against the numbers.
    """
    return record_app(app, now, weight=indirect_weight())


def opens() -> int:
    """How many times the launcher has been opened, as far as the file knows."""
    try:
        return int(config.FIRST_RUN_FILE.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def looks_fresh() -> bool:
    """True while nothing suggests sponux has been used here before.

    Two signals, because either alone lies: the counter file is missing on an
    install older than the counter, and the count is low on someone who opened
    the launcher twice a year ago.
    """
    return opens() <= config.FIRST_RUN_HINTS and not _table()


def record_open() -> int:
    """Count one open of the launcher; return the count, capped.

    The number stops rising one past the hint — the point is answering "is
    this a fresh install?", not keeping a lifetime tally.

    Installing sponux and not binding a key gives a program that looks broken:
    the desktop entry opens the window, and nothing in the window says that a
    hotkey is the point. The first few opens say so instead of teaching the
    prefixes, which needs a count that outlives the daemon.

    Deliberately not a row in the usage table: that table is keyed by result
    and gets pruned, so a phantom key in it would be both rankable and
    perishable. This is one small file with a number in it.
    """
    total = opens() + 1
    if total == 1 and _table():
        # No counter file, but things have been opened: this is an install that
        # predates the counter, not a first run. Do not lecture it.
        total = config.FIRST_RUN_HINTS + 1
    if total > config.FIRST_RUN_HINTS + 1:
        # Past the hint for good; stop writing on every open forever.
        return total
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        config.FIRST_RUN_FILE.write_text(f"{total}\n")
    except OSError as exc:
        report.problem(f"cannot record the first-run hint: {exc}")
    return total


def _prune(con):
    """Drop the least recently used entries once the table gets long."""
    keep = config.MAX_USAGE_ENTRIES
    con.execute(
        """DELETE FROM usage WHERE key NOT IN
           (SELECT key FROM usage ORDER BY last DESC LIMIT ?)""",
        (keep,),
    )
    con.commit()
    global _cache
    _cache = None


def bonus(key: str, now: float = None) -> float:
    """The score to add for something's history. 0 when it has none."""
    if not key:
        return 0.0
    enabled, weight = _settings()
    if not enabled:
        return 0.0
    entry = _table().get(key)
    if entry is None:
        return 0.0
    hits, last = entry
    now = time.time() if now is None else now
    age = max(0.0, now - last)
    decay = _DECAY_OLD
    for limit, factor in _DECAY:
        if age < limit:
            decay = factor
            break
    # log2 so the tenth use matters less than the second, and the hundredth
    # barely at all — otherwise one heavily used file would win every search.
    return min(weight, weight * math.log2(1 + hits) / 4.0 * decay)


def order_by_usage(items, key_of, now: float = None):
    """`items` reordered most-used first, leaving what has no history put.

    Python's sort is stable, so this promotes what has actually been opened
    rather than ranking everything: the caller's own order — applications
    registered for the type before the rest, the desktop default before its
    peers — still decides between two things the user has never chosen.

    Deliberately applied across the whole list rather than inside those groups.
    The editor someone opens every project folder with is very often not
    registered for `inode/directory` at all, and pinning it below the file
    managers forever is the exact case this is here for.
    """
    now = time.time() if now is None else now
    return sorted(items, key=lambda item: -bonus(key_of(item), now))


def stats(key: str):
    """(hits, last_used) for `key`, or None. For --which and tests."""
    return _table().get(key)


def forget(key: str) -> bool:
    """Drop one thing's history. True if there was anything to drop.

    The database is written before the in-memory table, which is the opposite
    order from record(). Deliberately: a failed write there would mean telling
    someone their history is gone while it is still on disk waiting to come
    back at the next restart, and that is not a lie they have any way to catch.
    """
    if not key or _table().get(key) is None:
        return False
    try:
        con = _connect()
        con.execute("DELETE FROM usage WHERE key = ?", (key,))
        con.commit()
    except sqlite3.Error as exc:
        # Someone pressed a key and nothing happened: exactly what a
        # notification is for.
        report.problem(f"cannot forget {key}: {exc}", notify=True)
        return False
    _table().pop(key, None)
    return True


def matching(pattern: str):
    """Remembered keys whose path or application id matches `pattern`.

    A bare word matches as a substring — `--forget notes` is meant to reach
    ~/work/notes.md, not something named exactly "notes" — while a pattern
    with a wildcard in it is used as written, so `*.py` and `/tmp/*` mean what
    they look like. fnmatch, the same glob dialect `[open.name]` already uses,
    and matched against the path or id rather than the whole key so that a
    pattern does not have to know about the `file:`/`app:` prefixes.
    """
    if not pattern:
        return []
    glob = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
    glob = glob.lower()
    return sorted(key for key in _table()
                  if fnmatch.fnmatch(key.partition(":")[2].lower(), glob))


def forget_all():
    """Drop everything. Behind `--forget --all`, and used by the tests."""
    global _cache
    _cache = {}
    try:
        con = _connect()
        con.execute("DELETE FROM usage")
        con.commit()
    except sqlite3.Error:
        pass
