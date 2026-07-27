"""Checks for the runtime log: what lands in it, and what it refuses to do.

The log exists because a launcher started from a hotkey has no terminal, so
the messages have to survive somewhere. That makes three things worth pinning
down, and all three are ways this could quietly become a liability instead:

  - it stays off until the application turns it on, so `--check` and the other
    command-line paths — which read the same config and print the same
    complaints — leave nothing behind;
  - it is bounded: it rotates one generation, and a message repeating inside
    the window is counted rather than written, so an indexer failing in a loop
    cannot fill a disk;
  - it never raises. This is where failures are reported; a failure of its own
    has nowhere to go, and must not take the launcher with it.

Run: python3 tests/test_report.py
"""

import contextlib
import io
import os
import pathlib
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="sponux-report-")
os.environ["XDG_STATE_HOME"] = f"{_TMP}/state"
os.environ["XDG_CACHE_HOME"] = f"{_TMP}/cache"
os.environ["XDG_CONFIG_HOME"] = f"{_TMP}/config"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import config, report  # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


def say(message, notify=False):
    """Report one thing, and hand back what went to stderr."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        report.problem(message, notify=notify)
    return err.getvalue()


def reset():
    """A fresh log and an empty suppression table between checks."""
    report._recent.clear()
    for path in (config.LOG_FILE, config.LOG_FILE.with_name(
            config.LOG_FILE.name + ".1")):
        if path.exists():
            path.unlink()


# ---- off until the application says otherwise -----------------------------

err = say("nobody is logging yet")
check("stderr carries the line even with the log off",
      "sponux: nobody is logging yet" in err, True)
check("nothing is written before start_logging()", config.LOG_FILE.exists(),
      False)
check("log_lines() on a missing file is empty", report.log_lines(), [])

report.start_logging()
reset()

# ---- the ordinary case ----------------------------------------------------

err = say("cannot run 'edit': no such file")
lines = report.log_lines()
check("one message writes one line", len(lines), 1)
check("stderr still gets it too", "sponux: cannot run 'edit'" in err, True)
check("the line is timestamped",
      lines[0][:2].isdigit() and lines[0][4] == "-" and lines[0][13] == ":",
      True)
check("the line keeps the sponux: prefix the terminal shows",
      "sponux: cannot run 'edit': no such file" in lines[0], True)

with contextlib.redirect_stderr(io.StringIO()):
    report.note("wrote [open] py to config.toml")
check("note() is recorded as well as problem()",
      "wrote [open] py" in report.log_lines()[-1], True)
check("both are in the log now", len(report.log_lines()), 2)

# ---- a message repeating in a loop cannot fill the disk --------------------

reset()
for _ in range(50):
    say("index update failed: database is locked")
check("50 identical messages in a row write one line",
      len(report.log_lines()), 1)

say("indexing failed: permission denied")
check("a different message is not suppressed by it",
      len(report.log_lines()), 2)

# Age the entry past the window instead of waiting a minute for it.
written, dropped = report._recent["index update failed: database is locked"]
report._recent["index update failed: database is locked"] = (
    written - report._REPEAT_WINDOW - 1, dropped)
say("index update failed: database is locked")
last = report.log_lines()[-1]
check("what was dropped is counted into the next line",
      "and 49 more like it" in last, True)

check("the suppression table does not grow without bound",
      all(len(report._recent) <= report._MAX_TRACKED
          for _ in [say(f"distinct problem {i}")
                    for i in range(report._MAX_TRACKED + 20)]),
      True)

# ---- the file itself is bounded -------------------------------------------

reset()
rotated = config.LOG_FILE.with_name(config.LOG_FILE.name + ".1")
config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
config.LOG_FILE.write_text("x" * (config.MAX_LOG_BYTES + 1))
say("the line that tips it over")
check("the full log is rotated one generation", rotated.exists(), True)
check("and the new one starts from the message that rotated it",
      report.log_lines()[-1].endswith("the line that tips it over"), True)
check("so the live log is small again",
      config.LOG_FILE.stat().st_size < config.MAX_LOG_BYTES, True)

# ---- nothing here may raise -----------------------------------------------

reset()
blocked = pathlib.Path(_TMP) / "a-file-not-a-directory"
blocked.write_text("")
original_dir, original_log = config.STATE_DIR, config.LOG_FILE
config.STATE_DIR = blocked / "state"
config.LOG_FILE = blocked / "state" / "sponux.log"
err = say("something failed while the log itself cannot be written")
check("an unwritable log says so on stderr", "cannot write" in err, True)
check("and turns itself off rather than fail on every later message",
      report._logging, False)
config.STATE_DIR, config.LOG_FILE = original_dir, original_log
report.start_logging()

reset()
err = say("cannot run 'edit': no such file", notify=True)
check("notify=True outside a running application is a no-op, not a crash",
      len(report.log_lines()), 1)

# ---- the application is what turns it on ----------------------------------

from sponux import app  # noqa: E402
check("app.main() starts the log",
      "start_logging" in app.main.__code__.co_names, True)

# ---- and the command line does not ----------------------------------------

from sponux import __main__ as cli  # noqa: E402
check("--check reads the log back", "_check_log" in cli._check.__code__.co_names,
      True)

shutil.rmtree(_TMP, ignore_errors=True)

print()
if _failures:
    print(f"{len(_failures)} failure(s): {', '.join(_failures)}")
    sys.exit(1)
print("all checks passed")
