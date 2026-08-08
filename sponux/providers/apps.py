"""Application provider — finds and launches desktop applications.

Uses Gio.AppInfo, so parsing of .desktop files, Exec field codes, icons
and correct launching (including startup notification) come for free and
stay consistent with the rest of the desktop.
"""

from gi.repository import Gio, GLib

from .base import Result, fuzzy_score
from .. import config, usage

def _launch(app):
    try:
        app.launch(None, None)
    except GLib.Error:
        pass


def search(query: str, limit: int = config.MAX_RESULTS):
    q = query.strip()
    if not q:
        return []

    # Not cached here on purpose. GIO already keeps this list and invalidates
    # it when .desktop files change (7 ms cold, 1.8 ms after), so a cache of
    # our own would only add a way to go stale — and the daemon is resident for
    # days, so "stale" means a newly installed application is never found.
    results = []
    for app in (a for a in Gio.AppInfo.get_all() if a.should_show()):
        name = app.get_display_name() or app.get_name() or ""
        score = fuzzy_score(q, name)

        # Also match keywords / generic name, but weight them lower.
        keywords = getattr(app, "get_keywords", lambda: [])() or []
        generic = getattr(app, "get_generic_name", lambda: "")() or ""
        for extra in (*keywords, generic):
            if extra:
                score = max(score, fuzzy_score(q, extra) * 0.6)

        if score <= 0:
            continue

        key = usage.key_for_appinfo(app)
        results.append(Result(
            title=name,
            subtitle=generic or app.get_description() or "Application",
            icon=app.get_icon() or "application-x-executable",
            score=score + usage.bonus(key),
            action=lambda a=app: _launch(a),
            kind="app",
            usage_key=key,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
