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

# ---- applications you open things with --------------------------------
#
# Opening a project folder with an editor is a use of that editor. Only the
# folder used to be counted, so the editor stayed ranked as though it had never
# been touched, and the "open with" list offered it in the same place forever.


class FakeApp:
    """The parts of Gio.AppInfo that ranking touches.

    A stub rather than the real thing because CI installs GTK and no
    applications: a check written against whatever .desktop files happen to
    exist would pass or fail for reasons that have nothing to do with sponux.
    The one check below that does need a real application says so, and skips.
    """

    def __init__(self, app_id, name, executable="", shown=True):
        self._id, self._name = app_id, name
        self._exe, self._shown = executable, shown

    def get_id(self):
        return self._id

    def get_display_name(self):
        return self._name

    def get_name(self):
        return self._name

    def get_executable(self):
        return self._exe

    def should_show(self):
        return self._shown

    def __repr__(self):
        return f"<{self._id or self._name}>"


code = FakeApp("code.desktop", "Visual Studio Code", "/usr/bin/code")
files_app = FakeApp("org.gnome.Nautilus.desktop", "Files", "/usr/bin/nautilus")
gedit = FakeApp("org.gnome.gedit.desktop", "Text Editor", "/usr/bin/gedit")
# Same binary, hidden: the shape that would otherwise make "code" ambiguous.
handler = FakeApp("code-url-handler.desktop", "Code URL Handler",
                  "/usr/bin/code", shown=False)

usage.forget_all()
check("an application is counted under its id",
      usage.key_for_appinfo(code), "app:code.desktop")
check("...and under its name when it has no id",
      usage.key_for_appinfo(FakeApp(None, "Nameless")), "app:Nameless")
check("recording one returns the key it was counted under",
      usage.record_app(code, now=NOW), usage.key_for_appinfo(code))
check("...and that key now carries a bonus",
      usage.bonus(usage.key_for_appinfo(code), now=NOW) > 0, True)

# The bug this whole thing dies of is a key mismatch: counted under one string,
# searched under another, no error anywhere. Checked against a real
# application, since that is where the two sides actually have to meet.
from gi.repository import Gio  # noqa: E402
from sponux.providers import apps as apps_provider  # noqa: E402

real = [a for a in Gio.AppInfo.get_all()
        if a.should_show() and a.get_display_name()]
if real:
    sample = real[0]
    name = sample.get_display_name()
    hits = [r for r in apps_provider.search(name, 50) if r.title == name]
    check("the apps provider ranks on the key open-with records",
          bool(hits) and hits[0].usage_key == usage.key_for_appinfo(sample),
          True)
else:
    print("skip  no applications installed here to check the key against")

# The "open with" list, which is ordered by this and nothing else.
shelf = [files_app, gedit, code]      # an editor is not registered for a folder
usage.forget_all()
check("with no history the order is left exactly as it was",
      usage.order_by_usage(shelf, usage.key_for_appinfo, now=NOW), shelf)
usage.record_app(code, now=NOW)
check("what was chosen before comes first, out of the tail",
      usage.order_by_usage(shelf, usage.key_for_appinfo, now=NOW)[0], code)
usage.record_app(files_app, now=NOW - 400 * DAY)
check("an old choice ranks above no choice, and below a recent one",
      usage.order_by_usage(shelf, usage.key_for_appinfo, now=NOW),
      [code, files_app, gedit])

# Opening by an [open] rule credits the application that rule runs, which has
# to be found from a command line: "code", not "code.desktop".
installed = [code, handler, files_app]
check("a command maps to the application that runs it",
      files_provider.app_for_command("code", installed), code)
check("...however the rule spells the path to it",
      files_provider.app_for_command("/usr/local/bin/code", installed), code)
check("a hidden twin does not make it ambiguous",
      files_provider.app_for_command("code", installed) is code, True)
check("an unknown command credits nobody",
      files_provider.app_for_command("nvim", installed), None)
rival = FakeApp("vscodium.desktop", "VSCodium", "/usr/bin/code")
check("two visible entries for one binary credit neither",
      files_provider.app_for_command("code", [rival, handler,
                                              FakeApp("other.desktop", "Other",
                                                      "/usr/bin/code")]), None)
check("...unless one of them is the entry named after the command",
      files_provider.app_for_command("code", [rival, code]), code)

# ---- the first-run counter, which shares this state directory -------------
#
# It answers one question — does this install look untouched? — and the two
# ways it can lie are an install older than the counter (no file, plenty of
# history) and someone who opened the launcher twice a year ago.

config.FIRST_RUN_FILE.unlink(missing_ok=True)
usage.forget_all()

check("an untouched install has no opens", usage.opens(), 0)
check("...and looks fresh", usage.looks_fresh(), True)

counted = [usage.record_open() for _ in range(config.FIRST_RUN_HINTS + 3)]
check("opens are counted until one past the hint, then stop",
      counted, [1, 2, 3, 4, 5, 5])
check("the hint is shown for exactly the first three",
      [n <= config.FIRST_RUN_HINTS for n in counted],
      [True, True, True, False, False, False])
check("...and the file holds the capped count", usage.opens(),
      config.FIRST_RUN_HINTS + 1)
check("a counted-out install no longer looks fresh", usage.looks_fresh(), False)

# The upgrade case: no counter file, but the usage table says otherwise.
config.FIRST_RUN_FILE.unlink(missing_ok=True)
usage.record(usage.key_for_app("kitty.desktop"), now=NOW)
check("history alone is enough to stop looking fresh",
      usage.looks_fresh(), False)
check("...and the first open after an upgrade skips the hint",
      usage.record_open() <= config.FIRST_RUN_HINTS, False)

shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
print("all frecency checks passed")
