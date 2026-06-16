# Floorplanner Enhancement Roadmap

Proposed additions to `tools/bdb_floorplanner.py` and its integration with the
BUDA routing pipeline (`buda_cli.py`, `buda_viz.py`, `NUTSResult`).  Items are
grouped by implementation effort and listed roughly in priority order within
each group.

---

## Quick wins — GUI only (hours each)

### 1. Undo / Redo
**Keybinding:** `Ctrl+Z` / `Ctrl+Y`

After every user action that mutates block positions (drag, resize, arrow-nudge,
optimize, align), snapshot the engine state into a bounded deque
(`collections.deque(maxlen=50)`).  On undo, pop the top snapshot and restore it
to `state.engine` via `resize_block_raw`.  On redo, push forward.

This is the single most-missed feature in any interactive placement editor.
A single accidental drag should never require re-running the optimizer.

**Key files:** `tools/bdb_floorplanner.py` only.  No C++ changes needed.

---

### 2. Live HPWL in the status bar

As the user drags a block, recompute and display total HPWL in the status bar
in real time.  `fpc.optimize_placement` already computes HPWL; a lightweight
standalone helper that reads `state.bdb.all_pins()` and current engine positions
can do the same without running the optimizer.

Immediate feedback on whether a manual move improves or worsens wire length.

**Key files:** `tools/floorplanner_commands.py` (add `compute_hpwl(state)`),
`tools/bdb_floorplanner.py` (call from `_on_motion` and `_on_release`).

---

### 3. Distribute evenly (H and V)

Natural complement to the four Align commands.  Select N blocks:
- **Distribute H** — fix leftmost and rightmost X positions; space the interior
  blocks at equal horizontal intervals.
- **Distribute V** — fix top and bottom Y positions; space interior blocks
  equally vertically.

Implementation follows the same pattern as `fpc.align_*`: a Python helper that
sorts blocks by position and reassigns coordinates, called from the Align menu
and via keybindings (e.g. `Ctrl+Shift+Left` / `Ctrl+Shift+Up`).

**Key files:** `tools/floorplanner_commands.py`, `tools/bdb_floorplanner.py`.

---

### 4. Convergence plot after optimization

The progress callback introduced for the progress bar already receives
`(current, total)` at each 5% milestone.  Extend it to also record the current
best cost (requires a small C++ change to pass cost alongside iteration count,
or sample it from the `OptimizerResult` at the end).

After the run, pop a small secondary matplotlib window showing:
- X axis: iteration / generation number
- Y axis: HPWL, overlap, and total cost as separate lines

Lets users compare SA vs GA convergence curves and tune weights empirically.

**Key files:** `src/placement_optimizer.h/cpp`, `src/bind_optimizer.cpp`,
`tools/bdb_floorplanner.py` (`_OptimizeDialog`).

---

## BUDA pipeline integration (medium effort — days each)

### 5. Congestion heatmap overlay

After `run_planner` has committed topology and layer assignments, map each
`GlobalCut`'s `band_usage / band_cap` ratio onto a colour grid drawn over the
die canvas:

- Green (≤ 50 % utilisation) → Yellow (50–80 %) → Red (> 80 % / overflow)
- Drawn as a semi-transparent `imshow` layer below the block patches
- Toggle on/off with a checkbox in the Canvas panel

This is the natural feedback loop between the floorplanner and the router: a
placement that looks fine geometrically may create routing hotspots that this
overlay immediately exposes.  The user can then move the offending blocks and
re-run the planner without leaving the GUI.

**Key files:** `tools/bdb_floorplanner.py` (new `_draw_congestion_overlay`),
`tools/floorplanner_commands.py` (expose planner cut data), possibly
`src/congestion_planner.h` (export band usage map).

---

### 6. Routing preview overlay

After `run_nuts`, draw the abstract `NUTSResult` track segments on top of the
floorplan canvas as thin coloured lines (one colour per layer), the same way
`buda_viz.py` does in `draw_nuts_tracks`.

The two tools already share the same BDB; `NUTSResult` can be passed in via a
shared Python object or re-loaded from BDB.  A toggle checkbox ("Show routing")
controls visibility.

Lets the user see how routing fills the space between blocks without switching
windows, and spot physical overlaps between routed segments and block boundaries.

**Key files:** `tools/bdb_floorplanner.py` (new `_draw_routing_overlay`),
`tools/buda_viz.py` (reuse `draw_nuts_tracks` logic or extract a shared helper).

---

### 7. "Run Flow" button

A single button (or keybinding `F5`) that triggers the full pipeline —
`run_bundler → generate_topologies → run_planner → run_nuts` — on the current
BDB, then refreshes the flylines and routing overlay without leaving the
floorplanner.

The `BudaSession` object from `buda_cli.py` can be instantiated as a library
(it already has no hard dependency on reading a script file).  Run the stages in
a background thread (same pattern as the Optimize dialog) so the GUI stays
responsive.

**Key files:** `src/buda_cli.py` (factor `BudaSession` into a reusable class),
`tools/bdb_floorplanner.py` (new `_run_flow` handler, already has a stub
button).

---

## Larger features (multiple days)

### 8. Snap-to-block-edge while dragging

When a dragged block's edge comes within one grid step of another block's edge,
snap to it automatically — the standard "smart snap" behaviour in layout editors.
Abutment placement (tiling blocks without gaps) becomes fast and precise.

Implementation: in `_on_motion`, after computing the raw new position, scan all
other visible blocks for edges within a snap threshold (e.g. `2 * grid`); snap
to the nearest qualifying edge.

**Key files:** `tools/bdb_floorplanner.py` (`_on_motion`).

---

### 9. Cross-link with `buda_viz`

Click a block in the floorplanner → highlight all its nets in an open `buda_viz`
routing window (and vice versa: click a routed segment in `buda_viz` →
highlight the source/sink blocks in the floorplanner).

Both tools already use the same BDB and can share a `selected_bundle_id`
variable via a small shared-state object or a simple `multiprocessing.Value`.

Turns the two tools into a coordinated pair for post-route analysis.

**Key files:** `tools/bdb_floorplanner.py`, `tools/buda_viz.py`,
new `tools/shared_selection.py`.

---

### 10. Interactive hierarchy building (Group into container)

Select N blocks on the canvas, right-click → **Group into container**.  Prompts
for a parent block name, creates it in the engine with a bounding box that
encloses all selected blocks, re-parents them as children, and refreshes the
tree.

The inverse — **Ungroup** — dissolves a container and promotes its children to
the parent scope.

Lets users build the BDB hierarchy interactively rather than scripting it, which
is particularly useful when importing a flat Verilog netlist and wanting to add
physical grouping on top.

**Key files:** `tools/bdb_floorplanner.py`, `tools/floorplanner_commands.py`
(new `group_blocks` / `ungroup_block` helpers), possibly
`src/floorplanner.h/cpp` (if grouping needs engine support).

---

## Summary table

| # | Feature | Effort | BUDA integration |
|---|---------|--------|-----------------|
| 1 | Undo / Redo | Low | No |
| 2 | Live HPWL in status bar | Low | Partial (BDB pins) |
| 3 | Distribute evenly H/V | Low | No |
| 4 | Convergence plot | Low–Medium | No |
| 5 | Congestion heatmap overlay | Medium | Yes — planner |
| 6 | Routing preview overlay | Medium | Yes — NUTS |
| 7 | "Run Flow" button | Medium | Yes — full pipeline |
| 8 | Snap-to-block-edge | Medium | No |
| 9 | Cross-link with buda_viz | Medium–High | Yes — viz |
| 10 | Interactive hierarchy building | High | Partial |
