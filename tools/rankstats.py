"""Is the frecency bonus still telling applications apart?

The curve saturates: ``log2(1+hits)/4`` reaches 1.0 at fifteen uses, so past
that an application's bonus is the full weight and only the decay steps
separate it from the next one. That was harmless while only applications
launched by name were counted — few of them get to fifteen. Now that opening a
file counts the application that opened it, the handful of things that open
everything will get there in days, and the worry is that they all pile up at
the cap and the bonus stops ranking anything.

This does not need two weeks of waiting to answer. usage.db already holds every
file this launcher has opened, with counts, and the `[open]` rules that decide
what opens them are deterministic — so the application hits that *would* have
been recorded can be replayed from data already on disk.

What comes out:

  now         the application half of the table as it stands
  projected   the same, plus every file open credited to its opener
  collisions  queries where the top two applications have both topped the
              curve out *and* carry the same bonus — the only place
              saturation can actually cost anything

Two things worth knowing before reading the numbers. Topping out is not the
same as reaching the cap: the cap needs decay 1.0 as well, i.e. a use within
the hour, so an application with fifty uses yesterday sits at 20.8 out of 25
and is still comfortably above one with two. And saturation on its own is not a
problem — ten applications with saturated counts that never appear in the same
search are ten correct answers. The check that matters is the last one.

Read-only: it opens usage.db with mode=ro and writes nothing anywhere.

Run: python3 tools/rankstats.py
"""

import argparse
import math
import os
import pathlib
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gi.repository import Gio                                    # noqa: E402

from sponux import config, usage, userconfig                     # noqa: E402
from sponux.providers import base                                # noqa: E402
from sponux.providers import files as files_provider             # noqa: E402

# How close two bonuses have to be before they are the same bonus. The point is
# whether two results are separated by anything a person could notice, and a
# tenth of a point out of 110 is not it.
SAME_BONUS = 0.1

# Uses at which log2(1+hits)/4 reaches 1.0 and the count stops contributing.
# Anything past this is separated from its rivals by the decay steps alone —
# which is the whole worry, so it is measured rather than assumed.
SATURATES_AT = 15

# Query lengths tried against each application's name. Nobody types eight
# characters into a launcher to reach an application they open every day; by
# the fourth the query has usually settled it on its own.
QUERY_LENGTHS = (1, 2, 3, 4)


def read_table(path):
    """usage.db as {key: (hits, last)}, without opening it for writing."""
    if not path.exists():
        sys.exit(f"{path} does not exist — nothing has been opened yet")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT key, hits, last FROM usage").fetchall()
    except sqlite3.Error as exc:
        sys.exit(f"cannot read {path}: {exc}")
    finally:
        con.close()
    return {key: (hits, last) for key, hits, last in rows}


def opener_for(path, apps):
    """The application that would open `path`, the way files._open() picks it.

    Deliberately the same two steps in the same order — the configured rule
    first, the desktop default second — because a projection that resolves
    openers differently from the code being projected measures nothing.
    """
    is_dir = os.path.isdir(path)
    argv, _rule = userconfig.resolve_opener(path, is_dir)
    if argv:
        return files_provider.app_for_command(argv[0], apps)
    return Gio.AppInfo.get_default_for_type(
        files_provider.content_type(path, is_dir), False)


def project(table, apps, indirect):
    """The application table as it would stand if every file open had also
    counted its opener, each such open worth `indirect` of a use.

    Returns (projected table, hits that resolved to no application).
    """
    out = {key: value for key, value in table.items() if key.startswith("app:")}
    unresolved = 0.0
    for key, (hits, last) in table.items():
        if not key.startswith("file:"):
            continue
        app = opener_for(key[len("file:"):], apps)
        if app is None:
            unresolved += hits
            continue
        app_key = usage.key_for_appinfo(app)
        seen_hits, seen_last = out.get(app_key, (0.0, 0.0))
        # The application's last use is the most recent of the things it
        # opened: that is what the decay would have been measured from.
        out[app_key] = (seen_hits + hits * indirect, max(seen_last, last))
    return out, unresolved


def bonuses(table, now):
    """The bonus each key would earn, through usage.py's own curve.

    The table is handed to usage.py by way of its cache rather than
    reimplemented here: a second copy of the formula would drift from the one
    that ships, and then this script would be measuring itself.
    """
    usage._cache = table
    return {key: usage.bonus(key, now) for key in table}


def uncapped(hits, last, now, weight):
    """What the bonus would have been with no ceiling on it.

    usage.bonus() with the min() taken off, sharing its decay table so only the
    one difference under examination differs. The gap between two of these is
    what the cap suppressed — and therefore the most saturation could have cost
    an ordering.
    """
    age = max(0.0, now - last)
    decay = usage._DECAY_OLD
    for limit, factor in usage._DECAY:
        if age < limit:
            decay = factor
            break
    return weight * math.log2(1 + hits) / 4.0 * decay


def names(apps):
    """{usage key: display name} for everything installed."""
    return {usage.key_for_appinfo(a): a.get_display_name() or a.get_name() or ""
            for a in apps}


def show(title, table, scores, label_for, limit=12):
    print(f"\n{title}")
    if not table:
        print("  (nothing)")
        return
    ranked = sorted(table.items(), key=lambda kv: -scores[kv[0]])
    for key, (hits, last) in ranked[:limit]:
        when = time.strftime("%Y-%m-%d", time.localtime(last)) if last else "—"
        name = label_for.get(key) or key[len("app:"):]
        print(f"  {name[:34]:<34} {hits:6.1f} uses   last {when}   "
              f"+{scores[key]:5.1f}")
    if len(ranked) > limit:
        print(f"  … and {len(ranked) - limit} more")


