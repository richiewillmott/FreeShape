"""A small DXF writer (AutoCAD Release 12 ASCII format — opened by AutoCAD / AutoCAD LT, DraftSight, LibreCAD,
QCAD, laser-cutter software …). Only what drawings need: layers with colours and line types, lines, circles,
arcs, polylines, filled triangles (arrowheads) and text. Units are millimetres."""

LINETYPES = {
    # name: (description, pattern in mm: dash > 0, gap < 0)
    "CONTINUOUS": ("Solid line", []),
    "DASHED": ("Dashed __ __ __", [3.0, -1.5]),
    "CENTER": ("Center ____ _ ____ _", [8.0, -1.5, 1.5, -1.5]),
}


def _num(v):
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _text(s):
    s = str(s).replace("Ø", "%%c").replace("°", "%%d").replace("±", "%%p")
    out = []
    for ch in s:
        out.append(ch if 32 <= ord(ch) < 127 else f"\\U+{ord(ch):04X}")
    return "".join(out)


class DxfWriter:
    def __init__(self):
        self.layers = {}
        self.ents = []

    def layer(self, name, colour=7, linetype="CONTINUOUS"):
        self.layers[name] = (colour, linetype)

    def _e(self, *pairs):
        self.ents.append(pairs)

    def line(self, layer, a, b):
        self._e((0, "LINE"), (8, layer), (10, a[0]), (20, a[1]), (30, 0), (11, b[0]), (21, b[1]), (31, 0))

    def circle(self, layer, c, r):
        self._e((0, "CIRCLE"), (8, layer), (10, c[0]), (20, c[1]), (30, 0), (40, r))

    def arc(self, layer, c, r, a0, a1):
        self._e((0, "ARC"), (8, layer), (10, c[0]), (20, c[1]), (30, 0), (40, r), (50, a0 % 360), (51, a1 % 360))

    def polyline(self, layer, pts, closed=False):
        if len(pts) < 2:
            return
        ent = [(0, "POLYLINE"), (8, layer), (66, 1), (70, 1 if closed else 0), (10, 0), (20, 0), (30, 0)]
        for p in pts:
            ent += [(0, "VERTEX"), (8, layer), (10, p[0]), (20, p[1]), (30, 0)]
        ent += [(0, "SEQEND"), (8, layer)]
        self.ents.append(tuple(ent))

    def solid(self, layer, a, b, c):
        self._e((0, "SOLID"), (8, layer), (10, a[0]), (20, a[1]), (30, 0), (11, b[0]), (21, b[1]), (31, 0),
                (12, c[0]), (22, c[1]), (32, 0), (13, c[0]), (23, c[1]), (33, 0))

    def text(self, layer, pos, s, height, angle=0.0, halign="left", valign="baseline"):
        h = {"left": 0, "center": 1, "right": 2}.get(halign, 0)
        v = {"baseline": 0, "bottom": 1, "middle": 2, "top": 3}.get(valign, 0)
        ent = [(0, "TEXT"), (8, layer), (10, pos[0]), (20, pos[1]), (30, 0), (40, height), (1, _text(s)),
               (50, angle), (7, "STANDARD")]
        if h or v:
            ent += [(72, h), (11, pos[0]), (21, pos[1]), (31, 0), (73, v)]
        self.ents.append(tuple(ent))

    # ------------------------------------------------------------------ file
    def save(self, path, ext_min, ext_max, ltscale=1.0):
        out = []
        w = lambda code, val: out.append(f"{code:>3}\n{_num(val) if isinstance(val, (int, float)) and code >= 10 else val}\n")
        w(0, "SECTION")
        w(2, "HEADER")
        for name, code, val in (("$ACADVER", 1, "AC1009"), ("$INSBASE", None, (0, 0, 0)),
                                ("$EXTMIN", None, (ext_min[0], ext_min[1], 0)),
                                ("$EXTMAX", None, (ext_max[0], ext_max[1], 0)),
                                ("$LIMMIN", None, (ext_min[0], ext_min[1])),
                                ("$LIMMAX", None, (ext_max[0], ext_max[1])),
                                ("$LTSCALE", 40, ltscale), ("$PDMODE", 70, 0), ("$TEXTSTYLE", 7, "STANDARD")):
            w(9, name)
            if code is None:
                for i, c in enumerate(val):
                    w(10 + 10 * i, c)
            else:
                w(code, val)
        w(0, "ENDSEC")
        w(0, "SECTION")
        w(2, "TABLES")
        # line types
        w(0, "TABLE")
        w(2, "LTYPE")
        w(70, len(LINETYPES))
        for name, (desc, pat) in LINETYPES.items():
            w(0, "LTYPE")
            w(2, name)
            w(70, 0)
            w(3, desc)
            w(72, 65)
            w(73, len(pat))
            w(40, sum(abs(x) for x in pat))
            for x in pat:
                w(49, x)
        w(0, "ENDTAB")
        # layers
        w(0, "TABLE")
        w(2, "LAYER")
        w(70, len(self.layers) + 1)
        for name, (col, lt) in [("0", (7, "CONTINUOUS"))] + list(self.layers.items()):
            w(0, "LAYER")
            w(2, name)
            w(70, 0)
            w(62, col)
            w(6, lt)
        w(0, "ENDTAB")
        # text style
        w(0, "TABLE")
        w(2, "STYLE")
        w(70, 1)
        w(0, "STYLE")
        w(2, "STANDARD")
        w(70, 0)
        w(40, 0)
        w(41, 1)
        w(50, 0)
        w(71, 0)
        w(42, 2.5)
        w(3, "arial.ttf")
        w(4, "")
        w(0, "ENDTAB")
        w(0, "ENDSEC")
        w(0, "SECTION")
        w(2, "ENTITIES")
        for ent in self.ents:
            for code, val in ent:
                w(code, val)
        w(0, "ENDSEC")
        w(0, "EOF")
        with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
            fh.write("".join(out))
