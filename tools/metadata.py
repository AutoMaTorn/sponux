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


def check_version_agreement():
    """__init__.py carries the version too, and nothing else would notice."""
    text = (SRC / "sponux" / "__init__.py").read_text()
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if found and found.group(1) != VERSION:
        raise SystemExit(
            f"sponux/__init__.py says {found.group(1)}, "
            f"pyproject.toml says {VERSION}"
        )


def payload():
    """The files that make up an installation, repo-relative and sorted."""
    return sorted(
        str(p.relative_to(SRC))
        for pattern in ("sponux/*.py", "sponux/*.css", "sponux/providers/*.py")
        for p in SRC.glob(pattern)
    )
