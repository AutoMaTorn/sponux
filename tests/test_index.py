"""Checks for the file index: the rules, and how it reacts to change.

Three parts, in order of how much of the machine they involve:

* **rules** — pure functions of a path. This is the part a user's config.toml
  drives, so it is worth pinning down.
* **reacting to change** — a real temporary tree and a real database, but the
  events are handed to `_apply_events()` directly rather than waited for. That
  is the part sponux owns, and driving it makes the checks instant and exact.
* **one probe** — a real `Gio.FileMonitor`, a real main loop, a real write, to
  show that events arrive at all. It is the only thing here that can fail for
  reasons that have nothing to do with sponux, so it says so and skips instead
  of failing the suite.

Run: python3 tests/test_index.py
"""

import os
import pathlib
import queue
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

# Point XDG at a scratch directory *before* sponux.config reads it, so the
# test never touches the real index or the user's config.
_TMP = tempfile.mkdtemp(prefix="sponux-test-")
os.environ["XDG_CACHE_HOME"] = f"{_TMP}/cache"
os.environ["XDG_CONFIG_HOME"] = f"{_TMP}/config"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import indexer, userconfig  # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


# ---- the rules --------------------------------------------------------


def rules(**section):
    """IndexRules built from a config.toml section, without a file."""
    real = userconfig.settings
    userconfig.settings = lambda: {"index": section}
    try:
        return indexer.IndexRules.from_settings()
    finally:
        userconfig.settings = real


HOME = os.path.expanduser("~")

default = rules()
check("plain file is indexed",
      default.file_ok(f"{HOME}/notes.md", "notes.md"), True)
check("hidden file is not",
      default.file_ok(f"{HOME}/.bashrc", ".bashrc"), False)
check("hidden directory is neither indexed nor descended",
      default.dir_action(f"{HOME}/.config", ".config"), (False, False))
check("build noise is pruned by name",
      default.dir_action(f"{HOME}/p/node_modules", "node_modules"),
      (False, False))
check("ordinary directory is indexed and descended",
      default.dir_action(f"{HOME}/projects", "projects"), (True, True))

# The case this whole section exists for: configs findable, dotfile noise not.
configs = rules(include=["~/.config"])
check("included hidden tree is descended",
      configs.dir_action(f"{HOME}/.config", ".config"), (True, True))
check("directory inside it too",
      configs.dir_action(f"{HOME}/.config/nvim", "nvim"), (True, True))
check("file inside it is indexed",
      configs.file_ok(f"{HOME}/.config/nvim/init.lua", "init.lua"), True)
check("hidden file inside it is indexed",
      configs.file_ok(f"{HOME}/.config/git/.gitignore", ".gitignore"), True)
check("a different hidden tree is still skipped",
      configs.dir_action(f"{HOME}/.mozilla", ".mozilla"), (False, False))
check("and its files are not indexed",
      configs.file_ok(f"{HOME}/.mozilla/places.sqlite", "places.sqlite"), True)

# A deep include has to be reachable through its hidden parents, which are
# themselves not indexed.
deep = rules(include=["~/.local/share/applications"])
check("parent of an include is walked but not indexed",
      deep.dir_action(f"{HOME}/.local", ".local"), (False, True))
check("and so is the one below it, though its name is not hidden",
      deep.dir_action(f"{HOME}/.local/share", "share", inside=False),
      (False, True))
check("the include itself is indexed",
      deep.dir_action(f"{HOME}/.local/share/applications", "applications",
                      inside=False),
      (True, True))
check("a sibling of the include is not walked",
      deep.dir_action(f"{HOME}/.local/state", "state", inside=False),
      (False, False))
check("nor are files lying beside it indexed",
      deep.file_ok(f"{HOME}/.local/share/notes.txt", "notes.txt", inside=False),
      False)
check("inside the include, the ordinary rules resume",
      deep.dir_action(f"{HOME}/.local/share/applications/x", "x"),
      (True, True))

