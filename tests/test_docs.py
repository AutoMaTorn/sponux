"""Checks for the user guides.

Two translations of the same document drift: a section gets added to one and
not the other, or a table-of-contents link stops resolving after a heading is
reworded. Neither shows up until a reader hits it, so both are checked here.

Anchors are generated the way GitHub does it — lowercase, drop everything that
is not a letter, digit, space, hyphen or underscore, then spaces to hyphens —
which is also what most other Markdown renderers do.

Run: python3 tests/test_docs.py
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
GUIDES = {"en": DOCS / "user-guide.en.md", "ru": DOCS / "user-guide.ru.md"}

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


def anchor(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def headings(text: str, level: int = 2):
    marker = "#" * level + " "
    return [ln[len(marker):].strip() for ln in text.splitlines()
            if ln.startswith(marker)]


for lang, path in GUIDES.items():
    check(f"{lang}: the guide exists", path.is_file(), True)

texts = {lang: path.read_text() for lang, path in GUIDES.items()}

# ---- the two translations describe the same document ----------------------

counts = {lang: len(headings(t)) for lang, t in texts.items()}
check("both guides have the same number of sections",
      counts["en"] == counts["ru"], True)
print(f"     ({counts['en']} sections each)")

# The numbered table of contents is the reader's map; if one language grew a
# section the other did not, the numbering is where it shows.
toc = {lang: re.findall(r"^\d+\. \[", t, re.M) for lang, t in texts.items()}
check("both tables of contents list the same number of entries",
      len(toc["en"]) == len(toc["ru"]), True)
check("…and there is one entry per section",
      len(toc["en"]), counts["en"])

# ---- every internal link resolves ----------------------------------------

for lang, text in texts.items():
    present = {anchor(h) for lvl in (2, 3) for h in headings(text, lvl)}
    linked = re.findall(r"\]\(#([^)]+)\)", text)
    broken = sorted(set(linked) - present)
    check(f"{lang}: every #anchor link resolves to a heading", broken, [])
    check(f"{lang}: there are links to check", len(linked) > 0, True)

# ---- the two guides point at each other ----------------------------------

for lang, text in texts.items():
    files = re.findall(r"\]\((user-guide\.\w+\.md)\)", text)
    missing = [f for f in files if not (DOCS / f).is_file()]
    check(f"{lang}: its cross-language link points at a real file", missing, [])
    check(f"{lang}: it links to the other language", len(files) > 0, True)

# ---- README points at both, and is no longer a second copy of them --------

READMES = {"en": ROOT / "README.md", "ru": ROOT / "README.ru.md"}
for lang, path in READMES.items():
    check(f"README.{lang} exists", path.is_file(), True)
readmes = {lang: path.read_text() for lang, path in READMES.items()}

for lang, text in readmes.items():
    for name in ("docs/user-guide.en.md", "docs/user-guide.ru.md"):
        check(f"README.{lang} links to {name}", name in text, True)

# The two READMEs are translations of one document and drift the same way the
# guides would.
r_counts = {lang: len(headings(t)) for lang, t in readmes.items()}
check("both READMEs have the same number of sections",
      r_counts["en"] == r_counts["ru"], True)
print(f"     ({r_counts['en']} sections each)")
check("README.md links to the Russian one", "README.ru.md" in readmes["en"], True)
check("README.ru.md links back", "README.md" in readmes["ru"], True)

# The README deliberately keeps only a quick start: the detail lives in the
# guides, so that there is one place to fix when something changes.
check("README stayed shorter than the guide it points to",
      len(readmes["en"]) < len(texts["en"]), True)

# ---- the screenshots are there and both languages show the same ones ------
#
# A broken image on the front page of the project is the first thing a visitor
# sees, and renaming an asset is exactly how it happens.
images = {}
for lang, text in readmes.items():
    images[lang] = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    # A remote image — the CI badge — is not ours to verify; only the files
    # committed alongside the README can be checked for existence.
    local = [p for p in images[lang] if not p.startswith(("http://", "https://"))]
    broken = sorted(p for p in local if not (ROOT / p).is_file())
    check(f"README.{lang}: every screenshot it embeds exists", broken, [])
check("both READMEs embed the same images",
      images["en"], images["ru"])
check("there are some to embed", len(images["en"]) >= 2, True)

# ---- the guides stay OUT of the shipped package --------------------------
#
# They live in the project wiki, so they can be corrected without cutting a
# release. A package that carried a copy would start answering questions with
# whatever was true when it was built, so this is asserted rather than assumed.

# Each builder names the files it ships, so each is a place a guide could creep
# back into. "docs/" would be a path to one; "user-guide" would be its name.
for builder in ("mkdeb.py", "mkicons.py", "mktarball.py", "mkwheel.py"):
    text = (ROOT / "tools" / builder).read_text()
    named = [n for n in ("user-guide", "docs/") if n in text]
    check(f"{builder} does not ship the guides", named, [])

check("and those are all the builders there are",
      sorted(p.name for p in (ROOT / "tools").glob("mk*.py")),
      ["mkdeb.py", "mkicons.py", "mktarball.py", "mkwheel.py"])

# ---- the directories say what they hold ----------------------------------
#
# tools/ is for building and for driving the app by hand; tests/ is for checks.
# They were one directory once and reading it meant knowing which prefix meant
# what.
check("no tests left behind in tools/",
      sorted(p.name for p in (ROOT / "tools").glob("test_*.py")), [])
check("every test lives in tests/",
      len(list((ROOT / "tests").glob("test_*.py"))) >= 6, True)

# Both READMEs tell the reader how to run them, so the paths have to be real.
for lang, text in readmes.items():
    quoted = set(re.findall(r"python3 ((?:tools|tests)/[\w.]+\.py)", text))
    missing = sorted(q for q in quoted if not (ROOT / q).is_file())
    check(f"README.{lang}: every script it tells you to run exists", missing, [])
    check(f"README.{lang}: it does quote some", len(quoted) > 0, True)

# Each script's own "Run:" line has to name where the script actually is. Moving
# these files into tests/ left six of them pointing at tools/, which is the kind
# of stale instruction nobody notices until they copy it.
wrong = []
for path in sorted(list((ROOT / "tests").glob("*.py"))
                   + list((ROOT / "tools").glob("*.py"))):
    # Anchored to the start of a line so this does not match the pattern
    # literal a few characters to the right of here.
    for quoted_path in re.findall(r"^Run: python3 (\S+\.py)",
                                  path.read_text(), re.M):
        if (ROOT / quoted_path).resolve() != path:
            wrong.append(f"{path.name} says {quoted_path}")
check("each script's 'Run:' line names its own path", wrong, [])

# ---- the version is the same everywhere it is written out ----------------
#
# The builders refuse to run when these disagree, but a build is not something
# you do on the way past; a test run is.

sys.path.insert(0, str(ROOT / "tools"))
import metadata                                      # noqa: E402

try:
    metadata.check_version_agreement()
    agreement = ""
except SystemExit as exc:
    agreement = str(exc)
check("every file that spells the version out agrees with pyproject.toml",
      agreement, "")
check("…and the manual page is one of the files checked",
      any("sponux.1" in rel for rel, _ in metadata._VERSION_IN), True)


# ---- the commands the guides tell people to run exist --------------------

main_py = (ROOT / "sponux" / "__main__.py").read_text()
flags = set()
for text in texts.values():
    flags |= set(re.findall(r"sponux (--[a-z-]+)", text))
unknown = sorted(f for f in flags if f not in main_py)
check("every 'sponux --flag' in the guides is a real flag", unknown, [])
print(f"     (checked {', '.join(sorted(flags))})")

print()
if _failures:
    print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
    sys.exit(1)
print("all documentation checks passed")
