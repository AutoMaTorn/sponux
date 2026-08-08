"""Checks for the web provider.

Two things here are worth pinning down. The first is that nothing is guessed
twice: the URL a row will open is built from the template and the query alone,
so it can be checked exactly rather than described. The second is where the
rows sit — the whole design of this provider is that it never displaces a
result the machine itself could answer with, and that is a property of three
numbers, not of any code path.

Nothing in here opens a browser or touches the network; the actions are built
and left unactivated, which is also all a keystroke ever does.

Run: python3 tests/test_web.py
"""

import pathlib
import re
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import config as spconfig                     # noqa: E402
from sponux import userconfig                             # noqa: E402
from sponux.providers import files as files_provider      # noqa: E402
from sponux.providers import web                          # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


_real_settings = userconfig.settings


def configure(toml_text=""):
    """Run the provider against a config parsed from TOML, as at runtime."""
    userconfig.settings = lambda: tomllib.loads(toml_text)


def rows(query, limit=9):
    return [(r.title, r.subtitle, r.score) for r in web.search(query, limit)]


def titles(query):
    return [r.title for r in web.search(query)]


# ---- building the URL --------------------------------------------------

check("the query is substituted where the template names the spot",
      web.url_for("https://x.test/?q={}", "cats"), "https://x.test/?q=cats")
check("...and appended when it does not",
      web.url_for("https://x.test/?q=", "cats"), "https://x.test/?q=cats")
check("a space is form-encoded, the way a query parameter expects",
      web.url_for("https://x.test/?q={}", "two words"),
      "https://x.test/?q=two+words")
check("and so is everything that means something in a URL",
      web.url_for("https://x.test/?q={}", "a&b=c/d?e#f"),
      "https://x.test/?q=a%26b%3Dc%2Fd%3Fe%23f")
check("non-ASCII survives as percent-escapes",
      web.url_for("https://x.test/?q={}", "кот"),
      "https://x.test/?q=%D0%BA%D0%BE%D1%82")
check("a template can name the spot more than once",
      web.url_for("https://{}.x.test/?q={}", "a"), "https://a.x.test/?q=a")

check("the host is what the row calls an engine",
      web.engine_host("https://duckduckgo.com/?q={}"), "duckduckgo.com")
check("www. is not part of the name",
      web.engine_host("https://www.google.com/search?q={}"), "google.com")


# ---- what counts as a URL ----------------------------------------------

check("a scheme settles it", web.target_url("https://github.com/x"),
      "https://github.com/x")
check("http counts too", web.target_url("http://example.org"),
      "http://example.org")
check("a bare host gets the scheme it did not have",
      web.target_url("github.com"), "https://github.com")
check("with its path kept", web.target_url("github.com/torn/sponux"),
      "https://github.com/torn/sponux")
check("a subdomain is still a host", web.target_url("news.ycombinator.com"),
      "https://news.ycombinator.com")

check("a phrase is not a URL", web.target_url("how to exit vim"), "")
check("...even with a scheme in front of it, since a URL has no spaces",
      web.target_url("https://x.test/a b"), "")
check("a word with no dot is not a host", web.target_url("localhost"), "")
check("a number with a dot is not a host either", web.target_url("2.5"), "")
check("nor is a version", web.target_url("v0.2.3"), "")
check("an address is left alone: a numeric last label is not a domain",
      web.target_url("192.168.1.1"), "")
check("a trailing dot is not a host", web.target_url("github."), "")
check("neither is a leading one", web.target_url(".com"), "")
check("URL syntax that a bare word never has is not a bare word",
      (web.target_url("user@host.test"), web.target_url("host.test:8080")),
      ("", ""))
check("an internationalised domain is left to the search row",
      web.target_url("сайт.рф"), "")
check("empty is not a URL", web.target_url("   "), "")


# ---- the rows ----------------------------------------------------------

configure()

check("a plain query offers one row, and says where it would go",
      rows("hello world"),
      [("Search duckduckgo.com for hello world",
        "https://duckduckgo.com/?q=hello+world", web.SEARCH_SCORE)])

check("a host offers to be opened as well as searched for",
      titles("github.com"),
      ["https://github.com", "Search duckduckgo.com for github.com"])
check("...and the open row is the URL, so it can be read before Enter",
      rows("github.com")[0][1], "Open in your browser")

check("a typed URL outranks anything fuzzy matching can produce",
      rows("https://github.com/x")[0][2], web.URL_SCORE)
check("...and is offered as itself, not as a search",
      titles("https://github.com/x")[0], "https://github.com/x")

# A path is the file provider's business: search_path() is answering it with
# real entries, and a web row underneath is noise.
check("a typed path is not a web search", rows("/etc/hosts"), [])
check("nor is one under home", rows("~/projects"), [])
check("an empty query offers nothing", rows("   "), [])


# ---- where the rows sit ------------------------------------------------
#
# The design is that a web row never pushes out something local. That is not a
# code path anywhere — it falls out of these numbers, so they are what gets
# checked.

WEAKEST_APP = 40.0 * 0.6      # a subsequence match on a keyword
WEAKEST_FILE = 40.0 * files_provider.FILE_WEIGHT
BEST_POSSIBLE = 110.0 + spconfig.FRECENCY_WEIGHT   # fuzzy_score + all frecency

