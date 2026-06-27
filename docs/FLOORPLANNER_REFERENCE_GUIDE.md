# BUDA Floorplanner Reference Guide

Complete reference for all keybindings, UI controls, menu items, dialogs, and
Python command-layer functions in `tools/bdb_floorplanner.py` and
`tools/floorplanner_commands.py`.

---

## Keybindings

### Navigation and Selection

| Key | Action |
|---|---|
| Click block | Single-select; start drag |
| Shift+Click block | Toggle block in/out of multi-selection |
| Double-click block | Drill into block (push to breadcrumb stack) |
| Click empty canvas | Clear all selection |

### Nudge (Arrow Keys)

Active when a block or multi-selection exists and no text widget has keyboard focus.
Step size is set by the **Step** spinbox in the Canvas panel.

| Key | Direction |
|---|---|
| `↑` | Move selected block(s) up (+Y) |
| `↓` | Move selected block(s) down (−Y) |
| `←` | Move selected block(s) left (−X) |
| `→` | Move selected block(s) right (+X) |

### Align (Ctrl+Arrow or Cmd+Arrow on macOS)

Requires two or more blocks selected.  `Ctrl+Arrow` is intercepted by macOS
(Mission Control / Spaces), so `Cmd+Arrow` is the macOS equivalent.  Both
bindings are registered; use whichever the OS delivers.

| Key (Linux/Windows) | Key (macOS) | Action |
|---|---|---|
| `Ctrl+↑` | `Cmd+↑` | Align top edges |
| `Ctrl+↓` | `Cmd+↓` | Align bottom edges |
| `Ctrl+←` | `Cmd+←` | Align left edges |
| `Ctrl+→` | `Cmd+→` | Align right edges |

### Distribute (Ctrl+Shift+Arrow or Cmd+Shift+Arrow on macOS)

Requires three or more blocks selected.

| Key (Linux/Windows) | Key (macOS) | Action |
|---|---|---|
| `Ctrl+Shift+←` | `Cmd+Shift+←` | Distribute horizontally (equal H gaps) |
| `Ctrl+Shift+→` | `Cmd+Shift+→` | Distribute horizontally (equal H gaps) |
| `Ctrl+Shift+↑` | `Cmd+Shift+↑` | Distribute vertically (equal V gaps) |
| `Ctrl+Shift+↓` | `Cmd+Shift+↓` | Distribute vertically (equal V gaps) |

### Undo / Redo

| Key (Linux/Windows) | Key (macOS) | Action |
|---|---|---|
| `Ctrl+Z` | `Cmd+Z` | Undo last placement mutation |
| `Ctrl+Y` | `Cmd+Shift+Z` | Redo |
| `Ctrl+Shift+Z` | — | Redo |

Undo history: bounded deque, max 50 snapshots.  Snapshots are taken before drag,
resize, arrow-nudge, align, distribute, and optimize.  Cancelling the Optimize
dialog does not push a snapshot.

---

## Toolbar Buttons

Located along the top of the window.

| Button | Action |
|---|---|
| **Open** | Open an existing `.bdb` file and load all placed components |
| **New** | Create a new empty `.bdb` with the current die/grid settings |
| **Import Verilog** | Parse a `.v`/`.sv` file, create a BDB, seed placeholder blocks |
| **Write** | Save current engine positions to the open BDB |
| **Export Flow** | Write a `.buda` HBundle flow script for the current layout |
| **Run Flow** | Write BDB, generate and execute the HBundle script, report result |

---

## Canvas Panel (left sidebar, "Canvas" frame)

| Control | Type | Purpose |
|---|---|---|
| **Die W** | Spinbox | Die width in layout units |
| **Die H** | Spinbox | Die height in layout units |
| **Grid** | Spinbox | Snap-to-grid pitch (all block moves snap to this) |
| **Step** | Spinbox | Arrow-key nudge distance per keypress |
| **Apply** | Button | Commit Die W / Die H / Grid changes to the engine |
| **Overlay −** | Button | Decrease child-hierarchy overlay depth (min 0) |
| **Overlay level** | Label | Current overlay depth (0 = off) |
| **Overlay +** | Button | Increase child-hierarchy overlay depth (max 4) |

