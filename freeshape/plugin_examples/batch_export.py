"""Batch export: every part (or the selected ones) to its own STL and / or STEP file, named after the part —
one click to send a whole Part Studio to the slicer or to a supplier."""
import os
import re

FORMATS = [("stl", "STL (3D printing)"), ("step", "STEP"), ("both", "STL and STEP")]


def file_name(name, used):
    """A safe file name from a part name, unique within `used`."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "part"
    out, n = base, 2
    while out.lower() in used:
        out = f"{base} ({n})"
        n += 1
    used.add(out.lower())
    return out


def export_parts(fs, parts, folder, fmt, prefix=""):
    """Write the parts; returns the file paths written."""
    used, written = set(), []
    for p in parts:
        stem = file_name(prefix + p.name, used)
        if fmt in ("stl", "both"):
            path = os.path.join(folder, stem + ".stl")
            fs.save_stl([p.shape], path)
            written.append(path)
        if fmt in ("step", "both"):
            path = os.path.join(folder, stem + ".step")
            fs.save_step([p.shape], path)
            written.append(path)
    return written


def register(api):
    api.info(name="Batch export", version="1.0",
             description="Every part to its own STL / STEP file, named after the part")
    fs = api.geom

    def run(ctx):
        from PySide6.QtWidgets import QFileDialog, QInputDialog
        st = ctx.state
        if st is None or not st.parts:
            ctx.message("There are no parts in this Part Studio.")
            return
        parts = ctx.selected_parts()
        hidden = getattr(ctx.doc, "hidden_parts", set()) or set()
        if not parts:
            parts = [p for p in st.parts.values() if p.id not in hidden]
        labels = [lab for _k, lab in FORMATS]
        choice, ok = QInputDialog.getItem(ctx.main, "Batch export",
                                          f"Export {len(parts)} part{'s' if len(parts) != 1 else ''} as:",
                                          labels, 0, False)
        if not ok:
            return
        fmt = FORMATS[labels.index(choice)][0]
        folder = QFileDialog.getExistingDirectory(ctx.main, "Folder to export the parts to")
        if not folder:
            return
        try:
            written = export_parts(fs, parts, folder, fmt)
        except Exception as e:                       # a part that can't be written: say which
            ctx.message(f"Export stopped: {e}", title="Batch export")
            return
        names = "".join(f"<li>{os.path.basename(p)}</li>" for p in written[:20])
        more = f"<li>… and {len(written) - 20} more</li>" if len(written) > 20 else ""
        ctx.message(f"Wrote <b>{len(written)} file{'s' if len(written) != 1 else ''}</b> to<br>{folder}"
                    f"<ul>{names}{more}</ul>", title="Batch export")

    api.add_command("Batch export parts…", run)
