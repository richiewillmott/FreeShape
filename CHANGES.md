# FreeShape revisions

## r8 — speed

### 1.8.3 (next)
- Extrude, Loft and other features that use sketch areas: **click a sketch in the feature list** to use it, like
  Onshape (also works for hidden sketches). For Loft, a sketch with several areas gives its outline (with any holes).

### 1.8.2
- Extrude, loft and other features that want a sketch's area: clicking the sketch's **line** now picks the
  area it outlines (the smallest one, so a hole's circle picks the hole), not just clicking inside it. If the
  sketch isn't a closed shape, the status bar says so instead of nothing happening.
- Deleting a feature shows the sketches it used again (they're hidden while a feature uses them), unless another
  feature still uses them. Undo puts it all back.
- **Show / hide eyes** in the feature and parts lists, like Onshape: hover a sketch, plane or part and click the
  eye at the end of the row. Hidden ones keep a faint crossed-out eye so you can see what's hidden.

### 1.8.1
- Drawings of flat patterns: the **bend lines can be dimensioned** (to an edge, a corner or another bend line),
  and those dimensions follow the model.
- **Linux AppImage**: one file that runs on recent Linux distros without installing Python
  (`packaging/linux/build_appimage.sh` builds it).
- **Mac** (in testing): `FreeShape.app` / `.dmg`, built and tested automatically on GitHub's Macs
  (Actions → Mac build); files double-clicked in Finder open in FreeShape.

### 1.8.0
- Windows installer: fixed "The setup files are corrupted" (files are now compressed one by one).
- **Drawings** stay smooth on big models:
  - The sheet is drawn once into a picture: panning just slides it, zooming stretches it and redraws sharply
    when the wheel stops, hovering and selecting only redraw the highlights.
  - Dragging a view (or a dimension / note) moves its picture; the drawing is updated once, when you let go.
  - Only views that actually changed are rebuilt (adding a dimension no longer redoes every view).
  - The Dimension tool's hover snapping uses cached, vectorised arrays per view (~6× faster).
  - Working out views of big STEP models: removing hidden lines that sit under visible ones went from minutes
    to well under a second (lines are grouped by the infinite line they're on and binary-searched).
- **3D view**: picking under the mouse checks all parts' boxes in one go (~15× faster on a 250-part PCB),
  hover picking runs once per event-loop turn with the latest mouse position instead of for every mouse-move
  event, and the plane labels don't re-test what's in front of them while the view is moving.
- Drawings: the Dimension tool no longer offers "invisible" circles. Round faces such as a gear's tooth flanks
  are only small parts of a cylinder; their full circle used to be clickable all the way round. Now a circle
  can only be picked where its line is actually drawn under the mouse — and that line must run along the circle
  to drawing accuracy, not just pass near it (a gear's tooth outlines run close to its flank arcs). An arc
  lights up only along its drawn part, not as a whole circle. Hidden edges only count when the view shows them.

## r7
- **Sheet metal**, like Onshape's:
  - **Sheet metal** turns a sketch region into a flat sheet: thickness, inside bend radius and K-factor
    (0.44 default — suits most steel and aluminium on a press brake).
  - **Flange**: click edges of the sheet to bend flanges up (or down) at any angle up to 180°, with the length
    measured to the outside of the bend. *Outside the edge* adds the bend beyond the edge; *inside the edge*
    keeps the outline the same size. Flanges can go on flanges (channels, boxes), and each can have its own
    bend radius.
  - **Flat pattern** button shows sheet metal parts unfolded; the flat is exact: each bend uses
    angle × (radius + K × thickness) of sheet.
  - Holes and cut-outs made afterwards with normal features (extrude cut, Hole …) appear in the flat pattern.
  - **Export flat pattern (DXF)** for laser / plasma / waterjet: the outline on layer CUT, bend lines dashed
    on layer BEND with "UP 90° R1.5" notes. Also from the right-click menu on the part.
  - **Drawings of the flat pattern** (pick "… — flat pattern" in New Drawing, or right-click the part): one
    face-on view with the bend lines and their notes, ready to dimension.
  - Everything is parametric: change the thickness, radius or a flange length and it all rebuilds.
  - **Automatic corner reliefs**: where two bends meet at a corner, a square notch is cut through the corner
    and the ends of both bends (in the 3D part and the flat pattern), so the corner folds cleanly instead of
    tearing. Size = 1.5 × thickness by default; switch it off or change it in the Sheet metal feature.

## r6
- **2D drawings** — a new Drawing tab (`.fdwg`), like Onshape's:
  - Pick a part, a whole Part Studio or an Assembly; choose the sheet (A4–A0, landscape / portrait) and first
    angle (ISO / BS 8888) or third angle (ANSI). The front, plan, side and isometric views are laid out at the
    biggest standard scale that fits.
  - Views from the real 3D model with hidden-line removal: thick visible edges, dashed hidden edges, centre
    marks on holes; optional tangent edges. Insert view (any direction and scale), Projected view (click beside
    a view; off a corner for an isometric). Projected views stay lined up; dimensions move with their view.
  - Dimensions: lengths, horizontal / vertical / aligned distances between corners and hole centres,
    point-to-edge distances, diameters and radii — with a live preview while placing, prefix / suffix text and
    decimal places. They follow the model: edit the part and the drawing shows the new sizes.
  - Notes, a border with grid zones, and a title block (title, drawing number, revision, drawn by, date, scale,
    sheet size, projection symbol, company, material, general tolerances).
  - Export **DXF** — AutoCAD 2010 DXF with **real AutoCAD dimensions** (grip-editable, restylable; they read
    the model sizes at any sheet scale), Arial text, proper layers and line types, at paper size or true 1:1
    size — and **PDF** (vector, exact sheet size). Needs the `ezdxf` library, which FreeShape installs itself
    on first start (without it, a plain R12 DXF with dimensions drawn as lines and text).
  - Saved drawings keep a copy of the model; home page thumbnails; `.fdwg` file icon and double-click to open
    from the installer.
  - Complex parts (gears, STEP models): views appear straight away from the part's mesh, and the exact views are
    worked out in background processes and swapped in (saved in the file, so reopening is instant). Exports
    wait for the exact views.
  - Centre marks only on whole circles (one per centre); hidden edges start off on views too busy to read them.
  - Cylinders can be dimensioned side-on: click its two outline edges for its Ø (added automatically), or one
    outline edge for its length; any two parallel edges give the distance between them.
  - Holes drilled across round parts (e.g. a grub-screw / tapped hole through a gear hub) are recognised from
    their cylinder: click the hole's outline for its Ø, and it gets a centre mark.

## r5
- Assemblies: Onshape-style **move / rotate triad**. Click a part (or its name in the list) and a triad appears:
  arrows slide along an axis, rings at the arrow ends turn about it (Shift for 15° steps), squares slide in a
  plane, the centre ball moves freely. The distance / angle shows while dragging; afterwards just type a
  number and press Enter to set it exactly (Esc leaves it as dragged). Works with mates: a hinged part turns about its hinge, a mated group with nothing fixed moves as one.
  Undo puts it back.
- Sketch dimensions can go to a **part's edges and corners**, like Onshape: with Dimension, click a sketch item
  and then an edge of the model (or near its end for the corner). Edges seen side-on — the rim of a cylinder or
  gear from the side — count as the straight line they look like. Use (`U`) handles those edges too.
- Dimensions: once you've picked what to measure, the dimension follows the mouse so the next click clearly
  places it, and the hint says what to click next. The number now sits in the middle of the dimension line.
- A dimension that would over-define the sketch (e.g. a second one from the other side) is refused with a
  red message instead of being added, and a value that can't work puts everything back and says so — no more
  geometry flying off the screen.
- **Splash screen** while FreeShape starts (from Python and from FreeShape.exe): logo, version and a progress
  bar through loading the geometry kernel, the interface and the file being opened.

## r4
- Windows app: `build_exe.bat` packs FreeShape into `FreeShape.exe` (no Python needed to run it), with the logo
  as the program icon, a portable zip, and — if Inno Setup is installed — an installer with Start-menu and
  desktop shortcuts, file icons for `.fshape` / `.fasm`, double-click-to-open, and "Open with" for STEP files.
- Sharper icons at every size (16–256 px), plus document icons for Part Studios and Assemblies.
- Hole: also goes at the centres of circles in the sketch, has a new size option "Size of the sketch circles"
  (chosen automatically when the sketch only has circles), and you can pick the sketch by clicking it.
- Assemblies: dragging a part whose mated group has nothing fixed now slides the whole group together (like
  Onshape) instead of spinning the parts round each other. With a fixed part, dragging prefers sliding and
  only turns parts when the mates make it.

## r3
- Onshape look: plain white background, steel-blue default part colour, and shiny surfaces (smooth
  per-pixel lighting with highlights — View → Shiny surfaces turns it off).
- Plane names fade when they're behind a part instead of printing over it.
- Default planes are a fixed 150 × 150 mm.

## r2
- Onshape-style view cube: bevelled edges and corners (click any of 26 views), names printed on the faces,
  arrows to turn the view 90° up / down / left / right, curved arrows to roll it, an X/Y/Z triad, and a
  view menu (▾) with the named views and zoom to fit. The cube's turns keep the origin (or the model's centre)
  in place on screen.
- Smoother: mouse-wheel zoom glides instead of jumping, and the view cube is drawn from a cached picture
  (about 5× less work per frame).

## r1
- Assemblies: insert linked parts, fastened / revolute / slider / cylindrical / planar / ball mates, drag through
  mates, precise move, interference check, bill of materials, STEP assembly export, `.fasm` files.
- Home page with recent projects and thumbnails; drag files onto the window.
- Big STEP imports read in the background with a progress window and Cancel.
- Much faster display and picking with hundreds of parts.
- Hover / click a feature in the list to highlight what it made.
- Rotation centre: the point under the mouse, else the model's centre (View → Rotate around).
- Fixes: whole-face threads on a plain rod; no reinstall when `.venv` is copied.
