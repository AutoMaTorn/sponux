"""Checks for the two ways a query can mean something other than "search":
a typed path, and a kind prefix.

Both exist to reach past the index rather than to widen it, so what matters
here is that they are exact — a path result must be the path that was typed,
and a prefix must not swallow part of the query it precedes.

Run: python3 tests/test_query.py
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import app as sponux_app                      # noqa: E402
from sponux.providers import files as files_provider      # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


# ---- kind prefixes ---------------------------------------------------------

split = sponux_app._split_kind_prefix

check("no prefix leaves the query alone", split("firefox"), (None, "firefox"))
check("f: means files", split("f:config"), ("file", "config"))
check("a: means apps", split("a:term"), ("app", "term"))
check("c: means the calculator", split("c:2+2"), ("calc", "2+2"))
check("= means the calculator too", split("=2+2"), ("calc", "2+2"))
check("the long form works", split("file:notes"), ("file", "notes"))
check("app: is not read as a:", split("app:term"), ("app", "term"))
# "fire" starts with "f" but not with "f:", so it must survive untouched —
# otherwise every query beginning with f, a or c would be mangled.
check("a bare letter is not a prefix", split("fire"), (None, "fire"))

# ---- the badge on the right of a row ---------------------------------

badge = sponux_app._badge
Result = files_provider.Result

check("an application is badged App",
      badge(Result(title="x", kind="app")), "App")
check("a file is badged File",
      badge(Result(title="x", kind="file")), "File")
check("a directory is badged Folder, not File",
      badge(Result(title="x", kind="file", is_dir=True)), "Folder")
check("a calculation is badged Calc",
      badge(Result(title="x", kind="calc")), "Calc")
check("an unknown kind gets no badge rather than a wrong one",
      badge(Result(title="x", kind="something-new")), "")
check("a colon later on is not a prefix", split("a:b:c"), ("app", "b:c"))
check("leading space is tolerated", split("  f:x"), ("file", "x"))
check("prefix with nothing after it", split("f:"), ("file", ""))
check("a path is not a prefix", split("/etc/hosts"), (None, "/etc/hosts"))


# ---- what counts as a path query -------------------------------------------

is_path = files_provider.is_path_query

check("absolute path", is_path("/etc"), True)
check("home path", is_path("~/projects"), True)
check("bare tilde", is_path("~"), True)
check("a plain word is not a path", is_path("etc"), False)
check("a relative path is not offered", is_path("./etc"), False)
check("an app name with a slash inside is not a path",
      is_path("libre/writer"), False)
check("empty", is_path(""), False)


# ---- path search against a real tree ---------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    (root / "alpha").mkdir()
    (root / "alphabet").mkdir()
    (root / "beta.txt").write_text("x")
    (root / "betamax.txt").write_text("x")
    (root / ".hidden").write_text("x")
    (root / "alpha" / "inner.txt").write_text("x")

    def titles(query, limit=20):
        return [r.title for r in files_provider.search_path(query, limit=limit)]

    def paths(query, limit=20):
        return [r.path for r in files_provider.search_path(query, limit=limit)]

    # A path that exists is always offered as itself, first, and completions of
    # its last component follow. So a query ending in "/" leads with the
    # directory — which is what makes Enter on "~/" open the home folder.
    inside = root.name

    check("an exact path is offered first",
          titles(f"{root}/beta.txt")[0], "beta.txt")
    check("…and it is the path that was typed",
          paths(f"{root}/beta.txt")[0], str(root / "beta.txt"))
    check("a prefix completes, directories first",
          titles(f"{root}/alpha"), ["alpha", "alphabet"])
    check("a partial name completes",
          titles(f"{root}/beta"), ["beta.txt", "betamax.txt"])
    check("a trailing slash leads with the directory, then lists it",
          titles(f"{root}/"),
          [inside, "alpha", "alphabet", "beta.txt", "betamax.txt"])
    check("the directory itself comes before its contents",
          titles(f"{root}/alpha")[0], "alpha")
    check("hidden entries stay out until a dot is typed",
          [t for t in titles(f"{root}/") if t.startswith(".")], [])
    # "." is a real component naming the directory itself, so it leads the way
    # a trailing slash does — and it is also the dot that unhides.
    check("…and appear once it is",
          titles(f"{root}/."), [inside, ".hidden"])
    check("descending into a directory works",
          titles(f"{root}/alpha/"), ["alpha", "inner.txt"])
    check("a path that does not exist offers nothing",
          titles(f"{root}/nope/deeper"), [])
    check("a nonexistent leaf under a real directory offers nothing",
          titles(f"{root}/zzz"), [])
    check("the exact hit is not repeated as its own completion",
          titles(f"{root}/alpha/inner.txt"), ["inner.txt"])
    check("limit is honoured", len(titles(f"{root}/", limit=2)), 2)

    # Ranking: a typed path has to beat anything the index can produce, or
    # naming a file exactly would still lose to a fuzzy match on something else.
    exact = files_provider.search_path(f"{root}/beta.txt")[0]
    check("a typed path outranks the best possible index hit",
          exact.score > 110 + 25, True)
    check("path results carry the file actions",
          bool(exact.path) and exact.kind == "file", True)
    check("…and are counted when opened",
          exact.usage_key, f"file:{root / 'beta.txt'}")

    # A directory that cannot be read must degrade, not raise.
    locked = root / "locked"
    locked.mkdir()
    (locked / "secret").write_text("x")
    os.chmod(locked, 0o000)
    try:
        check("an unreadable directory yields itself and no contents, "
              "rather than raising", titles(f"{locked}/"), ["locked"])
    finally:
        os.chmod(locked, 0o755)

    # A dangling symlink: is_dir() raises for it, and it must not take the
    # whole completion list down with it.
    (root / "dangling").symlink_to(root / "gone")
    check("a dangling symlink is listed, not fatal",
          "dangling" in titles(f"{root}/d"), True)

print()
if _failures:
    print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
    sys.exit(1)
print("all query checks passed")
