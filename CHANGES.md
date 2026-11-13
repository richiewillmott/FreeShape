# FreeShape revisions

## 2.0.0

The biggest update yet: everyday modelling (splines, ellipses, text, "Up to" ends, feature patterns, sketching
from a part's edges), configurations and derived parts, assemblies (standard parts, exploded views, relations,
patterns), drawings (sections, details, parts lists, balloons), sheet metal (hems, jogs, tabs, convert, nesting
for cutting), version history, Python plugins, and new navigation. It was built and tested in stages — the
1.9.x previews below — which together make 2.0.0.

### Split, reworked (issue #20)
- **Split with any surface**: planes, any face — flat or **curved** (a cylinder, cone, sphere, torus or an extruded
  curve is extended so it cuts right through) — or **sketch curves** (lines, arcs, splines, pushed out both ways
  into a cutting surface).
- **As many splitting tools as you like** in one Split. Each has its own **Keep both sides**; untick it and an
  **arrow** shows which side stays — drag it across, or click **Flip**, to keep the other side. Two crossing
  planes through a cube give four pieces, or just the one quarter you choose.
- Splits made before 2.0 open unchanged (their plane becomes the first tool).
- Sweep can now follow sketch splines too.

### 1.9.5 — stage 5 preview: plugins and version history
- **Python plugins**: a plugin is a `.py` file in the plugins folder (Document ▸ Plugins ▸ Manage plugins…). It
  can add
  - **parametric features**, with the same dialog as built-in ones: numbers (expressions and `#variables`
    work), choices, tick boxes, selections (faces, edges, vertices, parts, planes, axes, sketch regions) and
    New / Add / Remove / Intersect. They preview live and rebuild with the model.
  - **commands**, run once from the jigsaw button on the toolbar or Document ▸ Plugins.

  A small geometry API (`freeshape.api`: boxes, cylinders, polygons, smooth outlines, extrude, revolve, union,
  subtract, fillet …) keeps plugins short. Documents keep a copy of what each plugin feature made, so they open
  — unchanged — on a computer without the plugin. **Run a Python script…** runs a script once against the open
  Part Studio. Two examples come with FreeShape: a **Spur gear** feature (real involute teeth from the module and
  number of teeth) and a **Part weights** command. See [PLUGINS.md](PLUGINS.md).
- **Version history** (clock button, or Document ▸ Version history, Ctrl+Alt+H):
  - **Create version…** keeps the model as it is now, with a name and a description; every save also keeps a
    version when something changed (the last 50 of those are kept). Versions are saved in the file.
  - Click a version to see **what changed since**, or Ctrl+click two to compare them, in plain words: features
    added and removed, renamed, moved, suppressed, each setting that changed ("Depth: 10 mm → 16 mm"), sketch
    edits ("Added a distance dimension (40 mm)", "Added 2 lines"), variables ("#wall: 3 → 4"), configurations,
    part names and colours — and each part's volume before and after.
  - **Show in 3D** shows the material added (green) and removed (red).
  - **Restore** goes back to a version (undoable; a version of how it was is kept first), and **Open in a new
    tab** opens an old version beside the current one.

### 1.9.4 — sketching on a part
- **Draw from the part's edges**, like Onshape: when you sketch on a face, its corners, edge midpoints, circle
  centres and edges are snap targets for every drawing tool (they light up as you get near). A point placed on
  a corner, midpoint or centre is fixed there; a point placed on an edge is held on it (a construction copy of
  the edge is added for it), so the sketch stays attached to the part.
- **Dimension from the part**: with the Dimension tool, click a corner of the face (or any part edge, as before)
  and then your sketch geometry.
- Drawing a horizontal or vertical line up to one of the face's edges ends it exactly on the edge, keeping it
  level / upright.
- The dashed diagonal that holds a centre rectangle's centre (and the edge copies made by snapping) only shows
  while you edit the sketch, not in the 3D view afterwards.

### 1.9.3 — stage 4 preview: configurations, derived parts, more sheet metal
- **Configurations**: the Configurations button opens a table — each row a configuration (say "M5 × 20",
  "M6 × 30") setting the studio's variables (values or expressions), plus optional columns that switch features
  off. Pick the configuration shown from the toolbar. Assemblies insert any configuration (each shows in the
  Insert panel), drawings can be of any configuration, and parts lists name it ("Spacer (Long)").
