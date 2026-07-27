"""Regenerate the installed icon sizes from the master artwork.

The icon ships as PNGs in hicolor's standard buckets because that is the only
way an icon is found: hicolor's `index.theme` lists fixed sizes, and a
directory named after some other number — the master is 202x202 — is not in it
and would never be looked in.

The sizes are committed rather than produced during the build. `tools/mkdeb.py`
and its siblings are standard-library Python on purpose, so that a build needs
nothing installed and stays reproducible; scaling needs GdkPixbuf. This script
is the dev-time half of that trade, run by hand when the artwork changes:

    python3 tools/mkicons.py

Only downscales are generated. Upscaling 202px art to 256 would ship a blurrier
icon than letting the theme scale the 128 one on demand.
"""

import pathlib
import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "icons"
MASTER = SRC / "io.github.sponux.png"
SIZES = (48, 64, 128)


def main():
    if not MASTER.is_file():
        print(f"no master artwork at {MASTER}", file=sys.stderr)
        return 1

    master = GdkPixbuf.Pixbuf.new_from_file(str(MASTER))
    print(f"{MASTER.name}: {master.get_width()}x{master.get_height()}")

    for size in SIZES:
        if size > master.get_width():
            print(f"  skip {size}px — larger than the master")
            continue
        out = SRC / "hicolor" / f"{size}x{size}" / "apps" / MASTER.name
        out.parent.mkdir(parents=True, exist_ok=True)
        # HYPER, not BILINEAR: the artwork is thin vertical bars, and bilinear
        # turns them to grey mush at 48px.
        scaled = master.scale_simple(size, size, GdkPixbuf.InterpType.HYPER)
        scaled.savev(str(out), "png", [], [])
        print(f"  wrote {out.relative_to(SRC.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