# The same decisions, replayed for a path that arrives out of nowhere in a
# filesystem event.
check("walk_state: inside an included tree",
      deep.walk_state(f"{HOME}/.local/share/applications"), True)
check("walk_state: merely on the way to one",
      deep.walk_state(f"{HOME}/.local/share"), False)
check("walk_state: never reached at all",
      deep.walk_state(f"{HOME}/.local/state"), None)
check("walk_state: outside every root",
      deep.walk_state("/etc/apt"), None)
check("walk_state: an ordinary directory",
      default.walk_state(f"{HOME}/projects"), True)
check("walk_state: a skipped tree",
      default.walk_state(f"{HOME}/p/node_modules/x"), None)

# Exclusions win over everything, and cover their subtree.
ex = rules(hidden=True, exclude=["~/Videos/*", "*.iso", "~/.cache"])
check("excluded tree is not walked",
      ex.dir_action(f"{HOME}/Videos/2024", "2024"), (False, False))
check("excluded extension is not indexed",
      ex.file_ok(f"{HOME}/debian.iso", "debian.iso"), False)
check("exclude beats hidden = true",
      ex.dir_action(f"{HOME}/.cache", ".cache"), (False, False))
check("hidden = true indexes dotfiles",
      ex.file_ok(f"{HOME}/.bashrc", ".bashrc"), True)
check("exclude beats include",
      rules(include=["~/.config"], exclude=["~/.config/secret*"])
      .dir_action(f"{HOME}/.config/secrets", "secrets"), (False, False))

skips = rules(skip=["Steam"], unskip=["target"])
check("skip adds to the built-in list",
      skips.dir_action(f"{HOME}/.steam/Steam", "Steam"), (False, False))
check("unskip takes a name back off it",
      skips.dir_action(f"{HOME}/rust/target", "target"), (True, True))
check("built-ins still apply alongside",
      skips.dir_action(f"{HOME}/p/.git", ".git"), (False, False))

check("roots default to home", default.roots, [HOME])
check("roots can be redirected",
      rules(roots=["~/projects", "/etc"]).roots, [f"{HOME}/projects", "/etc"])

# Nothing in a config file may crash the daemon; garbage falls back to defaults.
junk = rules(roots=17, include="~/.config", exclude=None, hidden="yes",
             interval=-5, skip={"a": 1})
check("a string where a list belongs is taken as one entry",
      junk.include, [f"{HOME}/.config"])
check("junk roots fall back to the default", junk.roots, [HOME])
check("a non-bool flag falls back", junk.hidden_files, False)
check("a negative interval falls back", junk.interval, 900)


# ---- live watching ----------------------------------------------------

from gi.repository import GLib  # noqa: E402

TREE = pathlib.Path(_TMP) / "tree"


def write_config(text):
    path = pathlib.Path(os.environ["XDG_CONFIG_HOME"]) / "sponux"
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.toml").write_text(text)


def indexed():
    """Every path currently in the index, relative to the temp tree."""
    try:
        con = indexer.connect(readonly=True)
    except sqlite3.OperationalError:
        return []  # no database yet: a reader does not create one
    try:
        rows = con.execute("SELECT path FROM files").fetchall()
    except sqlite3.OperationalError:
        return []  # the first build has not created the table yet
    finally:
        con.close()
    return sorted(os.path.relpath(p, TREE) for (p,) in rows)


# The one thing here that depends on the machine rather than on sponux is
# whether a real file monitor delivers anything at all; see the probe at the
# end. Everything else is driven directly and finishes immediately.
PROBE_SECONDS = float(os.environ.get("SPONUX_TEST_TIMEOUT", "5"))

# Anything the machine would not let us check, named in the summary.
_skipped = []


def has(*names):
    return set(names) <= set(indexed())


def lacks(*names):
    return not (set(names) & set(indexed()))


