# Feature parity

Generated from `freeshape/parity.py` — edit that file, then run `python -m freeshape.parity`.

Following [issue #27](https://github.com/richiewillmott/FreeShape/issues/27): features are finished option by option, and any option more than one feature has is built **once** as a shared tool (below) and reused, so it behaves the same everywhere. Options not built yet are listed too.

**Overall: 92%** of the listed options (partial counts half).

✅ done · 🟡 partial · ⬜ to do

## Features

| Feature | Parity |
|---|---|
| [Extrude](#extrude) | 100% |
| [Revolve](#revolve) | 100% |
| [Sweep](#sweep) | 96% |
| [Loft](#loft) | 82% |
| [Fillet](#fillet) | 70% |
| [Chamfer](#chamfer) | 100% |
| [Shell](#shell) | 100% |
| [Draft](#draft) | 80% |
| [Hole](#hole) | 100% |
| [Linear pattern](#linear-pattern) | 100% |
| [Circular pattern](#circular-pattern) | 100% |
| [Curve pattern](#curve-pattern) | 100% |
| [Point pattern](#point-pattern) | 100% |
| [Transform](#transform) | 100% |
| [Mate connector](#mate-connector) | 75% |
| [Mirror](#mirror) | 100% |
| [Thicken](#thicken) | 92% |
| [Enclose](#enclose) | 100% |
| [Fill](#fill) | 58% |
| [Constrained surface](#constrained-surface) | 75% |
| [Rib](#rib) | 64% |
| [Helix](#helix) | 83% |
| [Delete part](#delete-part) | 100% |
| [Boolean](#boolean) | 100% |
| [Split](#split) | 100% |

### Extrude

100% · shared tools used: Blind / symmetric ends, Creation type, Draft / taper, Merge scope, Merge with the model, Opposite direction, Profile picking, Second end, Smart selection, Surface New / Add, Symmetric second end, Thin wall, Through all, Up to next / face / part / vertex

- ✅ Solid *(shared: Creation type)*
- ✅ Surface *(shared: Creation type)* — from sketch curves, edges or region outlines
- ✅ Thin *(shared: Thin wall)* — both sides equally, or one side
- ✅ Thin — two different thicknesses *(shared: Thin wall)*
- ✅ Faces and sketch regions *(shared: Profile picking)*
- ✅ Sketch curves and edges *(shared: Profile picking)* — for Surface and Thin
- ✅ Edges picked first make a surface; Solid ⇄ Thin / Surface keeps the selection *(shared: Smart selection)*
- ✅ Surface: New / Add *(shared: Surface New / Add)*
- ✅ Blind *(shared: Blind / symmetric ends)*
- ✅ Symmetric *(shared: Blind / symmetric ends)*
- ✅ Through all *(shared: Through all)*
- ✅ Up to next / face / part / vertex *(shared: Up to next / face / part / vertex)* — Solid, Thin and Surface
- ✅ Offset from the up-to face *(shared: Up to next / face / part / vertex)* — 'Stop short by'
- ✅ Opposite direction *(shared: Opposite direction)*
- ✅ Draft *(shared: Draft / taper)* — Blind, Symmetric and Through all
- ✅ Second end position *(shared: Second end)* — Blind, Through all or any Up to
- ✅ Second end draft *(shared: Draft / taper)* — its own angle
- ✅ Second end: Symmetric (mirror of the first end, any type) *(shared: Symmetric second end)*
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Revolve

100% · shared tools used: Axis / direction picking, Creation type, Merge scope, Merge with the model, Opposite direction, Profile picking, Smart selection, Surface New / Add, Symmetric second end, Thin wall, Two directions, Up to next / face / part / vertex

- ✅ Solid *(shared: Creation type)*
- ✅ Surface *(shared: Creation type)*
- ✅ Thin *(shared: Thin wall)*
- ✅ Faces and sketch regions *(shared: Profile picking)*
- ✅ Sketch curves and edges *(shared: Profile picking)* — for Surface and Thin
- ✅ Edges picked first make a surface; Solid ⇄ Thin / Surface keeps the selection *(shared: Smart selection)*
- ✅ Surface: New / Add *(shared: Surface New / Add)*
- ✅ Revolve axis *(shared: Axis / direction picking)*
- ✅ Full
- ✅ One direction
- ✅ Symmetric
- ✅ Two directions (second angle) *(shared: Two directions)*
- ✅ Up to next / face / part / vertex *(shared: Up to next / face / part / vertex)* — Solid, Thin and Surface
- ✅ Up to … mirrored both ways (symmetric) *(shared: Symmetric second end)*
- ✅ Opposite direction *(shared: Opposite direction)*
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Sweep

96% · shared tools used: Creation type, Guide curves, Merge scope, Merge with the model, Profile control, Profile picking, Smart selection, Surface New / Add, Thin wall

- ✅ Solid *(shared: Creation type)*
- ✅ Surface *(shared: Creation type)*
- ✅ Thin *(shared: Thin wall)*
- ✅ Faces and sketch regions *(shared: Profile picking)*
- ✅ Sketch curves and edges *(shared: Profile picking)* — for Surface and Thin
- ✅ Edges picked first make a surface; Solid ⇄ Thin / Surface keeps the selection *(shared: Smart selection)*
- ✅ Surface: New / Add *(shared: Surface New / Add)*
- ✅ Path
- ✅ Profile control *(shared: Profile control)* — normal to path, keep orientation, lock to a direction or to faces
- 🟡 Guide curves *(shared: Guide curves)* — one guide curve
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Loft

82% · shared tools used: Creation type, Guide curves, Loft along a path, Merge scope, Merge with the model, Profile picking, Start / end tangency, Surface New / Add, Thin wall

- ✅ Solid *(shared: Creation type)*
- ✅ Surface *(shared: Creation type)*
- ✅ Thin *(shared: Thin wall)*
- ✅ Profiles in order *(shared: Profile picking)* — regions, faces and sketch curves
- ✅ Surface: New / Add *(shared: Surface New / Add)*
- ✅ Ruled (straight) sides
- ⬜ Guide curves *(shared: Guide curves)* — OpenCascade's loft has no guide curves — needs a different surfacing approach
- ⬜ Start / end conditions *(shared: Start / end tangency)* — OpenCascade's loft has no tangency control
- ✅ Path *(shared: Loft along a path)*
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Fillet

70% · shared tools used: Tangent chains

- ✅ Edges and faces
- ✅ Constant radius
- ✅ Tangent propagation *(shared: Tangent chains)*
- ⬜ Conic / curvature cross-section — OpenCascade fillets are circular-section only
- 🟡 Variable radius — start and end radii (either can be an expression); Onshape also allows radii at points

### Chamfer

100% · shared tools used: Tangent chains

- ✅ Edges and faces
- ✅ Equal distance
- ✅ Two distances — with Flip sides
- ✅ Distance and angle
- ✅ Tangent propagation *(shared: Tangent chains)* — a checkbox: off bevels just the picked edges

### Shell

100% · shared tools used: none

- ✅ Faces to remove
- ✅ Thickness
- ✅ Shell outward

### Draft

80% · shared tools used: Draft / taper, Tangent chains

- ✅ Faces to draft
- ✅ Neutral plane *(shared: Draft / taper)*
- ✅ Pull direction / flip *(shared: Draft / taper)*
- ✅ Tangent propagation *(shared: Tangent chains)*
- ⬜ Parting line draft — a substantial feature of its own

### Hole

100% · shared tools used: Blind / symmetric ends, Up to next / face / part / vertex

- ✅ Simple / counterbore / countersink
- ✅ ISO clearance and tapped sizes
- ✅ Modelled thread
- ✅ Through all / blind *(shared: Blind / symmetric ends)*
- ✅ Up to next / face / vertex *(shared: Up to next / face / part / vertex)* — with 'Stop short by'
- ✅ Drill point

### Linear pattern

100% · shared tools used: Axis / direction picking, Merge scope, Merge with the model, Pattern / mirror faces, Two directions

- ✅ Parts
- ✅ Features
- ✅ Faces *(shared: Pattern / mirror faces)*
- ✅ Direction *(shared: Axis / direction picking)*
- ✅ Second direction *(shared: Two directions)*
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Circular pattern

100% · shared tools used: Axis / direction picking, Merge scope, Merge with the model, Pattern / mirror faces

- ✅ Parts
- ✅ Features
- ✅ Faces *(shared: Pattern / mirror faces)*
- ✅ Axis *(shared: Axis / direction picking)*
- ✅ Angle and count
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Curve pattern

100% · shared tools used: Merge scope, Merge with the model, Pattern / mirror faces

- ✅ Parts
- ✅ Features
- ✅ Faces *(shared: Pattern / mirror faces)*
- ✅ Path (sketch curves or edges)
- ✅ Count, spread or distance apart
- ✅ Turn with the path / keep orientation
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Point pattern

100% · shared tools used: Merge scope, Merge with the model, Pattern / mirror faces

- ✅ Parts
- ✅ Features
- ✅ Faces *(shared: Pattern / mirror faces)*
- ✅ Locations (vertices, sketch points)
- ✅ Reference point — optional: else the middle of what's patterned
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Transform

100% · shared tools used: Axis / direction picking

- ✅ Translate by line *(shared: Axis / direction picking)* — by the line's own length
- ✅ Translate by distance *(shared: Axis / direction picking)*
- ✅ Translate by XYZ
- ✅ Transform by mate connectors
- ✅ Rotate *(shared: Axis / direction picking)*
- ✅ Copy in place
- ✅ Scale — uniform, about a point or the origin; a scaled sheet metal part becomes a plain part
- ✅ Copy part — 'Keep the original'; sheet metal parts moved, turned or copied stay sheet metal
- ✅ Translate from point to point — FreeShape extra

### Mate connector

75% · shared tools used: Opposite direction

- ✅ On a face, edge or vertex
- ✅ Points of the face to choose from (Shift) — middle, corners, edge middles, round-edge centres; a round face's axis ends
- ✅ Flip primary axis *(shared: Opposite direction)*
- ✅ Rotate about Z
- ✅ Move along X / Y / Z
- ✅ Used by Transform
- ⬜ Used in assemblies — assemblies make their own from what you click
- ⬜ Owner part / Point pattern of mate connectors

### Mirror

100% · shared tools used: Merge scope, Merge with the model, Pattern / mirror faces

- ✅ Parts
- ✅ Features
- ✅ Faces *(shared: Pattern / mirror faces)*
- ✅ Mirror plane
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Thicken

92% · shared tools used: Merge scope, Merge with the model, Thicken

- ✅ Surfaces and faces
- ✅ One side / other side / symmetric *(shared: Thicken)*
- ✅ Two thicknesses *(shared: Thicken)*
- 🟡 Neighbouring faces joined, outside corners filled *(shared: Thicken)* — flat faces; curved faces are joined but their corners aren't filled
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Enclose

100% · shared tools used: Merge scope, Merge with the model

- ✅ Surfaces, faces and planes — they may cross each other; the closed-off space is kept
- ✅ Keep tools
- ✅ New / Add / Remove / Intersect *(shared: Merge with the model)*
- ✅ Merge scope *(shared: Merge scope)*

### Fill

58% · shared tools used: Surface bodies

- ✅ Boundary: edges and sketch curves — one closed loop
- 🟡 Position / tangent continuity — one choice for the whole boundary (Onshape sets it edge by edge); tangent to surface bodies' edges
- ⬜ Curvature continuity — OpenCascade's filling stops at tangent
- ✅ Guide points
- ⬜ Guide curves
- ✅ Surface: New / Add *(shared: Surface bodies)*

### Constrained surface

75% · shared tools used: Surface bodies

- ✅ Points (vertices, sketch points)
- ✅ Parts (all their vertices) — dense ones are sampled (2000 points)
- ⬜ Mesh (STL / OBJ) — FreeShape doesn't import meshes yet; a point cloud or scan imported as STEP / BREP works through its vertices
- ✅ Tolerance — an error when the fit can't keep to it
- 🟡 Trimmed to the points' outline — trimmed to a rectangle in the surface's own (u, v)
- ✅ Surface: New / Add *(shared: Surface bodies)*

### Rib

64% · shared tools used: Draft / taper, Opposite direction

- 🟡 Sketch lines — straight lines; arcs and splines not yet
- ✅ Thickness
- ✅ Parallel / normal to the sketch plane
- ✅ Opposite direction *(shared: Opposite direction)*
- ✅ Parts to grow out to
- ⬜ Extend profiles up to part — the lines must reach the part already
- ⬜ Draft *(shared: Draft / taper)*

### Helix

83% · shared tools used: Axis / direction picking, Opposite direction

- ✅ Around an axis, circle edge or cylinder *(shared: Axis / direction picking)*
- ✅ Turns and pitch / height and turns / height and pitch
- ✅ Start angle
- ✅ Clockwise / anticlockwise
- ✅ Opposite direction *(shared: Opposite direction)*
- ⬜ Around a cone

### Delete part

100% · shared tools used: none

- ✅ Parts

### Boolean

100% · shared tools used: none

- ✅ Union / subtract / intersect
- ✅ Keep tools
- ✅ Subtract complement — FreeShape's Intersect: keeps only the targets' material inside the tools

### Split

100% · shared tools used: none

- ✅ By plane
- ✅ By face (flat or curved)
- ✅ By sketch curve
- ✅ Keep both sides or one

## Shared tools

Each is built once and used by every feature listed under it. A tool marked *to build* is needed by the options shown, and should be built as a shared tool when the first of them is done.

- **Profile picking** — Sketch regions and flat faces as profiles; sketch curves and edges for surfaces / thin walls. `freeshape.body_options:profiles`
  - ✅ Extrude: Faces and sketch regions
  - ✅ Extrude: Sketch curves and edges
  - ✅ Revolve: Faces and sketch regions
  - ✅ Revolve: Sketch curves and edges
  - ✅ Sweep: Faces and sketch regions
  - ✅ Sweep: Sketch curves and edges
  - ✅ Loft: Profiles in order
- **Creation type** — Solid / Surface / Thin from the same profiles. `freeshape.body_options:profiles`
  - ✅ Extrude: Solid
  - ✅ Extrude: Surface
  - ✅ Revolve: Solid
  - ✅ Revolve: Surface
  - ✅ Sweep: Solid
  - ✅ Sweep: Surface
  - ✅ Loft: Solid
  - ✅ Loft: Surface
- **Thin wall** — Thicken curves or region outlines into a wall: one thickness either side or split, or two different thicknesses. `freeshape.body_options:thin_faces`
  - ✅ Extrude: Thin
  - ✅ Extrude: Thin — two different thicknesses
  - ✅ Revolve: Thin
  - ✅ Sweep: Thin
  - ✅ Loft: Thin
- **Surface bodies** — Open surfaces become their own surface bodies. `freeshape.body_options:apply`
  - ✅ Fill: Surface: New / Add
  - ✅ Constrained surface: Surface: New / Add
- **Merge with the model** — New / Add / Remove / Intersect. `freeshape.features:apply_body`
  - ✅ Extrude: New / Add / Remove / Intersect
  - ✅ Revolve: New / Add / Remove / Intersect
  - ✅ Sweep: New / Add / Remove / Intersect
  - ✅ Loft: New / Add / Remove / Intersect
  - ✅ Linear pattern: New / Add / Remove / Intersect
  - ✅ Circular pattern: New / Add / Remove / Intersect
  - ✅ Curve pattern: New / Add / Remove / Intersect
  - ✅ Point pattern: New / Add / Remove / Intersect
  - ✅ Mirror: New / Add / Remove / Intersect
  - ✅ Thicken: New / Add / Remove / Intersect
  - ✅ Enclose: New / Add / Remove / Intersect
- **Merge scope** — Choose which parts Add / Remove / Intersect act on. `freeshape.ui.dialogs:_scope_field`
  - ✅ Extrude: Merge scope
  - ✅ Revolve: Merge scope
  - ✅ Sweep: Merge scope
  - ✅ Loft: Merge scope
  - ✅ Linear pattern: Merge scope
  - ✅ Circular pattern: Merge scope
  - ✅ Curve pattern: Merge scope
  - ✅ Point pattern: Merge scope
  - ✅ Mirror: Merge scope
  - ✅ Thicken: Merge scope
  - ✅ Enclose: Merge scope
- **Blind / symmetric ends** — A set distance, one way or both ways equally. `freeshape.kernel:extrude`
  - ✅ Extrude: Blind
  - ✅ Extrude: Symmetric
  - ✅ Hole: Through all / blind
- **Through all** — Far enough to go through every part. `freeshape.features:run_extrude`
  - ✅ Extrude: Through all
- **Up to next / face / part / vertex** — Stop at geometry, optionally short of it by an offset. `freeshape.features:_sweep_up_to`
  - ✅ Extrude: Up to next / face / part / vertex
  - ✅ Extrude: Offset from the up-to face
  - ✅ Revolve: Up to next / face / part / vertex
  - ✅ Hole: Up to next / face / vertex
- **Second end** — A second, independent end the other way, with any end type and its own draft. `freeshape.features:run_extrude`
  - ✅ Extrude: Second end position
- **Opposite direction** — Reverse the direction. `freeshape.features:run_extrude`
  - ✅ Extrude: Opposite direction
  - ✅ Revolve: Opposite direction
  - ✅ Mate connector: Flip primary axis
  - ✅ Rib: Opposite direction
  - ✅ Helix: Opposite direction
- **Draft / taper** — Lean faces by an angle about a neutral plane. `freeshape.body_options:taper`
  - ✅ Extrude: Draft
  - ✅ Extrude: Second end draft
  - ✅ Draft: Neutral plane
  - ✅ Draft: Pull direction / flip
  - ⬜ Rib: Draft
- **Axis / direction picking** — Sketch line, edge or world axis as an axis or direction. `freeshape.features:resolve_axis`
  - ✅ Revolve: Revolve axis
  - ✅ Linear pattern: Direction
  - ✅ Circular pattern: Axis
  - ✅ Transform: Translate by line
  - ✅ Transform: Translate by distance
  - ✅ Transform: Rotate
  - ✅ Helix: Around an axis, circle edge or cylinder
- **Tangent chains** — Pick one edge or face, get the smooth chain it belongs to (OpenCascade carries fillets, chamfers and drafts along it). `freeshape.kernel:fillet`
  - ✅ Fillet: Tangent propagation
  - ✅ Chamfer: Tangent propagation
  - ✅ Draft: Tangent propagation
- **Pattern / mirror faces** — Repeat or mirror the boss / pocket some faces make (found by healing them away, as Delete face does). `freeshape.features:face_feature_bodies`
  - ✅ Linear pattern: Faces
  - ✅ Circular pattern: Faces
  - ✅ Curve pattern: Faces
  - ✅ Point pattern: Faces
  - ✅ Mirror: Faces
- **Two directions** — A second direction (patterns) or second angle (revolve). `freeshape.features:run_linear_pattern`
  - ✅ Revolve: Two directions (second angle)
  - ✅ Linear pattern: Second direction
- **Guide curves** — Extra curves a loft or sweep must follow. `freeshape.kernel:_pipe_shell`
  - 🟡 Sweep: Guide curves
  - ⬜ Loft: Guide curves
- **Start / end tangency** — Make a loft or sweep start/end normal or tangent to a face. *to build*
  - ⬜ Loft: Start / end conditions
- **Loft along a path** — Sections placed along a path curve. `freeshape.kernel:loft_wires`
  - ✅ Loft: Path
- **Surface New / Add** — A new surface body, or sewn into the surfaces it meets. `freeshape.body_options:_sew_into_surfaces`
  - ✅ Extrude: Surface: New / Add
  - ✅ Revolve: Surface: New / Add
  - ✅ Sweep: Surface: New / Add
  - ✅ Loft: Surface: New / Add
- **Smart selection** — Edges picked first make a surface; switching Solid ⇄ Thin / Surface turns the faces into their outlines and back. `freeshape.ui.dialogs:FeatureDialog`
  - ✅ Extrude: Edges picked first make a surface; Solid ⇄ Thin / Surface keeps the selection
  - ✅ Revolve: Edges picked first make a surface; Solid ⇄ Thin / Surface keeps the selection
  - ✅ Sweep: Edges picked first make a surface; Solid ⇄ Thin / Surface keeps the selection
- **Symmetric second end** — The other side is the first end mirrored over the sketch plane, whatever its end type. `freeshape.features:run_extrude`
  - ✅ Extrude: Second end: Symmetric (mirror of the first end, any type)
  - ✅ Revolve: Up to … mirrored both ways (symmetric)
- **Thicken** — A solid from a face or surface: one side, the other, centred or two thicknesses. `freeshape.more_features:thicken_shape`
  - ✅ Thicken: One side / other side / symmetric
  - ✅ Thicken: Two thicknesses
  - 🟡 Thicken: Neighbouring faces joined, outside corners filled
- **Profile control** — How a swept profile turns along the path (keep orientation, lock to a direction or faces, follow a guide). `freeshape.kernel:_pipe_shell`
  - ✅ Sweep: Profile control
