"""Checks for the file index: the rules, and the live incremental updates.

Two halves:

* **rules** — pure functions of a path, checked directly. This is the part a
  user's config.toml drives, so it is worth pinning down.
* **watching** — a real temporary tree, a real GLib main loop and real inotify
  events. Nothing here is mocked; the test creates, moves and deletes files and
  waits for the index to catch up, which is the only way to know the
  main-thread/worker-thread split actually works.

Run: python3 tests/test_index.py
"""

import os
import pathlib
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
    con = indexer.connect(readonly=True)
    try:
        rows = con.execute("SELECT path FROM files").fetchall()
    except sqlite3.OperationalError:
        return []  # the first build has not created the table yet
    finally:
        con.close()
    return sorted(os.path.relpath(p, TREE) for (p,) in rows)


def wait_for(predicate, timeout=6.0):
    """Poll until the index reflects a change, or give up. Returns the wait."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return round(time.monotonic() - start, 2)
        time.sleep(0.05)
    return None


def has(*names):
    return lambda: set(names) <= set(indexed())


def lacks(*names):
    return lambda: not (set(names) & set(indexed()))


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

# The main loop has to be running for the monitors to deliver anything; the
# daemon has one, this test starts its own.
loop = GLib.MainLoop()
threading.Thread(target=loop.run, daemon=True).start()
indexer.start_background()

check("initial build indexes the tree",
      wait_for(has("docs", "docs/notes.md")) is not None, True)
check("the included hidden tree is in it",
      sorted(p for p in indexed() if p.startswith(".config")),
      [".config", ".config/nvim", ".config/nvim/init.lua"])
check("the other hidden tree is not",
      [p for p in indexed() if p.startswith(".cache")], [])

armed = wait_for(lambda: len(indexer._monitors) >= 3)
check("watches were armed for the indexed directories", armed is not None, True)

# Now the point of the exercise: changes must land without a rebuild, which is
# an hour away.
(TREE / "docs" / "invoice.pdf").write_text("x")
took = wait_for(has("docs/invoice.pdf"))
check("a new file appears in the index", took is not None, True)
print(f"     … it took {took}s (the next full rebuild is 3600s away)")

(TREE / ".config" / "nvim" / "plugins.lua").write_text("x")
check("so does one inside a watched hidden tree",
      wait_for(has(".config/nvim/plugins.lua")) is not None, True)

(TREE / ".cache" / "more.tmp").write_text("x")
time.sleep(0.6)
check("a file in an unindexed tree stays out",
      [p for p in indexed() if p.startswith(".cache")], [])

os.remove(TREE / "docs" / "notes.md")
check("a deleted file leaves the index",
      wait_for(lacks("docs/notes.md")) is not None, True)

os.rename(TREE / "docs" / "invoice.pdf", TREE / "docs" / "invoice-2024.pdf")
check("a rename replaces the old name",
      wait_for(lambda: has("docs/invoice-2024.pdf")()
               and lacks("docs/invoice.pdf")()) is not None, True)

# A whole tree can arrive in one event — inotify says nothing about what is
# inside it, so the indexer has to walk it itself.
staging = pathlib.Path(_TMP) / "staging"
(staging / "src").mkdir(parents=True)
(staging / "src" / "main.rs").write_text("x")
(staging / "README.md").write_text("x")
shutil.move(str(staging), str(TREE / "imported"))
check("a directory moved in is indexed with its contents",
      wait_for(has("imported", "imported/src", "imported/src/main.rs",
                   "imported/README.md")) is not None, True)
check("and it is watched, so changes inside it are seen",
      wait_for(lambda: (TREE / "imported" / "src" / "lib.rs").write_text("x")
               or has("imported/src/lib.rs")()) is not None, True)

shutil.rmtree(TREE / "imported")
check("deleting a tree removes every path under it",
      wait_for(lacks("imported", "imported/src", "imported/src/main.rs"))
      is not None, True)

# Dotfiles are usually one repository symlinked into place. A symlinked
# directory that is indexed as a file makes its whole contents unfindable,
# which is most of the point of indexing ~/.config in the first place.
store = pathlib.Path(_TMP) / "dotfiles"
(store / "nvim").mkdir(parents=True)
(store / "nvim" / "init.lua").write_text("x")
os.symlink(store / "nvim", TREE / ".config" / "nvim-linked")
check("a symlinked config directory is followed",
      wait_for(has(".config/nvim-linked", ".config/nvim-linked/init.lua"))
      is not None, True)


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
      wait_for(lambda: is_dir_of(".config/nvim-linked") is True) is not None,
      True)

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

loop.quit()
shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
print("all index checks passed")