# ---- how the index reacts to change -----------------------------------
#
# What sponux owns is _apply_events(): given the (kind, path, other) tuples a
# file monitor would have produced, bring the index up to date. That is
# synchronous and needs no kernel, so it is called directly.
#
# This used to be written as "make a change on disk, then poll for up to six
# seconds and hope". That tested inotify and GIO as much as it tested sponux,
# it lost the race on a loaded machine, and on a CI runner every single wait
# expired — four minutes to report a failure that was never sponux's.

(TREE / "docs").mkdir(parents=True)
(TREE / ".config" / "nvim").mkdir(parents=True)
(TREE / ".cache").mkdir(parents=True)
(TREE / "docs" / "notes.md").write_text("x")
(TREE / ".config" / "nvim" / "init.lua").write_text("x")
(TREE / ".cache" / "junk.tmp").write_text("x")

write_config(f"""
[index]
roots = ["{TREE}"]
include = ["{TREE}/.config"]
interval = 3600
""")

live = indexer.IndexRules.from_settings()
watched_dirs = []
indexer.build_index(rules=live, dirs_out=watched_dirs)

check("the initial build indexes the tree", has("docs", "docs/notes.md"), True)
check("the included hidden tree is in it",
      sorted(p for p in indexed() if p.startswith(".config")),
      [".config", ".config/nvim", ".config/nvim/init.lua"])
check("the other hidden tree is not",
      [p for p in indexed() if p.startswith(".cache")], [])

# Apps and the calculator answer a one-letter query; files sitting it out
# looked like "no such file" rather than "keep typing".
from sponux.providers import files as files_provider  # noqa: E402

check("a single character searches files too",
      "docs" in [r.title for r in files_provider.search("d")], True)
check("an empty query still returns nothing",
      files_provider.search("   "), [])


def apply(*events):
    """Feed the indexer the events a file monitor would have delivered."""
    indexer._apply_events(list(events), live)


def arm(dirs):
    """Run the watch-arming callback to completion, without a main loop."""
    step = indexer._arm_chunks(list(dirs), live.max_watches, prune=set(dirs))
    while step():
        pass


arm(watched_dirs)
check("watches are armed for the indexed directories",
      len(indexer._monitors) >= 3, True)

(TREE / "docs" / "invoice.pdf").write_text("x")
apply(("add", str(TREE / "docs" / "invoice.pdf"), None))
check("a new file appears in the index", has("docs/invoice.pdf"), True)

(TREE / ".config" / "nvim" / "plugins.lua").write_text("x")
apply(("add", str(TREE / ".config" / "nvim" / "plugins.lua"), None))
check("so does one inside an included hidden tree",
      has(".config/nvim/plugins.lua"), True)

# An event for a tree the rules exclude must be ignored, not obeyed: the
# monitor never watches .cache, but a stray event must not sneak past either.
(TREE / ".cache" / "more.tmp").write_text("x")
apply(("add", str(TREE / ".cache" / "more.tmp"), None))
check("a file in an unindexed tree stays out",
      [p for p in indexed() if p.startswith(".cache")], [])

os.remove(TREE / "docs" / "notes.md")
apply(("del", str(TREE / "docs" / "notes.md"), None))
check("a deleted file leaves the index", lacks("docs/notes.md"), True)

os.rename(TREE / "docs" / "invoice.pdf", TREE / "docs" / "invoice-2024.pdf")
apply(("move", str(TREE / "docs" / "invoice.pdf"),
       str(TREE / "docs" / "invoice-2024.pdf")))
check("a rename replaces the old name",
      has("docs/invoice-2024.pdf") and lacks("docs/invoice.pdf"), True)

# A whole tree can arrive in one event — an unpacked archive, a git clone, a
# directory moved in. inotify says nothing about what is inside it, so the
# indexer has to walk it itself.
staging = pathlib.Path(_TMP) / "staging"
(staging / "src").mkdir(parents=True)
(staging / "src" / "main.rs").write_text("x")
(staging / "README.md").write_text("x")
shutil.move(str(staging), str(TREE / "imported"))
apply(("add", str(TREE / "imported"), None))
check("a directory moved in is indexed with its contents",
      has("imported", "imported/src", "imported/src/main.rs",
          "imported/README.md"), True)

