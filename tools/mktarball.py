"""Build dist/sponux-<version>.tar.gz — unpack it and run it.

This is the non-Debian install path, for people who would rather download an
archive than clone the repository. It is not a source release: it carries what
is needed to *run* sponux and nothing else. There is no install step and no
install script — `bin/sponux` finds the package next to itself, so:

    tar xf sponux-0.1.0.tar.gz -C ~/opt
    ~/opt/sponux-0.1.0/bin/sponux

Uninstalling is deleting the directory. To build the .deb, or to run the tests,
clone the repository instead — those inputs are deliberately not in here.

Reproducible: sorted entries, one timestamp taken from packaging/changelog,
owner root:root, and gzip with no mtime field. Two builds of the same tree give
byte-identical archives.
"""
import gzip
import io
import tarfile

import metadata as m

TOP = f"{m.NAME}-{m.VERSION}"
OUT = m.DIST / f"{TOP}.tar.gz"

# Deliberately absent: tools/ and tests/ (build inputs and checks, useless to
# someone who just wants to run it), packaging/changelog and copyright (.deb
# metadata), and the user guides — those live in the project wiki so they can be
# fixed without cutting a release, which is also why the .deb omits them.
CONTENTS = [
    "README.md",
    "README.ru.md",
    "LICENSE",
    "bin/sponux",
    # Readable in place with `man -l packaging/sponux.1`; there is no install
    # step here to put it on the manpath.
    "packaging/sponux.1",
    *m.payload(),
]

EXECUTABLE = {"bin/sponux"}


def main():
    m.check_version_agreement()
    m.DIST.mkdir(exist_ok=True)

    missing = [rel for rel in CONTENTS if not (m.SRC / rel).is_file()]
    if missing:
        raise SystemExit("missing from the tree: " + ", ".join(missing))

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel in CONTENTS:
            data = (m.SRC / rel).read_bytes()
            info = tarfile.TarInfo(f"{TOP}/{rel}")
            info.size = len(data)
            info.mtime = m.SOURCE_DATE_EPOCH
            info.mode = 0o755 if rel in EXECUTABLE else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))

    # mtime=0 keeps the gzip header out of the reproducibility argument.
    with open(OUT, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(raw.getvalue())

    print(f"built {OUT}")
    print(f"  {len(CONTENTS)} files, {OUT.stat().st_size} bytes")
    print(f"  use it with: tar xf {OUT.name} -C ~/opt && ~/opt/{TOP}/bin/sponux")


if __name__ == "__main__":
    main()
