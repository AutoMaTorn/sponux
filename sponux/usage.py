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


def record(key: str, now: float = None):
    """Count one use of `key`. Called when something is actually opened."""
    if not key:
        return
    now = time.time() if now is None else now
    table = _table()
    hits = table.get(key, (0, 0.0))[0] + 1
    table[key] = (hits, now)
    try:
        con = _connect()
        con.execute(
            """INSERT INTO usage(key, hits, last) VALUES(?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET hits = hits + 1, last = excluded.last""",
            (key, hits, now),
        )
        con.commit()
        if len(table) > config.MAX_USAGE_ENTRIES:
            _prune(con)
    except sqlite3.Error as exc:
        report.problem(f"cannot record use of {key}: {exc}")


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


def stats(key: str):
    """(hits, last_used) for `key`, or None. For --which and tests."""
    return _table().get(key)


def forget_all():
    """Drop everything. Only for tests; there is no UI for this."""
    global _cache
    _cache = {}
    try:
        con = _connect()
        con.execute("DELETE FROM usage")
        con.commit()
    except sqlite3.Error:
        pass
