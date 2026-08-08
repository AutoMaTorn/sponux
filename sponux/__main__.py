"""Entry point: ``python3 -m sponux`` (and the ``sponux`` wrapper).

Supports a few non-GUI subcommands for maintenance:
    python3 -m sponux --reindex        rebuild the file index and exit
    python3 -m sponux --write-config   create starter files in ~/.config/sponux
    python3 -m sponux --which PATH     explain how PATH would be opened
    python3 -m sponux --check          check config.toml for problems
    python3 -m sponux --autostart on   run the daemon at login
    python3 -m sponux --daemon         go resident without showing the window
"""

import sys


def _reindex():
    from . import indexer
    rules = indexer.IndexRules.from_settings()
    print(f"Indexing {', '.join(rules.roots)}…", flush=True)
    n = indexer.build_index(rules=rules)
    print(f"Indexed {n} entries into {indexer.config.INDEX_DB}")
    if n >= rules.max_files:
        print(f"  WARNING: stopped at max_files = {rules.max_files}; the rest "
              "of the tree is unfindable until the limit is raised")
    if rules.include:
        print(f"  including {', '.join(rules.include)}")
    if rules.exclude:
        print(f"  excluding {', '.join(rules.exclude)}")
    return 0


def _write_config(force):
    from . import userconfig
    written, replaced, skipped = userconfig.write_starter_files(force=force)
    for path in written:
        print(f"wrote {path}")
    for path, backup in replaced:
        print(f"      the previous {path.name} was kept as {backup.name}")
    for path in skipped:
        print(f"kept  {path} (already exists; --force to overwrite)")
    return 0


def _which(argv):
    """Explain which application would open a path, and why.

    The rules are matched against mime types nobody can guess — GIO calls a
    .ts file Qt Linguist — so the only honest way to write a config is to be
    able to ask.
    """
    import os
    import shlex
    import shutil
    from . import userconfig

    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: sponux --which PATH [PATH…]", file=sys.stderr)
        return 2

    from gi.repository import Gio

    status = 0
    for raw in args:
        path = os.path.abspath(os.path.expanduser(raw))
        is_dir = os.path.isdir(path)
        exists = os.path.exists(path)

        from .providers import files as files_provider
        ctype = files_provider.content_type(path, is_dir)
        argv_, rule = userconfig.resolve_opener(path, is_dir)

        print(path + ("" if exists else "   (does not exist — rules still apply)"))
        print(f"  kind          {'directory' if is_dir else 'file'}")
        print(f"  content type  {ctype}")

        from . import usage
        seen = usage.stats(usage.key_for_file(path))
        if seen is not None:
            import datetime
            hits, last = seen
            when = datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M")
            print(f"  opened        {hits}x, last {when} "
                  f"(+{usage.bonus(usage.key_for_file(path)):.1f} to its rank)")
        if rule is None:
            default = Gio.AppInfo.get_default_for_type(ctype, False)
            name = default.get_display_name() if default else "nothing registered"
            print("  matched       no rule in config.toml")
            print(f"  opens with    the desktop default: {name}")
        else:
            print(f"  matched       {rule}")
            print(f"  command       {shlex.join(argv_)}")
            if shutil.which(argv_[0]) is None:
                print(f"  PROBLEM       {argv_[0]!r} is not on PATH; sponux would "
                      "fall back to the desktop default")
                status = 1
        print()
    return status


def _check():
    """Report what is wrong with the config, and what went wrong while running.

    The static half is what config.toml says; the runtime half is the log,
    which is the only place a failure lands when sponux was started from a
    hotkey or at login and had no terminal to complain to.
    """
    rc = _check_config()
    pid = _check_daemon()
    _check_startup(pid)
    _check_log()
    return rc


def _check_startup(daemon_pid):
    """How sponux gets started — the usual reason a fresh install looks broken.

    Installed and never bound to a key, sponux is a program that opens when
    you pick it out of a menu and does nothing else, which is not what it is
    for. The window says so on its first few opens; this says it here, where
    someone who has gone looking for an explanation will be.
    """
    from . import config, usage

    print("\nstartup")
    if config.AUTOSTART_FILE.exists():
        print(f"  ok      starts at login: {config.AUTOSTART_FILE}")
    elif daemon_pid is not None:
        print(f"  ok      a daemon is up (pid {daemon_pid}), so something "
              "starts it")
        print(f"  note    not {config.AUTOSTART_FILE} though — i3's "
              "`exec_always`, or by hand")
    else:
        print("  note    nothing starts the daemon at login: "
              f"{config.AUTOSTART_FILE} does not exist")
        print("          `sponux --autostart on` moves the ~420 ms cold start "
              "off your first keypress")

    if usage.looks_fresh():
        print(f"  note    opened {usage.opens()} time(s) and nothing opened "
              "through it yet.")
        print("          sponux is meant to be on a key binding, and nothing "
              "here can read your")
        print("          window manager's config — bind `sponux` to a key "
              "yourself.")


