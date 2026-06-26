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

## Topology Explorer (`TopologyExplorer`)

The Topology Explorer allows you to inspect the alternative routing candidate shapes (topologies) generated for a specific bundle, and manually assign layers to specific segments of the trunk.

| Key(s) | Action |
| :--- | :--- |
| `d`, `n`, `cmd+n` | View the **next** topology candidate for this bundle. |
| `a`, `p`, `cmd+p` | View the **previous** topology candidate for this bundle. |
| `]`, `pagedown` | Switch to the **next bundle** and view its selected topology. |
| `[`, `pageup` | Switch to the **previous bundle** and view its selected topology. |
| `k` | Select the **previous segment** of the current topology. |
| `j` | Select the **next segment** of the current topology. |
| `←` `→` `↑` `↓` | **Pan** the view left / right / up / down. |
| `+`, `=` | **Layer Up**: Assign the selected segment to the next higher valid routing layer. |
| `-`, `_` | **Layer Down**: Assign the selected segment to the next lower valid routing layer. |
| `s` | **Select/Pin**: Pin the currently viewed topology (and any manual layer assignments) so the planner uses it. |
| `x` | **Deselect/Unpin**: Remove the manual pin, letting the planner choose automatically. |
| `r` | **Re-run Planner**: Re-evaluate global routing and NUTS track assignment after changing a pin. |
| `b` | Toggle visibility of the floorplan **blocks**. |
| `t` | Toggle visibility of **busterms**. |
| `g` | Toggle visibility of the **Hanan** grid. |
| `cmd+1`, `ctrl+1` | Bring the **Main View** window to the front. |
| `z` | **Zoom In** (centered on the mouse cursor). |
| `Z` (Shift+z) | **Zoom Out** (centered on the mouse cursor). |
| `cmd+z`, `ctrl+z` | **Zoom to Selection**: Fit the view to the active bundle's terminals/topology. |
| `h`, `H`, `cmd+a`, `ctrl+a` | **Home**: Reset zoom and return to the original full view. |
| `f`, `cmd+f`, `ctrl+f` | Toggle Fullscreen mode. |
| `cmd+q`, `ctrl+q` | Close all visualizer windows. |

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
| `v` | **Validate**: run overlap / out-of-die checks and show results in the sidebar. |
| `Esc` | **Deselect** all blocks. |
| `ctrl+z` / `cmd+z` | **Undo** the last placement change. |
| `ctrl+Z` / `ctrl+y` / `cmd+Z` | **Redo**. |
| `ctrl+a` / `cmd+a` | **Select All** blocks. |
| `f` | **Toggle fullscreen** / maximise the window. |