def collisions(apps, projected, scores, saturated, now, weight):
    """Queries whose top two applications are separated by nothing.

    Built from prefixes of the names of the saturated applications, because
    those are the queries that reach them. A pair counts only when both have
    topped the curve out *and* land on the same bonus: then the history the
    launcher kept contributed nothing to the order, fuzzy_score() alone decided
    it, and the ranking is back to the behaviour it was added to improve on.

    Three things are deliberately not collisions. A saturated application
    beating an unsaturated one, and a pair in different decay buckets — "used
    today" over "used last month" is the ranking working at a resolution the
    count no longer provides. And two applications with the same history: they
    are *supposed* to tie, and the cap suppressed nothing to make it happen.
    So a pair is only reported when lifting the ceiling would have put the
    other one first, which is the sharpest form of the question — did
    saturation cost this search its answer?
    """
    if len(saturated) < 2:
        return []

    queries = set()
    for app in apps:
        if usage.key_for_appinfo(app) not in saturated:
            continue
        name = (app.get_display_name() or "").lower()
        for length in QUERY_LENGTHS:
            if len(name) >= length:
                queries.add(name[:length])

    found = []
    for query in sorted(queries):
        ranked = []
        for app in apps:
            score = base.fuzzy_score(query, app.get_display_name() or "")
            if score <= 0:
                continue
            key = usage.key_for_appinfo(app)
            ranked.append((score + scores.get(key, 0.0), key, app))
        ranked.sort(key=lambda row: -row[0])
        if len(ranked) < 2:
            continue
        (top_score, top_key, top), (next_score, next_key, runner) = ranked[:2]
        if top_key not in saturated or next_key not in saturated:
            continue
        if abs(scores[top_key] - scores[next_key]) > SAME_BONUS:
            continue
        # What the cap held back, against what the query decided by. The
        # runner-up only had an outcome taken from it if the first exceeds
        # the second.
        held_back = (uncapped(*projected[next_key], now, weight)
                     - uncapped(*projected[top_key], now, weight))
        if held_back <= top_score - next_score:
            continue
        found.append((query, top.get_display_name(), top_score,
                      runner.get_display_name(), next_score, held_back))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=pathlib.Path, default=config.USAGE_DB,
                        help=f"usage database to read (default {config.USAGE_DB})")
    parser.add_argument("--indirect", type=float, default=1.0, metavar="W",
                        help="what one file open is worth to the application "
                             "that opened it (default 1.0, as shipped; try 0.5 "
                             "to see whether fractional credit restores the "
                             "spread)")
    args = parser.parse_args()

    enabled, weight = usage._settings()
    if not enabled:
        print("note: [rank] frecency = false in config.toml — the numbers below "
              "are what the bonus would be if it were on\n")

    now = time.time()
    apps = [a for a in Gio.AppInfo.get_all() if a.should_show()]
    label_for = names(apps)
    table = read_table(args.db)

    current = {k: v for k, v in table.items() if k.startswith("app:")}
    files_seen = sum(1 for k in table if k.startswith("file:"))
    print(f"{args.db}: {len(current)} application(s), {files_seen} file(s), "
          f"cap {weight:g}")
    show("now — applications as they are counted today",
         current, bonuses(dict(current), now), label_for)

    projected, unresolved = project(table, apps, args.indirect)
    scores = bonuses(projected, now)
    show(f"projected — every file open credited to its opener at "
         f"{args.indirect:g}x", projected, scores, label_for)
    if unresolved:
        print(f"  ({unresolved:g} file open(s) resolved to no application: no "
              "rule, no registered default, or an ambiguous command)")

    with_history = [k for k, (hits, _) in projected.items() if hits]
    saturated = {k for k in with_history
                 if projected[k][0] >= SATURATES_AT}
    distinct = len({round(scores[k], 1) for k in with_history})
    print(f"\nspread — {len(saturated)} of {len(with_history)} application(s) "
          f"with history have topped the curve out (>= {SATURATES_AT} uses), "
          f"{distinct} distinct bonus value(s), cap {weight:g}")

    clashes = collisions(apps, projected, scores, saturated, now, weight)
    print(f"collisions — {len(clashes)} query/queries the cap decided: top two "
          "saturated, same bonus, and lifting it would swap them")
    for query, top, top_score, runner, next_score, held_back in clashes[:10]:
        print(f"  {query!r:<8} {top} ({top_score:.1f}) over {runner} "
              f"({next_score:.1f}) — uncapped {runner} leads by "
              f"{held_back - (top_score - next_score):.1f}")
    if len(clashes) > 10:
        print(f"  … and {len(clashes) - 10} more")

    print()
    if not clashes:
        print("verdict: saturation costs nothing here — no query reaches two "
              "applications that have both topped out on the same bonus, so "
              "the history is still deciding every contest it takes part in.")
    else:
        print("verdict: there are searches where two applications carry an "
              "identical bonus and the query alone breaks the tie. If the "
              "wrong one of a listed pair wins in practice, fractional credit "
              "for indirect opens is the fix — rerun with --indirect 0.5 to "
              "see whether it separates them again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
