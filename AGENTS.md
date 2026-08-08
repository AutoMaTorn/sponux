# AGENTS.md

Orientation for an AI agent picking up this repo cold. Keep this file short —
it is a map, not a log. Details and reasons live in the files it points to.

## What this is

sponux: a Spotlight-like launcher for Linux — applications, files and a
calculator in one GTK 4 window, toggled by a hotkey from a resident daemon.
Python 3.11+, GTK 4 / PyGObject, SQLite (WAL) for the file index, ctypes/libX11
for window placement. No third-party pip dependencies, deliberately.

Working, published, installed on the maintainer's machine. Latest tag
`v0.2.1`; the tree says **0.2.3**, unreleased and untagged (0.2.2 was skipped
deliberately). Bound to `$mod+d` in the user's i3 config (not part of this repo).

## Rules before you touch anything

- **Never `git commit` or `git push` unless asked.** The user does all
  commits, pushes, tags and releases themselves. This has been said before;
  don't re-ask, just don't do it.
- **Don't edit the user's dotfiles** (`~/.config/i3/config`, etc.) to solve a
  sponux problem. The app places its own window and needs no WM rules — see
  `placement.py` and the "window takes itself out of the WM's hands" section
  of `HANDOFF.md`. If something feels like it needs a dotfiles change, it
  probably means the app should do it instead.
- **Application launching is settled** (`Gio.AppInfo`, not configurable) —
  don't reopen that decision.
- `HANDOFF.md` is gitignored (it carries absolute paths) — don't expect it to
  show up in `git log`, and don't be surprised if it drifts from a fresh
  clone's reality.

## Where the real information is

| File | What's in it |
| --- | --- |
| `README.md` / `README.ru.md` | user-facing: what it does, install, keys, layout |
| `HANDOFF.md` | **read this first for any real work.** Non-obvious internals, decisions with their reasons, measurements, traps, and an `OPEN` section of unfinished business |
| `ROADMAP.md` | what to build next, in Russian, with costs already measured and options already rejected — read before proposing new work |
| `REPORT.md` | a dated project review (bugs, gaps, priorities) — a snapshot, not maintained; check its date before trusting it |
| `docs/user-guide.{en,ru}.md` | end-user documentation (not shipped in the package; lives in the wiki) |

## Layout

```
sponux/            app.py (GTK4 window/actions), placement.py (X11),
                    indexer.py (SQLite + inotify), usage.py (frecency),
                    userconfig.py, report.py, config.py, providers/
bin/sponux          hotkey entry point; D-Bus fast path, ~20 ms toggle
tools/              mkdeb.py / mktarball.py / mkwheel.py, bench.py, visual.py
tests/              test_*.py — each runs standalone, no pytest, no display
packaging/          desktop entry, AppStream metainfo, man page, icons,
                    Debian changelog, copyright
```

`sponux/` and `bin/` are what ships and runs; `packaging/` and `tools/` are
build inputs.

## Quick checks

```sh
for t in tests/test_*.py; do python3 "$t" || break; done
python3 tools/bench.py sponux        # open latency from this checkout
./bin/sponux --check                 # config.toml + runtime log
```
