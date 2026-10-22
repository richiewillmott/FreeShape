"""Works out one exact drawing view in a separate process (exact hidden-line removal can take seconds on
complex parts, and it holds Python's lock, so a thread wouldn't keep the window responsive).

    python -m freeshape.hlr_worker shape.brep "dx,dy,dz" "ux,uy,uz" out.json
"""
import json
import os
import sys


def run(brep_path, d, up, out_path):
    from freeshape import kernel as K
    with open(brep_path, "rb") as fh:
        shape = K.shape_from_brep(fh.read())
    d = [float(x) for x in d.split(",")]
    up = [float(x) for x in up.split(",")]
    res = K.hlr_project(shape, d, up)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(res, fh)
    os.replace(tmp, out_path)


if __name__ == "__main__":
    run(*sys.argv[1:5])
