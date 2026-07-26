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

    from . import usage
    enabled, weight = usage._settings()
    print("\n[rank]")
    if enabled:
        print(f"  ok      frecency on, weight {weight:g}, "
              f"{len(usage._table())} thing(s) remembered in {config.USAGE_DB}")
    else:
        print("  note    frecency off; results are ranked on the query alone")

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
