"""User configuration in ~/.config/sponux/.

Laid out the way rofi's is: behaviour in one file, appearance in another, both
in the user's dotfiles and both optional.

    config.toml   which application opens which file
    style.css     appearance, layered on top of the bundled stylesheet

Both are re-read whenever they change on disk, so editing them takes effect on
the next time the launcher is opened — no need to restart the resident daemon.
Nothing here is required: with neither file present, sponux behaves as it did
before, opening things with the desktop's default application.
"""

import fnmatch
import os
import re
import shlex
import tomllib

from . import config

CONFIG_FILE = config.CONFIG_DIR / "config.toml"
STYLE_FILE = config.CONFIG_DIR / "style.css"
# Kept whenever sponux itself overwrites a config file, so nothing it does can
# be the reason a hand-written one is lost. Deliberately in the state
# directory rather than beside the original: these files are often symlinks
# into a dotfiles repository, and leaving .bak files inside someone's repo is
# not sponux's business.
BACKUP_DIR = config.STATE_DIR
BACKUP_FILE = BACKUP_DIR / "config.toml.bak"

# TOML keys that need no quoting.
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

# The path is substituted here if the template mentions it, and appended
# otherwise, so both "code" and "code --goto {path}" do the right thing.
PATH_PLACEHOLDER = "{path}"

_cache = None
_cache_stamp = None


def _stamp(path):
    """A cheap change token for a file that may not exist."""
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def settings() -> dict:
    """The parsed config.toml, reloaded when the file changes."""
    global _cache, _cache_stamp
    stamp = _stamp(CONFIG_FILE)
    if _cache is not None and stamp == _cache_stamp:
        return _cache
    _cache_stamp = stamp
    if stamp is None:
        _cache = {}
        return _cache
    try:
        with open(CONFIG_FILE, "rb") as fh:
            _cache = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # A typo in the config must not take the launcher down with it.
        print(f"sponux: ignoring {CONFIG_FILE}: {exc}")
        _cache = {}
    return _cache


def style_changed() -> bool:
    """True when style.css appeared, vanished or was edited since last asked."""
    global _style_stamp
    stamp = _stamp(STYLE_FILE)
    changed = stamp != _style_stamp
    _style_stamp = stamp
    return changed


_style_stamp = _stamp(STYLE_FILE)


def opener_command(path: str, is_dir: bool):
    """The argv configured to open `path`, or None to use the desktop default."""
    return resolve_opener(path, is_dir)[0]


def resolve_opener(path: str, is_dir: bool):
    """(argv, rule) for opening `path`, either of which may be None.

    Rules are tried most specific first: the file's name, then its extension,
    then a mime-type pattern, then the directory or file fallback. `rule` names
    the one that matched — '[open.extension] py' — for `--which` and for
    messages about a command that would not start.
    """
    section = settings().get("open")
    if not isinstance(section, dict):
        return (None, None)

    basename = path.rsplit("/", 1)[-1]
    # By name first, because it is the only rule that can reach a file with no
    # extension and no useful mime type — ".bashrc", "Makefile", "PKGBUILD".
    template, pattern = _match_glob(basename, section.get("name"))
    rule = f'[open.name] "{pattern}"' if template is not None else None

    if template is None and not is_dir:
        # A leading dot is a hidden file, not an extension: ".bashrc" has none.
        stem, _, ext = basename.rpartition(".")
        if stem and ext:
            by_ext = section.get("extension")
            if isinstance(by_ext, dict):
                template = by_ext.get(ext.lower())
                if template is not None:
                    rule = f"[open.extension] {ext.lower()}"
        if template is None:
            template, pattern = _match_glob_mime(path, section.get("mime"))
            if template is not None:
                rule = f'[open.mime] "{pattern}"'

    if template is None:
        key = "directory" if is_dir else "file"
        template = section.get(key)
        if template is not None:
            rule = f"[open] {key}"
    if template is None:
        template = section.get("default")
        if template is not None:
            rule = "[open] default"
    if not template:
        return (None, None)

    argv = command_argv(template, path)
    return (argv, rule if argv else None)


def command_argv(template, path: str):
    """A configured command template, turned into argv for `path`."""
    if not isinstance(template, str):
        # A number or a table here is a typo, not a command; saying so beats
        # spawning something nonsensical.
        print(f"sponux: {CONFIG_FILE}: {template!r} is not a command string")
        return None
    try:
        argv = shlex.split(template)
    except ValueError as exc:
        print(f"sponux: bad command {template!r} in {CONFIG_FILE}: {exc}")
        return None
    if not argv:
        return None

    # "~/bin/edit" is a path the shell would have expanded and we do not run a
    # shell; without this it would just fail to start.
    argv[0] = os.path.expanduser(argv[0])

    if any(PATH_PLACEHOLDER in arg for arg in argv):
        return [arg.replace(PATH_PLACEHOLDER, path) for arg in argv]
    return argv + [path]


