# Icon names

Put an `.svg` or `.png` with one of these names in your icons folder (View ▸ Icons ▸ *Open my icons folder…*) and
it replaces FreeShape's built-in icon, on the toolbars and in the feature list. Then View ▸ Icons ▸ *Reload icons*.
Delete the file to get the built-in icon back.

- **SVG** is best: it stays sharp at any size. Draw on a 32 × 32 canvas.
- **PNG**: 64 × 64 with a transparent background looks right on high-resolution screens too.
- View ▸ Icons ▸ *Save the built-in icons there* writes every built-in icon as a PNG with its name (and a
  `names.txt`) into a `built-in` folder, to see what each one is or to start from.
- A plugin feature's icon is `plugin_<id>`, e.g. `plugin_gridfinity.svg`, `plugin_timing_pulley.svg`.

**Part Studio features:** `boolean`, `bounding_box`, `chamfer`, `circular_pattern`, `compound`, `delete_face`, `delete_part`, `derived`, `draft`, `enclose`, `explode_part`, `extrude`, `fillet`, `helix`, `hole`, `import`, `linear_pattern`, `loft`, `measure_value`, `mirror`, `move`, `move_face`, `offset_surface`, `plane`, `revolve`, `rib`, `shell`, `sketch`, `sm_base`, `sm_convert`, `sm_flange`, `sm_hem`, `sm_jog`, `sm_tab`, `split`, `sweep`, `thicken`, `thread`, `variable`

**Sketch tools and constraints:** `arc3`, `arc_center`, `center_rect`, `circle`, `coincident`, `concentric`, `construction`, `dim`, `dimension`, `ellipse`, `equal`, `fix`, `hexagon`, `horizontal`, `line`, `midpoint`, `normal_to`, `offset`, `parallel`, `perpendicular`, `point`, `rect`, `sk_cpattern`, `sk_fillet`, `sk_lpattern`, `sk_mirror`, `sk_text`, `slot`, `spline`, `symmetric`, `tangent`, `trim`, `use`, `vertical`

**Assembly:** `assembly`, `bom`, `explode`, `insert`, `interference`, `m_ball`, `m_cylindrical`, `m_fastened`, `m_planar`, `m_revolute`, `m_slider`, `relation`

**Sheet metal:** `flat`, `flat_dxf`, `nest`

**Drawings:** `auto_balloon`, `balloon`, `base_view`, `detail_view`, `drawing`, `dxf`, `note`, `pdf`, `proj_view`, `section_view`, `sheet`

**Everything else:** `configs`, `delete`, `error`, `export`, `fit`, `folder`, `history`, `iso`, `iso_view`, `new_folder`, `origin`, `part`, `plugin`, `redo`, `search`, `section`, `undo`
