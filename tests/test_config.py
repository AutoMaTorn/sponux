"""Checks for config.toml, and for appearance staying out of it.

The [open] resolution order is the whole feature there, and it is easy to get
wrong in a way that only shows up as "sponux opened my video in an editor".
Every case below is the argv that would actually be spawned, built from a
config parsed as TOML rather than hand-built, so the config syntax is checked
along with the behaviour.

Run: python3 tests/test_config.py
"""

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import userconfig  # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


def opener(toml_text, path, is_dir=False):
    real = userconfig.settings
    userconfig.settings = lambda: tomllib.loads(toml_text)
    try:
        return userconfig.opener_command(path, is_dir)
    finally:
        userconfig.settings = real


FULL = """
[open]
directory = "thunar"
default = "xdg-open"

[open.name]
".*rc" = "code"
"Makefile" = "code"

[open.extension]
py = "code --goto {path}:1"
png = "eog"

[open.mime]
"text/*" = "code"
"video/*" = "mpv"
"""

check("a directory goes to the file manager",
      opener(FULL, "/home/u/projects", is_dir=True), ["thunar", "/home/u/projects"])
check("an extension rule wins over mime",
      opener(FULL, "/home/u/a.py"), ["code", "--goto", "/home/u/a.py:1"])
check("{path} is substituted, not appended",
      opener(FULL, "/home/u/a.py")[-1], "/home/u/a.py:1")
check("mime catches what no extension rule named",
      opener(FULL, "/home/u/a.rs"), ["code", "/home/u/a.rs"])
check("a video does not end up in the editor",
      opener(FULL, "/home/u/a.mp4"), ["mpv", "/home/u/a.mp4"])
check("an image rule by extension", opener(FULL, "/home/u/a.png"),
      ["eog", "/home/u/a.png"])
check("anything else falls through to the default",
      opener(FULL, "/home/u/a.iso"), ["xdg-open", "/home/u/a.iso"])

# The reason [open.name] exists: no extension, and GIO cannot type it either.
check("a dotfile is reached by name", opener(FULL, "/home/u/.bashrc"),
      ["code", "/home/u/.bashrc"])
check("so is an extensionless file", opener(FULL, "/home/u/src/Makefile"),
      ["code", "/home/u/src/Makefile"])
check("name rules beat extension rules",
      opener("""
[open.name]
"*.py" = "vim"
[open.extension]
py = "code"
""", "/home/u/a.py"), ["vim", "/home/u/a.py"])
check("a name rule can match a directory too",
      opener("""
[open]
directory = "thunar"
[open.name]
"*.git" = "gitk"
""", "/home/u/p/bare.git", is_dir=True), ["gitk", "/home/u/p/bare.git"])

check("the file fallback catches unmatched files",
      opener('[open]\nfile = "code"\n', "/home/u/a.mp4"),
      ["code", "/home/u/a.mp4"])
check("but not directories",
      opener('[open]\nfile = "code"\ndirectory = "thunar"\n', "/home/u/p", True),
      ["thunar", "/home/u/p"])
check("no rules at all means the desktop default",
      opener("[open]\n", "/home/u/a.py"), None)
check("an empty config means the desktop default",
      opener("", "/home/u/a.py"), None)

check("extensions are matched case-insensitively",
      opener('[open.extension]\npng = "eog"\n', "/home/u/A.PNG"),
      ["eog", "/home/u/A.PNG"])
check("a multi-part extension matches its last part",
      opener('[open.extension]\ngz = "file-roller"\n', "/home/u/a.tar.gz"),
      ["file-roller", "/home/u/a.tar.gz"])
check("quoted arguments survive",
      opener('[open]\nfile = "kitty -e nvim"\n', "/home/u/a.txt"),
      ["kitty", "-e", "nvim", "/home/u/a.txt"])
