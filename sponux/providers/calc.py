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


class CalcError(Exception):
    pass


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalcError("only numbers allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
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
        value = evaluate(query)
    except (CalcError, ZeroDivisionError, ValueError, OverflowError, TypeError):
        return []
    text = _format(value)
    return [Result(
        title=f"= {text}",
        subtitle="Enter to copy result",
        icon="accessories-calculator",
        score=1000.0,  # calculator always wins when it matches
        action=lambda t=text: copy_text(t),
        kind="calc",
    )]