def opener_rules():
    """Every [open] rule as (rule, template), in resolution order.

    Used by --check, which has to look at rules nobody has triggered yet.
    """
    section = settings().get("open")
    if not isinstance(section, dict):
        return []
    rules = []
    for table, label in (("name", "[open.name]"), ("extension", "[open.extension]"),
                         ("mime", "[open.mime]")):
        entries = section.get(table)
        if isinstance(entries, dict):
            rules += [(f"{label} {key}", value) for key, value in entries.items()]
    for key in ("directory", "file", "default"):
        if key in section:
            rules.append((f"[open] {key}", section[key]))
    return rules


def _match_glob(basename: str, patterns):
    """Match a file's own name against patterns like ".*rc" or "Makefile"."""
    if not isinstance(patterns, dict):
        return (None, None)
    for pattern, command in patterns.items():
        if fnmatch.fnmatch(basename, pattern):
            return (command, pattern)
    return (None, None)


def _match_glob_mime(path: str, patterns):
    """Match the file's content type against patterns like "text/*"."""
    if not isinstance(patterns, dict) or not patterns:
        return (None, None)
    from gi.repository import Gio

    content_type, _ = Gio.content_type_guess(path, None)
    if not content_type:
        return (None, None)
    for pattern, command in patterns.items():
        if fnmatch.fnmatch(content_type, pattern):
            return (command, pattern)
    return (None, None)


# ---- writing a rule back into config.toml ------------------------------
#
# The config is a file the user owns and comments, so it is edited as text
# rather than round-tripped through a TOML writer, which would throw every
# comment away. Only one line is ever touched.


def rule_for(path: str, is_dir: bool):
    """(table, key) naming the rule that would remember an opener for `path`.

    An extension when there is one, because that is what generalises; the file
    name otherwise, which is the only thing that identifies a "Makefile" or a
    "config".
    """
    if is_dir:
        return ("open", "directory")
    basename = path.rsplit("/", 1)[-1]
    stem, _, ext = basename.rpartition(".")
    if stem and ext:
        return ("open.extension", ext.lower())
    return ("open.name", basename)


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else '"' + _escape(key) + '"'


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def remember_opener(path: str, is_dir: bool, command: str):
    """Persist `command` as the opener for paths like `path`.

    Returns the line written, or None if the file could not be updated. The
    daemon picks the change up on its own: settings() re-reads on mtime.
    """
    table, key = rule_for(path, is_dir)
    line = f'{_toml_key(key)} = "{_escape(command)}"'

    try:
        original = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else ""
        updated = _with_rule(original, table, key, line)
        tomllib.loads(updated)  # never write a file we cannot read back
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"sponux: cannot update {CONFIG_FILE}: {exc}")
        return None

    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if original:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            BACKUP_FILE.write_text(original)
        _write_through(CONFIG_FILE, updated)
    except OSError as exc:
        print(f"sponux: cannot write {CONFIG_FILE}: {exc}")
        return None
    return f"[{table}] {line}"


def _write_through(path, text: str):
    """Replace a config file's contents without replacing the file itself.

    Written to a temporary file and renamed, so a crash mid-write cannot leave
    a half-config behind — but renamed onto the **resolved** path, because
    config.toml is very often a symlink into a dotfiles repository and
    renaming onto the link would quietly replace it with a regular file, and
    with it the user's whole arrangement.
    """
    target = path.resolve()
    mode = target.stat().st_mode & 0o777 if target.exists() else None
    tmp = target.with_name(target.name + ".new")
    tmp.write_text(text)
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(target)


def _with_rule(text: str, table: str, key: str, line: str) -> str:
    """`text` with `line` set in `[table]`, replacing any existing entry."""
    lines = text.splitlines()
    header = f"[{table}]"
    assignment = re.compile(r"\s*(" + re.escape(key) + r"|\"" + re.escape(key)
                            + r"\")\s*=")

    in_table = False
    header_at = None
    for i, current in enumerate(lines):
        stripped = current.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = stripped == header
            if in_table:
                header_at = i
            continue
        if in_table and assignment.match(current):
            lines[i] = line
            return "\n".join(lines) + "\n"

    if header_at is not None:
        lines.insert(header_at + 1, line)
        return "\n".join(lines) + "\n"

    tail = "" if not lines or not lines[-1].strip() else "\n"
    return "\n".join(lines) + tail + f"\n{header}\n{line}\n"