def _import_root(environ: dict, cwd, argv):
    """Which directory a running daemon imported the sponux package from.

    Pure, so the awkward part can be tested without a daemon. The wrapper
    exports PYTHONPATH, which is the answer in both installed and checkout
    layouts; the rest is for a daemon someone started by hand.
    """
    import os

    def holds_package(directory):
        return bool(directory) and os.path.isfile(
            os.path.join(directory, "sponux", "__main__.py"))

    for entry in (environ.get("PYTHONPATH") or "").split(os.pathsep):
        if holds_package(entry):
            return entry

    # `python3 -m sponux` without -P imports from the working directory.
    if "-P" not in argv and holds_package(cwd):
        return cwd

    # `python3 /path/to/sponux/__main__.py` names it outright.
    for arg in argv[1:]:
        if os.path.basename(arg) == "__main__.py":
            root = os.path.dirname(os.path.dirname(os.path.abspath(arg)))
            if holds_package(root):
                return root
    return None


def _version_at(root):
    """The version of the package in `root`, read off disk. None if unreadable.

    Asking the daemon would be better, but it exposes no such call, and the
    point here is to identify an installation that may well be older than the
    one asking.
    """
    import os
    import re

    try:
        with open(os.path.join(root, "sponux", "__init__.py")) as fh:
            text = fh.read()
    except OSError:
        return None
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _daemon_facts():
    """(pid, import root, why the root is unknown) for the daemon on the bus.

    pid is None when nothing owns the name. The root can be None while the pid
    is not: /proc is readable only for one's own processes.
    """
    import os
    from gi.repository import Gio, GLib

    from . import config

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID",
            GLib.Variant("(s)", (config.APP_ID,)),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 2000, None,
        )
        pid = reply.unpack()[0]
    except GLib.Error as exc:
        # NameHasNoOwner is the ordinary case: no daemon is running.
        return None, None, exc.message

    proc = f"/proc/{pid}"
    try:
        with open(f"{proc}/environ") as fh:
            environ = dict(kv.split("=", 1)
                           for kv in fh.read().split("\0") if "=" in kv)
        with open(f"{proc}/cmdline") as fh:
            argv = fh.read().split("\0")
        cwd = os.readlink(f"{proc}/cwd")
    except OSError as exc:
        return pid, None, str(exc)
    return pid, _import_root(environ, cwd, argv), ""


def _check_daemon():
    """Say which installation is answering, since two of them can be present.

    A packaged sponux and a checkout share io.github.sponux, so whichever
    daemon reached the bus first serves every keypress — silently, which is
    the wrong way round when the reason you are running --check is that a
    change of yours seems to have done nothing.
    """
    import os

    from . import config

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"\ndaemon — this tree is {here} ({_version_at(here) or 'version?'})")

    pid, root, why = _daemon_facts()
    if pid is None:
        print("  ok      none running; the next press starts one from here")
        return None
    if root is None:
        print(f"  note    pid {pid} holds {config.APP_ID}, but where it imports "
              f"from could not be read ({why})")
        return pid

    version = _version_at(root) or "version?"
    if os.path.realpath(root) == os.path.realpath(here):
        print(f"  ok      pid {pid} from {root} ({version}) — this one, so a "
              "keypress runs what you are looking at")
        return pid
    print(f"  WARN    pid {pid} from {root} ({version}) answers every keypress, "
          "not this tree")
    print("          Both share one name on the bus and the first to claim it "
          "wins. Stop it")
    print(f"          with Ctrl+Q in the launcher, or `kill {pid}`, then start "
          "the one you want.")
    return pid


def _check_log():
    """Show the tail of the runtime log — what --check cannot find by itself."""
    from . import config, report

    lines = report.log_lines()
    print(f"\nruntime log — {config.LOG_FILE}")
    if not lines:
        print("  ok      nothing reported while running")
        return
    shown = lines[-5:]
    tail = f"; the last {len(shown)}" if len(lines) > len(shown) else ""
    print(f"  note    {len(lines)} line(s){tail}:")
    for line in shown:
        print(f"          {line}")
    print("  note    these have already happened; delete the file to reset it")