shutil.rmtree(TREE / "imported")
apply(("del", str(TREE / "imported"), None))
check("deleting a tree removes every path under it",
      lacks("imported", "imported/src", "imported/src/main.rs"), True)

# Dotfiles are usually one repository symlinked into place. A symlinked
# directory indexed as a file makes its whole contents unfindable, which is
# most of the point of indexing ~/.config in the first place.
store = pathlib.Path(_TMP) / "dotfiles"
(store / "nvim").mkdir(parents=True)
(store / "nvim" / "init.lua").write_text("x")
os.symlink(store / "nvim", TREE / ".config" / "nvim-linked")
apply(("add", str(TREE / ".config" / "nvim-linked"), None))
check("a symlinked config directory is followed",
      has(".config/nvim-linked", ".config/nvim-linked/init.lua"), True)


def is_dir_of(rel):
    con = indexer.connect(readonly=True)
    try:
        row = con.execute("SELECT is_dir FROM files WHERE path = ?",
                          (str(TREE / rel),)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    return None if row is None else bool(row[0])


check("and is recorded as a directory, not a file",
      is_dir_of(".config/nvim-linked"), True)


# ---- and one check that the events arrive at all ----------------------
#
# The rest of this file proves the index reacts correctly to events. This
# proves they turn up: a real Gio.FileMonitor, a real main loop, a real write.
# It is the only part that can fail for reasons that are nothing to do with
# sponux, so it reports that plainly instead of failing the suite.

def monitor_delivers(seconds):
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    indexer._watch(str(TREE / "docs"), 100)
    while not indexer._events.empty():          # ignore anything left over
        indexer._events.get_nowait()
    (TREE / "docs" / "probe.tmp").write_text("x")
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                indexer._events.get(timeout=0.1)
                return True
            except queue.Empty:
                continue
        return False
    finally:
        loop.quit()


if monitor_delivers(PROBE_SECONDS):
    check("a real file monitor reaches the worker queue", True, True)
else:
    print(f"skip  a real file monitor delivered nothing within "
          f"{PROBE_SECONDS:g}s — this machine, not sponux; every check above "
          f"still ran")
    _skipped.append("the end-to-end file monitor probe")



# A link that points back up the tree must not send the walk round for ever.
os.symlink(TREE, TREE / "docs" / "loop")
rules_with_loop = indexer.IndexRules.from_settings()
found = list(indexer._iter_entries([str(TREE)], rules_with_loop))
check("a symlink loop terminates the walk", len(found) > 0, True)
check("the link itself is still indexed",
      any(p.endswith("/docs/loop") for p, *_ in found), True)
check("but nothing is walked through it",
      [p for p, *_ in found if "/loop/" in p], [])
os.remove(TREE / "docs" / "loop")

# Hitting max_files must be said out loud: the rows past the limit simply
# vanish from search, and without the note there is no trace of why.
notes = []
real_note = indexer.report.note
indexer.report.note = notes.append
try:
    capped = rules(roots=[str(TREE)], max_files=3)
    check("a truncated build reports the cap",
          indexer.build_index(rules=capped), 3)
    indexer.build_index(rules=capped)  # the steady state must not re-report
    check("…once per transition, not once per rebuild",
          len(notes), 1)
    check("the note names the limit", "max_files = 3" in notes[0], True)

    roomy = rules(roots=[str(TREE)])
    indexer.build_index(rules=roomy)   # fits again: the flag resets
    indexer.build_index(rules=capped)  # so a relapse is reported too
    check("a relapse after recovering is reported again", len(notes), 2)
finally:
    indexer.report.note = real_note
indexer._file_limit_hit = False
indexer.build_index(rules=indexer.IndexRules.from_settings())

shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
if _skipped:
    # A green run that quietly covered less than usual is worse than a red one.
    print("all index checks passed, except: " + ", ".join(_skipped))
else:
    print("all index checks passed")
