# BUDA Visualizer Key Bindings

BUDA provides two main visualization windows: the **Main View** (showing the overall floorplan, all bundles, and congestion heatmaps) and the **Topology Explorer** (a focused view for inspecting and tuning individual topologies for a single bundle).

Here are the keyboard shortcuts available in each view.

## Main View (`BudaVisualizer`)

| Key(s) | Action |
| :--- | :--- |
| `n`, `cmd+n`, `ctrl+n` | Select the **next** bundle in the list. |
| `p`, `cmd+p`, `ctrl+p` | Select the **previous** bundle in the list. |
| `]`, `pageup` | Select the **next** bundle. |
| `[`, `pagedown` | Select the **previous** bundle. |
| `v`, `cmd+t`, `ctrl+t` | Open the **Topology Explorer** for the currently highlighted bundle. |
| `z` | **Zoom In** (centered on the mouse cursor). |
| `Z` (Shift+z) | **Zoom Out** (centered on the mouse cursor). |
| `cmd+z`, `ctrl+z` | **Zoom to Selection**: Fit the view to the currently highlighted bundle. |
| `h`, `H`, `cmd+a`, `ctrl+a` | **Home**: Reset zoom and return to the original full view. |
| `←` `→` `↑` `↓` | **Pan** the view left / right / up / down. |
| `a` | **Toggle Reset/Highlight**: Toggle between clear/reset mode (showing all/reset view) and highlighting the last selected bundle. Works in both abstract and detailed modes. |
| `b` | Toggle visibility of the floorplan **blocks**. |
| `t` | Toggle visibility of **busterms**. |
| `g` | Toggle visibility of the **Hanan** grid. |
| `s` | Toggle **Solo** mode (ON/OFF): show only the highlighted bundle, fully hiding the rest. |
| `d` | Toggle **Detailed Mode** (shows 1.5-D track assignments if `run_detailed_nuts` was used). |
| `f`, `cmd+f`, `ctrl+f` | Toggle Fullscreen mode. |
| `cmd+q`, `ctrl+q` | Close the visualizer. |

### Mouse

| Interaction | Action |
| :--- | :--- |
| **Right-drag LR** (left → right) | **Zoom to Box**: fit the drawn box to the window (blue rubber band). |
| **Right-drag RL** (right → left) | **Zoom Out**: expand the view so the current view fits in the drawn box (orange rubber band). |
| **Scroll wheel** | Zoom in / out. |
| **Left-click** a bundle | Select / highlight it; click empty space to deselect. |

## Topology Explorer (`TopologyExplorer`)

The Topology Explorer allows you to inspect the alternative routing candidate shapes (topologies) generated for a specific bundle, and manually assign layers to specific segments of the trunk.

| Key(s) | Action |
| :--- | :--- |
| `d`, `n`, `cmd+n` | View the **next** topology candidate for this bundle. |
| `a`, `p`, `cmd+p` | View the **previous** topology candidate for this bundle. |
| `]`, `pagedown` | Switch to the **next bundle** and view its selected topology. When launched from a BUDA viz window, steps in that window's **bundle-panel order** (opens-first), so the two stay in step; standalone, it steps in numeric bundle-id order. |
| `[`, `pageup` | Switch to the **previous bundle** (same order as `]`). |
| `k` | Select the **previous segment** of the current topology. |
| `j` | Select the **next segment** of the current topology. The selected segment — its wire, its slide-range band, and its bounds — is highlighted (others dimmed) and named in a top-left info line (`Selected V segment 3 on M5.`); `-`/`+` restyle it to a new layer with a live update. |
| `←` `→` `↑` `↓` | **Pan** the view left / right / up / down. |
| `+`, `=` | **Layer Up**: Assign the selected segment to the next higher valid routing layer. |
| `-`, `_` | **Layer Down**: Assign the selected segment to the next lower valid routing layer. |
| `s` | **Select/Pin**: Pin the currently viewed topology (and any manual layer assignments) so the planner uses it. |
| `x` | **Deselect/Unpin**: Remove the manual pin, letting the planner choose automatically. |
| `r` | **Re-run Planner**: Re-evaluate global routing and NUTS track assignment after changing a pin. |
| `b` | Toggle visibility of the floorplan **blocks**. |
| `t` | Toggle visibility of **busterms**. |
| `g` | Toggle visibility of the **Hanan** grid. |
| `v`, `cmd+1`, `ctrl+1` | Bring the **Main View** window to the front. Since the Main View's `v` opens/raises this explorer, tapping `v` cycles between the two windows. |
| `z` | **Zoom In** (centered on the mouse cursor). |
| `Z` (Shift+z) | **Zoom Out** (centered on the mouse cursor). |
| `cmd+z`, `ctrl+z` | **Zoom to Selection**: Fit the view to the active bundle's terminals/topology. |
| `h`, `H`, `cmd+a`, `ctrl+a` | **Home**: Reset zoom and return to the original full view. |
| `f`, `cmd+f`, `ctrl+f` | Toggle Fullscreen mode. |
| `cmd+q`, `ctrl+q` | Close all visualizer windows. |

### TopoEdit mode (expert hand-editing)