check("~ in the command is expanded, since no shell runs it",
      opener('[open]\nfile = "~/bin/edit"\n', "/home/u/a.txt"),
      [str(pathlib.Path.home() / "bin/edit"), "/home/u/a.txt"])

# Nothing a user can type into the config may take the launcher down.
check("an unbalanced quote is reported and ignored",
      opener('[open]\nfile = \'code "\'\n', "/home/u/a.txt"), None)
check("an empty command is ignored",
      opener('[open]\nfile = ""\n', "/home/u/a.txt"), None)
check("a number where a command belongs is rejected",
      opener("[open]\nfile = 42\n", "/home/u/a.txt"), None)
check("a table where a command belongs is rejected",
      opener("[open]\n[open.file]\nx = 1\n", "/home/u/a.txt"), None)
check("a broken [open] section is ignored entirely",
      opener("open = 3\n", "/home/u/a.txt"), None)


# ---- the rule that gets written when you pick an application ----------

import shutil  # noqa: E402
import tempfile  # noqa: E402

check("a source file is remembered by extension",
      userconfig.rule_for("/home/u/a.rs", False), ("open.extension", "rs"))
check("an extensionless one by name",
      userconfig.rule_for("/home/u/.bashrc", False), ("open.name", ".bashrc"))
check("a config file by name",
      userconfig.rule_for("/home/u/.config/i3/config", False),
      ("open.name", "config"))
check("a directory sets the directory rule",
      userconfig.rule_for("/home/u/projects", True), ("open", "directory"))

# Writing goes through the real file, because preserving what the user wrote
# around the edited line is the entire difficulty.
_TMP = tempfile.mkdtemp(prefix="sponux-openers-")
userconfig.CONFIG_FILE = pathlib.Path(_TMP) / "config.toml"
userconfig.BACKUP_FILE = pathlib.Path(_TMP) / "config.toml.bak"
userconfig.config.CONFIG_DIR = pathlib.Path(_TMP)
userconfig.CONFIG_FILE.write_text('''# a comment of mine
[open]
directory = "thunar"

# my code rules
[open.extension]
py = "code"
''')


def written():
    userconfig._cache = None  # the writer is a different process in real life
    userconfig._cache_stamp = None
    return userconfig.CONFIG_FILE.read_text()


check("a new rule joins the right table",
      userconfig.remember_opener("/home/u/a.rs", False, "code"),
      '[open.extension] rs = "code"')
check("an existing rule is replaced, not duplicated",
      (userconfig.remember_opener("/home/u/b.py", False, "nvim"),
       written().count("py = ")), ('[open.extension] py = "nvim"', 1))
check("a name rule creates its table when missing",
      userconfig.remember_opener("/home/u/Makefile", False, "code"),
      '[open.name] Makefile = "code"')
check("a directory rule lands in [open]",
      userconfig.remember_opener("/home/u/p", True, "nautilus"),
      '[open] directory = "nautilus"')
check("the user's comments survive every write",
      ("# a comment of mine" in written(), "# my code rules" in written()),
      (True, True))
check("the result is still valid TOML",
      sorted(tomllib.loads(written())["open"]),
      ["directory", "extension", "name"])
check("and says what was written",
      tomllib.loads(written())["open"],
      {"directory": "nautilus",
       "extension": {"py": "nvim", "rs": "code"},
       "name": {"Makefile": "code"}})
check("a backup of the previous version is kept",
      userconfig.BACKUP_FILE.exists(), True)

# A command with characters that mean something in TOML must come back out
# of the file unchanged.
userconfig.remember_opener("/home/u/a.tex", False, 'sh -c "x \\ y"')
check("quotes and backslashes survive the round trip",
      tomllib.loads(written())["open"]["extension"]["tex"], 'sh -c "x \\ y"')

shutil.rmtree(_TMP, ignore_errors=True)


# ---- the dotfiles case: config files symlinked into a repository ------

