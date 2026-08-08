"""Shared result type and fuzzy scoring used by all providers."""

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Result:
    title: str
    subtitle: str = ""
    icon: object = None            # Gio.Icon, a themed icon name (str), or None
    score: float = 0.0
    action: Optional[Callable[[], None]] = None
    kind: str = ""                 # "app" | "file" | "calc"
    # Set by the file provider. The window uses it for the alternate actions
    # (reveal, copy, open with), which are the same for every file result and
    # so are not worth a closure each.
    path: str = ""
    is_dir: bool = False
    # Identity under which this result's use is counted; see usage.py. Empty
    # for things there is no point remembering, such as a calculation.
    usage_key: str = ""


def copy_text(text: str):
    """Put text on the clipboard. Imported lazily: no display, no clipboard."""
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if display is None:
        return
    clipboard = display.get_clipboard()
    clipboard.set(text)
    # On X11 the clipboard belongs to the process that set it, so left like
    # this the text dies with the daemon: copy a path, Ctrl+Q, and there is
    # nothing to paste. store_async() asks the clipboard manager to keep a
    # copy; with none running it is simply a no-op. The synchronous store()
    # is not exposed to PyGObject, so the async call is given a bounded spin
    # of the main context — without it "copy, then immediately Ctrl+Q" would
    # quit before the manager was even asked.
    from gi.repository import GLib
    import time

    done = []
    clipboard.store_async(GLib.PRIORITY_DEFAULT, None,
                          lambda cb, res: done.append(True))
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 0.2
    while not done and time.monotonic() < deadline:
        context.iteration(True)


def fuzzy_score(query: str, text: str) -> float:
    """Return a match score in roughly [0, 100], or 0 for no match.

    Ranking intent (best to worst): exact > prefix > word-start >
    substring > subsequence. Shorter targets win ties so that a query
    like "fi" ranks "Files" above "Firefox Developer Edition".
    """
    if not query:
        return 0.0
    q = query.lower()
    t = text.lower()

    if t == q:
        base = 100.0
    elif t.startswith(q):
        base = 90.0
    elif _word_start_match(q, t):
        base = 75.0
    elif q in t:
        base = 60.0
    elif _is_subsequence(q, t):
        base = 40.0
    else:
        return 0.0

    # Prefer shorter targets (small, bounded bonus).
    length_bonus = max(0.0, 10.0 - (len(t) - len(q)) * 0.1)
    return base + length_bonus


def _word_start_match(q: str, t: str) -> bool:
    for sep in (" ", "-", "_", ".", "/"):
        t = t.replace(sep, " ")
    return any(word.startswith(q) for word in t.split())


def _is_subsequence(q: str, t: str) -> bool:
    it = iter(t)
    return all(ch in it for ch in q)
