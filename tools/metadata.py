"""The one place the build scripts read the project's identity from.

pyproject.toml holds name/version/description; packaging/changelog holds the
Debian revision and the release date. Everything else derives from those, so
there is nothing to keep in step by hand.
"""
import email.utils
import pathlib
import os
import re
import tomllib

SRC = pathlib.Path(__file__).resolve().parent.parent
DIST = SRC / "dist"

PROJECT = tomllib.loads((SRC / "pyproject.toml").read_text())["project"]
NAME = PROJECT["name"]
VERSION = PROJECT["version"]
SUMMARY = PROJECT["description"]
REQUIRES_PYTHON = PROJECT["requires-python"]
CLASSIFIERS = PROJECT.get("classifiers", [])

_CHANGELOG = (SRC / "packaging" / "changelog").read_text()
# "sponux (0.1.0-1) unstable; urgency=medium"
_HEAD = re.match(r"^(\S+) \((\S+)\) ", _CHANGELOG)
# " -- automatorn <a@b.c>  Sun, 26 Jul 2026 12:00:00 +0000"
_TRAILER = re.search(r"^ -- (.+?)  (.+)$", _CHANGELOG, re.M)
if not _HEAD or not _TRAILER:
    raise SystemExit("packaging/changelog: cannot parse the top entry")

DEB_VERSION = _HEAD.group(2)              # 0.1.0-1
MAINTAINER = _TRAILER.group(1)
RELEASED = email.utils.parsedate_to_datetime(_TRAILER.group(2))

if not DEB_VERSION.startswith(VERSION + "-"):
    raise SystemExit(
        f"packaging/changelog says {DEB_VERSION}, pyproject.toml says {VERSION}"
    )

# Reproducible archives: every timestamp we write is this one.
SOURCE_DATE_EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", RELEASED.timestamp()))


# Everywhere the version is written out, and how to find it there. The
# changelog is compared separately, above, because its version carries a Debian
# revision. Anything not on this list can drift without anyone noticing, which
# is exactly what the manual page did until it was added.
_VERSION_IN = (
    ("sponux/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
    # .TH SPONUX 1 "2026-07-26" "sponux 0.1.0" "User Commands"
    ("packaging/sponux.1", r'^\.TH\s+\S+\s+\d+\s+"[^"]*"\s+"sponux ([^"]+)"'),
)


def check_version_agreement():
    """Every file that spells the version out has to agree with pyproject."""
    for rel, pattern in _VERSION_IN:
        text = (SRC / rel).read_text()
        found = re.search(pattern, text, re.M)
        if found is None:
            raise SystemExit(
                f"{rel}: no version found — the file changed shape, and the "
                f"check that keeps it honest cannot see it any more"
            )
        if found.group(1) != VERSION:
            raise SystemExit(
                f"{rel} says {found.group(1)}, pyproject.toml says {VERSION}"
            )


def payload():
    """The files that make up an installation, repo-relative and sorted."""
    return sorted(
        str(p.relative_to(SRC))
        for pattern in ("sponux/*.py", "sponux/*.css", "sponux/providers/*.py")
        for p in SRC.glob(pattern)
    )
