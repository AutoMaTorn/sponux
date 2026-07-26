"""Checks for frecency: the bonus curve, the store, and the effect on results.

The curve matters more than it looks. Too weak and the feature is decoration;
too strong and the launcher stops answering the question you typed. The checks
below pin down both ends: history settles near-ties, and it never overturns a
clearly better match.

Run: python3 tests/test_usage.py
"""

import os
import pathlib
import shutil
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="sponux-usage-")
os.environ["XDG_STATE_HOME"] = f"{_TMP}/state"
os.environ["XDG_CACHE_HOME"] = f"{_TMP}/cache"
os.environ["XDG_CONFIG_HOME"] = f"{_TMP}/config"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import config, indexer, usage, userconfig  # noqa: E402
from sponux.providers import base, files as files_provider  # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


NOW = time.time()
HOUR, DAY, WEEK = 3600, 86400, 7 * 86400


def bonus_after(hits, ago):
    usage.forget_all()
    for _ in range(hits):
        usage.record("k", now=NOW - ago)
    return round(usage.bonus("k", now=NOW), 2)


# ---- the curve --------------------------------------------------------

check("something never opened gets nothing",
      usage.bonus("never-seen", now=NOW), 0.0)
check("one use today is a nudge, not a takeover",
      bonus_after(1, 60) < 10, True)
check("more uses help", bonus_after(5, 60) > bonus_after(1, 60), True)
check("but with diminishing returns",
      bonus_after(50, 60) - bonus_after(10, 60)
      < bonus_after(10, 60) - bonus_after(2, 60), True)
check("the bonus is capped at the configured weight",
      bonus_after(10000, 60) <= config.FRECENCY_WEIGHT, True)

check("the same count decays with age",
      [bonus_after(4, ago) for ago in (60, 2 * HOUR, 2 * DAY, 2 * WEEK)],
      sorted([bonus_after(4, ago)
              for ago in (60, 2 * HOUR, 2 * DAY, 2 * WEEK)], reverse=True))
check("twice today beats ten times last year",
      bonus_after(2, HOUR) > bonus_after(10, 400 * DAY), True)
check("but last year is not nothing", bonus_after(10, 400 * DAY) > 0, True)

# The cap is the whole safety argument: fuzzy_score gives 90 to a prefix match
# and 60 to a substring, and no amount of history may close a gap that size.
prefix = base.fuzzy_score("con", "config")
substring = base.fuzzy_score("con", "unconfigured")
check("a prefix match still wins against a heavily used substring match",
      prefix > substring + config.FRECENCY_WEIGHT, True)

# ---- the store --------------------------------------------------------

usage.forget_all()
usage.record("file:/a", now=NOW)
usage.record("file:/a", now=NOW)
usage.record("file:/b", now=NOW - DAY)
check("uses are counted", usage.stats("file:/a")[0], 2)
check("and timed", round(usage.stats("file:/a")[1]), round(NOW))
check("separately per key", usage.stats("file:/b")[0], 1)

usage._cache = None  # force a reload from disk
check("they survive a restart", usage.stats("file:/a"), (2, NOW))
check("the database is outside the cache directory",
      config.USAGE_DB.parent.name, "sponux")
check("in the state directory, which nobody clears to free space",
      "state" in str(config.USAGE_DB), True)

real_max = config.MAX_USAGE_ENTRIES
config.MAX_USAGE_ENTRIES = 10
for i in range(30):
    usage.record(f"file:/many/{i}", now=NOW - (30 - i) * HOUR)
check("the table is pruned to the cap", len(usage._table()) <= 10, True)
check("and it is the stale entries that go",
      usage.stats("file:/many/29") is not None
      and usage.stats("file:/many/0") is None, True)
config.MAX_USAGE_ENTRIES = real_max

# ---- switched off -----------------------------------------------------

real_settings = userconfig.settings
userconfig.settings = lambda: {"rank": {"frecency": False}}
usage.forget_all()
usage.record("file:/x", now=NOW)
check("[rank] frecency = false turns the bonus off",
      usage.bonus("file:/x", now=NOW), 0.0)
userconfig.settings = lambda: {"rank": {"weight": 5}}
check("and the weight is configurable",
      usage.bonus("file:/x", now=NOW) <= 5, True)
userconfig.settings = real_settings

# ---- the effect on a real search --------------------------------------

TREE = pathlib.Path(_TMP) / "tree"
(TREE / "old").mkdir(parents=True)
(TREE / "new").mkdir(parents=True)
(TREE / "old" / "notes.md").write_text("x")
(TREE / "new" / "notes.md").write_text("x")

(pathlib.Path(_TMP) / "config" / "sponux").mkdir(parents=True)
(pathlib.Path(_TMP) / "config" / "sponux" / "config.toml").write_text(
    f'[index]\nroots = ["{TREE}"]\ninterval = 0\nwatch = false\n'
)
indexer.build_index()
usage.forget_all()
files_provider._con = None

first = [r.subtitle for r in files_provider.search("notes")]
check("two equally good matches are found", len(first), 2)

# Open one of them, and it should come first from then on.
loser, winner = first[0], first[1]
usage.record(usage.key_for_file(winner), now=NOW)
after = [r.subtitle for r in files_provider.search("notes")]
check("the one that was opened is now first", after[0], winner)
check("the other is still offered", after[1], loser)

shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
print("all frecency checks passed")
