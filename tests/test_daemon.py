"""Checks for the daemon section of --check: which installation answers.

A packaged sponux and a source checkout share `io.github.sponux`, so the
daemon that reached the session bus first serves every keypress and the other
one is never consulted. Silently — which is precisely backwards, because the
reason anyone runs --check is that a change of theirs appears to have done
nothing.

The awkward half is working out where a *running* process imports its package
from, so `_import_root()` is pure and gets the fake /proc facts here. The rest
is what the four possible answers look like: a wrong or vague one costs exactly
the debugging session this is meant to save.

Run: python3 tests/test_daemon.py
"""

import contextlib
import io
import os
import pathlib
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="sponux-daemon-")
os.environ["XDG_STATE_HOME"] = f"{_TMP}/state"
os.environ["XDG_CACHE_HOME"] = f"{_TMP}/cache"
os.environ["XDG_CONFIG_HOME"] = f"{_TMP}/config"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import __main__ as cli  # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


def fake_tree(name, version="9.9.9"):
    """A directory that looks like somewhere sponux could be imported from."""
    root = pathlib.Path(_TMP) / name
    (root / "sponux").mkdir(parents=True, exist_ok=True)
    (root / "sponux" / "__main__.py").write_text("")
    (root / "sponux" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return str(root)


def section(facts):
    """The daemon section of --check, with the /proc lookup replaced."""
    original = cli._daemon_facts
    cli._daemon_facts = lambda: facts
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            cli._check_daemon()
    finally:
        cli._daemon_facts = original
    return out.getvalue()


INSTALLED = fake_tree("usr-share-sponux", "0.1.0")
CHECKOUT = fake_tree("checkout", "0.2.0")
HERE = str(pathlib.Path(cli.__file__).resolve().parent.parent)

# ---- where a running daemon imports from ----------------------------------

check("PYTHONPATH is the answer, as the wrapper sets it",
      cli._import_root({"PYTHONPATH": INSTALLED}, "/home/user",
                       ["python3", "-P", "-m", "sponux"]),
      INSTALLED)

check("entries that hold no package are skipped",
      cli._import_root({"PYTHONPATH": os.pathsep.join(["/nowhere", CHECKOUT])},
                       "/home/user", ["python3", "-P", "-m", "sponux"]),
      CHECKOUT)

# Started by hand as `python3 -m sponux`: the working directory is on sys.path.
check("without -P, the working directory counts",
      cli._import_root({}, CHECKOUT, ["python3", "-m", "sponux"]),
      CHECKOUT)

# The wrapper passes -P precisely so that it does not.
check("with -P, it does not",
      cli._import_root({}, CHECKOUT, ["python3", "-P", "-m", "sponux"]),
      None)

check("a path named outright is read off the command line",
      cli._import_root({}, "/home/user",
                       ["python3", f"{CHECKOUT}/sponux/__main__.py"]),
      CHECKOUT)

check("and nothing identifiable stays None",
      cli._import_root({}, "/home/user", ["python3", "-P", "-m", "sponux"]),
      None)

check("the version is read from the tree that is actually running",
      cli._version_at(INSTALLED), "0.1.0")
check("a tree with no package has no version",
      cli._version_at(f"{_TMP}/not-a-tree"), None)

# ---- the four things it can say -------------------------------------------

text = section((None, None, "NameHasNoOwner"))
check("nothing running says so", "none running" in text, True)
check("...and is not a warning", "WARN" not in text, True)

text = section((4242, HERE, ""))
check("the daemon being this tree is an ok line",
      "  ok      pid 4242" in text, True)
check("...and says the keypress runs what you are looking at",
      "what you are looking at" in text, True)

text = section((4242, INSTALLED, ""))
check("another installation answering is a WARN", "WARN" in text, True)
check("...naming the prefix that wins", INSTALLED in text, True)
check("...and its version, which is the tell when it is stale",
      "(0.1.0)" in text, True)
check("...and how to stop it", "kill 4242" in text, True)

text = section((4242, None, "Permission denied"))
check("an unreadable /proc is a note, not a guess",
      "note" in text and "Permission denied" in text, True)
check("...and still names the pid", "4242" in text, True)

shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failure(s): {', '.join(_failures)}")
    sys.exit(1)
print("all checks passed")
