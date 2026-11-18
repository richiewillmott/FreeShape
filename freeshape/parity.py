"""Feature parity with Onshape, tracked option by option — and the shared tools those options are built from.

Issue #27: go feature by feature; whenever an option turns out to be something several features have, build it
once as a shared tool and use it everywhere, so the same option never ends up written twice (or behaving two
different ways). Options that aren't built yet are still listed here, so the to-do list is a tree of shared
tools rather than one-off requests.

Run  python -m freeshape.parity  to rewrite FEATURE_PARITY.md from this file. tests/test_parity.py checks the
document is current and that every shared tool marked built points at real code.

Status: "done", "partial" (works with limits — see the note), "todo", "n/a" (not relevant to this feature).
"""
from __future__ import annotations

DONE, PARTIAL, TODO, NA = "done", "partial", "todo", "n/a"

# ----------------------------------------------------------------------------- shared tools
# id: (name, what it does, where the code is — "module:function" — or None while it's still to build)
SHARED = {
    "profiles": ("Profile picking", "Sketch regions and flat faces as profiles; sketch curves and edges for "
                 "surfaces / thin walls", "freeshape.body_options:profiles"),
    "creation": ("Creation type", "Solid / Surface / Thin from the same profiles",
                 "freeshape.body_options:profiles"),
    "thin": ("Thin wall", "Thicken curves or region outlines into a wall: one thickness either side or "
             "split, or two different thicknesses",
             "freeshape.body_options:thin_faces"),
    "surface_result": ("Surface bodies", "Open surfaces become their own surface bodies",
                       "freeshape.body_options:apply"),
    "merge": ("Merge with the model", "New / Add / Remove / Intersect", "freeshape.features:apply_body"),
    "merge_scope": ("Merge scope", "Choose which parts Add / Remove / Intersect act on",
                    "freeshape.ui.dialogs:_scope_field"),
    "end_blind": ("Blind / symmetric ends", "A set distance, one way or both ways equally",
                  "freeshape.kernel:extrude"),
    "end_through": ("Through all", "Far enough to go through every part", "freeshape.features:run_extrude"),
    "end_up_to": ("Up to next / face / part / vertex", "Stop at geometry, optionally short of it by an offset",
                  "freeshape.features:_sweep_up_to"),
    "second_end": ("Second end", "A second, independent end the other way, with any end type and its own "
                   "draft", "freeshape.features:run_extrude"),
    "flip": ("Opposite direction", "Reverse the direction", "freeshape.features:run_extrude"),
    "draft": ("Draft / taper", "Lean faces by an angle about a neutral plane", "freeshape.body_options:taper"),
    "axis": ("Axis / direction picking", "Sketch line, edge or world axis as an axis or direction",
             "freeshape.features:resolve_axis"),
    "edge_chains": ("Tangent chains", "Pick one edge or face, get the smooth chain it belongs to (OpenCascade "
                    "carries fillets, chamfers and drafts along it)", "freeshape.kernel:fillet"),
    "pattern_faces": ("Pattern / mirror faces", "Repeat or mirror the boss / pocket some faces make (found by "
                      "healing them away, as Delete face does)", "freeshape.features:face_feature_bodies"),
    "two_directions": ("Two directions", "A second direction (patterns) or second angle (revolve)",
                       "freeshape.features:run_linear_pattern"),
    "guides": ("Guide curves", "Extra curves a loft or sweep must follow", "freeshape.kernel:_pipe_shell"),
    "end_conditions_tangency": ("Start / end tangency", "Make a loft or sweep start/end normal or tangent to "
                                "a face", None),
    "loft_path": ("Loft along a path", "Sections placed along a path curve", "freeshape.kernel:loft_wires"),
    "profile_control": ("Profile control", "How a swept profile turns along the path (keep orientation, lock "
                        "to a direction or faces, follow a guide)", "freeshape.kernel:_pipe_shell"),
}