- **Derived parts**: the Derived feature brings parts from another Part Studio (all, or the ones you pick, in
  any configuration), kept linked: edit them there and they update here. A copy is saved with the document, so it
  still opens if the other studio isn't there.
- **Sheet metal**:
  - **Hem**: fold edges right back over the sheet (180°), with a tight radius (half the thickness unless you
    choose one).
  - **Jog**: from an edge the sheet steps up or down by an offset and carries on parallel (two opposite 90°
    bends).
  - **Tab**: more sheet in a panel's own plane, from a sketch region drawn on it and touching it; flanges can go
    on the tab's edges.
  - **Convert to sheet metal**: a flat plate of even thickness (imported, or extruded) becomes sheet metal —
    holes and all — so it can get flanges, be unfolded and be nested. Anything that isn't a plate is refused
    with a clear message.
  - All of them unfold exactly in the flat pattern (bend allowance from the K-factor) and export to DXF.

### 1.9.2 — stage 3 preview: assemblies
- **Standard parts library**, made on the spot from ISO size tables (no download, works offline), in the
  assembly's Insert panel: socket head cap screws (ISO 4762), countersunk screws (ISO 10642), hex head screws
  (ISO 4017), hex nuts (ISO 4032), washers (ISO 7089), dowel pins (ISO 8734), deep groove ball bearings (623 to
  6205) and heat-set inserts, M2 to M20, with sensible lengths per size and a quantity. Screws sit with the
  underside of the head on their origin and nuts / washers / bearings on theirs, so mates are quick. They show in
  the bill of materials by their proper names, and are saved in the assembly.
- **Exploded views**: the Explode button opens a panel: *Auto explode* moves every part out from the fixed one —
  screws come out towards their heads, and stacks (washer, then nut) come apart in order, each clear of the last —
  or add your own steps (select parts, a direction and a distance). A slider runs between assembled and
  exploded. Only the display moves; positions and mates don't change. *Drawing of this exploded view* makes a
  drawing of it, parts list and balloons included.
- **Relations** between mates: **Gear** (two revolute mates turn together by the ratio of their teeth, opposite
  ways or the same way for an internal gear), **Rack and pinion** (a revolute drives a slider: one turn moves
  2π × the pinion radius) and **Screw** (a cylindrical mate moves by its pitch per turn). They keep counting
  through any number of turns, take away a degree of freedom, and appear in the mates list (double-click to edit).
