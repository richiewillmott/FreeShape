"""Part weights: the volume, surface area and weight of the selected parts (or all of them) in common materials."""

MATERIALS = [("Steel", 7.85), ("Stainless steel", 8.0), ("Aluminium", 2.70), ("Brass", 8.5),
             ("PLA", 1.24), ("PETG", 1.27), ("ABS", 1.04), ("Oak", 0.75)]   # g/cm³


def register(api):
    api.info(name="Part weights", version="1.0", description="Volume, area and weight of parts in common materials")
    fs = api.geom

    def run(ctx):
        st = ctx.state
        if st is None or not st.parts:
            ctx.message("There are no parts in this Part Studio.")
            return
        parts = ctx.selected_parts() or list(st.parts.values())
        rows = []
        total = 0.0
        for p in parts:
            v = fs.volume(p.shape)
            total += v
            rows.append(f"<tr><td>{p.name}</td><td align=right>{v / 1000:.2f} cm³</td></tr>")
        mats = "".join(f"<tr><td>{n}</td><td align=right>{total / 1000 * d:.1f} g</td></tr>" for n, d in MATERIALS)
        ctx.message(f"<b>{len(parts)} part{'s' if len(parts) != 1 else ''}</b><table>{''.join(rows)}</table>"
                    f"<p><b>Total {total / 1000:.2f} cm³</b> — weighs:</p><table>{mats}</table>",
                    title="Part weights")

    api.add_command("Part weights…", run)
