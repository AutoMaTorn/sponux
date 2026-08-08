"""Web provider — the query, handed to the browser.

Two rows, both built entirely on this machine:

* **Search** — "Search duckduckgo.com for …", which opens the engine's results
  page. Scored so low that it only surfaces once the list has room for it, so
  "nothing was found here" turns into "look for it out there" without the
  window needing a single special case for an empty result list.
* **Open** — offered when the query names a URL rather than describing one.

**Nothing goes to the network before Enter.** Live suggestions — what a
browser's address bar shows as you type — would mean an HTTP request per
keystroke: asynchronous I/O on the GTK main thread, timeouts, an offline path,
and, more to the point, every letter typed leaving the machine before anyone
decided to search. The URL is built locally and the request happens at the
moment the row is activated, which is also the moment the user asked for it.

The launching itself is the same call files.py opens files with,
``launch_default_for_uri()``, so the browser is whatever the desktop says it
is and sponux has no opinion to keep in step.
"""

import urllib.parse

from gi.repository import Gio, GLib

from .base import Result
from .files import is_path_query
from .. import config, report, usage, userconfig

# Where these rows land among everything else. fuzzy_score() runs 0-110 and the
# weakest match a provider will return is a subsequence one, 40 before its
# weighting — so anything down here is below every real result and only appears
# while there is room left in the list.
SEARCH_SCORE = 5.0
# Just above it: when the query does name a host, offering to open it is the
# more specific reading of the same text.
OPEN_SCORE = 8.0
# Naming an engine ("g:cats") is a statement, not a guess, so it does not have
# to wait for a gap in the results. Below a typed path (300-400), above
# anything fuzzy matching can reach.
ENGINE_SCORE = 200.0
# A query with a scheme in it is as unambiguous as a typed path, and is scored
# in the same band for the same reason — nothing here was inferred.
URL_SCORE = 350.0

_SCHEMES = ("http://", "https://")


def url_for(template: str, query: str) -> str:
    """`query` encoded into an engine template.

    Form encoding (spaces as '+'), because that is what a `?q=` parameter
    expects and every engine template is one. A template that puts `{}` inside
    a path segment instead will get a '+' where it wanted a space; that is the
    price of one rule rather than two, and no engine worth configuring needs
    the other one.
    """
    encoded = urllib.parse.quote_plus(query)
    if userconfig.QUERY_PLACEHOLDER in template:
        return template.replace(userconfig.QUERY_PLACEHOLDER, encoded)
    return template + encoded


def target_url(text: str) -> str:
    """The URL `text` names, with a scheme added if it lacked one. "" if none.

    Two cases, and they are deliberately not equally certain:

    * **A scheme was typed** — `https://github.com/x`. Nothing else it could
      mean, and nothing else can produce that string.
    * **A bare host** — `github.com`. This one competes with the file provider,
      because `notes.md` is also a word with a dot in it and `.md` is also a
      real top-level domain. There is no test that separates them — a domain
      list would go stale and would still call `notes.md` a domain — so the
      ambiguity is settled by ranking instead: the row is offered at
      OPEN_SCORE, below every file the index can match, and it is simply the
      last row on a list that had room for it.

    Non-ASCII hosts are left to the search row: a URI is ASCII, and encoding an
    internationalised domain properly is punycode, not percent-escapes.
    """
    q = text.strip()
    if not q or any(ch.isspace() for ch in q):
        return ""
    if q.lower().startswith(_SCHEMES):
        return q
    return f"https://{q}" if _is_bare_host(q) else ""


def _is_bare_host(q: str) -> bool:
    host = q.partition("/")[0].partition("?")[0].partition("#")[0]
    # ':' would be a port or a scheme fragment, '@' a userinfo part: both are
    # URL syntax that a bare word never has, so they mean this is not the plain
    # "github.com" case this function is for.
    if "." not in host or ":" in host or "@" in host or not host.isascii():
        return False
    labels = host.split(".")
    if not all(label and all(ch.isalnum() or ch == "-" for ch in label)
               for label in labels):
        return False
    tld = labels[-1]
    return len(tld) >= 2 and tld.isalpha()


def engine_host(template: str) -> str:
    """What to call an engine in the row: the host of its URL, without www."""
    host = urllib.parse.urlsplit(template).hostname or ""
    return host[4:] if host.startswith("www.") else host or "the web"


def _pick_engine(q: str, conf):
    """(named, template, query) for a query that may carry an engine prefix.

    `g:cats` means the engine configured as `g`; anything else is the default
    engine and the whole query. The kind prefixes (`f:`, `a:`, …) never reach
    here — app.py has already taken them off — so the only names that can
    collide with one are names that could never be typed anyway, which is what
    `--check` reports.
    """
    if q.lower().startswith(_SCHEMES):
        return ("", conf.template, q)
    head, sep, rest = q.partition(":")
    if sep and head and not any(ch.isspace() for ch in head):
        template = conf.engines.get(head.lower())
        if template is not None:
            return (head.lower(), template, rest.strip())
    return ("", conf.template, q)


def open_url(url: str):
    """Hand a URL to whatever the desktop opens that scheme with.

    The browser is credited the way an application that opened a file is —
    indirectly, since nobody named it, the desktop's default did. See
    usage.record_opener().
    """
    try:
        Gio.AppInfo.launch_default_for_uri(url, None)
    except GLib.Error as exc:
        # Enter was pressed and no window appeared: exactly what a notification
        # is for. The usual cause is a session with no browser registered.
        report.problem(f"cannot open {url}: {exc.message}", notify=True)
        return
    browser = default_browser(urllib.parse.urlsplit(url).scheme or "https")
    if browser is not None:
        usage.record_opener(browser)


def default_browser(scheme: str = "https"):
    """What would open a link, as a Gio.AppInfo. None if nothing claims it.

    Deliberately not consulted while typing: it cost 0.22 ms per call measured
    2026-08-08, which is real money against a 25 ms keystroke, and the row does
    not need it — Enter resolves the handler itself, so nothing can go to the
    wrong browser. `--check` asks, once, where the answer is worth having.
    """
    return Gio.AppInfo.get_default_for_uri_scheme(scheme)


def search(query: str, limit: int = config.MAX_RESULTS):
    q = query.strip()
    # A path is being typed, not a search: files.search_path() is answering it
    # with real entries, and "search the web for /etc/ho" is noise underneath.
    if not q or is_path_query(q):
        return []
    conf = userconfig.web_settings()
    if not conf.enabled:
        return []

    results = []
    url = target_url(q)
    if url:
        explicit = q.lower().startswith(_SCHEMES)
        results.append(Result(
            title=url,
            # The URL is already the title here, so the subtitle spends itself
            # on what Enter does; on the search row below it is the other way
            # round, because there the URL is the part nobody can see.
            subtitle="Open in your browser",
            icon="web-browser-symbolic",
            score=URL_SCORE if explicit else OPEN_SCORE,
            action=lambda u=url: open_url(u),
            kind="web",
        ))

    named, template, term = _pick_engine(q, conf)
    if term:
        target = url_for(template, term)
        results.append(Result(
            title=f"Search {engine_host(template)} for {term}",
            subtitle=target,
            icon="system-search-symbolic",
            score=ENGINE_SCORE if named else SEARCH_SCORE,
            action=lambda u=target: open_url(u),
            kind="web",
        ))
    # No usage_key on either row, for the reason the calculator has none: what
    # would be remembered is one query typed once, and the usage table ranks
    # things that can come back.
    return results[:limit]