def _check_config():
    """Report everything wrong with the config that can be found statically."""
    import os
    import shlex
    import shutil
    import tomllib
    from . import config, indexer, userconfig

    problems = warnings = 0

    if not userconfig.CONFIG_FILE.exists():
        print(f"{userconfig.CONFIG_FILE} does not exist — all defaults apply.")
        print("Run 'sponux --write-config' to create a commented starter file.")
        return 0

    try:
        with open(userconfig.CONFIG_FILE, "rb") as fh:
            tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"{userconfig.CONFIG_FILE}: {exc}")
        print("\nThe whole file is ignored while it is unparsable.")
        return 1

    print(f"{userconfig.CONFIG_FILE}")

    rules = userconfig.opener_rules()
    print(f"\n[open] — {len(rules)} rule(s)")
    for rule, template in rules:
        argv = userconfig.command_argv(template, "/tmp/x")
        if argv is None:
            print(f"  ERROR   {rule}: {template!r} is not a usable command")
            problems += 1
            continue
        binary = shutil.which(argv[0])
        if binary is None:
            print(f"  ERROR   {rule} = {template!r}: {argv[0]!r} is not on PATH")
            problems += 1
        else:
            print(f"  ok      {rule} -> {binary}")

    index = indexer.IndexRules.from_settings()
    print("\n[index]")
    for root in index.roots:
        if os.path.isdir(root):
            print(f"  ok      root {root}")
        else:
            print(f"  ERROR   root {root} is not a directory")
            problems += 1
    for path in index.include:
        # A glob is a pattern, not a path; only literal ones can be checked.
        if any(ch in path for ch in "*?["):
            continue
        if os.path.exists(path):
            print(f"  ok      include {path}")
        else:
            print(f"  WARN    include {path} does not exist")
            warnings += 1
        if not any(path == r or path.startswith(r + "/") for r in index.roots):
            print(f"  WARN    include {path} is outside every root, so nothing "
                  "will reach it")
            warnings += 1
    for pattern in index.exclude:
        print(f"  ok      exclude {pattern}")
    if not index.watch:
        print(f"  note    watching is off; the index only refreshes every "
              f"{index.interval}s")
    if index.watch and index.interval == 0:
        print("  WARN    no periodic rebuild, so dropped inotify events are "
              "never recovered")
        warnings += 1

    # An index sitting at max_files means every file past the limit is
    # unfindable, and nothing but this says so outside the runtime log.
    import sqlite3
    try:
        con = indexer.connect(readonly=True)
        try:
            rows = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            con.close()
    except sqlite3.OperationalError:
        rows = None  # no index yet, or no table yet: nothing to compare
    if rows is not None:
        if rows >= index.max_files:
            print(f"  WARN    the index holds {rows} entries — at max_files = "
                  f"{index.max_files}, so it is truncated; raise the limit")
            warnings += 1
        elif rows >= index.max_files * 0.9:
            print(f"  note    the index holds {rows} entries, nearing "
                  f"max_files = {index.max_files}")
        # Size costs latency long before it costs correctness, and the search
        # runs on the GTK main thread, so this is the number that decides
        # whether typing stays smooth. Nothing else reports it.
        if rows >= config.SEARCH_SLOW_ROWS:
            print(f"  WARN    the index holds {rows} entries; a keystroke "
                  f"searches it in roughly {indexer.search_cost_ms(rows):.0f} ms, "
                  f"and the search runs on the UI thread — narrow [index] "
                  f"roots/exclude, or lower max_files")
            warnings += 1
        elif rows >= config.SEARCH_SLOW_ROWS // 2:
            print(f"  note    the index holds {rows} entries, about "
                  f"{indexer.search_cost_ms(rows):.0f} ms per keystroke; "
                  f"still comfortable, worth watching as it grows")

    from . import usage
    enabled, weight = usage._settings()
    print("\n[rank]")
    if enabled:
        print(f"  ok      frecency on, weight {weight:g}, "
              f"{len(usage._table())} thing(s) remembered in {config.USAGE_DB}")
    else:
        print("  note    frecency off; results are ranked on the query alone")

    # [window] and [keys] report their own problems while being read, so any
    # complaint has already been printed above by the time we get here; what is
    # left to show is what actually ended up in force.
    from gi.repository import Gtk
    window = userconfig.window_settings()
    print("\n[window]")
    print(f"  ok      {window.width}px wide, {window.max_results} results, "
          f"{window.debounce}ms debounce")
    where = ("centred vertically" if window.position == "center"
             else f"{window.top_fraction:.0%} down the monitor")
    print(f"  ok      position {window.position} — {where}")
    if not window.hide_on_focus_loss:
        print("  note    stays open when it loses focus; only Escape and the "
              "hotkey dismiss it")

    print("\n[keys]")
    seen = {}
    for action in sorted(window.keys):
        keyval, mods = window.keys[action]
        label = Gtk.accelerator_get_label(keyval, mods)
        print(f"  ok      {action} -> {label}")
        seen.setdefault((keyval, mods), []).append(action)
    for actions in seen.values():
        if len(actions) > 1:
            # Both are reachable in principle, but only the first one _on_key
            # tests will ever fire, so say which wins rather than let it be a
            # mystery.
            print(f"  WARN    {' and '.join(sorted(actions))} share one "
                  "binding; the earlier of them wins")
            warnings += 1

    print()
    if problems:
        print(f"{problems} problem(s), {warnings} warning(s)")
        return 1
    print(f"no problems, {warnings} warning(s)" if warnings else "no problems")
    return 0


