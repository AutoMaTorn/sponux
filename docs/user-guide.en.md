# sponux — user guide

A keyboard launcher for Linux: applications, files and arithmetic in one window.
Press a key, type a few letters, press Enter.

Русская версия: [user-guide.ru.md](user-guide.ru.md)

1. [Installing](#installing)
2. [First run](#first-run)
3. [Binding a hotkey](#binding-a-hotkey)
4. [Starting it at login](#starting-it-at-login)
5. [Using it](#using-it)
6. [Opening files your way](#opening-files-your-way)
7. [What the file search can find](#what-the-file-search-can-find)
8. [Ranking: what you use comes first](#ranking-what-you-use-comes-first)
9. [Appearance](#appearance)
10. [Keeping your config in a dotfiles repository](#keeping-your-config-in-a-dotfiles-repository)
11. [When something is wrong](#when-something-is-wrong)
12. [Files sponux writes](#files-sponux-writes)
13. [Uninstalling](#uninstalling)
14. [Building from source](#building-from-source)

---

## Installing

sponux needs PyGObject and GTK 4 from your distribution. It has no third-party
Python dependencies of its own — everything else it uses is in the standard
library.

**Debian / Ubuntu.** The package declares those dependencies, so this is all:

```sh
sudo apt install ./sponux_0.1.0-1_all.deb
```

**Anywhere else** there is nothing to install. Install the two system packages,
unpack sponux wherever you keep such things, and run it from there:

```sh
sudo pacman -S python-gobject gtk4        # Arch; use your own package manager
tar xf sponux-0.1.0.tar.gz -C ~/opt
~/opt/sponux-0.1.0/bin/sponux
```

Cloning the repository instead works the same way, if you would rather track it
with git.

`bin/sponux` finds the package next to itself, so this needs no root, no
`PATH` changes and no uninstall procedure — to remove it, delete the directory.
Bind your hotkey to the full path (`~/opt/sponux-0.1.0/bin/sponux`) and
everything in this guide works the same.

The archive holds only what is needed to run sponux, so there is no man page on
your manpath — read it in place with
`man -l ~/opt/sponux-0.1.0/packaging/sponux.1`.

It is the same wrapper the `.deb` installs as `/usr/bin/sponux`; the only
difference is where it finds the package.

If you have the GTK 4 runtime already — and you do if any GTK 4 application is
installed — sponux adds about 3 MB of Python bindings and 139 KB of itself.

This guide is not part of the package: it lives in the project wiki, so it can
be corrected without waiting for a release. What an installed copy carries is
`man sponux`.

## First run

```sh
sponux
```

The window appears at the top of the screen. Type to search; `Esc` closes it.

Run `sponux` again and the window toggles instead of starting a second copy.
That is the whole design: the first call starts a small resident daemon, and
every later call just tells it to show itself. Showing the window takes about
20 ms; the first call of the session takes about 420 ms because Python and GTK
have to load. [Starting it at login](#starting-it-at-login) removes that wait.

Get commented starter configuration files whenever you want them:

```sh
sponux --write-config
```

Both files are optional. Without them sponux uses its own defaults.

## Binding a hotkey

Bind your shortcut to the plain command `sponux`. Nothing else is needed —
no hotkey daemon, and no window-manager rules.

**i3 / sway:**

```
bindsym $mod+d exec --no-startup-id sponux
```

Reload with `$mod+Shift+r`. Unlike rofi, sponux does not need `--release` on the
binding.

**GNOME, KDE, XFCE:** Settings → Keyboard → Custom Shortcuts, add one that runs
`sponux`, and give it e.g. `Super+Space`.

The window deliberately stays out of the window manager's hands (it is X11
override-redirect, the same trick rofi uses). So:

- a tiling window manager will not tile it, and needs no `for_window` rule;
- it never becomes the *focused* window, so i3 keeps its focus highlight on
  whatever you were working in — the launcher only takes keyboard input while
  it is open;
- clicking anywhere else, or switching windows, dismisses it.

It appears on whichever monitor your pointer is on.

## Starting it at login

Opening the launcher is fast *because a daemon is already running*. Start it
when you log in and even the first press is instant:

```sh
sponux --autostart on      # 'off' to undo, no argument to see the state
```

This writes `~/.config/autostart/sponux.desktop`, which runs `sponux --daemon` —
resident, with no window. Nothing appears on screen until you press your hotkey.

**Bare window managers do not read `~/.config/autostart`.** Under i3, sway and
friends, put this in the window manager's config instead — `--autostart on`
tells you so when it detects one:

```
exec_always --no-startup-id sponux --daemon
```

`exec_always` rather than `exec`: i3 runs `exec` only when it first starts, so
`exec` would never bring the daemon back after `Ctrl+Q` or an i3 restart.
Running it twice costs nothing — `--daemon` checks whether a daemon is already
on the session bus and exits if it is.

## Using it

| Key | What it does |
| --- | --- |
| type | search applications, files and arithmetic |
| ↑ / ↓ | move the selection |
| `Enter` | launch, open, or copy a calculator result |
| `Ctrl+Enter` | open the folder containing the selected file |
| `Ctrl+C` | copy the selected file's path |
| `Shift+Enter` | open with… (and optionally remember the choice) |
| `Esc` | hide the window |
| `Ctrl+Q` | quit the daemon |

The keys that apply to whatever is selected are shown under the results, so
there is nothing to memorise.

**The calculator** takes ordinary expressions — `2+2*10`, `sqrt(2)`,
`sin(pi/2)`, `1/7` — and `Enter` copies the result. It is a restricted
expression evaluator, not `eval()`: it cannot call anything but the maths
functions it knows.

### Narrowing the search

A prefix restricts results to one kind, and a leading `/` or `~/` means you are
typing a path rather than searching:

| Type | You get |
| --- | --- |
| `f:` `file:` | files only |
| `a:` `app:` | applications only |
| `c:` `=` | the calculator only — `=1/7` |
| `/…` `~/…` | that path, and completions of its last component |

These are listed under the search field while it is empty and disappear as soon
as you type.

### Typing a path

This is the way *out* of the file index, and it is worth knowing about, because
the index deliberately does not cover your whole filesystem.

- `/etc/ho` → `/etc/hosts`. No index involved; `/etc` is not indexed at all.
- `~/pro` → `~/projects`.
- A trailing slash lists a directory: `~/Documents/`.
- Hidden entries appear once you type the dot, as in a shell.
- Only the last component completes, and only by prefix — like a shell, not
  like the fuzzy search above.
- A path you typed always ranks above every search result. You named it, so
  there is nothing left to guess.

Only `/` and `~/` count. Relative paths like `./x` do not, because there is no
sensible directory for them to be relative to — the daemon has been running
since login.

## Opening files your way

Press `Shift+Enter` on a file to get the list of applications for it: the ones
registered for that kind of file first, then everything else installed. Type to
filter, `Enter` to open, `Esc` to go back to your search.

The first row of that list is a switch. Turn it on and your choice is *also*
written into `config.toml` as a rule, so the next file of the same kind opens
that way without asking. Each row shows what would be saved before you commit
to it. With the switch off, nothing is written.

You can also write the rules by hand, in `~/.config/sponux/config.toml`:

```toml
[open]
directory = "thunar"        # directories
file = "kitty -e nvim"      # any file with no more specific rule
default = "xdg-open"        # last resort

[open.name]                 # by file name; "*" and "?" allowed
".*rc" = "code"
"Makefile" = "code"

[open.extension]            # by extension, without the dot
py = "code --goto {path}:1"
png = "eog"

[open.mime]                 # by mime type; "*" allowed
"text/*" = "code"
"video/*" = "mpv"
```

Rules are tried most specific first — `name`, `extension`, `mime`, then
`directory` / `file`, then `default` — and the first match wins. Anything that
matches no rule is opened by your desktop's own default application.

Three things that save time:

- **`[open.name]` is the only rule that reaches a file with no extension**, and
  those are exactly the ones you care about: `.bashrc`, `Makefile`,
  `~/.config/i3/config`.
- **`file` catches everything**, videos and archives included. Leaving it unset
  and listing extensions instead is usually what people actually want.
- **Not all code is `text/*`.** JSON is `application/json`, a shell script is
  `application/x-shellscript`, and a `.ts` file is reported as *Qt Linguist*.
  Name those by extension rather than trusting a mime pattern. When in doubt,
  ask: `sponux --which somefile.ts`.

The file's path is appended to the command, unless you write `{path}` yourself
and put it where you want it. A leading `~` in the command is expanded. If the
command cannot be started, sponux falls back to the desktop default rather than
doing nothing, and says why on stderr.

## What the file search can find

File search is backed by a small index of *filenames* (not contents) under your
home directory, kept up to date as files appear and disappear. What it covers is
yours to decide:

```toml
[index]
roots = ["~", "/etc"]        # where to look at all
include = ["~/.config"]      # hidden trees to index anyway
exclude = ["~/.config/BraveSoftware/*", "*.iso"]
skip = ["Steam"]             # directory names pruned anywhere
unskip = ["build"]           # …or taken back off the built-in skip list
hidden = false               # index every dotfile (usually far too much)
follow_symlinks = true       # follow symlinked directories
watch = true                 # keep the index current as files change
interval = 900               # seconds between full rebuilds; 0 disables
max_files = 200000
max_watches = 8192
```

By default it covers your home directory and skips hidden files and directories
along with build and cache noise (`.git`, `node_modules`, `__pycache__`,
`.cache`, `.venv`, `target`, `build`, `dist`, …).

That default also hides your configuration files, which is what `include` is
for. It names hidden trees to index despite the rule, and directories on the way
to them are walked *without* being indexed — so
`include = ["~/.local/share/applications"]` traverses `~/.local` and
`~/.local/share` without pulling in everything else that lives there.

`exclude` beats everything else. Its patterns are globs matched against the
whole path, and `*` crosses directory separators, so `~/Videos/*` covers that
whole tree.

**Prefer `include` to `hidden = true`.** On one fairly ordinary home directory:
618 entries with the defaults, 672 with `~/.config` included and the browser
profiles inside it excluded, 7821 including those profiles, and 91743 with
`hidden = true` — at which point most of what you find is application state
rather than your files.

Symlinked directories are followed by default. Turn that off with
`follow_symlinks = false` — but if you keep your dotfiles as one repository
symlinked into place, following is what makes `~/.config/nvim/init.lua` findable
at all. Symlink loops terminate safely.

You should never need to rebuild by hand: the daemon watches every indexed
directory, so a file you create is findable about a tenth of a second later, and
one you delete stops being offered. A full rebuild runs at startup and every 15
minutes as a safety net, because the kernel can drop change notifications under
load and nothing else would notice. If you want one now:

```sh
sponux --reindex
```

## Ranking: what you use comes first

sponux counts what you open and when, and nudges those results up.

```toml
[rank]
frecency = true      # rank what you open above what merely matches
weight = 25          # the most a result can gain from its history
```

Two files called `config` no longer tie — the one you edited this morning comes
first. Recency outweighs volume: twice today beats ten times last year. The
count is compressed logarithmically, so something you once opened in a loop does
not dominate every search afterwards.

The bonus is capped deliberately below the gap between match qualities, so
history settles near-ties but never overrules the query: a prefix match still
beats a heavily used substring match. `sponux --which PATH` reports what a given
file has earned.

Set `frecency = false` to rank on the query alone.

## Appearance

Appearance lives entirely in `~/.config/sponux/style.css`. The bundled
stylesheet is dark and drives the whole window from a handful of variables, so
restyling means redefining a few of them — never copying the sheet:

```css
:root {
    --sponux-font: "JetBrainsMono Nerd Font";
    --sponux-font-size: 16px;
    --sponux-bg: #1e1e2e;
    --sponux-accent: #f38ba8;
    --sponux-radius: 10px;
}
```

| Variable | |
| --- | --- |
| `--sponux-font` | family name; unset means your desktop's UI font |
| `--sponux-font-size` | the size of a result title — the query, paths, badges and hints are derived from it, so this one number scales the whole window |
| `--sponux-bg` `--sponux-fg` | background; titles and query text |
| `--sponux-muted` `--sponux-dim` | paths and descriptions; badges, hints, placeholder |
| `--sponux-accent` | selection and text cursor |
| `--sponux-line` `--sponux-border` `--sponux-shadow` | separator and badge fill, border, shadow |
| `--sponux-radius` `--sponux-row-radius` | window and result-row corners |

Your file is loaded *after* the bundled one, so anything you set wins.

Give a font family exactly as fontconfig knows it, and check the name before
trusting it — when it is wrong, fontconfig silently substitutes something else
and it looks like the setting is broken:

```sh
fc-match "JetBrainsMono Nerd Font"
```

Anything the variables do not reach is ordinary GTK CSS. The selectors are
`.sponux-card`, `.sponux-search`, `.sponux-sep`, `.sponux-results`,
`.sponux-row`, `.sponux-title`, `.sponux-subtitle`, `.sponux-badge` and
`.sponux-hints`.

Edits to either config file apply the next time you open the launcher. The
daemon does not need restarting.

## Keeping your config in a dotfiles repository

Both files are plain text with no machine-managed sections, so they belong in a
dotfiles repository perfectly well. Symlink them into place, per file or as a
whole directory:

```sh
ln -s ~/dotfiles/.config/sponux ~/.config/sponux
```

sponux is careful with that arrangement:

- when it writes a rule itself (the "remember" switch), it **writes through the
  symlink**, so the edit lands in your repository and the link survives;
- backups it takes before overwriting anything go to `~/.local/state/sponux/`,
  never beside the original — no stray `.bak` files appearing in your repo;
- the index follows symlinked directories, so configs kept in a repository and
  linked into `~/.config` are findable at the path you actually use.

## When something is wrong

Two commands answer most questions:

```sh
sponux --check                       # everything wrong with config.toml
sponux --which ~/.config/i3/config   # how this file would be opened, and why
man sponux                           # the full reference
```

`--check` goes over every rule, verifies each command exists on your `PATH`, and
checks the index roots and includes. `--which` prints a file's content type, the
rule that matched, the exact command that would run, and says so when the
program is missing.

**The hotkey does nothing.** Run `sponux` in a terminal and read the error.
Note that the window is intentionally invisible to window-manager tooling — it
will not show up in `wmctrl -l` or `_NET_CLIENT_LIST`. To confirm it exists:

```sh
xwininfo -root -tree | grep '"sponux"'
```

**It seems to be running an old version.** You may have two installations — one
from a package on `PATH` and one in a source checkout. They share a single name
on the session bus, so whichever daemon started first answers every keypress.
Open the launcher, press `Ctrl+Q` to stop that daemon, then start the one you
want:

```sh
sponux --daemon          # or /path/to/checkout/bin/sponux --daemon
```

`Ctrl+Q` is the reliable way to stop it. Do not reach for
`pkill -f 'm sponux'`: that pattern also matches the shell you type it in, so
it can take your own session down with it.

**A file I know exists cannot be found.** It is outside the index rather than
missing. Either add its tree to `[index] include`, or just type the path —
that needs no index at all. `sponux --check` lists what the roots and includes
currently are.

**It opened in the wrong application.** `sponux --which THEFILE` names the rule
that matched. The usual cause is a mime pattern that does not apply — see the
note about `text/*` above.

**My font setting is ignored.** The family name is almost certainly not what
fontconfig calls it; check with `fc-match`. "JetBrains Mono" and "JetBrainsMono
Nerd Font" are different names, and only the installed one resolves.

**My style.css seems ignored.** A CSS parse error is reported on stderr with a
file and line number — start the daemon in a terminal to see it.

**Edits to config.toml do nothing.** If the file cannot be parsed as TOML, the
whole file is ignored rather than half-applied. `sponux --check` says so, with
the position of the syntax error.

**The first press of the session is slow.** That is the daemon starting. See
[Starting it at login](#starting-it-at-login).

**On Wayland** the window cannot place itself and leaves positioning to the
compositor. Everything else works.

## Files sponux writes

| Path | |
| --- | --- |
| `~/.config/sponux/` | your two config files — only ever written by `--write-config` or the "remember" switch |
| `~/.cache/sponux/index.db` | the filename index; safe to delete, it rebuilds |
| `~/.local/state/sponux/usage.db` | what you have opened, for ranking |
| `~/.local/state/sponux/*.bak` | the last version of a config file sponux replaced |
| `~/.config/autostart/sponux.desktop` | only if you asked for it with `--autostart on` |

## Uninstalling

```sh
sudo apt remove sponux           # if you installed the .deb
rm -rf ~/opt/sponux-0.1.0        # if you unpacked the archive
```

Neither touches your configuration, index or history. To remove those too:

```sh
sponux --autostart off
rm -rf ~/.config/sponux ~/.cache/sponux ~/.local/state/sponux
```

Remember to take the hotkey out of your window manager's config.

## Building from source

You do not need to build anything to use sponux — a clone runs as it is, with
`./bin/sponux`. Build only if you want one of the installable artifacts.

Nothing here needs pip, setuptools, a virtualenv or a network connection. The
builders are plain standard-library Python.

**What you need:**

- Python 3.11 or newer.
- For the `.deb` only: `dpkg-deb`. It comes from `dpkg`, which is an Essential
  package on Debian and Ubuntu — so it is already there — and is packaged as
  `dpkg` on Arch and Fedora.
- Optional: `lintian`. `mkdeb.py` runs it on the finished package if it is
  installed, and skips it if not.

**Build:**

```sh
git clone https://github.com/automatorn/sponux && cd sponux

python3 tools/mkdeb.py         # dist/sponux_0.1.0-1_all.deb
python3 tools/mktarball.py     # dist/sponux-0.1.0.tar.gz
python3 tools/mkwheel.py       # dist/sponux-0.1.0-py3-none-any.whl
```

| Which one | For |
| --- | --- |
| `.deb` | Debian and Ubuntu: `sponux` on your `PATH`, a menu entry, `man sponux` |
| `.tar.gz` | anywhere else: unpack and run, no install step |
| wheel | only if you specifically want `pip` — and read the warning in [Installing](#installing) first |

**Run the checks:**

```sh
for t in tests/test_*.py; do python3 "$t" || break; done
```

Each one is standalone and prints a line per check. The whole suite takes about
two seconds, half of it `test_index.py`, which builds a real directory tree in
`/tmp` and waits on live filesystem events.

**Both archives are reproducible.** Every timestamp in them comes from
`packaging/changelog` rather than from the clock, entries are sorted, and
everything is owned by root, so two builds of the same tree produce
byte-identical files. That means you can check an artifact somebody handed you
against one you built yourself:

```sh
sha256sum dist/sponux-0.1.0.tar.gz
```

**Changing the version** means editing three files: `pyproject.toml`,
`packaging/changelog` and `sponux/__init__.py`. The builders compare them and
refuse to run when they disagree, so a half-finished bump cannot be shipped —
but they cannot fill the other two in for you. The `.TH` line in
`packaging/sponux.1` also names the version, and nothing checks that one.