check("the search row loses to the weakest real match there is",
      web.SEARCH_SCORE < min(WEAKEST_APP, WEAKEST_FILE), True)
check("so does the open row, which is why notes.md stays a file",
      web.OPEN_SCORE < min(WEAKEST_APP, WEAKEST_FILE), True)
check("...but it is offered above the search for the same text",
      web.OPEN_SCORE > web.SEARCH_SCORE, True)
check("a named engine does not have to wait for a gap in the results",
      web.ENGINE_SCORE > BEST_POSSIBLE, True)
check("a typed URL sits in the typed-path band, and below an exact path",
      files_provider.PATH_COMPLETION_SCORE < web.URL_SCORE
      < files_provider.PATH_EXACT_SCORE, True)

# The one case the ranking is there to settle: a real file with a real TLD for
# an extension. The file provider's own score for it has to win.
check("notes.md is a file first and a domain second",
      web.OPEN_SCORE < 60.0 * files_provider.FILE_WEIGHT, True)


# ---- engines -----------------------------------------------------------

configure("""
[web]
search = "https://x.test/?q={}"

[web.engines]
g = "https://www.google.com/search?q={}"
w = "https://en.wikipedia.org/w/index.php?search={}"
""")

check("a prefix picks its engine and drops out of the query",
      rows("g:cats"),
      [("Search google.com for cats",
        "https://www.google.com/search?q=cats", web.ENGINE_SCORE)])
check("the prefix is case-insensitive, like the config key",
      titles("G:cats"), ["Search google.com for cats"])
check("a prefix with nothing after it searches for nothing",
      rows("g:"), [])
check("an unconfigured prefix is just part of the query",
      titles("x:cats"), ["Search x.test for x:cats"])
check("a colon inside a phrase is not a prefix",
      titles("error: not found"), ["Search x.test for error: not found"])
check("a URL is not read as an engine prefix",
      titles("https://x.test/a"),
      ["https://x.test/a", "Search x.test for https://x.test/a"])
check("the default engine is the configured one",
      titles("cats"), ["Search x.test for cats"])


# ---- what the config may say -------------------------------------------

configure("[web]\nenabled = false\n")
check("the row can be turned off entirely", rows("cats"), [])

configure("[web]\nenabled = false\n")
check("...including for a URL", rows("https://x.test"), [])

configure("[web]\nenabled = 'yes'\n")
check("a non-boolean enabled falls back to the default",
      userconfig.web_settings().enabled, spconfig.WEB_SEARCH)

configure("[web]\nsearch = 42\n")
check("a template that is not a string falls back to the default",
      userconfig.web_settings().template, spconfig.WEB_ENGINE)

# A template without a scheme would be handed to launch_default_for_uri(),
# which would open it with whatever claims that scheme — or nothing.
configure('[web]\nsearch = "duckduckgo.com/?q={}"\n')
check("neither is a template that would not open in a browser",
      userconfig.web_settings().template, spconfig.WEB_ENGINE)
configure('[web]\nsearch = "ftp://x.test/?q={}"\n')
check("...whatever scheme it does carry",
      userconfig.web_settings().template, spconfig.WEB_ENGINE)

configure('[web]\n[web.engines]\ngood = "https://x.test/?q={}"\nbad = 3\n')
check("a broken engine is dropped and the rest survive",
      userconfig.web_settings().engines, {"good": "https://x.test/?q={}"})

configure('[web.engines]\n"a b" = "https://x.test/?q={}"\n')
check("an engine name that could never be typed as a prefix is dropped",
      userconfig.web_settings().engines, {})

configure("web = 3\n")
check("a [web] that is not a section leaves the defaults standing",
      (userconfig.web_settings().enabled, userconfig.web_settings().template),
      (spconfig.WEB_SEARCH, spconfig.WEB_ENGINE))

configure("")
check("no [web] at all means the defaults",
      userconfig.web_settings(),
      userconfig.WebSettings(spconfig.WEB_SEARCH, spconfig.WEB_ENGINE, {}))


# ---- the starter config documents something that works -----------------
#
# Its [web] examples are commented out, so nothing else would ever notice if
# one of them stopped parsing. Uncommenting them here is the only way to find
# out before a user does.

_block = userconfig.STARTER_CONFIG.split("\n[web]\n")[1].split("\n[window]\n")[0]
_assignment = re.compile(r"^# ([\w.-]+ = .+)$")
_live = "[web]\n" + "\n".join(
    _assignment.sub(r"\1", line) for line in _block.splitlines())

configure(_live)
_starter = userconfig.web_settings()
check("the starter file's [web] parses once uncommented",
      isinstance(tomllib.loads(_live), dict), True)
check("...its engine templates are all usable",
      sorted(_starter.engines), ["aw", "g", "w"])
check("...and its search line is the default spelled out",
      _starter.template, spconfig.WEB_ENGINE)
check("...and none of its examples collide with a kind prefix",
      sorted(set(_starter.engines) & {"f", "file", "a", "app", "c", "web"}), [])

userconfig.settings = _real_settings

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
print("all web checks passed")
