# BUDA Floorplanner User Guide

The BUDA floorplanner (`./fp`) is an interactive placement editor for chip
floorplanning.  It combines a tkinter/matplotlib canvas with the C++
`FloorplannerEngine` and BDB for placement, and can drive the full hierarchical
bundle flow directly from the GUI.

---

## Launch

```bash
./fp                        # open with a blank canvas
./fp path/to/design.bdb    # open an existing BDB
```

The wrapper sets `PYTHONPATH` for `build/` and `tools/` automatically.

---

## Starting a New Floorplan

### From a Verilog Netlist

1. Click **Import Verilog**.
2. Select a `.v` or `.sv` file.
3. Choose the output `.bdb` path.
4. Top-level instances are seeded as editable placeholder blocks at default
   sizes; depth-1 children are seeded inside their parent block in a grid layout.

Verilog contains hierarchy and connectivity but no physical size or placement.
Treat the initial layout as a rough starting point and refine with drag, resize,
and Optimize.

### From an Existing BDB

1. Click **Open**.
2. Select a `.bdb` file.
3. All placed components are loaded into the engine and shown on the canvas.

### From Scratch

1. Set **Die W**, **Die H**, and **Grid** in the Canvas panel.
2. Click **New** and choose a `.bdb` path.
3. Click **Add** to create blocks one at a time.

---

## Canvas Panel

Located in the left sidebar.

| Field | Purpose |
|---|---|
| **Die W / Die H** | Die dimensions in layout units (microns) |
| **Grid** | Snap-to-grid resolution; all moves snap to this pitch |
| **Step** | Arrow-key nudge distance |
| **Apply** | Commit Die W / Die H / Grid changes to the engine |
| **Overlay −/+** | Show 0–4 extra child-hierarchy levels as a faint orange overlay |

---

## Blocks Panel

### Adding Blocks

Click **Add**:

- At top level: enter an instance path (e.g. `u_cpu`), X/Y origin, width, and
  height.
- Drilled into a parent: enter a short leaf name (e.g. `core0`); the full path
  (`u_cpu/core0`) and the absolute origin are computed automatically.

### Hierarchy Tree

The tree on the left shows all blocks at the current drill level.  Double-click
any block to drill into it and view its children.  The breadcrumb bar at the top
shows the current path; click any crumb or **[top]** to navigate back.

### Selecting Blocks

| Action | Effect |
|---|---|
| Click block on canvas | Single-select; enables drag and corner-resize |
| Shift+Click | Toggle that block in/out of the multi-selection |
| Click tree row | Single-select |
| Ctrl/Shift-click tree rows | Multi-select in tree |
| Click empty canvas | Clear all selection |

The **Selection** panel below the tree shows coordinates and dimensions of the
primary selected block, and a "N blocks selected." count when more than one is
active.

### Shared (Replicated) Instances

When multiple blocks share the same cell type, a **Make Unique** button appears.
Clicking it creates a private cell definition so resizing that block no longer
affects its siblings.

---

## Moving and Resizing

### Drag to Move