- **Instance patterns**: select parts and choose Linear pattern (along X, Y, Z or a mate's axis, count and
  spacing) or Circular pattern (round X, Y, Z or a revolute / cylindrical mate's axis, count and angle). The
  copies follow their originals when those move; double-click the pattern to change it; deleting a copy skips it,
  deleting an original removes its copies.

### Navigation and export (from the 2.0 list)
- **Binary STL**: exports are binary instead of text, about a fifth of the size for curved parts.
- **Wheel zoom** follows how far you scroll, at most one notch per wheel event, so high-resolution wheels and
  touchpads no longer zoom far too fast. **View > Zoom speed**: Slow, Normal, Fast.
- **View > Mouse style**: Onshape (the default, unchanged), Touchpad (two-finger scroll pans, pinch or
  Ctrl + scroll zooms, Alt-drag turns), and the FreeCAD-style CAD, Gesture, Blender, SolidWorks, OpenInventor,
  Revit and OpenSCAD. A click without moving still selects in every style. All bindings are one table in
  `freeshape/ui/navstyles.py`.
- **Ctrl+Shift+arrow** pans the view.
- **N** in the 3D view squares up to the flat face, plane or sketch under the mouse (or else the selected one)
  from the nearer side, turned so its edges run level and upright on screen; again for the other side. With
  nothing flat there it looks from the opposite side (Right
  becomes Left); over a curved face it does nothing. (Issue #13.)
- **Shift+E / Shift+W while sketching** finish the sketch and open Extrude / Revolve with its areas picked.
- **Open, Save as, Import and Export** dialogs start in the folder you used last.

### 1.9.1 — stage 2 preview: drawings and cutting
- **Section views**: click *Section view*, click two points across a view for the cutting line (it snaps level
  or upright; it can start just outside the view), then click beside it to place the section — you look from the
  side you place it on. The cut faces are hatched (alternating direction from one part to the next), the parent
  view gets the chain cutting line with thick ends, arrows and letters, and the view is titled *SECTION A-A*.
- **Detail views**: click the middle of an area, click again for the circle, then place the enlarged view (2×
  its parent). It shows everything the parent does inside the circle, hatching included, titled
  *DETAIL B (4:1)*. Deleting a view takes its sections and details with it.
- **Nest for cutting** (sheet metal toolbar, Document menu, or a sheet-metal part's right-click menu): set how
  many of each part, the stock sheet (presets or custom), gap, edge margin and kerf compensation, and see them
  laid out as you change things. Each part is turned to its tightest box; copies that interlock (triangles,
  L shapes) are paired 180° apart. Shows sheets, material use, part and sheet weight, optional cost and the
  offcut left, and exports every sheet in one DXF (layers SHEET, CUT, BEND, NOTES). Parts are filtered by
  thickness, since a sheet is one thickness.
- **Parts list and balloons** in drawings of an assembly (or of all the parts in a Part Studio): *Parts list*
  puts the bill of materials as a table on the title block (ITEM, PART, QTY); *Balloon* — click a part in a view,
  then where its number goes; *Auto balloon* numbers every part in a view, pointing at a spot you can actually
  see. Balloons stay attached to their part, and everything exports to DXF and PDF.
- Fixed: making a vertical sketch line horizontal (or the other way round) squashed it to a dot. The new
  constraint now replaces the old one and the line turns, keeping its length; H / V work on several lines at once.

### 1.9.0 — stage 1 preview: everyday modelling
- **Sketch splines**: click the points the curve passes through; double-click or Enter to finish, or click the
  first point again to close it into a loop. Its points are ordinary sketch points, so they can be dragged,
  dimensioned and constrained (fix, coincident, horizontal / vertical distance …).
- **Sketch ellipses**: click the centre, the end of the long axis, then the width. The long axis is a dashed
  construction line you can dimension or align; the Dimension tool on the ellipse sets half its width. Points can
  be put on an ellipse.
- **Sketch text**: click where it starts and type it; choose the font, size (mm), bold, italic and angle.
  Double-click text to edit it. Letters are real outlines, so they can be extruded, cut or engraved — the holes in
  letters such as "o" and "A" stay holes. Text can be dimensioned from its start point.
- **"Up to" end types for Extrude and Revolve**: *Up to next* (the first part in the way), *Up to face* (a face
  or plane, flat or curved), *Up to part* and *Up to vertex*. Face and vertex take *Stop short by* (negative goes
  past). Works for adding and cutting.
- **Feature patterns and mirror**: Linear pattern, Circular pattern and Mirror can now repeat *features* (a hole,
  a pocket, a boss) instead of whole parts: set *What to pattern* to Features and click them in the feature list.
  The copies follow when the original feature is edited.
- Test builds on GitHub now include **FreeShape-portable-<version>.zip** for Windows: unzip it and run
  FreeShape.exe, nothing to install.

## 1.8.5
- **Folders in the feature list**, like Onshape. Select a run of features (next to each other in the list) and
  right-click → *Add to new folder*, or click the new-folder button beside *Features*. A folder row shows its name
  and how many features it holds; click its arrow (or double-click it) to collapse or expand it. Right-click a
  folder to rename it, suppress / unsuppress everything in it, ungroup it (the features stay) or delete it with its
  features. Selecting a folder row selects its features. Drag a feature under a folder row or between two of its
  features to put it in; drag it elsewhere to take it out (or right-click → *Remove from folder*). The filter box
  also matches folder names. A folder holding the rollback bar, or the feature being edited, always shows open.
  Folders are saved in the file and are part of undo; grouping features never rebuilds the model.
- **Hovered faces are outlined, not filled**: the face (or part) under the mouse gets an orange outline of its
  edges, while selected faces stay filled, so the two can't be confused. Faces with no edges to outline (a sphere)
  are still filled.
- **Selection count beside the pointer**: a small badge next to the mouse shows how many things are selected, so
  you can check you have all five faces before starting Delete face.
- **Auto colour**: select several parts, right-click (in the Parts list or the 3D view) → *Auto colour N parts*.
  Each part gets a different colour; choose it again for a different set, or Undo. Right-click empty space for
  *Auto colour all parts*. Oranges and yellows are avoided, as those are the selection colours.
- **BREP export and import** (OpenCascade's native `.brep`): Document → *Export BREP…*, the export dialogs, and
  the Parts list menu. BREP stores the exact shapes as the kernel holds them, so nothing is translated or healed —
  the most faithful exchange with FreeCAD and other OpenCascade programs. It has no part names or colours. BREP
  files open like STEP files (a new tab, or *Import into this tab*), and assemblies can export BREP too.
- **Right-click acts on the whole selection**: with several parts selected (in the Parts list or the 3D view),
  right-clicking one of them hides, shows, isolates or exports them all. Several selected sketches / planes hide
  together too. Right-clicking something that isn't selected still acts on just that one.
- Fixed: hiding a part while a feature window (Split, Extrude …) was open left it in the view, catching clicks
  meant for the parts behind it.
- Fixed: small circles and holes on big parts were drawn as polygons (14 sides on a 5 mm hole in a 44 mm gear).
  Curved edges now always get at least ~63 segments per full circle.
- Fixed: the show / hide eyes disappeared off the right of the feature and parts lists when the side panel was
  narrow or a name was long. Long names are now shortened with "…" (hover for the full name).

### 1.8.4
- **Space bar** deselects everything, in the 3D view and in the sketcher.
- **Arrow keys turn the view** 15° per tap (Right, Left, Up, Down), animated. From the Right view, three taps
  give the edge between Right and Front, six taps give Front.
- **Search tools** (Ctrl+Space, Alt+C, or the "Search tools…" box in the top bar): type to filter tools and
  commands (words like "round" find Fillet), Up / Down to choose, a description on the right, Enter starts the
  tool, Esc closes.
- **Delete face** for imported (STEP) bodies: remove faces and **Heal** the gap, **Cap** it, or **Leave void open**.
  **Move face** pushes or pulls faces. **Offset surface** makes a new surface body a set distance from flat or
  curved faces.
- **3-point arc keeps its three points**: the third point is a real point on the arc instead of being turned
  into a two-point arc with a radius.
- **Sketch snapping / inference** (issue #1): hover a point or a line's midpoint, then move the cursor roughly
  level or plumb with it — a yellow dotted guide appears and the new point snaps in line, with a horizontal /
  vertical constraint added when you click. Hovering a line's midpoint snaps to it (midpoint constraint) and can
  be used as an alignment source too. Works for lines, rectangles, circles and points. **Tangent inference**:
  start a line at an arc's end and move along its tangent, or hover a circle / arc to snap to the point where the
  line touches it — a "T" guide appears and a tangent constraint is added.
- **Box selection** (issue #2), in the 3D view and in the sketcher: drag a box on empty space. Left-to-right (blue,
  solid) selects what is fully inside; right-to-left (green, dashed) also selects what it touches. Hold Shift or
  Ctrl to add to the selection.
- **Select menu** (issue #3) in the top bar: switch vertices, edges, faces, solids, sketches and planes on or off, for
  clicks and for box selection. **Vertices can now be selected** (off by default; tick Vertices in the menu).
- **Resizable feature windows** (issue #4): drag the right edge, bottom edge or corner of a feature or sketch
  panel. The width is remembered, and extra height goes to the selection lists.
- **Plane types** (issue #5): Offset, Plane + point, Line + angle, Point + edge normal, Three points, Mid plane,
  Curve + point and Tangent. The default *Automatic* picks the type from what is selected (three vertices → three
  point plane, a face and a vertex → plane through the point, two faces → mid plane …), and you can select first
  and press Plane. **Sketch does the same**: with a selection that defines a plane it creates that plane and starts
  the sketch on it. Existing planes open as Offset planes, unchanged.

### 1.8.3
- **Linux: fixed the 3D view breaking** on Wayland desktops (most modern Ubuntu / Fedora / KDE setups): the
  sketch and model vanished with "Attempt to retrieve context when no valid context". The OpenGL library picked
  Wayland's EGL while the window used X11's GLX; it now always matches the window.
- Extrude, Loft and other features that use sketch areas: **click a sketch in the feature list** to use it, like
  Onshape (also works for hidden sketches). For Loft, a sketch with several areas gives its outline (with any holes).
- Fillet and chamfer: a radius / distance too big for the edges is now an error that says the largest size that
  fits, instead of a broken, overlapping part (OpenCascade sometimes "succeeded" with an invalid result).

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