# ----------------------------------------------------------------------------- features, option by option
# feature: (name, [(option, shared tool id or None, status, note)])
FEATURES = {
    "extrude": ("Extrude", [
        ("Solid", "creation", DONE, ""),
        ("Surface", "creation", DONE, "from sketch curves, edges or region outlines"),
        ("Thin", "thin", DONE, "both sides equally, or one side"),
        ("Thin — two different thicknesses", "thin", DONE, ""),
        ("Faces and sketch regions", "profiles", DONE, ""),
        ("Sketch curves and edges", "profiles", DONE, "for Surface and Thin"),
        ("Blind", "end_blind", DONE, ""),
        ("Symmetric", "end_blind", DONE, ""),
        ("Through all", "end_through", DONE, ""),
        ("Up to next / face / part / vertex", "end_up_to", DONE, "Solid, Thin and Surface"),
        ("Offset from the up-to face", "end_up_to", DONE, "'Stop short by'"),
        ("Opposite direction", "flip", DONE, ""),
        ("Draft", "draft", DONE, "Blind, Symmetric and Through all"),
        ("Second end position", "second_end", DONE, "Blind, Through all or any Up to"),
        ("Second end draft", "draft", DONE, "its own angle"),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "revolve": ("Revolve", [
        ("Solid", "creation", DONE, ""),
        ("Surface", "creation", DONE, ""),
        ("Thin", "thin", DONE, ""),
        ("Faces and sketch regions", "profiles", DONE, ""),
        ("Sketch curves and edges", "profiles", DONE, "for Surface and Thin"),
        ("Revolve axis", "axis", DONE, ""),
        ("Full", None, DONE, ""),
        ("One direction", None, DONE, ""),
        ("Symmetric", None, DONE, ""),
        ("Two directions (second angle)", "two_directions", DONE, ""),
        ("Up to next / face / part / vertex", "end_up_to", DONE, "Solid, Thin and Surface"),
        ("Opposite direction", "flip", DONE, ""),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "sweep": ("Sweep", [
        ("Solid", "creation", DONE, ""),
        ("Surface", "creation", DONE, ""),
        ("Thin", "thin", DONE, ""),
        ("Faces and sketch regions", "profiles", DONE, ""),
        ("Sketch curves and edges", "profiles", DONE, "for Surface and Thin"),
        ("Path", None, DONE, ""),
        ("Profile control", "profile_control", DONE, "normal to path, keep orientation, lock to a direction "
         "or to faces"),
        ("Guide curves", "guides", PARTIAL, "one guide curve"),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "loft": ("Loft", [
        ("Solid", "creation", DONE, ""),
        ("Surface", "creation", DONE, ""),
        ("Thin", "thin", DONE, ""),
        ("Profiles in order", "profiles", DONE, "regions, faces and sketch curves"),
        ("Ruled (straight) sides", None, DONE, ""),
        ("Guide curves", "guides", TODO, "OpenCascade's loft has no guide curves — needs a different surfacing "
         "approach"),
        ("Start / end conditions", "end_conditions_tangency", TODO, "OpenCascade's loft has no tangency "
         "control"),
        ("Path", "loft_path", DONE, ""),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "fillet": ("Fillet", [
        ("Edges and faces", None, DONE, ""),
        ("Constant radius", None, DONE, ""),
        ("Tangent propagation", "edge_chains", DONE, ""),
        ("Conic / curvature cross-section", None, TODO, "OpenCascade fillets are circular-section only"),
        ("Variable radius", None, PARTIAL, "start and end radii; Onshape also allows radii at points"),
    ]),
    "chamfer": ("Chamfer", [
        ("Edges and faces", None, DONE, ""),
        ("Equal distance", None, DONE, ""),
        ("Two distances", None, DONE, "with Flip sides"),
        ("Distance and angle", None, DONE, ""),
        ("Tangent propagation", "edge_chains", DONE, ""),
    ]),
    "shell": ("Shell", [
        ("Faces to remove", None, DONE, ""),
        ("Thickness", None, DONE, ""),
        ("Shell outward", None, DONE, ""),
    ]),
    "draft": ("Draft", [
        ("Faces to draft", None, DONE, ""),
        ("Neutral plane", "draft", DONE, ""),
        ("Pull direction / flip", "draft", DONE, ""),
        ("Tangent propagation", "edge_chains", DONE, ""),
        ("Parting line draft", None, TODO, "a substantial feature of its own"),
    ]),
    "hole": ("Hole", [
        ("Simple / counterbore / countersink", None, DONE, ""),
        ("ISO clearance and tapped sizes", None, DONE, ""),
        ("Modelled thread", None, DONE, ""),
        ("Through all / blind", "end_blind", DONE, ""),
        ("Up to next / face / vertex", "end_up_to", DONE, "with 'Stop short by'"),
        ("Drill point", None, DONE, ""),
    ]),
    "linear_pattern": ("Linear pattern", [
        ("Parts", None, DONE, ""),
        ("Features", None, DONE, ""),
        ("Faces", "pattern_faces", DONE, ""),
        ("Direction", "axis", DONE, ""),
        ("Second direction", "two_directions", DONE, ""),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "circular_pattern": ("Circular pattern", [
        ("Parts", None, DONE, ""),
        ("Features", None, DONE, ""),
        ("Faces", "pattern_faces", DONE, ""),
        ("Axis", "axis", DONE, ""),
        ("Angle and count", None, DONE, ""),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "mirror": ("Mirror", [
        ("Parts", None, DONE, ""),
        ("Features", None, DONE, ""),
        ("Faces", "pattern_faces", DONE, ""),
        ("Mirror plane", None, DONE, ""),
        ("New / Add / Remove / Intersect", "merge", DONE, ""),
        ("Merge scope", "merge_scope", DONE, ""),
    ]),
    "boolean": ("Boolean", [
        ("Union / subtract / intersect", None, DONE, ""),
        ("Keep tools", None, DONE, ""),
        ("Subtract complement", None, DONE, "FreeShape's Intersect: keeps only the targets' material "
         "inside the tools"),
    ]),
    "split": ("Split", [
        ("By plane", None, DONE, ""),
        ("By face (flat or curved)", None, DONE, ""),
        ("By sketch curve", None, DONE, ""),
        ("Keep both sides or one", None, DONE, ""),
    ]),
}

SCORE = {DONE: 1.0, PARTIAL: 0.5, TODO: 0.0}


def feature_parity(fid) -> float:
    """Share of a feature's options that are built (partial counts half), 0–100."""
    opts = [o for o in FEATURES[fid][1] if o[2] != NA]
    return 100.0 * sum(SCORE[o[2]] for o in opts) / max(len(opts), 1)


def overall_parity() -> float:
    opts = [o for _n, os_ in FEATURES.values() for o in os_ if o[2] != NA]
    return 100.0 * sum(SCORE[o[2]] for o in opts) / max(len(opts), 1)


def users(tool):
    """[(feature name, option, status)] of every option built on a shared tool."""
    return [(name, opt, st) for _fid, (name, opts) in FEATURES.items() for opt, t, st, _n in opts if t == tool]


MARK = {DONE: "✅", PARTIAL: "🟡", TODO: "⬜", NA: "—"}


def render_markdown() -> str:
    L = ["# Feature parity", "",
         "Generated from `freeshape/parity.py` — edit that file, then run `python -m freeshape.parity`.", "",
         "Following [issue #27](https://github.com/richiewillmott/FreeShape/issues/27): features are finished "
         "option by option, and any option more than one feature has is built **once** as a shared tool "
         "(below) and reused, so it behaves the same everywhere. Options not built yet are listed too.", "",
         f"**Overall: {overall_parity():.0f}%** of the listed options (partial counts half).", "",
         "✅ done · 🟡 partial · ⬜ to do", "", "## Features", "",
         "| Feature | Parity |", "|---|---|"]
    for fid, (name, _opts) in FEATURES.items():
        L.append(f"| [{name}](#{name.lower().replace(' ', '-')}) | {feature_parity(fid):.0f}% |")
    L.append("")
    for fid, (name, opts) in FEATURES.items():
        L += [f"### {name}", "", f"{feature_parity(fid):.0f}% · shared tools used: "
              + (", ".join(sorted({SHARED[t][0] for _o, t, _s, _n in opts if t})) or "none"), ""]
        for opt, tool, st, note in opts:
            extra = f" — {note}" if note else ""
            via = f" *(shared: {SHARED[tool][0]})*" if tool else ""
            L.append(f"- {MARK[st]} {opt}{via}{extra}")
        L.append("")
    L += ["## Shared tools", "",
          "Each is built once and used by every feature listed under it. A tool marked *to build* is needed by "
          "the options shown, and should be built as a shared tool when the first of them is done.", ""]
    for tid, (name, about, code) in SHARED.items():
        where = f"`{code}`" if code else "*to build*"
        L.append(f"- **{name}** — {about}. {where}")
        for fname, opt, st in users(tid):
            L.append(f"  - {MARK[st]} {fname}: {opt}")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "FEATURE_PARITY.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown())
    print(f"Wrote {path} — overall {overall_parity():.0f}%")
