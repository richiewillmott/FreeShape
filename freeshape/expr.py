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


# ---------------------------------------------------------------------- units: what kind of quantity
# A dimension is (length power, angle power): a length is (1, 0), an area (2, 0), an angle (0, 1), a plain
# number (0, 0). None means "unknown" (a variable of kind "any") and is never complained about.
KIND_DIMS = {"length": (1, 0), "angle": (0, 1), "area": (2, 0), "volume": (3, 0), "number": (0, 0), "any": None}
_UNIT_DIMS = {u: ((0, 1) if u in ("deg", "°", "rad") else (1, 0)) for u in _UNITS}
_UNIT_KEYS = {u: f"U{i}" for i, u in enumerate(sorted(_UNITS, key=len, reverse=True))}
_UNIT_BY_KEY = {v: k for k, v in _UNIT_KEYS.items()}


def dims_name(d) -> str:
    return {(1, 0): "a length", (0, 1): "an angle", (2, 0): "an area", (3, 0): "a volume",
            (0, 0): "a plain number"}.get(d, "a mixed quantity")


class UnitError(ValueError):
    pass


def _dims(node, kinds):
    if isinstance(node, ast.Expression):
        return _dims(node.body, kinds)
    if isinstance(node, ast.Constant):
        return (0, 0)
    if isinstance(node, ast.Name):
        if node.id.startswith("V_"):
            k = (kinds or {}).get(node.id[2:], "any")
            return KIND_DIMS.get(k)
        return (0, 0)                                           # pi, e
    if isinstance(node, ast.UnaryOp):
        return _dims(node.operand, kinds)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = node.func.id
        if fn.startswith("U") and fn in _UNIT_BY_KEY:           # a number with a unit: 5 mm, 30 deg
            return _UNIT_DIMS[_UNIT_BY_KEY[fn]]
        args = [_dims(a, kinds) for a in node.args]
        if fn in ("sin", "cos", "tan"):
            if args and args[0] not in (None, (0, 0), (0, 1)):
                raise UnitError(f"{fn}() takes an angle, not {dims_name(args[0])}")
            return (0, 0)
        if fn == "sqrt":
            a = args[0] if args else (0, 0)
            if a is None:
                return None
            if a[0] % 2 or a[1] % 2:
                raise UnitError(f"sqrt() of {dims_name(a)}")
            return (a[0] // 2, a[1] // 2)
        out = args[0] if args else (0, 0)                       # abs, round, min, max: like + for several
        for a in args[1:]:
            out = _add_dims(out, a)
        return out
    if isinstance(node, ast.BinOp):
        a, b = _dims(node.left, kinds), _dims(node.right, kinds)
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
            return _add_dims(a, b)
        if a is None or b is None:
            return None
        if isinstance(node.op, ast.Mult):
            return (a[0] + b[0], a[1] + b[1])
        if isinstance(node.op, ast.Div):
            return (a[0] - b[0], a[1] - b[1])
        if isinstance(node.op, ast.Pow):
            if b != (0, 0):
                raise UnitError("a power must be a plain number")
            try:
                n = _eval(node.right)
            except ValueError:
                return None
            if a == (0, 0):
                return a
            if abs(n - round(n)) > 1e-9:
                return None
            return (int(a[0] * round(n)), int(a[1] * round(n)))
    return None


def _add_dims(a, b):
    """Adding (or comparing) two quantities: they must be the same kind; a bare number takes the other's."""
    if a is None or b is None:
        return None
    if a == (0, 0):
        return b
    if b == (0, 0):
        return a
    if a != b:
        raise UnitError(f"can't add {dims_name(a)} and {dims_name(b)}")
    return a


def expr_dims(text, kinds=None):
    """The kind of quantity an expression is: (length power, angle power), None if it can't be told (it uses a
    variable of kind "any"). Raises UnitError when it mixes kinds ("10 mm + 5 deg")."""
    if isinstance(text, (int, float)) or text is None:
        return (0, 0)
    t = str(text).strip().replace(",", ".").replace("×", "*")
    t = VAR_RE.sub(lambda m: f"V_{m.group(1)}", t)
    units_alt = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    m = re.match(r"^(.*?)\s*(" + units_alt + r")$", t)
    if m and m.group(1) and not re.search(r"\d\s*(" + units_alt + r")(?![A-Za-z])", m.group(1)):
        inner = expr_dims_raw(m.group(1), kinds)                  # "1/2 in": the unit applies to it all
        u = _UNIT_DIMS[m.group(2)]
        return None if inner is None else (inner[0] + u[0], inner[1] + u[1])
    return expr_dims_raw(t, kinds)


def expr_dims_raw(t, kinds):
    units_alt = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    t = re.sub(r"(\d+(?:\.\d*)?|\.\d+)\s*(" + units_alt + r")(?![A-Za-z])",
               lambda m: f"{_UNIT_KEYS[m.group(2)]}({m.group(1)})", t)
    try:
        tree = ast.parse(t, mode="eval")
    except SyntaxError:
        return None
    return _dims(tree, kinds)


def check_units(text, want, kinds=None):
    """None if the expression fits a field that wants `want` (length | angle | number | any), else the reason.
    A bare number fits any field (it is in the field's own unit)."""
    if want == "any" or not isinstance(text, str):
        return None
    try:
        d = expr_dims(text, kinds)
    except UnitError as e:
        return str(e)
    if d is None or d == (0, 0) or d == KIND_DIMS.get(want):
        return None
    return f"that's {dims_name(d)} — this needs {dims_name(KIND_DIMS[want])}"


def fmt(v, digits=4) -> str:
    if v is None:
        return ""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s