The overlay draws child blocks at the current view level as faint orange
rectangles, up to the specified number of extra levels below the visible depth.

---

## Blocks Panel (left sidebar, "Blocks" frame)

| Control | Type | Purpose |
|---|---|---|
| **Add** | Button | Add a new block (root or child, depending on current drill level) |
| **Align ▾** | Menu button | Open the alignment/distribution menu |
| **Optimize…** | Button | Open the placement optimizer dialog |
| **Tree** | Treeview | Hierarchical block list; supports single and multi-select |

### Align ▾ Menu

| Item | Keybinding | Minimum selection | Effect |
|---|---|---|---|
| Top ↑ | `Ctrl+↑` | 2 blocks | Align top edges to the topmost block's top |
| Bottom ↓ | `Ctrl+↓` | 2 blocks | Align bottom edges to the bottommost block's bottom |
| Left ← | `Ctrl+←` | 2 blocks | Align left edges to the leftmost block's left |
| Right → | `Ctrl+→` | 2 blocks | Align right edges to the rightmost block's right |
| Distribute H | `Ctrl+Shift+←/→` | 3 blocks | Equal horizontal gaps; outer blocks anchored |
| Distribute V | `Ctrl+Shift+↑/↓` | 3 blocks | Equal vertical gaps; outer blocks anchored |

---

## Selection Panel (left sidebar, "Selection" frame)

Displays information about the current selection.

| State | Display |
|---|---|
| Nothing selected | "No block selected." |
| One block | Name, child count, cell type, (x1,y1)–(x2,y2), width × height |
| Shared instance | Warning "⚠ Shared: \<cell\> (×N)" + **Make Unique** button |
| Multi-select | "N blocks selected." |

**Make Unique** — Creates a private copy of the cell so subsequent resizes do not
propagate to other instances of the same cell.

---

## Validation Panel (left sidebar, "Validation" frame)

| Control | Purpose |
|---|---|
| **Validate** button | Run overlap and boundary checks on all visible blocks |
| Issue list | First 5 issues; "… M more" when there are additional |
| **HPWL label** (blue) | Live half-perimeter wirelength; updated on every draw |

### Issue Kinds

| Kind | Meaning |
|---|---|
| `OVERLAP` | Two blocks intersect; lists both names |
| `OUTSIDE_DIE` | Block extends beyond die boundary |
| `ERROR` | Engine or BDB constraint violation |

---

## Breadcrumb Bar

Located below the toolbar, above the main canvas.

| Element | Action |
|---|---|
| **[top]** button | Return to top-level view (clear drill path) |
| Intermediate crumb | Navigate to that depth in the hierarchy |

Leaf crumb shows "(shared ×N)" when the current block is a replicated cell.
Drilling into a block with no children shows a status message; use **Add** to
create sub-blocks.

---

## Optimize Dialog

Opened by the **Optimize…** button.  Settings persist across dialog opens within
a session (reset when the application is restarted).

### Algorithm Section

| Control | Values | Default | Effect |
|---|---|---|---|
| Algorithm | SA / GA | SA | Simulated Annealing or Genetic Algorithm |
| Iterations (SA) | 100–500 000 | 20 000 | Total SA move attempts |
| Generations (GA) | 100–500 000 | 200 | Number of GA generations |

### Weights Section

| Weight | Range | Default | Effect |
|---|---|---|---|
| Wire-length | 0–100 | 1.0 | Coefficient on HPWL in the cost function |
| Area | 0–100 | 0.1 | Coefficient on bounding-box area (normalized) |
| Overlap | 0–100 | 10.0 | Penalty for pairwise block overlap |

### Block Constraints Table

One row per root-level block.

| Column | Type | Default | Effect |
|---|---|---|---|
| Block | Label | — | Instance name |
| Fixed | Checkbox | off | Position locked; block is never moved |
| Reshapeable | Checkbox | off | Optimizer may vary w/h (area = w×h constant) |
| Min Width | Spinbox | 0 | Minimum width when Reshapeable is on |
| Min Height | Spinbox | 0 | Minimum height when Reshapeable is on |

### Buttons

| Button | Effect |
|---|---|
| **Run** | Persist settings, start optimizer in background thread, show progress bar |
| **Cancel** | Close dialog; block positions are unchanged; settings are not saved |

