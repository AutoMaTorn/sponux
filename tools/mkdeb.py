"""Build dist/sponux_<version>_all.deb — the only way sponux is installed.

Everywhere without dpkg, sponux is used straight from an unpacked source tree:
`bin/sponux` finds the package next to itself, so there is nothing to install
and no install script to maintain. This file is therefore the single
description of the layout.

Beyond the file tree it adds the parts that only mean something inside a .deb:
DEBIAN/control, md5sums, the copyright file and the changelog.

No maintainer scripts. The .desktop file and the manual page are picked up by
dpkg triggers that desktop-file-utils and man-db already declare, so there is
nothing for a postinst to do — and a package with no scripts is a package that
cannot fail to remove.
"""
import gzip
import hashlib
import os
import shutil
import subprocess
import sys

import metadata as m

STAGE = m.SRC / "build" / "deb"
OUT = m.DIST / f"{m.NAME}_{m.DEB_VERSION}_all.deb"

# Where each file goes, relative to the package root. The Python package lands
# in /usr/share/sponux rather than dist-packages because sponux is an
# application, not a library to import — which is what Debian's Python policy
# asks for, and it keeps the name out of every other program's import path.
# bin/sponux looks one level up from itself and finds share/sponux; keep the two
# in step.
PACKAGE_DIR = "usr/share/sponux"

FILES = [
    ("bin/sponux", "usr/bin/sponux", 0o755),
    ("packaging/io.github.sponux.desktop",
     "usr/share/applications/io.github.sponux.desktop", 0o644),
    # Icons go in hicolor's standard buckets: a directory named after any
    # other size is not in hicolor's index.theme and is never looked in.
    *[(f"packaging/icons/hicolor/{s}x{s}/apps/io.github.sponux.png",
       f"usr/share/icons/hicolor/{s}x{s}/apps/io.github.sponux.png", 0o644)
      for s in (48, 64, 128)],
    ("README.md", "usr/share/doc/sponux/README.md", 0o644),
    # In a .deb the licence is `copyright`, in the format policy defines.
    ("packaging/copyright", "usr/share/doc/sponux/copyright", 0o644),
]

# Installed gzipped, as policy requires for manual pages and changelogs.
GZIPPED = [
    ("packaging/sponux.1", "usr/share/man/man1/sponux.1.gz"),
    ("packaging/changelog", "usr/share/doc/sponux/changelog.Debian.gz"),
]

# python3-gi-cairo is not imported anywhere, but GTK 4 needs the cairo
# integration for PyGObject at runtime; leaving it out gives an unhelpful
# failure the first time something draws.
DEPENDS = [
    "python3 (>= 3.11)",
    "python3-gi",
    "python3-gi-cairo",
    "gir1.2-gtk-4.0",
]

DESCRIPTION = """\
 sponux is a keyboard launcher for X11. It searches installed applications,
 a self-maintained index of filenames under your home directory, and evaluates
 arithmetic expressions.
 .
 It runs as a single-instance resident daemon: the first call starts it, and
 every later call toggles the window over D-Bus without starting an
 interpreter. The window takes itself out of the window manager's hands
 (override-redirect) and places itself on the monitor holding the pointer, so
 no window-manager rules are needed on a tiling or a stacking WM alike.
 .
 Which application opens which file, what the index covers and how results are
 ranked are configured in ~/.config/sponux/config.toml; appearance is a
 style.css layered over the bundled dark theme.
"""


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def place(src_rel, dst_rel, mode):
    dst = STAGE / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(m.SRC / src_rel, dst)
    dst.chmod(mode)
    return dst


def stage():
    """Lay the package out under build/deb/."""
    if STAGE.exists():
        shutil.rmtree(STAGE)

    # The Python package. Globbed from the tree by metadata.payload(), so
    # adding a module needs no edit here.
    for rel in m.payload():
        place(rel, f"{PACKAGE_DIR}/{rel}", 0o644)

    for src_rel, dst_rel, mode in FILES:
        place(src_rel, dst_rel, mode)

    for src_rel, dst_rel in GZIPPED:
        dst = STAGE / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = (m.SRC / src_rel).read_bytes()
        with open(dst, "wb") as fh:
            # mtime=0: the gzip header must not make the build unreproducible.
            with gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=9,
                               mtime=0) as gz:
                gz.write(data)
        dst.chmod(0o644)

    # Directory permissions, explicitly. Path.mkdir() applies the process
    # umask — 0775 under the common umask 002 — and Debian requires 0755;
    # `install -d` used to handle this. Doing it here also makes the package
    # independent of the umask of whoever builds it.
    for root, dirs, _files in os.walk(STAGE):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o755)
    STAGE.chmod(0o755)


def installed_size():
    """In KiB, the way dpkg counts it: apparent size rounded up per file."""
    total = 0
    for root, _dirs, files in os.walk(STAGE):
        if "DEBIAN" in root.split(os.sep):
            continue
        for name in files:
            size = os.path.getsize(os.path.join(root, name))
            total += (size + 1023) // 1024
    return total


def control():
    debian = STAGE / "DEBIAN"
    debian.mkdir(parents=True)
    (debian / "control").write_text(
        f"Package: {m.NAME}\n"
        f"Version: {m.DEB_VERSION}\n"
        "Architecture: all\n"
        f"Maintainer: {m.MAINTAINER}\n"
        f"Installed-Size: {installed_size()}\n"
        f"Depends: {', '.join(DEPENDS)}\n"
        "Section: x11\n"
        "Priority: optional\n"
        "Homepage: https://github.com/AutoMaTorn/sponux\n"
        f"Description: {m.SUMMARY}\n"
        f"{DESCRIPTION}"
    )
    (debian / "control").chmod(0o644)

    lines = []
    for root, dirs, files in sorted(os.walk(STAGE)):
        dirs.sort()
        if "DEBIAN" in root.split(os.sep):
            continue
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, STAGE)
            digest = hashlib.md5(open(path, "rb").read()).hexdigest()
            lines.append(f"{digest}  {rel}\n")
    (debian / "md5sums").write_text("".join(lines))
    (debian / "md5sums").chmod(0o644)


def main():
    m.check_version_agreement()
    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb is not installed; cannot build a .deb here")
    m.DIST.mkdir(exist_ok=True)

    stage()
    control()

    # --root-owner-group makes every entry root:root without needing fakeroot.
    # SOURCE_DATE_EPOCH clamps the mtimes dpkg-deb records: staging has just
    # stamped every copied file with the time of the build, so without it two
    # builds of the same tree differ. With it they are byte-identical.
    run(["dpkg-deb", "--root-owner-group", "--build", str(STAGE), str(OUT)],
        env=dict(os.environ, SOURCE_DATE_EPOCH=str(m.SOURCE_DATE_EPOCH)))
    print(f"built {OUT} ({OUT.stat().st_size} bytes)")
    print(f"  install with: sudo apt install ./{OUT.name}")

    if shutil.which("lintian") and "--no-lintian" not in sys.argv:
        print("\nlintian:")
        # initial-upload-closes-no-bugs is the one expected tag: it asks the
        # changelog to close a Debian bug number, which only applies to an
        # upload to the Debian archive.
        subprocess.run(["lintian", "--tag-display-limit", "0", str(OUT)])


if __name__ == "__main__":
    main()
