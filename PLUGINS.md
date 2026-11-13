# FreeShape plugins

A plugin is a Python file that adds **parametric features** (with their own dialog, rebuilt with the model like
Extrude or Fillet) and **commands** (run once from the Plugins menu).

## Installing

**Document ▸ Plugins ▸ Manage plugins…** shows what's installed. From there:

- **Install plugin file…** copies a `.py` file into your plugins folder.
- **Add the examples** installs the two examples that come with FreeShape: a *Spur gear* feature and a *Part
  weights* command.
- **Open plugins folder** opens the folder, so you can put files there yourself.
- **Reload** picks up changes after you edit a plugin.

The plugins folder is `FreeShape/plugins` in your data folder: `%APPDATA%` on Windows,
`~/Library/Application Support` on macOS and `~/.local/share` on Linux. Set `FREESHAPE_PLUGINS` to use another
folder. Files whose names start with `_` are skipped, so you can keep helper modules there.

Plugin features appear under the **jigsaw button** on the Part Studio toolbar and in **Document ▸ Plugins**.
Commands appear in the same menus.

> Plugins are ordinary Python and run with the same rights as FreeShape. Only install plugins you trust.

## A first plugin

Save this as `washer.py` in the plugins folder, then click **Reload**:

```python
"""Plain washers."""

def register(api):
    api.info(name="Washer", version="1.0", description="Plain washers")
    fs = api.geom

    def build(ctx):
        t = ctx["t"]
        ring = fs.subtract(fs.cylinder(ctx["od"] / 2, t), fs.cylinder(ctx["id"] / 2, t))
        return fs.move(ring, *ctx.points("at")[0]) if ctx["at"] else ring

    api.add_feature("washer", "Washer", build, fields=[
        api.number("od", "Outside diameter", 20),
        api.number("id", "Hole diameter", 10.5),
        api.number("t", "Thickness", 2),
        api.select("at", "Centre (a vertex)", kinds="vertex", multi=False),
    ])
```

**Washer** now sits in the Plugins menu. The dialog previews as you type. Numbers can be expressions such as
`#bolt + 0.5`, which follow your variables, and *New / Add / Remove / Intersect* work like they do for Extrude.

## `register(api)`

| | |
|---|---|
| `api.info(name, version, description)` | What the Plugins window shows. |
| `api.add_feature(id, label, build, fields=[], boolean=True, help="")` | A parametric feature. `build(ctx)` returns a solid or a list of solids, which are added to the model with the chosen New / Add / Remove / Intersect. With `boolean=False` the result is always new parts. If `build` changed parts itself (see `ctx.replace_part`), return `None`. |
| `api.add_command(label, run)` | A menu command. `run(ctx)` gets a command context (below). |
| `api.geom` | The geometry functions (`freeshape.api`, below). |
| `api.version` | The plugin API version (1). |

### Dialog fields

| | |
|---|---|
| `api.number(key, label, default, unit="mm", minimum=None)` | A number. Units can be `"mm"`, `"deg"` or `""`. |
| `api.integer(key, label, default, minimum=1)` | A whole number. |
| `api.angle(key, label, default)` | An angle in degrees. |
| `api.choice(key, label, options, default=None)` | A drop-down. Options are values, or `(value, label)` pairs. |
| `api.check(key, label, default=False)` | A tick box. |
| `api.text(key, label, default="")` | Text. |
| `api.select(key, label, kinds, multi=True)` | Things to click. Kinds can be `face`, `edge`, `vertex`, `part`, `plane`, `axis` or `region` (sketch regions). |
| `api.note(text)` | A line of help. |

### The feature context, `build(ctx)`

| | |
|---|---|
| `ctx["key"]`, `ctx.get("key", default)` | A field's value. Expressions have already been worked out. |
| `ctx.faces(key)`, `ctx.edges(key)` | The selected faces / edges, as OCP shapes. |
| `ctx.points(key)` | The selected vertices, as `(x, y, z)` arrays. |
| `ctx.parts(key)` | The selected parts (`.id`, `.name`, `.shape`, `.color`). `ctx.parts()` returns every part. |
| `ctx.plane(key, default="Top")` | The selected plane, or the default. Its methods are `.origin`, `.normal`, `.xdir`, `.to_world(u, v)` and `.to_local(p)`. |
| `ctx.axis(key, default="Z")` | `(point, direction)`. |
| `ctx.regions(key)` | The selected sketch regions, as faces. |
| `ctx.variables` | The studio's variables. |
| `ctx.add_part(shape, name)`, `ctx.replace_part(id, shape)`, `ctx.remove_part(id)` | Change parts directly. |
| `ctx.fail("message")` | Stop with a message. The feature turns red and shows the message. |

### The command context, `run(ctx)`

| | |
|---|---|
| `ctx.doc`, `ctx.state` | The open document and its model (`state.parts`, `state.vars`, …). |
| `ctx.selection`, `ctx.selected_parts()` | What's selected. |
| `ctx.message(text)`, `ctx.ask_number(label, value)` | Talk to the user. Messages can be simple HTML. |
| `ctx.add_shape(shape, name)` | Put a solid into the Part Studio. It is added as an imported feature, so it can be undone and is saved. |
| `ctx.add_feature(type, **params)` | Add any feature, built-in or a plugin's: `ctx.add_feature("washer", od=30)`. |

## Geometry (`freeshape.api`)

All lengths are in millimetres and all angles in degrees. Shapes are OCP (OpenCascade) shapes, so the full OCP
library works on them too, but the functions below are the ones FreeShape promises to keep.

- **Solids:** `box(x, y, z, at=(0, 0, 0), centred=False)`, `cylinder(r, h, at, axis)`, `cone(r1, r2, h, at, axis)`,
  `sphere(r, at)`
- **Flat profiles** (on `"Top"`, `"Front"`, `"Right"` or any `plane(...)`):
  - `polygon(points, on)`
  - `circle(r, centre, on)`
  - `outline(segments, on)`: a closed chain of segments. Two points make a straight line; more make a smooth curve.
- **From profiles:** `extrude(face, height, direction=None)`, `revolve(face, angle=360, axis, about)`
- **Combining:** `union(*shapes)`, `subtract(shape, *tools)`, `intersect(a, b)`
- **Moving:** `move(shape, dx, dy, dz)`, `rotate(shape, angle, axis, about)`, `mirror(shape, about="Right")`
- **Edges and faces:** `fillet(shape, edges, r)`, `chamfer(shape, edges, d)`, `shell(shape, open_faces, t)`
- **Looking:** `faces`, `edges`, `solids`, `volume`, `bbox`, `centre_of_mass`, `is_valid`
- **Files:** `save_step(shapes, path)`, `save_stl(shapes, path)`
- `plane("Top")` or `plane(origin, normal, xdir)`; `KernelError`

## Opening files without the plugin

A document saves a copy of what each plugin feature made. On a computer without the plugin, the file still opens
and shows that saved shape. To change the feature's settings, though, you need the plugin. The Plugins window
lists the plugins a document uses but which aren't installed.

## Scripts

**Document ▸ Plugins ▸ Run a Python script…** runs a `.py` file once. In the script, `fs` is the geometry module
and `ctx` is a command context:

```python
part = fs.subtract(fs.box(60, 40, 10), fs.cylinder(5, 10, at=(30, 20, 0)))
ctx.add_shape(part, "Plate")
```

The examples in [`freeshape/plugin_examples`](freeshape/plugin_examples) are good starting points.
