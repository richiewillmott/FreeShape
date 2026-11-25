# FreeShape

**Parametric CAD that works like Onshape, but free, offline and yours.** Windows, Mac and Linux. No cloud, no
account, no public-by-default designs.

![FreeShape demo: change a number and the part rebuilds, surfaces that turn into a solid, a gear assembly with an exploded view, and a 2D drawing](docs/images/freeshape_demo.gif)

**[⬇ Download the latest release](https://github.com/richiewillmott/FreeShape/releases/latest)** · [User guide](docs/USER_GUIDE.md) · [What's new](CHANGES.md)

## Why

Onshape is great to use but cloud-only. FreeCAD is free and local but hard to love. FreeShape sits in between:
the Onshape workflow — feature list, sketcher, mates, drawings — running on your own computer, on the same
OpenCascade kernel FreeCAD uses, so you get real B-rep solids and proper STEP files.

## What you can do

- **Part Studios** — constrained sketches (polygons, conics, splines, DXF and image import …); extrude, revolve,
  sweep and loft as solids, surfaces or thin walls; fillets, chamfers, shell, draft, holes and real ISO threads;
  patterns, mirror, booleans, split; variables with units, and variables *measured from the model* that follow every
  change.
- **Assemblies** — Onshape-style mates, sub-assemblies, gear / rack / screw relations, a standard parts library
  (ISO screws, nuts, bearings), exploded views, interference check and bill of materials.
- **Drawings** — projected, section and detail views, dimensions that follow the model, parts lists and balloons;
  export **PDF** or **DXF with real AutoCAD dimensions**.
- **Sheet metal** — flanges, hems, jogs and tabs, exact flat patterns with bend lines, and nesting on stock
  sheets for laser cutting.
- **Configurations, derived parts and version history** — many sizes from one table, and a readable *what
  changed* between any two versions, shown in 3D.
- **Import and export** — STEP (with colours, fast even with hundreds of parts), STL for 3D printing, BREP.
- **Plugins and scripts** — ready-made Gridfinity bins, timing pulleys, gears, Lighten pockets, laser-cut boxes and
  batch export, or write your own in Python ([PLUGINS.md](PLUGINS.md)); export any project as a Python script that
  builds it again.

| Part Studio | Assemblies | Drawings | Sheet metal |
|---|---|---|---|
| ![](docs/screenshot.png) | ![](docs/images/assembly.png) | ![](docs/images/drawing.png) | ![](docs/images/sheet_metal.png) |

## New in 2.6

- **Selecting like Onshape in the lists** — each click in the feature list or the parts list adds to the
  selection (click Part 1, then Part 2: both picked), and the parts list and the 3D view pick the same parts.
- **Check interference** for parts in a Part Studio — right-click several parts, see their overlaps in red.
- **Fill** (a surface patch over a closed loop, tangent if you like) and **Constrained surface** (a surface fitted
  through points, or a part's vertices).
- **Variables follow a rename** wherever you rename them, and value boxes offer `#variables` as you type a name.

### New in 2.5

- **Variables on the fly** — type an unknown `#name` in any value box and FreeShape offers to create it, just
  before that feature, with the value the box had; type `#` for a list of the variables there are.
- **Variables table** — every variable, its value and what uses it; change values and names in place.
- **Copy and paste features** — `Ctrl+C` / `Ctrl+V` in the feature list, within or between Part Studios.
- **The view faces a sketch when you edit it**, not only when you make one (Orient normal to sketch on edit).
- **Transform with Onshape's types** — by line, distance, XYZ, mate connectors, rotate, copy in place and scale.
- **Mate connectors in Part Studios** — on a face, edge or vertex; Shift steps through the face's points.
- **Curve and Point patterns**, and a **Normal** sketch constraint (Perpendicular on a line and a curve).
- **A second arrow for an Extrude's second end**, to drag its depth as you do the first.
- **Sturdier models** — features stay on their own part when earlier ones are suppressed, a lost edge is
  reported instead of swapped for another, a steadier sketch solver, right DXF dimensions on detail views, sheet
  metal that follows Transform, and saves that can't leave an empty file. Files are saved in a new format; older
  ones convert as they open ([upgrading](docs/releases/2.5.0.md#upgrading-from-24)).

Release notes: [2.6.0](docs/releases/2.6.0.md), [2.5.0](docs/releases/2.5.0.md). Full history in [CHANGES.md](CHANGES.md).

## Install

Grab the file for your system from the [latest release](https://github.com/richiewillmott/FreeShape/releases/latest):

| | File | First run |
|---|---|---|
| **Windows** | `FreeShape-Setup-<version>.exe` (or the portable zip) | SmartScreen warning: **More info → Run anyway** |
| **Mac** (Apple Silicon) | `FreeShape-<version>-mac-arm64.dmg` | Drag to Applications, then right-click → **Open** |
| **Linux** | `FreeShape-<version>-x86_64.AppImage` | `chmod +x` it and run; or `am -i freeshape` |

The installers aren't code-signed yet, hence the warnings. If the AppImage says FUSE is missing, run it with
`--appimage-extract-and-run` or install `libfuse2`. To run from source or build it yourself, see
[BUILDING.md](BUILDING.md).

## Know Onshape? You already know FreeShape

Right-drag rotates, middle-drag pans, the wheel zooms. Select a plane and press `Shift+S` to sketch; `L`, `R`,
`C`, `D` draw lines, rectangles, circles and dimensions. `S` opens your tools at the mouse, `Tab` picks what's
behind, `P` hides the planes. Double-click any feature to edit it, and drag the rollback bar through history. Type
`#name` in any box to use a variable.

Everything else — every tool, key and option — is in the **[user guide](docs/USER_GUIDE.md)**. Option-by-option
progress against Onshape is tracked in [FEATURE_PARITY.md](FEATURE_PARITY.md).

## Honest limits

FreeShape is a capable tool for hobby, prototyping and small-business work, not a drop-in Onshape replacement:
no FeatureScript (Python plugins and scripts instead), no flexible sub-assemblies or mate limits yet, no real-time
collaboration or cloud (on purpose), and references are re-found by geometry, so a drastic upstream change can
occasionally make a later feature need re-picking (it turns red rather than using the wrong edge). The full list is in the [user guide](docs/USER_GUIDE.md#honest-limits).

## Licence

Free software under the **GNU GPL v3** — use it for anything, share it, change it; if you share a changed version,
share its source too. See [LICENSE](LICENSE). Built on OpenCascade and Qt / PySide6
([THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)).

Copyright © 2026 Richard Willmott. Made with a lot of help from Claude.