Press `e` to open an **edit session** on a working *copy* of the shown
candidate (`E` starts from an empty topology).  While a session is open the
copy replaces the candidate on screen, a boxed red banner (top-left, inside
the axes) shows each operation's verdict (violations, wire-graph components,
pinch), the **bundle-scoped Hanan grid** turns on (the lines generation
derives this bundle's candidates from — busterm-block + keepout edges; also
the only `T`/`Y` snap targets), segments thin so the slide bands stay
readable, and candidate/bundle navigation is parked.  The same operations
are scriptable as `.buda` commands (`edit_topology` … `edit_commit` — see
the Script Reference).

| Key(s) | Action |
| :--- | :--- |
| `e` / `E` | Open an edit session: copy of the shown candidate / empty topology. The busterm blocks are highlighted, and `j`/`k` select segments immediately (no need to pin first). |
| `T` (Shift+t) | **Arm** a **horizontal trunk**: the target row (a bundle-grid line) highlights as you hover; press `T` again, `enter`, or **click** to place it, `esc` to cancel. The trunk spans the busterm extent (no overshoot), not the whole die. |
| `Y` (Shift+y) | **Arm** a **vertical trunk** (same two-step hover→place as `T`). Bundle-grid columns include out-of-bounds detour lines, so a U-shape/OOB trunk is placeable. |
| `S` (Shift+s) | Add a **stub** from the block under the cursor to the selected segment (`j`/`k`); with only the trunk present it auto-selects the trunk as the target. |
| `C` (Shift+c) | **Connect** two perpendicular segments: press once to mark the selected segment, re-select (`j`/`k`), press again. |
| `D` (Shift+d) | **Disconnect** a junction pair (same two-step marking); the cursor position sets where the retracted endpoint lands. |
| `W` (Shift+w) | **Refine the selected segment's slide window**: press at one perpendicular bound, then at the other — the window (∩ the structural slide range) is staged and lands as a NUTS override (`plan.seg_slide_lo/hi`) on commit. The drawn slide band follows live. |
| `w` | **Clear** the selected segment's staged slide window. |
| `X` (Shift+x) | **Remove** the selected segment (annotations re-keyed; staged slide windows re-keyed too). |
| `enter` | **Commit**: append the copy to the bundle's pool as a `USER` candidate (uid-deduped), pin it, save the sidecar, and apply any staged slide windows to the plan. |
| `escape` | **Abort**: discard the working copy (staged slide windows included). |

### Mouse

| Interaction | Action |
| :--- | :--- |
| **Right-drag LR** (left → right) | **Zoom to Box**: fit the drawn box to the window (blue rubber band). |
| **Right-drag RL** (right → left) | **Zoom Out**: expand the view so the current view fits in the drawn box (orange rubber band). |

## Floorplanner (`bdb_floorplanner.py`)

### Mouse

| Interaction | Action |
| :--- | :--- |
| **Right-drag LR** (lower-left → upper-right) | **Zoom In**: fit the drawn box to the viewport (blue rubber band). |
| **Right-drag RL** (upper-right → lower-left) | **Zoom Out**: expand the view so the current viewport fits in the drawn box (orange rubber band). |
| **Left-drag block** | **Move** the block (snaps to placement grid). |
| **Left-drag corner handle** | **Resize** block (two edges at once, snaps to Hanan grid / block edges). |
| **Left-drag mid-edge handle** | **Resize** one edge (snaps to Hanan grid / block edges). |
| **Double-click block** | **Drill into** the block (view its children). |
| **Shift+click** | **Multi-select** blocks on the canvas. |
| **Left-click edge / diamond** *(edge mode)* | **Toggle** that edge in the edge selection (all-V or all-H; picking the opposite orientation restarts the selection). |
| **Left-drag a selected edge** *(edge mode)* | **Move** all selected edges by the same delta (snaps to Hanan grid / block edges). |

### Keyboard

| Key(s) | Action |
| :--- | :--- |
| `h`, `H` | **Home**: reset zoom to the full auto-fit view. |
| `z` | **Zoom In** one step, centered on the cursor. |
| `Z` (Shift+z) | **Zoom Out** one step, centered on the cursor. |
| `←` `→` `↑` `↓` | **Pan** the view (when no block is selected). |
| `←` `→` `↑` `↓` | **Nudge** the selected block(s) by one grid step (when a block is selected). |
| `ctrl/cmd` + `←` `→` `↑` `↓` | **Align** selected blocks left / right / top / bottom. |
| `ctrl/cmd+shift` + `←` `→` | **Distribute** selected blocks horizontally / vertically. |
| `e` | **Toggle Edge mode** (mid-edge handles appear on all blocks; click edges to select). |
| `←` `→` *(edge mode, V edges)* / `↑` `↓` *(edge mode, H edges)* | **Move** the selected edges by one grid step. |
| `v` | **Validate**: run overlap / out-of-die / gap checks and show results in the sidebar. |
| `Esc` | **Deselect** all blocks (or, in edge mode, clear the edge selection). |
| `ctrl+z` / `cmd+z` | **Undo** the last placement change. |
| `ctrl+Z` / `ctrl+y` / `cmd+Z` | **Redo**. |
| `ctrl+a` / `cmd+a` | **Select All** blocks. |
| `f` | **Toggle true fullscreen** (borderless, fills the screen — like the viewer); press `f` again to exit. |

In **Edge mode** (`e` key or the **Edges** checkbox in the Blocks panel), the
**Align ▾** menu's *Edges → Min / Max / Mean* entries snap all selected edges to
a common coordinate (leftmost/topmost, rightmost/bottommost, or average).
