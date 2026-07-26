"""Checks for multi-monitor placement, with monitors faked.

The X11 side of placement.py is verified by opening the real window (see
tools/visual.py and the geometry checks in HANDOFF.md), but that only ever
exercises the monitors actually attached to the machine. What can go wrong
with several monitors is the *choice* between them and the arithmetic on a
monitor whose origin is not 0,0 — both are pure functions of GDK's monitor
geometry, so they are checked here against stubs instead of hardware.

Run: python3 tests/test_placement.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux import placement  # noqa: E402


class Geo:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class Monitor:
    """The three GdkMonitor methods placement.py uses."""

    def __init__(self, name, x, y, w, h, scale=1):
        self.name = name
        self._geo = Geo(x, y, w, h)
        self._scale = scale

    def get_geometry(self):
        return self._geo

    def get_scale_factor(self):
        return self._scale

    def __repr__(self):
        return self.name


class Window:
    def __init__(self, monitors):
        self._monitors = monitors

    def get_display(self):
        return self

    def get_monitors(self):
        return self._monitors


_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got}, want {want}")
    if not ok:
        _failures.append(label)


def pick(monitors, pointer):
    """_target_monitor() with the X pointer faked out."""
    real = placement._pointer_position
    placement._pointer_position = lambda: pointer
    try:
        return placement._target_monitor(Window(monitors))
    finally:
        placement._pointer_position = real


# ---- which monitor ----------------------------------------------------

left = Monitor("left", 0, 0, 1920, 1080)
right = Monitor("right", 1920, 0, 2560, 1440)

check("pointer on the left monitor", pick([left, right], (100, 100)), left)
check("pointer on the right monitor", pick([left, right], (2500, 700)), right)
check("pointer on the seam belongs right", pick([left, right], (1920, 5)), right)
check("last pixel of the left monitor", pick([left, right], (1919, 5)), left)
check("no pointer falls back to the first",
      pick([left, right], None), left)

# A laptop under an external screen: nothing owns the pointer's column below
# the external monitor, and the y gap must not send the card to the wrong one.
top = Monitor("top", 0, 0, 2560, 1440)
bottom = Monitor("bottom", 300, 1440, 1920, 1080)
check("pointer in a gap picks the nearest monitor",
      pick([top, bottom], (100, 2000)), bottom)
check("nearest really is nearest: 161px below top beats 200px left of bottom",
      pick([top, bottom], (100, 1600)), top)
check("pointer above the gap stays on top",
      pick([top, bottom], (100, 1400)), top)

# Monitors need not be listed left to right, and x can be negative.
far_left = Monitor("far_left", -1920, 0, 1920, 1080)
check("negative origins work", pick([left, far_left], (-500, 500)), far_left)

# GDK reports geometry in application pixels; XQueryPointer answers in device
# pixels, so a HiDPI monitor covers twice the device range it declares.
hidpi = Monitor("hidpi", 0, 0, 1920, 1080, scale=2)
after = Monitor("after", 1920, 0, 1920, 1080, scale=2)
check("hidpi: device 3000 is still the first monitor",
      pick([hidpi, after], (3000, 100)), hidpi)
check("hidpi: device 4000 is the second",
      pick([hidpi, after], (4000, 100)), after)

check("a single monitor is always the answer",
      pick([left], (99999, 99999)), left)

# ---- where on that monitor -------------------------------------------

CARD = 640

check("centred on a monitor at the origin",
      placement.card_position(Geo(0, 0, 2160, 1440), CARD, 56), (760, 316))
check("centred on a monitor with an offset origin",
      placement.card_position(Geo(1920, 0, 2560, 1440), CARD, 56), (2880, 316))
check("offset in both axes",
      placement.card_position(Geo(300, 1440, 1920, 1080), CARD, 56),
      (940, 1677))
check("negative origin",
      placement.card_position(Geo(-1920, 0, 1920, 1080), CARD, 56),
      (-1280, 237))
check("a monitor narrower than the card clamps to its left edge",
      placement.card_position(Geo(800, 0, 500, 1440), CARD, 56), (800, 316))
check("a tall card on a short monitor is pushed up to fit",
      placement.card_position(Geo(0, 0, 1920, 800), CARD, 700), (640, 100))
check("a card taller than the monitor sticks to the top edge",
      placement.card_position(Geo(0, 720, 1920, 400), CARD, 600), (640, 720))
check("height 0 (not yet allocated) skips the vertical clamp",
      placement.card_position(Geo(0, 0, 1920, 800), CARD, 0), (640, 176))


# ---- [window] position ------------------------------------------------

_FULL = Geo(0, 0, 2160, 1440)

check("position defaults to top, a fraction of the way down",
      placement.card_position(_FULL, CARD, 400), (760, 316))
check("…which is what asking for it explicitly does",
      placement.card_position(_FULL, CARD, 400, position="top"), (760, 316))
check("center puts the card's middle on the monitor's middle",
      placement.card_position(_FULL, CARD, 400, position="center"), (760, 520))
check("top_fraction moves the top placement and nothing else",
      placement.card_position(_FULL, CARD, 400, position="top",
                              top_fraction=0.4), (760, 576))
check("top_fraction does not affect centring",
      placement.card_position(_FULL, CARD, 400, position="center",
                              top_fraction=0.4), (760, 520))
# Height is 0 on the first pass, before the window has been allocated. Centring
# on a height of 0 would put the card halfway down and then jump it upwards once
# the real height arrived, so it falls back to the top placement for that pass.
check("centring with no height yet falls back to the top placement",
      placement.card_position(_FULL, CARD, 0, position="center"), (760, 316))
check("a centred card taller than the monitor still clamps to the top edge",
      placement.card_position(Geo(0, 0, 1920, 300), CARD, 400,
                              position="center"), (640, 0))

print()
if _failures:
    print(f"{len(_failures)} failed: {', '.join(_failures)}")
    sys.exit(1)
print("all placement checks passed")