Progress bar updates at every 5% milestone.  The GUI remains responsive during
the run.  After completion, HPWL, overlap, and iteration count appear in the
status bar.

---

## Mouse Interactions on Canvas

| Gesture | Effect |
|---|---|
| Left-click block body | Single-select; begin move drag |
| Shift+Left-click block | Toggle block in/out of canvas multi-selection |
| Drag block body | Move block (snaps to grid on release) |
| Left-click corner handle | Begin resize drag on that corner |
| Drag corner handle | Resize block live |
| Double-click block | Drill into hierarchy |
| Left-click empty canvas | Clear all canvas selection |

---

## Hierarchy-Aware Editing

Blocks are stored in a flat map keyed by their hierarchical path
(`parent/child`), each with absolute coordinates plus a cached local offset
relative to its immediate parent.  Editing commands split into two semantics:

| Semantics | Commands | Effect on a block's children |
|---|---|---|
| **Relocate** | drag-move, arrow nudge, align L/R/T/B, center H/V, distribute H/V, rotate CW/CCW, SA/GA optimize | The whole sub-hierarchy moves/rotates with the block |
| **Resize** | corner / mid-edge handle, edge move / align, optimizer reshape | The block grows/shrinks around its children; children keep their absolute positions |

Implementation notes:

- `FloorplannerEngine::move_block_raw` computes the move delta and calls
  `_translate_descendants(name, dx, dy)`, shifting every block named `name/…`.
  Child local offsets are relative to the immediate parent (which moves by the
  same delta), so only absolute coords change.
- `FloorplannerEngine::rotate_block(name, cw)` rotates the block **and** every
  descendant about the block's lower-left pivot (corners rotated, width/height
  swapped), then recomputes local offsets; the rotated bbox is clamped to at
  least one grid unit so a sub-grid dimension cannot collapse.
- `resize_block_raw` deliberately does **not** carry children — resizing a
  container leaves its contents in place.  Parent containment is not enforced:
  `validate` reports die-boundary violations and overlaps between non-nested
  blocks but skips ancestor/descendant pairs, so a child left outside a shrunken
  parent is not flagged.
- `fpc.topmost(names)` prunes a selection to its topmost blocks (dropping any
  name whose ancestor is also selected) before a relocate, so a selection holding
  both a parent and a descendant never moves the descendant twice.  It is applied
  by every relocate command (and the GUI arrow-nudge).
- `optimize_placement` operates on root-level blocks and applies a pure
  relocation via `move_block_raw` (children follow); only a genuine reshape of a
  `reshapeable` block uses `resize_block_raw`.

---

## Python Command-Layer API (`tools/floorplanner_commands.py`)

These functions are the testable backend.  The GUI calls them; tests call them
directly without a display.

### State

| Function | Returns | Description |
|---|---|---|
| `new_state()` | `FloorplannerAppState` | Empty state with a fresh engine |
| `load_bdb(path)` | `FloorplannerAppState` | Load BDB; populate engine with placed components |
| `create_bdb(path, die_w, die_h, grid)` | `FloorplannerAppState` | New empty BDB |

### Die and Grid

| Function | Description |
|---|---|
| `set_die(state, w, h)` | Update engine die dimensions |
| `set_grid(state, grid)` | Update engine snap grid |

### Block Management

| Function | Description |
|---|---|
| `add_block(state, name, x, y, w, h)` | Add root-level block at absolute coords |
| `add_child_block(state, name, local_x, local_y, w, h)` | Add child in local-parent coords |
| `move_block(state, name, raw_x, raw_y)` | Move block origin (snapped); **carries children** |
| `resize_block(state, name, x1, y1, x2, y2)` | Resize with snap; children stay in place |
| `topmost(names)` | Drop any name whose ancestor is also selected (used before relocates) |

### Alignment and Distribution