STARTER_CONFIG = """\
# sponux — behaviour. Appearance lives in style.css, next to this file.
#
# Everything here is optional — delete a line to get the default behaviour
# back. Edits apply the next time you open the launcher; the daemon does not
# need restarting.
#
# Both files are plain text and safe to keep in a dotfiles repository and
# symlink into place: sponux writes through a symlink rather than over it.

[open]
# Rules are tried most specific first:
#     [open.name] -> [open.extension] -> [open.mime] -> directory / file
#     -> default
# The first one that matches wins, and anything that matches nothing is opened
# by the desktop's own default application.

# Directories.
# directory = "thunar"

# Files with no more specific rule. Careful: this catches videos, archives and
# binaries too — usually better left unset so they go to the desktop default.
# file = "code"

# Last resort, for anything the rules above did not match.
# default = "xdg-open"

# By file name, with "*" and "?" wildcards. The only rule that reaches a file
# with no extension and no useful mime type.
[open.name]
# ".*rc" = "code"
# ".gitconfig" = "code"
# "Makefile" = "code"
# "*.tar.*" = "file-roller"

# By file extension, without the dot.
[open.extension]
# py = "code"
# md = "code"
# png = "eog"
# pdf = "zathura"

# By mime type; "*" wildcards are allowed. Checked when no extension matched.
# Note that plenty of code is not text/* to GIO: JSON is application/json and
# a shell script is application/x-shellscript.
[open.mime]
# "text/*" = "code"
# "image/*" = "eog"
# "video/*" = "mpv"

# The path is appended to the command, unless you write {path} yourself:
#   py = "code --goto {path}:1"

# ---------------------------------------------------------------------------
# What the file search can find. Defaults: your home directory, no hidden
# files or directories, no build/cache noise.
# ---------------------------------------------------------------------------
[index]
# Where to look. Add directories outside home if you want them searchable.
# roots = ["~", "/etc"]

# Index dotfiles and dot-directories everywhere. Usually the wrong lever —
# it pulls in ~/.local, ~/.var and every tool's state directory. Prefer
# "include" below.
# hidden = false

# Hidden directories worth indexing anyway, listed one by one. This is how you
# make your configs findable without indexing the rest of your dotfiles:
# include = ["~/.config", "~/.local/share/applications", "~/.ssh/config"]

# Never index these, whatever else matches. Glob patterns over the whole path;
# "*" also crosses directory separators, so "~/Videos/*" covers the tree.
# exclude = ["~/Videos/*", "*.iso", "~/projects/*/node_modules"]

# Directory names pruned anywhere they appear, on top of the built-in list
# (.git, node_modules, __pycache__, .cache, .venv, target, …).
# skip = ["Steam", "vendor"]

# Take names back off that built-in list, if you do want them indexed.
# unskip = ["target"]

# Follow symlinked directories. On by default: dotfiles usually live in one
# repository symlinked into place, and without this ~/.config/nvim is a single
# entry with nothing inside it findable.
# follow_symlinks = true

# Changes reach the index immediately via inotify; the full rebuild below is
# only a safety net for events the kernel dropped. Seconds; 0 turns it off.
# watch = true
# interval = 900

# Caps, both there to keep a pathological home directory from hurting.
# max_files = 200000
# max_watches = 8192

# ---------------------------------------------------------------------------
# Ranking.
# ---------------------------------------------------------------------------
[rank]
# Rank what you actually open above what merely matches as well. The weight is
# the most a result can gain from its history; it is capped low enough that a
# clearly better text match still wins.
# frecency = true
# weight = 25
"""

STARTER_STYLE = """\
/* sponux — appearance. Layered on top of the bundled stylesheet, which is
   dark and drives everything from the variables below, so changing the look
   usually means redefining a few of them rather than writing rules.

     --sponux-font        family name; unset = the desktop's UI font.
                          Check a name resolves: fc-match "Some Font"
     --sponux-font-size   size of a result title; the query, paths, badges
                          and hints are derived from it
     --sponux-bg          card background
     --sponux-fg          titles and the query
     --sponux-muted       paths and descriptions
     --sponux-dim         badges, key hints, placeholder
     --sponux-accent      selection and caret
     --sponux-line        separator and badge background
     --sponux-border      card border
     --sponux-shadow      card shadow
     --sponux-radius      card corners
     --sponux-row-radius  result row corners
*/

/*
:root {
    --sponux-font: "JetBrainsMono Nerd Font";
    --sponux-font-size: 16px;
    --sponux-bg: #1e1e2e;
    --sponux-accent: #f38ba8;
}
*/

/* Anything the variables do not reach can still be written as ordinary CSS.
   Selectors: .sponux-card, .sponux-search, .sponux-sep, .sponux-results,
   .sponux-row, .sponux-title, .sponux-subtitle, .sponux-badge, .sponux-hints

.sponux-card {
    border-radius: 12px;
}
*/
"""


def write_starter_files(force: bool = False):
    """Create commented starter files. Returns (written, replaced, skipped).

    With --force this overwrites a configuration someone may have spent an
    evening on, so whatever it replaces is copied to <name>.bak first — the
    starter files are one command away, a hand-written config is not.
    """
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    written, replaced, skipped = [], [], []
    for path, content in ((CONFIG_FILE, STARTER_CONFIG),
                          (STYLE_FILE, STARTER_STYLE)):
        if path.exists():
            if not force:
                skipped.append(path)
                continue
            backup = BACKUP_DIR / (path.name + ".bak")
            try:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                backup.write_text(path.read_text())
                replaced.append((path, backup))
            except OSError as exc:
                print(f"sponux: not overwriting {path}: cannot back it up ({exc})")
                skipped.append(path)
                continue
        # Written through the file rather than onto it: if this is a symlink
        # into a dotfiles repository, the link has to survive.
        _write_through(path, content) if path.exists() else path.write_text(content)
        written.append(path)
    return written, replaced, skipped