_LINKED = pathlib.Path(tempfile.mkdtemp(prefix="sponux-dotfiles-"))
(_LINKED / "repo").mkdir()
(_LINKED / "xdg").mkdir()
real = _LINKED / "repo" / "config.toml"
real.write_text('# kept in a dotfiles repo\n[open]\ndirectory = "thunar"\n')
link = _LINKED / "xdg" / "config.toml"
link.symlink_to(real)

userconfig.CONFIG_FILE = link
userconfig.BACKUP_DIR = _LINKED / "state"
userconfig.BACKUP_FILE = userconfig.BACKUP_DIR / "config.toml.bak"
userconfig.config.CONFIG_DIR = _LINKED / "xdg"

check("a rule can be written through a symlink",
      userconfig.remember_opener("/home/u/a.rs", False, "code"),
      '[open.extension] rs = "code"')
check("and the symlink is still a symlink afterwards", link.is_symlink(), True)
check("pointing where it did", link.resolve(), real.resolve())
check("the edit landed in the repository file",
      'rs = "code"' in real.read_text(), True)
check("the repo's own comment survived",
      "# kept in a dotfiles repo" in real.read_text(), True)
check("no .bak was dropped into the repository",
      sorted(p.name for p in (_LINKED / "repo").iterdir()), ["config.toml"])
check("the backup went to the state directory instead",
      userconfig.BACKUP_FILE.exists(), True)

shutil.rmtree(_LINKED, ignore_errors=True)


# ---- the same, but the whole config DIRECTORY is the symlink -----------
#
# Which is how dotfiles are usually kept — one `ln -s ~/dotfiles/.config/sponux
# ~/.config/sponux` rather than a link per file. The file itself is then an
# ordinary file, so write-through has to resolve the *directory* component;
# getting that wrong writes a real file into ~/.config and silently detaches
# the user's configuration from their repository.

_DIRLINK = pathlib.Path(tempfile.mkdtemp(prefix="sponux-dotfiles-dir-"))
_repo_cfg = _DIRLINK / "repo" / ".config" / "sponux"
_repo_cfg.mkdir(parents=True)
(_DIRLINK / "xdg").mkdir()
real = _repo_cfg / "config.toml"
real.write_text('# kept in a dotfiles repo\n[open]\ndirectory = "thunar"\n')
linked_dir = _DIRLINK / "xdg" / "sponux"
linked_dir.symlink_to(_repo_cfg)

userconfig.CONFIG_FILE = linked_dir / "config.toml"
userconfig.BACKUP_DIR = _DIRLINK / "state"
userconfig.BACKUP_FILE = userconfig.BACKUP_DIR / "config.toml.bak"
userconfig.config.CONFIG_DIR = linked_dir

check("a rule can be written through a symlinked directory",
      userconfig.remember_opener("/home/u/a.rs", False, "code"),
      '[open.extension] rs = "code"')
check("the directory is still a symlink afterwards",
      linked_dir.is_symlink(), True)
check("the config file inside it was not replaced by a link",
      (linked_dir / "config.toml").is_symlink(), False)
check("the edit landed in the repository",
      'rs = "code"' in real.read_text(), True)
check("the repo's own comment survived it",
      "# kept in a dotfiles repo" in real.read_text(), True)
check("nothing else appeared in the repo's config directory",
      sorted(p.name for p in _repo_cfg.iterdir()), ["config.toml"])

shutil.rmtree(_DIRLINK, ignore_errors=True)


# ---- appearance is not in this file at all ----------------------------

check("[look] is gone: appearance belongs to style.css",
      hasattr(userconfig, "look"), False)
_STYLES = pathlib.Path(userconfig.__file__).parent
check("one bundled stylesheet, not a theme per file",
      sorted(p.name for p in _STYLES.glob("style*.css")), ["style.css"])
_SHEET = (_STYLES / "style.css").read_text()
check("it defines the variables the starter file documents",
      all(v in _SHEET for v in ("--sponux-font", "--sponux-font-size",
                                "--sponux-bg", "--sponux-accent")), True)
