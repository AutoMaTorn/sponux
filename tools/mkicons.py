"""Regenerate the installed icon sizes from the master artwork.

The master is `packaging/icons/io.github.sponux.svg`. It is installed as-is
into hicolor's `scalable/` directory, and rendered into hicolor's standard
pixel buckets besides: `scalable/` is only read by lookups that know how to
rasterise SVG, and a size hicolor's `index.theme` does not list is never
looked in at all, so the PNGs are what makes the icon findable everywhere.

The sizes are committed rather than produced during the build. `tools/mkdeb.py`
and its siblings are standard-library Python on purpose, so that a build needs
nothing installed and stays reproducible; rendering needs GdkPixbuf and its
SVG loader (librsvg). This script is the dev-time half of that trade, run by
hand when the artwork changes:

    python3 tools/mkicons.py

Every size is rendered from the vector directly rather than downscaled from one
raster, which is what keeps the small ones sharp.
"""

import pathlib
import shutil
import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "icons"
MASTER = SRC / "io.github.sponux.svg"
SIZES = (48, 64, 128, 256)


def main():
    if not MASTER.is_file():
        print(f"no master artwork at {MASTER}", file=sys.stderr)
        return 1

    scalable = SRC / "hicolor" / "scalable" / "apps" / MASTER.name
    scalable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MASTER, scalable)
    print(f"wrote {scalable.relative_to(SRC.parent.parent)}")

    for size in SIZES:
        out = SRC / "hicolor" / f"{size}x{size}" / "apps" / f"{MASTER.stem}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        # Rendered at the target size, not scaled after the fact: librsvg
        # rasterises the paths at that resolution, so the thin strokes in the
        # artwork stay strokes at 48px instead of turning to grey mush.
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(MASTER), size, size)
        pixbuf.savev(str(out), "png", [], [])
        print(f"wrote {out.relative_to(SRC.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
