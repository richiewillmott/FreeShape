"""Reads a STEP file in a separate process so the window stays responsive (and the import can be cancelled).

    python -m freeshape.step_worker input.step output_prefix

Writes output_prefix.brep + output_prefix.json (see kernel.save_step_result). Progress goes to stdout as
lines starting with '@@FS ' (OpenCascade prints its own chatter there too, which the app ignores).
"""
import sys


def main():
    run(sys.argv[1], sys.argv[2])


def run(src, prefix):
    """Read `src`, write prefix.brep / prefix.json. Progress goes to stdout and to prefix.progress (a windowed
    .exe has no stdout, so the app reads the file)."""
    def say(text):
        try:
            with open(prefix + ".progress", "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        try:
            if sys.stdout is not None:
                sys.stdout.write(f"\n@@FS {text}\n")
                sys.stdout.flush()
        except Exception:
            pass
    say("Starting")
    from freeshape import kernel as K
    if src.endswith(".fsimport"):              # an import saved as exact geometry: just read it back
        import json
        say("Reading the saved geometry")
        with open(src) as fh:
            d = json.load(fh)
        result = K.unpack_import(d["brep"], d["meta"])
    else:
        try:
            result = K._import_step_with_colors(src, say)
        except Exception:
            say("Reading it as plain geometry")
            result = [(s, None, None, {}) for s in K.import_step(src)]
    # Build the display meshes here too, off the app's main thread, so the window doesn't freeze afterwards
    # while every part is turned into triangles.
    n = len(result)
    meshes = []
    try:
        for i, (sol, *_rest) in enumerate(result):
            if i % 10 == 0:
                say(f"Preparing the 3D view ({i}/{n} parts)")
            meshes.append(K.tessellate(sol))
    except Exception:
        meshes = None                 # the app will mesh the parts itself, as before
    say(f"Handing over {n} parts")
    K.save_step_result(result, prefix, meshes)
    say("DONE")


if __name__ == "__main__":
    main()
