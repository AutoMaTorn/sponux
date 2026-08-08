"""Direct checks for the safe calculator (sponux/providers/calc.py).

The calculator runs inside the GTK debounce callback, so its failure mode is
not a wrong answer but a hung daemon: an unbounded big-int (9**100000,
2**10**9, factorial(10**8)) allocates hundreds of megabytes on the main
thread, and str() on a huge int raises outside any handler. These checks pin
both the math itself and the size guards that keep the daemon responsive.

Run: python3 tests/test_calc.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sponux.providers import calc                        # noqa: E402

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        _failures.append(label)


def titles(query):
    return [r.title for r in calc.search(query)]


# ---- ordinary arithmetic still works ----------------------------------------

check("plain addition", calc.evaluate("2+2"), 4)
check("operator precedence", calc.evaluate("2+3*4"), 14)
check("power", calc.evaluate("2**10"), 1024)
check("unary minus", calc.evaluate("-3 + 1"), -2)
check("float division", calc.evaluate("7/2"), 3.5)
check("modulo", calc.evaluate("7 % 3"), 1)
check("a function", calc.evaluate("sqrt(16)"), 4.0)
check("a constant", calc.evaluate("pi") > 3.14, True)
check("a nested expression", calc.evaluate("max(1, sqrt(9)) * 2"), 6.0)
check("a large but printable result",
      titles("2**10000")[0].startswith("= "), True)

# ---- the sandbox ------------------------------------------------------------

def refuses(query):
    # A rejected expression yields no result row rather than raising.
    return titles(query) == []


check("unknown name refused", refuses("os.system(1)"), True)
check("attribute access refused", refuses("(2).bit_length()"), True)
check("keyword args refused", refuses("round(x=1)"), True)
check("syntax error refused", refuses("2 +* 2"), True)
check("division by zero refused", refuses("1/0"), True)
check("non-math text is ignored, not refused",
      calc.search("firefox"), [])

# ---- the size guards ---------------------------------------------------------

# Each of these must fail fast; before the guards they computed a giant int
# on the GTK main thread (and str() on it then raised uncaught ValueError).
check("huge exponent refused", refuses("9**100000"), True)
check("nested power bomb refused", refuses("2**10**9"), True)
check("huge factorial refused", refuses("factorial(10**8)"), True)
check("factorial just over the limit refused", refuses("factorial(1001)"), True)
check("factorial at the limit still works",
      len(titles("factorial(1000)")) == 1, True)
check("oversized product refused", refuses("10**4000 * 10**4000"), True)
check("non-finite float refused", refuses("1e308 * 10"), True)
check("float inf through exp() refused", refuses("exp(1000)"), True)

print()
if _failures:
    print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
    sys.exit(1)
print("all calc checks passed")
