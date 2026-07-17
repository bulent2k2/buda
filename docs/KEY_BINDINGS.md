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
| `j` | Select the **next segment** of the current topology — on **any** shown candidate, pinned or not. The selected segment — its wire, its slide-range band, and its bounds — is highlighted (others dimmed) and described in a three-line top-left info box: `Selected V segment 3 on M5.`, its position, span, and slide range (`x=<perp> V-span=[lo,hi] H-slide=[lo,hi]` for a V segment, `y=<perp> H-span=[lo,hi] V-slide=[lo,hi]` for an H one), and its **connectivity** — net-pull always, plus busterm taps, pass-through blocks, and connected segs when present (`pull=→hi(+2) · busterms: b1,b2 · passthru: c1 · segs: 1,3`); `-`/`+` restyle it to a new layer with a live update. |
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
| `cmd+z`, `ctrl+z` | **Zoom to Selection**: Fit the view to the active bundle's terminals/topology. With a segment selected (`j`/`k`), repeated presses **toggle** between the selected segment and the bundle bbox — **segment first**: the press right after selecting frames that segment, the next returns to the bundle. The segment view frames its **span + slide box** (the region the drawn wire and its slide band actually occupy — a NUTS-displaced wire is never shifted out of frame), **centered** and scaled so the box covers **at most 1/9 of the canvas** (1/3 per dimension). |
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
| `T` (Shift+t) | **Arm** a **horizontal trunk**: hover snaps to the **CENTERLINE of the Hanan cell** under the cursor (the mid-channel row between two bundle-grid lines — not a block face), the full cell highlights, and the banner **live-reports** the prospective trunk (`ADD H TRUNK: y=550 slide=[400,700] busterms: lo,up`); press `T` again, `enter`, or **click** to place it, `esc` to cancel. Placement **seeds the slide window from the cell's two bounding lines** (staged like a `W` refinement, `edit_set_slide` logged — `W` re-refines it), sets the along span from the **busterms touching the slice** (all-busterm extent when none touch; refine with `P`), and **auto-selects** the new segment — the info banner tracks it exactly as when stepping with `j`/`k`, and `S` stubs target it directly. A trunk **geometrically identical** to an existing segment (same line + span) is rejected — re-span the existing one with `P` or remove it with `X`. |
| `Y` (Shift+y) | **Arm** a **vertical trunk** (same two-step hover→place as `T`: centerline snap, slide seed, auto-select, duplicate rejection). Bundle-grid columns include out-of-bounds detour lines, so a U-shape/OOB trunk lands mid-detour-band. |
| `G` (Shift+g) | While a trunk is **armed**: drop a **temporary Hanan line** at the cursor on the armed axis (a row for `T`, a column for `Y`) and pin the placement **exactly there** (hover otherwise snaps to cell centerlines, so a channel's centerline needs no `G` — this is the escape for an **off-center** coordinate, and dropping a line also **splits the cell**, making the two half-cells' centerlines hoverable: the `c_ddd_detour` upper/lower-half channel trunks, each seeded with its half's slide). The line joins the grid (snap target + display) for the session; the placed trunk's `[edit-cmd]` log carries the explicit coordinate, so replay needs no grid. |
| `S` (Shift+s) | Add a **stub** from the block under the cursor to the selected segment (`j`/`k`); with only the trunk present it auto-selects the trunk as the target. |
| `P` (Shift+p) | **Pin the selected segment's span to chosen anchors**: click **busterm blocks**, **perpendicular segments**, and/or **Hanan grid lines** the trunk should reach — a busterm block at the span's **extreme** is a trunk ENDPOINT and lands like an auto-generated trunk: at the block's **inner face, overlapping min-stub INTO the block** (`a1.right-20`), never stretched to the centre (an interior block anchor keeps the stub-drop centre); a click near a perpendicular segment anchors at **its exact perp coordinate** AND **connects the pair on apply** — the pinned end lands on that trunk's line and the partner trunk is **stretched to the crossing** when its span falls short (`edit_connect`), so "end on that trunk" produces a real junction, not two touching coordinates — anything else pins the nearest along-axis grid line. Each click toggles; picked blocks outline red, line anchors draw red-dashed, and the resulting span previews live. `enter` applies, `esc` cancels. **Two+ anchors** → span = [min, max]. **One anchor** → only the **nearest endpoint** moves there (the far end — e.g. a junction — stays put): the "re-span one end" gesture. **One block alone** → span the block's extent. A grid line reaches **beyond** the outermost busterm (C-detours); two trunks on one grid line can each cover a limited span (the dd-detour's right V trunks). |
| `C` (Shift+c) | **Connect** two perpendicular segments: press once to mark the selected segment, re-select (`j`/`k`), press again. |
| `D` (Shift+d) | **Disconnect** a junction pair (same two-step marking); the cursor position sets where the retracted endpoint lands. |
| `W` (Shift+w) | **Refine the selected segment's slide window**: press at one perpendicular bound, then at the other — the window (∩ the structural slide range) is staged and lands as a NUTS override (`plan.seg_slide_lo/hi`) on commit. Bounds **snap to the bundle grid's Hanan lines** by default (`[grid]` in the banner); **`enter` mid-refine toggles the gridless sub-mode** (`[free]`) for off-grid bounds — both marks store the raw cursor coordinate, so the mode at apply time decides. The drawn slide band follows live. Scriptable as `edit_set_slide <seg#> <lo> <hi>`, so staged windows ride the `[edit-cmd]` log and the sidecar replay too. |
| `w` | **Clear** the selected segment's staged slide window (`edit_set_slide <seg#> clear`). |
| `X` (Shift+x) | **Remove** the selected segment (annotations re-keyed; staged slide windows re-keyed too). |
| `enter` | **Commit**: append the copy to the bundle's pool as a `USER` candidate (uid-deduped), pin it, save the sidecar, and apply any staged slide windows to the plan. |
| `escape` | **Abort**: discard the working copy (staged slide windows included). |

Every applied edit op also prints its `.buda` equivalent as an `[edit-cmd]` line
(`edit_add_trunk V 450 lo.cy up.cy layer 5`, `edit_set_layer 3 7`, …) — fold
them into the flow script for automation.  Coordinates are emitted as
**block/face references** where they match one (`lo.cx`, `up.bottom`,
`a1.right-20` for a P block-pinned endpoint), and every `edit_*` coordinate
argument accepts the same grammar — `<block>.<left|right|top|bottom|cx|cy>[±N]`,
resolved against the edit session's own floorplan — so folded commands track
the floorplan instead of hard-coding numbers.  The commit stores the same op-log in the
**sidecar** (`user_topo`: base candidate uid + ops), and a re-run of the flow
**replays it after `generate_topologies`**, so the hand-built USER candidate —
which regeneration never produces — exists again and the pin resolves instead
of the old `sidecar selection … could not be resolved` warning.

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