Click and drag any block on the canvas.  The origin snaps to the active grid.
When multiple blocks are selected, dragging the primary moves only that block;
use arrow keys to move the entire selection together.  Dragging a block that
contains children carries the whole sub-hierarchy with it (see
[Editing Hierarchical Blocks](#editing-hierarchical-blocks)).

### Arrow-Key Nudge

With one or more blocks selected, press the arrow keys to move them by the
**Step** value.  Step defaults to 10 and can be changed in the Canvas panel.
Arrow keys are ignored when a text entry or spinbox has keyboard focus.

| Key | Direction |
|---|---|
| `↑` | Move up (+Y) |
| `↓` | Move down (−Y) |
| `←` | Move left (−X) |
| `→` | Move right (+X) |

### Corner-Handle Resize

Select a block (single-select).  Drag any of the four corner handles to resize
it.  Resizing a shared-cell block updates all sibling instances; use **Make
Unique** first to resize independently.

---

## Editing Hierarchical Blocks

A block may contain a sub-hierarchy of child blocks — for example a cell
instance and the blocks placed inside it (`chip/i_dnuts1_0` contains
`chip/i_dnuts1_0/u0`, …).  The editing commands fall into two groups that treat
children differently:

- **Relocations carry their children.**  Moving a block — by **drag**,
  **arrow-key nudge**, **Align**, **Center**, **Distribute**, **Rotate**, or the
  **placement optimizer** — translates (or rotates) the entire sub-hierarchy with
  it, so a parent and all of its descendants move together as a unit.
- **Resizes keep their children in place.**  Changing a block's size — by a
  **corner** or **mid-edge handle**, an **edge move / align**, or a reshape
  during optimization — grows or shrinks the block *around* its contents.  The
  children keep their absolute positions, exactly as resizing a container window
  does not move what is inside it.  A child that ends up outside its parent is
  reported by **Validate**.

When a selection contains both a parent and one of its own descendants, the
descendant is moved only once (it follows the parent) — never twice.

---

## Undo and Redo

| Linux / Windows | macOS | Action |
|---|---|---|
| `Ctrl+Z` | `Cmd+Z` | Undo the last placement action |
| `Ctrl+Y` / `Ctrl+Shift+Z` | `Cmd+Shift+Z` | Redo |

The undo history holds up to 50 snapshots.  Every mutating action creates a
snapshot: drag, resize, arrow-nudge, align, distribute, and optimize.  Cancelling
the Optimize dialog does **not** create a snapshot.

---

## Align and Distribute

Select two or more blocks, then open the **Align ▾** menu or use a keybinding.

### Align Commands

Aligns all selected blocks to the edge of the block furthest in that direction.
`Ctrl+Arrow` is intercepted by macOS (Mission Control / Spaces); use `Cmd+Arrow`
there instead.

| Menu item | Linux/Windows | macOS | Effect |
|---|---|---|---|
| Top ↑ | `Ctrl+↑` | `Cmd+↑` | Align top edges |
| Bottom ↓ | `Ctrl+↓` | `Cmd+↓` | Align bottom edges |
| Left ← | `Ctrl+←` | `Cmd+←` | Align left edges |
| Right → | `Ctrl+→` | `Cmd+→` | Align right edges |

### Distribute Commands

Requires three or more blocks.  The two outermost blocks stay fixed; interior
blocks are spaced with equal gaps.

| Menu item | Linux/Windows | macOS | Effect |
|---|---|---|---|
| Distribute H | `Ctrl+Shift+←/→` | `Cmd+Shift+←/→` | Equal horizontal spacing |
| Distribute V | `Ctrl+Shift+↑/↓` | `Cmd+Shift+↑/↓` | Equal vertical spacing |

All align and distribute operations are undo-able.

---

## Placement Optimizer

Click **Optimize…** to open the optimizer dialog.

### Algorithm

| Option | Description |
|---|---|
| **SA** | Simulated Annealing — good for large move diversity; set Iterations (default 20 000) |
| **GA** | Genetic Algorithm — good for exploring many placements; set Generations (default 200) |

### Weights

| Weight | Controls |
|---|---|
| **Wire-length** | HPWL contribution (higher = pack connected blocks closer) |
| **Area** | Bounding-box compaction |
| **Overlap** | Penalty for block-to-block overlap (should be highest) |

### Block Constraints

Each root-level block has three constraint columns:

| Column | Effect |
|---|---|
| **Fixed** | Block is never moved; position is anchored |
| **Reshapeable** | The optimizer may change w/h (area = w×h stays constant) |
| **Min Width / Min Height** | Lower bound on dimensions when Reshapeable is checked |

Settings persist across dialog opens within a session.  A progress bar shows
5%-step milestones; the GUI stays responsive during the run.  Click **Cancel** at
any time — the block positions are not changed if the dialog is cancelled.

After a successful run, HPWL, overlap, and iteration count are shown in the
status bar.

---

## Live HPWL

The **Validation** panel shows a continuously updated HPWL (half-perimeter
wirelength) in blue.  It is recomputed from BDB pin connectivity and live engine
positions on every draw, including during drag.  A lower HPWL means connected
blocks are closer together.

HPWL is shown only when a BDB with net/pin data is loaded.

---

## Flylines

When a single block is selected, dashed flylines connect it to every other block
it shares nets with.  A number near the midpoint shows how many distinct nets
connect the two blocks.

Flylines use the live engine positions (updated by the optimizer and drag) and
BDB connectivity (which never changes during a session).

---

## Validation

Click **Validate** to check the current layout.

| Issue kind | Meaning |
|---|---|
| `OVERLAP` | Two blocks intersect |
| `OUTSIDE_DIE` | A block extends beyond the die boundary |
| `ERROR` | Engine reports an invalid state (e.g. zero die dimensions) |

Up to five issues are shown; the status bar reports the total count.

---

## Saving and Exporting

### Write to BDB

Click **Write** to persist current block positions to the open BDB.

### Export HBundle Flow Script

Click **Export Flow** to generate a `.buda` script for the current floorplan.
The script includes `run_hier_bundler`, `generate_hier_topologies`,
`run_planner hier`, `run_nuts`, and `visualize` commands ready to run with
`./buda`.

### Run HBundle Flow

Click **Run Flow** to:

1. Write current placements to BDB.
2. Export a temporary `.buda` script.
3. Execute `buda_cli.py <script> --no-viz`.
4. Report completion or failure in the status bar.

This is the fastest feedback path for checking whether the current floorplan
produces valid HBundle topologies and NUTS track assignments.

---

## Recommended Workflow

1. `./fp` — launch.
2. **Import Verilog** or **New** — create a BDB.
3. Set die and grid in the Canvas panel → **Apply**.
4. Place top-level blocks by dragging, nudging, or running **Optimize…**.
5. Use **Align ▾** to clean up edges; **Ctrl+Z** to undo mistakes.
6. Drill into containers to place sub-blocks.
7. Check the live **HPWL** label to gauge placement quality.
8. **Validate** to confirm no overlaps or out-of-die blocks.
9. **Write** to persist placements.
10. **Run Flow** to run the HBundle pipeline and get routing feedback.
