# Advanced Sketch Inferencing & Smart Snapping

This extends the existing sketch snapping system and the existing constraint solver.

## Use
- Draw a point, line, rectangle, arc, or circle; hover over existing geometry.
- The gold symbol and label tell you which snap is active. Source edges are
  highlighted. Hover a source point, then move horizontally or vertically
  away from it to see a dotted guide. Two sources can form an X/Y intersection.
- Hold Shift to temporarily bypass snapping.

## Test cases
1. Point/line start at a line midpoint; edit the line and verify attachment.
2. Circle centre at a rectangle centre; resize rectangle.
3. Crossing lines, line/circle and circle/circle intersections, including rectangle helper diagonals.
4. Arc midpoint and quadrant; edit source geometry.
5. Horizontal/vertical alignment from previously hovered points.
6. Shift bypass; undo/redo and save/reopen.

## Scope / limitations
Analytic intersections are implemented for finite segments and circular
arcs/circles, not arbitrary splines or ellipses. Existing model-edge snapping
is retained; its fixed model points are not live external CAD references.
Rectangle centres are inferred for four connected straight edges forming a
rectangle. Some drawing stages that project onto a fixed radius or direction
still have tool-specific behavior; not every relation is inferred in every
tool.
The arc midpoint constraint extends the solver; avoid opening experimental
files containing this constraint in older FreeShape builds. Back up files.
Headless tests do not validate visual appearance or GPU interaction.

Helper diagonals participate in finite intersection candidates, so a point placed at a circle/diagonal crossing receives both point-on constraints. Rectangle discovery continues to ignore helper edges.
