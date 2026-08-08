"""Checks for the packaging metadata.

Three files describe sponux to something other than a human reader — the
desktop entry, the AppStream metainfo and the .deb control block — and each
repeats identifiers the others already carry: the component id, the icon name,
the categories, the version, the release dates. Nothing at runtime reads them
together, so a disagreement is invisible until a software centre shows the
wrong thing, or shows nothing at all.

The metainfo file is also validated with appstreamcli when it is installed,
which is the only check here that knows the specification rather than just
this project's own consistency.

Run: python3 tests/test_packaging.py
"""

import email.utils
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
METAINFO = PACKAGING / "io.github.sponux.metainfo.xml"
DESKTOP = PACKAGING / "io.github.sponux.desktop"

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


sys.path.insert(0, str(ROOT / "tools"))
import metadata                                        # noqa: E402
import mkdeb                                           # noqa: E402

# ---- the metainfo is well-formed and says what the spec requires ----------

check("the metainfo file exists", METAINFO.is_file(), True)
root = ET.parse(METAINFO).getroot()

check("it is a desktop-application component",
      root.get("type"), "desktop-application")

component_id = root.findtext("id")
# The id, the file name and the desktop entry all have to be the same string:
# a software centre matches the metainfo to the launcher by it, and gets no
# Launch button when they differ.
check("the component id matches the file name",
      f"{component_id}.metainfo.xml", METAINFO.name)
check("the desktop entry is named after the same id",
      f"{component_id}.desktop", DESKTOP.name)
check("<launchable> points at that desktop entry",
      root.findtext("launchable"), DESKTOP.name)

# A stock icon has to be an icon that is actually installed, under hicolor's
# name for it — which is the icon the desktop entry names too.
icon = root.find("icon[@type='stock']")
check("there is a stock icon", icon is not None, True)
if icon is not None:
    check("the stock icon is the installed one", icon.text, component_id)

for tag in ("metadata_license", "project_license", "name", "summary"):
    check(f"<{tag}> is present and non-empty",
          bool((root.findtext(tag) or "").strip()), True)

check("the summary is pyproject's description, not a second wording",
      root.findtext("summary"), metadata.SUMMARY)
check("the project licence agrees with pyproject",
      root.findtext("project_license"), metadata.LICENSE)

# An <content_rating> that is present but empty means "every OARS question
# answered none"; leaving the tag out entirely means "unrated", which is what
# a store then displays.
check("there is a content rating",
      root.find("content_rating") is not None, True)

# ---- the desktop entry and the metainfo agree ----------------------------

desktop = dict(
    line.split("=", 1) for line in DESKTOP.read_text().splitlines()
    if "=" in line and not line.startswith("#")
)

meta_categories = [e.text for e in root.findall("categories/category")]
desktop_categories = [c for c in desktop["Categories"].split(";") if c]
check("the categories are the desktop entry's", meta_categories, desktop_categories)

meta_keywords = [e.text for e in root.findall("keywords/keyword")]
desktop_keywords = [k for k in desktop["Keywords"].split(";") if k]
check("the keywords are the desktop entry's", meta_keywords, desktop_keywords)

check("the icon the desktop entry names is the component id",
      desktop["Icon"], component_id)

# ---- releases line up with the Debian changelog --------------------------
#
# Two hand-maintained release histories in one tree is one too many to trust,
# so the shorter one is checked against the one a release is actually cut from.

releases = [(r.get("version"), r.get("date")) for r in root.findall("releases/release")]
check("there are releases listed", len(releases) > 0, True)
check("the newest release is this version", releases[0][0], metadata.VERSION)

# "sponux (0.2.1-1) unstable; urgency=medium" … " -- name <mail>  Wed, 29 Jul …"
changelog = (PACKAGING / "changelog").read_text()
entries = []
for deb_version, trailer in zip(
        re.findall(r"^sponux \((\S+?)-\d+\) ", changelog, re.M),
        re.findall(r"^ -- .+?  (.+)$", changelog, re.M)):
    when = email.utils.parsedate_to_datetime(trailer)
    entries.append((deb_version, when.strftime("%Y-%m-%d")))

check("every release is in the changelog, with the same date",
      releases, entries)
check("…and they are newest first, which the spec asks for and "
      "check_version_agreement() relies on",
      [v for v, _ in releases], sorted((v for v, _ in releases), reverse=True))

# ---- the version cannot drift ---------------------------------------------

check("the metainfo is one of the files check_version_agreement() covers",
      any("metainfo" in rel for rel, _ in metadata._VERSION_IN), True)

# ---- appstreamcli, when it is installed -----------------------------------
#
# The only check here that knows the specification. CI installs it; a developer
# machine without it still gets everything above.

if shutil.which("appstreamcli"):
    done = subprocess.run(
        ["appstreamcli", "validate", "--pedantic", "--no-color", str(METAINFO)],
        capture_output=True, text=True)
    check("appstreamcli validates it", done.returncode, 0)
    if done.returncode != 0:
        print(done.stdout or done.stderr)
else:
    print("note appstreamcli is not installed; skipped the specification check")

# ---- the builders carry the metadata into the artifacts -------------------
#
# A metainfo file that is not installed is a file nobody reads.
check("the .deb installs the metainfo into /usr/share/metainfo",
      [dst for _src, dst, _mode in mkdeb.FILES if "metainfo" in dst],
      ["usr/share/metainfo/io.github.sponux.metainfo.xml"])

# gdbus is what makes the wrapper's fast path work and it is not pulled in by
# any of the hard dependencies, so it has to be asked for by name somewhere.
check("the .deb recommends the package gdbus comes from",
      "libglib2.0-bin" in mkdeb.RECOMMENDS, True)
check("…and suggests the Wayland layer-shell bindings",
      "gir1.2-gtk4layershell-1.0" in mkdeb.SUGGESTS, True)
check("the control file's Homepage comes from pyproject",
      mkdeb.m.HOMEPAGE, metadata.URLS["Homepage"])

# ---- pyproject carries the fields a package index shows ------------------

project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
for field in ("authors", "keywords", "urls", "classifiers"):
    check(f"pyproject declares {field}", bool(project.get(field)), True)

for label in ("Homepage", "Repository", "Issues"):
    check(f"…including a {label} url", label in project["urls"], True)

# requires-python promises these; claiming only the floor was the mismatch.
declared = {c.rsplit(" :: ", 1)[1] for c in project["classifiers"]
            if c.startswith("Programming Language :: Python :: 3.")}
check("the Python classifiers cover more than the floor version",
      declared >= {"3.11", "3.12", "3.13"}, True)

# The wheel is assembled by hand, so a field added to pyproject reaches the
# artifact only if mkwheel.py was taught to write it out.
mkwheel_src = (ROOT / "tools" / "mkwheel.py").read_text()
for field in ("Author", "Author-email", "Keywords", "Project-URL", "License"):
    check(f"the wheel's METADATA carries {field}",
          f"{field}: " in mkwheel_src, True)

print()
if _failures:
    print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
    sys.exit(1)
print("all packaging checks passed")
