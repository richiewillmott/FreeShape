"""Numbers that accept expressions, units and variables, like Onshape's fields:
"25/2", "1 in", "3*4 mm", "#wall * 2 + 1".  No Qt here — the geometry engine uses it too."""
from __future__ import annotations

import ast
import math
import operator
import re

_UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, '"': 25.4, "ft": 304.8,
          "deg": 1.0, "°": 1.0, "rad": 180.0 / math.pi}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)), "cos": lambda d: math.cos(math.radians(d)),
          "tan": lambda d: math.tan(math.radians(d)), "abs": abs, "min": min, "max": max, "round": round}
_CONST = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONST:
        return _CONST[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return float(_FUNCS[node.func.id](*[_eval(a) for a in node.args]))
    raise ValueError("bad expression")


VAR_RE = re.compile(r"#([A-Za-z_][A-Za-z0-9_]*)")


def has_vars(text) -> bool:
    return isinstance(text, str) and bool(VAR_RE.search(text))


def var_names(text):
    return VAR_RE.findall(text) if isinstance(text, str) else []


def _split_terms(t):
    """[(+1|-1, term), ...] split at + / - outside parentheses (not exponent signs)."""
    out, depth, start, sign = [], 0, 0, 1
    i = 0
    while i < len(t):
        ch = t[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch in "+-" and depth == 0:
            prev = t[:i].rstrip()
            if prev and prev[-1] not in "*/(+-eE":
                out.append((sign, t[start:i]))
                sign = 1 if ch == "+" else -1
                start = i + 1
        i += 1
    out.append((sign, t[start:]))
    return [(sg, term.strip()) for sg, term in out if term.strip()] if len(out) > 1 else [(1, t)]


def parse_number(text, angle=False, variables=None):
    """Returns a float in mm (or degrees), or None if the text can't be parsed
    (including when it uses a variable that isn't defined)."""
    if isinstance(text, (int, float)):
        return float(text)
    if text is None:
        return None
    t = str(text).strip().replace(",", ".").replace("×", "*")
    if not t:
        return None
    if "#" in t:
        missing = []

        def sub(m):
            v = (variables or {}).get(m.group(1))
            if v is None:
                missing.append(m.group(1))
                return "0"
            return f"({float(v)!r})"
        t = VAR_RE.sub(sub, t)
        if missing:
            return None
    # split at top-level + and - so a unit belongs to its own term: "#wall + 1/2 in"
    terms = _split_terms(t)
    if len(terms) > 1:
        total = 0.0
        for sign, term in terms:
            v = parse_number(term, angle, variables)
            if v is None:
                return None
            total += sign * v
        return total
    units_alt = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    # a single trailing unit applies to the whole expression: "1/2 in" -> (1/2)*25.4
    m = re.match(r"^(.*?)\s*(" + units_alt + r")$", t)
    if m and m.group(1) and not re.search(r"\d\s*(" + units_alt + r")(?![A-Za-z])", m.group(1)):
        inner = parse_number(m.group(1), angle, variables)
        return None if inner is None else inner * _UNITS[m.group(2)]

    # otherwise apply units attached to individual numbers: "1in + 5mm"
    def unit_sub(m):
        num, unit = m.group(1), m.group(2)
        return f"({num}*{_UNITS[unit]!r})"
    pattern = r"(\d+(?:\.\d*)?|\.\d+)\s*(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")(?![A-Za-z])"
    t = re.sub(pattern, unit_sub, t)
    try:
        return _eval(ast.parse(t, mode="eval"))
    except Exception:
        return None


def fmt(v, digits=4) -> str:
    if v is None:
        return ""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s