def _autostart_command():
    """The command an autostart entry should run, for this installation.

    A checkout and a packaged install can both be present; the answer is
    whichever one this process is running out of, not whichever is on PATH.
    """
    import shutil
    from pathlib import Path

    # <repo>/sponux/__main__.py -> <repo>/bin/sponux. In an installed layout
    # the package sits in <prefix>/share/sponux/, where there is no bin/.
    wrapper = Path(__file__).resolve().parent.parent / "bin" / "sponux"
    if wrapper.is_file():
        return str(wrapper)
    return shutil.which("sponux") or "sponux"


def _autostart(argv):
    """Show, enable or disable starting the daemon at login.

    The point of the daemon is that opening the launcher costs 20 ms instead
    of the ~420 ms of a cold start; that only holds if something has already
    started it, and login is the natural moment.
    """
    from . import config

    args = [a for a in argv[1:] if not a.startswith("-")]
    action = args[0] if args else "status"
    path = config.AUTOSTART_FILE

    if action == "status":
        if path.exists():
            print(f"on   {path}")
            for line in path.read_text().splitlines():
                if line.startswith("Exec="):
                    print(f"     {line[5:]}")
        else:
            print(f"off  ({path} does not exist)")
            print("Run 'sponux --autostart on' to start the daemon at login.")
        return 0

    if action == "off":
        if path.exists():
            path.unlink()
            print(f"removed {path}")
        else:
            print(f"already off ({path} does not exist)")
        return 0

    if action != "on":
        print(f"usage: sponux --autostart [on|off|status]  (not {action!r})",
              file=sys.stderr)
        return 2

    command = _autostart_command()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=sponux\n"
        "Comment=Start the sponux launcher daemon\n"
        f"Exec={command} --daemon\n"
        "Terminal=false\n"
        "StartupNotify=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    import os
    print(f"wrote {path}")
    print(f"      Exec={command} --daemon")

    # A bare window manager does not read ~/.config/autostart at all — i3
    # included — and the file quietly doing nothing is the worst outcome here.
    if os.environ.get("XDG_CURRENT_DESKTOP", "").lower() in ("i3", "sway", ""):
        print("\nYour session does not advertise a desktop environment, and a "
              "bare window\nmanager (i3, sway, …) does not read "
              "~/.config/autostart. Put this in its\nconfig instead — the file "
              "above is then only useful if you later switch:")
        print(f"    exec --no-startup-id {command} --daemon")
    else:
        print("The daemon starts at your next login; nothing appears on screen "
              "until\nyou press your hotkey.")
    return 0


def main():
    if "--autostart" in sys.argv:
        return _autostart(sys.argv[sys.argv.index("--autostart"):])
    if "--daemon" in sys.argv:
        from .app import main as app_main
        return app_main([a for a in sys.argv if not a.startswith("--")],
                        daemon=True)
    if "--reindex" in sys.argv:
        return _reindex()
    if "--write-config" in sys.argv:
        return _write_config("--force" in sys.argv)
    if "--which" in sys.argv:
        return _which(sys.argv[sys.argv.index("--which"):])
    if "--check" in sys.argv:
        return _check()
    from .app import main as app_main
    # Drop our own flags before handing argv to GApplication.
    argv = [a for a in sys.argv if not a.startswith("--")]
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
