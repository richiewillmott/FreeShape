"""Interference: where solids overlap. Used by the assembly's interference check and by Check interference on parts
selected in a Part Studio. No Qt here."""
from __future__ import annotations

import numpy as np

from . import kernel as K

MIN_VOLUME = 1e-3          # mm³: less than this is touching, not overlapping


def overlaps(shapes: dict, progress=None, min_volume=MIN_VOLUME) -> list:
    """Every pair of the given shapes ({id: shape}, in order) whose solids overlap:
    [(id_a, id_b, overlap volume, overlap shape)]. Pairs whose boxes don't meet are skipped without a boolean.
    progress(n, total), if given, is called before each pair."""
    ids = list(shapes)
    boxes = {}
    for k in ids:
        try:
            boxes[k] = K.bbox(shapes[k])
        except Exception:
            boxes[k] = None
    pairs = [(a, b) for n, a in enumerate(ids) for b in ids[n + 1:]]
    out = []
    for n, (a, b) in enumerate(pairs):
        if progress:
            progress(n, len(pairs))
        if boxes[a] is None or boxes[b] is None:
            continue
        (la, ha), (lb, hb) = boxes[a], boxes[b]
        if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
            continue
        try:
            ov = K.common(shapes[a], shapes[b])
            v = K.volume(ov) if not ov.IsNull() else 0.0
        except Exception:
            continue
        if v > min_volume:
            out.append((a, b, v, ov))
    return out
