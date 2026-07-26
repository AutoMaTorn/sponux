"""Measure launcher open latency: time from spawning the command until the
launcher's window is actually mapped on screen.

Uses X11 SubstructureNotify events on the root window rather than polling, so
the measurement loop itself doesn't compete for CPU with the thing timed.
"""
import ctypes
import ctypes.util
import pathlib
import statistics
import subprocess
import sys
import time

SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
MAP_NOTIFY = 19

x = ctypes.CDLL(ctypes.util.find_library("X11"))
xtst = ctypes.CDLL("libXtst.so.6")


class XClassHint(ctypes.Structure):
    _fields_ = [("res_name", ctypes.c_char_p), ("res_class", ctypes.c_char_p)]


x.XOpenDisplay.restype = ctypes.c_void_p
x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong
x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
x.XPending.restype = ctypes.c_int
x.XPending.argtypes = [ctypes.c_void_p]
x.XGetClassHint.restype = ctypes.c_int
x.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                            ctypes.POINTER(XClassHint)]
x.XStringToKeysym.restype = ctypes.c_ulong
x.XStringToKeysym.argtypes = [ctypes.c_char_p]
x.XKeysymToKeycode.restype = ctypes.c_ubyte
x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x.XFlush.argtypes = [ctypes.c_void_p]
xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_ulong]

dpy = x.XOpenDisplay(None)
root = x.XDefaultRootWindow(dpy)
x.XSelectInput(dpy, root, SUBSTRUCTURE_NOTIFY_MASK)


def window_class(win):
    hint = XClassHint()
    if x.XGetClassHint(dpy, win, ctypes.byref(hint)):
        return (hint.res_class or b"").decode(errors="replace").lower()
    return ""


def drain():
    buf = (ctypes.c_long * 24)()
    while x.XPending(dpy):
        x.XNextEvent(dpy, ctypes.byref(buf))


def wait_for_map(want_class, timeout=6.0):
    """Block until a window whose WM_CLASS matches is mapped. Returns elapsed
    seconds measured by the caller's clock, or None on timeout."""
    buf = (ctypes.c_long * 24)()
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not x.XPending(dpy):
            time.sleep(0.0005)
            continue
        x.XNextEvent(dpy, ctypes.byref(buf))
        if buf[0] != MAP_NOTIFY:
            continue
        win = ctypes.cast(ctypes.byref(buf, 40),
                          ctypes.POINTER(ctypes.c_ulong)).contents.value
        if want_class in window_class(win):
            return time.perf_counter()
    return None


def tap(name):
    kc = x.XKeysymToKeycode(dpy, x.XStringToKeysym(name.encode()))
    xtst.XTestFakeKeyEvent(dpy, kc, 1, 0)
    xtst.XTestFakeKeyEvent(dpy, kc, 0, 0)
    x.XFlush(dpy)


def bench(label, argv, want_class, runs=10, settle=1.0):
    samples = []
    for _ in range(runs):
        drain()
        t0 = time.perf_counter()
        subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        t1 = wait_for_map(want_class)
        if t1 is not None:
            samples.append((t1 - t0) * 1000)
        time.sleep(0.25)
        tap("Escape")          # dismiss before the next round
        time.sleep(settle)
    if not samples:
        print(f"{label:34} NO WINDOW SEEN")
        return
    print(f"{label:34} median {statistics.median(samples):7.1f} ms   "
          f"min {min(samples):7.1f}   max {max(samples):7.1f}   n={len(samples)}")


if __name__ == "__main__":
    which = sys.argv[1]
    if which == "rofi":
        bench("rofi (cold start, your config)",
              ["rofi", "-show", "drun", "-no-default-config",
               "-config", os.path.expanduser("~/.config/rofi/config.rasi")], "rofi")
    elif which == "sponux":
        bench("sponux (daemon warm, via bin/sponux)",
              [str(pathlib.Path(__file__).resolve().parent.parent / "bin" / "sponux")], "sponux")
    elif which == "installed":
        # The same wrapper, but the one a package put on PATH — so a packaged
        # install can be measured as the user actually invokes it.
        import shutil
        found = shutil.which("sponux")
        if found is None:
            raise SystemExit("no 'sponux' on PATH; nothing installed to measure")
        bench(f"sponux (daemon warm, via {found})", [found], "sponux")
    elif which == "gdbus":
        bench("sponux (daemon warm, via gdbus)",
              ["gdbus", "call", "--session", "--dest", "io.github.sponux",
               "--object-path", "/io/github/sponux",
               "--method", "org.freedesktop.Application.Activate", "{}"],
              "sponux")