check("and names the font on the Entry's inner text node too",
      ".sponux-search > text" in _SHEET, True)

# ---- [window] and [keys] ----------------------------------------------
#
# These decide how the launcher behaves rather than what it opens, and the
# whole point of them living in config.toml is that a mistake there must not
# take the window down with it: every bad value falls back to the default and
# says so.

import gi                                          # noqa: E402
gi.require_version("Gtk", "4.0")                    # noqa: E402
from gi.repository import Gtk                       # noqa: E402

from sponux import config as spconfig               # noqa: E402


def window(toml_text=""):
    real = userconfig.settings
    userconfig.settings = lambda: tomllib.loads(toml_text)
    try:
        return userconfig.window_settings()
    finally:
        userconfig.settings = real


def accel(name):
    ok, keyval, mods = Gtk.accelerator_parse(name)
    assert ok, name
    return (keyval, mods)


w = window()
check("no [window] means the defaults from config.py",
      (w.width, w.max_results, w.debounce, w.position),
      (spconfig.WIDTH, spconfig.MAX_RESULTS, spconfig.DEBOUNCE_MS,
       spconfig.POSITION))
check("and the default bindings", w.keys["copy_path"], accel("<Ctrl>c"))
check("every action has a binding", sorted(w.keys), sorted(spconfig.KEYS))

w = window("""
[window]
width = 900
max_results = 4
debounce = 0
position = "center"
top_fraction = 0.5
hide_on_focus_loss = false
""")
check("values are taken as written",
      (w.width, w.max_results, w.debounce, w.position, w.top_fraction,
       w.hide_on_focus_loss),
      (900, 4, 0, "center", 0.5, False))

# Each of these is a different way to be wrong, and each must be survivable.
w = window("""
[window]
width = "wide"
max_results = 0
debounce = 100000
position = "middle"
top_fraction = 3.0
hide_on_focus_loss = "yes"
""")
check("a non-number width falls back", w.width, spconfig.WIDTH)
check("max_results below the range falls back",
      w.max_results, spconfig.MAX_RESULTS)
check("debounce above the range falls back", w.debounce, spconfig.DEBOUNCE_MS)
check("an unknown position falls back", w.position, spconfig.POSITION)
check("top_fraction outside 0–0.9 falls back",
      w.top_fraction, spconfig.TOP_FRACTION)
check("a non-boolean flag falls back",
      w.hide_on_focus_loss, spconfig.HIDE_ON_FOCUS_LOSS)

w = window("""
[keys]
reveal = "<Ctrl><Alt>o"
copy_path = "F2"
""")
check("a binding is parsed", w.keys["reveal"], accel("<Ctrl><Alt>o"))
check("a function key needs no modifier", w.keys["copy_path"], accel("F2"))
check("actions left alone keep their default",
      w.keys["open_with"], accel("<Shift>Return"))

w = window("""
[keys]
close = "not a key"
quit = 17
teleport = "<Ctrl>t"
""")
check("an unparsable binding keeps the default",
      w.keys["close"], accel("Escape"))
check("a non-string binding keeps the default",
      w.keys["quit"], accel("<Ctrl>q"))
check("an unknown action does not appear in the table",
      "teleport" in w.keys, False)

# The window matches a keypress against the physical key as well as the keyval,
# so a binding written for the Latin layout survives a Cyrillic one. That logic
# lives in app.py; here we only pin down what it is matching against.
check("bindings are stored parsed, not as text",
      all(isinstance(v, tuple) and len(v) == 2 for v in w.keys.values()), True)

# Escape is the way out whatever `close` says — checked in app.py's handler,
# and asserted here so the guarantee is written down where the config is.
check("the source keeps Escape as an unconditional escape hatch",
      "keyval == Gdk.KEY_Escape" in
      (pathlib.Path(userconfig.__file__).parent / "app.py").read_text(), True)

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
print("all config checks passed")