All of these are **relocations** — they carry each block's child sub-hierarchy
and prune nested selections via `topmost` (see
[Hierarchy-Aware Editing](#hierarchy-aware-editing)).

| Function | Min blocks | Description |
|---|---|---|
| `align_top(state, names)` | 2 | Align top edges |
| `align_bottom(state, names)` | 2 | Align bottom edges |
| `align_left(state, names)` | 2 | Align left edges |
| `align_right(state, names)` | 2 | Align right edges |
| `align_center_h(state, names)` | 2 | Share the first block's horizontal centerline |
| `align_center_v(state, names)` | 2 | Share the first block's vertical centerline |
| `distribute_h(state, names)` | 3 | Equal horizontal gaps; outer blocks anchored |
| `distribute_v(state, names)` | 3 | Equal vertical gaps; outer blocks anchored |
| `rotate_blocks_cw(state, names)` | 1 | Rotate 90° CW about each block's lower-left corner |
| `rotate_blocks_ccw(state, names)` | 1 | Rotate 90° CCW about each block's lower-left corner |

### Optimization

```python
optimize_placement(
    state,
    method="sa",          # "sa" or "ga"
    fixed=None,           # list of block names to pin in place
    reshapeable=None,     # list of block names whose aspect ratio may change
    min_sizes=None,       # dict {name: (min_w, min_h)}
    **kwargs              # passed to run_sa() or run_ga()
) -> OptimizerResult
```

`OptimizerResult` fields: `placements`, `hpwl`, `area`, `overlap`, `iterations`.
Results are applied to the engine as relocations (`move_block_raw`), so an
optimized top-level instance carries its children; only a reshaped `reshapeable`
block is applied as a resize.

### Metrics

| Function | Returns | Description |
|---|---|---|
| `compute_hpwl(state)` | `float` | Half-perimeter wirelength from live engine positions and BDB pin connectivity |

### Validation and I/O

| Function | Description |
|---|---|
| `validate(state)` | Return list of `ValidationIssue` objects |
| `write_bdb(state)` | Persist engine positions to `state.bdb_path` |
| `export_hbundle_script(state, path, depth)` | Write `.buda` script |
| `run_hbundle_flow(state, depth)` | Write BDB + script, run `buda_cli.py`, return `CompletedProcess` |

### Hierarchy Utilities

| Function | Returns | Description |
|---|---|---|
| `build_hierarchy_tree(state)` | `list[BlockNode]` | Nested tree for the treeview widget |
| `get_block_cell(state, name)` | `str \| None` | Cell type name for a block |
| `count_cell_instances(state, cell)` | `int` | Number of blocks sharing a cell type |
| `make_block_unique(state, name)` | `str \| None` | Give block a private cell; return new cell name |
| `sync_cell_to_instances(state, name, x1, y1, x2, y2)` | `(cell, n)` | Resize all instances sharing the same cell |
| `sync_move_to_instances(state, name, x, y)` | `(parent_cell, n)` | Sync child-block move to sibling instances |

### Verilog Import

```python
import_verilog(
    v_path,               # path to .v or .sv file
    bdb_path,             # output .bdb path
    die_w=2000.0,
    die_h=1200.0,
    grid=10.0,
    default_w=200.0,      # default seed block width
    default_h=160.0,      # default seed block height
    seed_depth=1          # number of hierarchy levels to seed with placeholders
) -> FloorplannerAppState
```

---

## `FloorplannerAppState` Fields

| Field | Type | Description |
|---|---|---|
| `engine` | `buda.FloorplannerEngine` | C++ placement engine (authoritative positions) |
| `bdb` | `buda.BDB \| None` | SQLite BDB (connectivity, component metadata) |
| `bdb_path` | `str` | Path to the open BDB file |
| `verilog_path` | `str` | Path to the source Verilog, if imported |
| `block_names` | `list[str]` | Sorted list of all known block names |
| `unplaced_names` | `list[str]` | Blocks seeded by Verilog import with no committed position |
| `selected` | `str \| None` | Name of the primary selected block |

Convenience methods: `block(name)`, `blocks()`, `names_at_depth(depth)`,
`blocks_at_depth(depth)`, `add_name(name)`.

---

## Architecture Note

The floorplanner uses a two-store model:

| Store | Authority | When used |
|---|---|---|
| `state.engine` (C++ `FloorplannerEngine`) | Block positions, die/grid | Always current; read for coordinates |
| `state.bdb` (SQLite) | Net/pin connectivity, component metadata | Never mutated by optimizer or drag; read for flylines, HPWL, cell names |

After the optimizer runs (or any drag), positions come from `engine.get_block()`,
not from `bdb.all_components()` (which holds stale coordinates).
