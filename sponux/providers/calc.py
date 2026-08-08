"""A safe arithmetic calculator provider.

Evaluates the query as a math expression using a restricted AST walk —
no eval(), no attribute access, no arbitrary names. Supports the common
operators plus the functions/constants exposed in ``_FUNCS``/``_CONSTS``.
"""

import ast
import math
import operator

from .base import Result, copy_text

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log2": math.log2, "log10": math.log10, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "min": min, "max": max, "pow": math.pow, "factorial": math.factorial,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

# The calculator runs on the GTK main thread (the debounce callback), so an
# unbounded expression like 2**10**9 or factorial(10**8) would allocate
# hundreds of megabytes and hang the whole daemon, not just the window.
# Results are capped just under Python's int→str digit limit (4300) so a
# valid result is always printable; exponents and factorial arguments are
# checked *before* the expensive operation runs.
_MAX_DIGITS = 4_000
_MAX_BITS = 13_287  # ~ _MAX_DIGITS * log2(10)
_MAX_EXPONENT = 10_000
_MAX_FACTORIAL = 1_000


class CalcError(Exception):
    pass


def _factorial(n):
    if isinstance(n, int) and n > _MAX_FACTORIAL:
        raise CalcError("factorial argument too large")
    return math.factorial(n)


_FUNCS["factorial"] = _factorial


def _check_size(value):
    if isinstance(value, int) and value.bit_length() > _MAX_BITS:
        raise CalcError("result too large")
    if isinstance(value, float) and not math.isfinite(value):
        raise CalcError("result out of range")
    return value


def _pow(base, exp):
    if isinstance(base, int) and isinstance(exp, int):
        # Estimate the result size from the operands instead of computing it:
        # 2**10**9 passes a bit_length check on the operands alone.
        if abs(exp) > _MAX_EXPONENT:
            raise CalcError("exponent too large")
        if exp > 0 and base != 0 and (base.bit_length() - 1) * exp > _MAX_BITS:
            raise CalcError("result too large")
    return _check_size(operator.pow(base, exp))


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalcError("only numbers allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        op = _pow if type(node.op) is ast.Pow else _BIN_OPS[type(node.op)]
        return _check_size(op(_eval(node.left), _eval(node.right)))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise CalcError(f"unknown function {node.func.id!r}")
        if node.keywords:
            raise CalcError("keyword args not supported")
        return fn(*[_eval(a) for a in node.args])
    raise CalcError("unsupported expression")


def evaluate(expr: str):
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError("syntax error") from e
    return _eval(tree)


def _looks_like_math(query: str) -> bool:
    q = query.strip()
    if not q:
        return False
    if q[0].isdigit() or q[0] in "+-.(":
        return True
    # Named functions/constants, e.g. "sqrt(2)" or "pi*2".
    head = q.split("(")[0].strip().lower()
    return head in _FUNCS or head in _CONSTS


def _format(value) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


def search(query: str, limit: int = 1):
    if not _looks_like_math(query):
        return []
    try:
        text = _format(evaluate(query))
    except (CalcError, ZeroDivisionError, ValueError, OverflowError, TypeError):
        return []
    return [Result(
        title=f"= {text}",
        subtitle="Enter to copy result",
        icon="accessories-calculator",
        score=1000.0,  # calculator always wins when it matches
        action=lambda t=text: copy_text(t),
        kind="calc",
    )]
