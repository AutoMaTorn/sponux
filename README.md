# sponux

A lightweight Spotlight-like launcher for Linux, built with Python + GTK 4.

*[Русский](README.ru.md)*

[![CI](https://github.com/AutoMaTorn/sponux/actions/workflows/ci.yml/badge.svg)](https://github.com/AutoMaTorn/sponux/actions/workflows/ci.yml)

- **App search & launch** — native, via `Gio.AppInfo` (icons and correct
  launching for free). What you open often floats to the top.
- **File search** — instant, backed by a small self-maintained SQLite index of
  filenames under your home directory, kept current by inotify. What it covers
  is configurable, down to individual trees. Type a path to reach anything
  outside it.
- **Calculator** — type an expression (`2+2*10`, `sqrt(2)`, `sin(pi/2)`),
  press Enter to copy the result. Safe evaluator, no `eval()`.

It runs as a single-instance resident daemon: the first launch starts it,
every later `sponux` call toggles the window in about 20 ms.

## Screenshots

Applications and files in one list — the badge on the right says which is which:

![Searching applications and files](docs/images/search.jpg)

Type a path and it completes, with no index involved. `/etc` is not indexed at
all, and does not need to be:

![Completing a typed path](docs/images/path-completion.jpg)

Arithmetic, evaluated by walking an AST rather than by calling `eval()`. Enter
copies the result:

![The calculator](docs/images/calculator.jpg)

## Documentation

| | |
| --- | --- |
| **User guide** | [English](docs/user-guide.en.md) · [Русский](docs/user-guide.ru.md) |
| Reference | `man sponux` |

The guides cover installing, the hotkey, autostart, every key and query prefix,
both configuration files, and what to do when something misbehaves. Everything
below is the short version plus what you need to build it.

## Requirements

Debian/Ubuntu:

```sh
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
```

There are no third-party Python dependencies — beyond PyGObject, sponux uses
only the standard library. The packages above cost ~3 MB; they are bindings to
the GTK 4 runtime (~43 MB), which any GTK 4 application on the system already
brings in. sponux itself is a 42 KB package, 139 KB installed.

Two more are optional, and the `.deb` asks for them itself — this is only for
running from a source tree or a tarball:

```sh
sudo apt install libglib2.0-bin                  # gdbus: the fast toggle
sudo apt install gir1.2-gtk4layershell-1.0       # Wayland only: placement
```

`libglib2.0-bin` provides `gdbus`, which is how `bin/sponux` reaches a running
daemon in ~11 ms instead of importing GTK to do it in ~327 ms. Nothing breaks
without it; every open just takes the slow path. `gir1.2-gtk4layershell-1.0`
does nothing on X11 — under Wayland it is what lets the window centre itself
and take focus. See [Wayland](docs/user-guide.en.md#when-something-is-wrong).

## Install

**Debian / Ubuntu** — the package declares its dependencies itself:

```sh
sudo apt install ./sponux_0.2.1-1_all.deb
```

**Anywhere else** — there is nothing to install. Unpack it and run it:

```sh
tar xf sponux-0.2.1.tar.gz -C ~/opt
~/opt/sponux-0.2.1/bin/sponux
```

Or clone this repository and run `bin/sponux` out of it — same thing.

`bin/sponux` finds the package next to itself, so bind your hotkey to that path
and you are done; uninstalling is deleting the directory. The same wrapper is
what the `.deb` puts in `/usr/bin`, where it finds the package in
`/usr/share/sponux` instead; from a wheel, the package is already on `sys.path`.

See the user guide for the rest — [English](docs/user-guide.en.md),
[Русский](docs/user-guide.ru.md).

## Quick start

Bind a hotkey to the command `sponux`; no window-manager rules are needed.

```
bindsym $mod+d exec --no-startup-id sponux         # i3
exec_always --no-startup-id sponux --daemon        # …and start it at login
```

| Key | Action |
| --- | --- |
| Type | search apps / files / math |
| ↑ / ↓ | move selection |
| Enter | launch / open / copy result |
| Ctrl+Enter | open the folder containing the file |
| Ctrl+C | copy the file's path |
| Shift+Enter | open with… (and optionally remember it) |
| Esc | hide the window |
| Ctrl+Q | quit the daemon |

Prefixes narrow the search: `f:` files, `a:` apps, `c:` or `=` the calculator,
and a leading `/` or `~/` completes a path instead of searching. Both the keys
and the prefixes are shown in the window itself, so nothing has to be
memorised.

Configuration is two optional files in `~/.config/sponux/` — `config.toml` for
behaviour, `style.css` for appearance. `sponux --write-config` writes commented
starters. `[window]` sets the card's width, result count and placement;
`[keys]` rebinds the modifier actions. Edits apply on the next open, with no
restart.

```sh
sponux --which ~/.config/i3/config   # what would open this, and why
sponux --check                       # check config.toml
sponux --reindex                     # rebuild the file index now
```

## Build the packages

```sh
python3 tools/mkdeb.py         # dist/sponux_0.2.1-1_all.deb   (needs dpkg-deb)
python3 tools/mktarball.py     # dist/sponux-0.2.1.tar.gz      (unpack and run)
python3 tools/mkwheel.py       # dist/sponux-0.2.1-py3-none-any.whl
```

Pure Python, so all three are architecture-independent, and all three read their
metadata from `pyproject.toml` and `packaging/changelog`. `mkdeb.py` holds the
install layout — it is the only thing that installs sponux anywhere. The tarball
carries only what is needed to *run* sponux: no build scripts, no tests, no
packaging metadata. Both the `.deb` and the tarball are reproducible — two builds
of the same tree are byte-identical.

With setuptools available, `python3 -m build` produces the same wheel. Note that
a wheel's `sponux` entry point is a *Python* script, so it pays ~230 ms of
`import gi` on every keypress instead of the wrapper's ~20 ms — the `.deb` and
the unpacked tree both use `bin/sponux`, which talks to the daemon over D-Bus.

## Tests

```sh
python3 tests/test_placement.py   # multi-monitor placement, against fake monitors
python3 tests/test_index.py       # index rules + live inotify updates
python3 tests/test_config.py      # [open] rules, the config writer, dotfiles
python3 tests/test_usage.py       # the frecency curve and its effect on results
python3 tests/test_query.py       # kind prefixes and typed-path completion
python3 tests/test_report.py      # the runtime log: bounds, and never raising
python3 tests/test_daemon.py      # which installation --check says is answering
python3 tests/test_docs.py        # the two guides: parity, links, documented flags
python3 tools/bench.py installed  # open latency of the installed sponux
python3 tools/visual.py /tmp/shots  # screenshot the real window
```

## Layout

```
sponux/
  app.py            GTK4 window, keys, actions, application lifecycle
  placement.py      EWMH hints + centring, so no WM rules are needed
  indexer.py        SQLite filename index: rules, inotify, periodic rebuild
  usage.py          what has been opened, and the ranking bonus for it
  userconfig.py     config.toml and style.css, and writing rules back
  report.py         where runtime problems go when there is no terminal
  config.py         paths and defaults
  style.css         the dark look, driven by CSS variables
  providers/
    base.py         Result type + fuzzy scoring + clipboard
    apps.py         desktop applications (Gio.AppInfo)
    files.py        file search, typed paths, opening, reveal, open-with
    calc.py         safe arithmetic calculator
bin/sponux          the wrapper a hotkey runs; D-Bus fast path
docs/               the user guide, English and Russian
packaging/
  io.github.sponux.desktop   the desktop entry
  sponux.1                   the manual page
  icons/                     the app icon: SVG master, plus hicolor sizes
  changelog, copyright       Debian packaging data
tools/
  mkdeb.py, mktarball.py, mkwheel.py, metadata.py   the builds
  mkicons.py                          icon sizes, rendered from the SVG master
  bench.py, visual.py                 latency, and screenshotting the real window
tests/
  test_*.py                           the checks; each one runs standalone
.github/workflows/ci.yml                the same checks, plus a reproducibility proof
```

`packaging/` and `tools/` are build inputs — nothing you need to *run* sponux
reads them. `sponux/` and `bin/` alone are enough for that.

Internals, decisions and their measurements are in `HANDOFF.md`.

## Licence

MIT — see [LICENSE](LICENSE).
