# BUDA Floorplanner User Guide

The BUDA floorplanner is an early prototype for quickly creating a manual
hierarchical floorplan from a BDB or Verilog netlist, then running the
hierarchical bundle flow for feedback.

It is intentionally thin: the GUI handles selection and drawing, while placement
semantics live in the C++ `FloorplannerEngine` and BDB.

## Launch

From the repository root:

```bash
./fp
```

To open an existing BDB directly:

```bash
./fp path/to/design.bdb
```

The wrapper sets `PYTHONPATH` for the local `build/` and `tools/` directories,
matching the existing `buda` and `viz` wrappers.

## Main Workflows

### Start From Verilog

1. Click `Import Verilog`.
2. Choose a `.v` or `.sv` file.
3. Choose the output `.bdb` path.
4. The tool runs `BDB.import_verilog()`.
5. Top-level instances are seeded as editable placeholder blocks.
6. Depth-1 child instances are seeded inside their parent blocks.

Verilog has hierarchy and connectivity but no physical size or placement. The
prototype therefore uses simple default sizing and packing heuristics. Treat the
initial layout as a starting point for manual refinement.

### Start From BDB

1. Click `Open`.
2. Choose an existing `.bdb`.
3. Placed components are loaded into the floorplanner engine.

Unplaced components are skipped until the Verilog import and placement seeding
flow grows richer.

### Create An Empty Floorplan

1. Set `Die W`, `Die H`, and `Grid`.
2. Click `New`.
3. Choose the output `.bdb`.
4. Click `Add` to create blocks manually.

## Canvas Controls

### Die And Grid

The `Canvas` panel controls:

| Field | Meaning |
|---|---|
| `Die W` | Die width in microns |
| `Die H` | Die height in microns |
| `Grid` | Placement snap grid in microns |
| `Depth` | Visible hierarchy depth |

Click `Apply` after changing die or grid values.

### Hierarchy Depth

Use `Depth` to switch visible blocks:

| Depth | Shows |
|---|---|
| `0` | Top-level instances, such as `u_cpu` |
| `1` | Child instances, such as `u_cpu/u_core` |

This is a first prototype. It does not yet provide a full tree browser or
local-parent canvas framing, but it does preserve absolute coordinates and lets
the designer place different hierarchy levels.

### Add Blocks

Click `Add`, enter:

| Prompt | Meaning |
|---|---|
| `Instance path` | BDB component name, e.g. `u_cpu` or `u_cpu/u_core` |
| `X origin` | Lower-left x coordinate |
| `Y origin` | Lower-left y coordinate |
| `Width` | Block width |
| `Height` | Block height |

### Move Blocks

Drag blocks on the canvas. The engine snaps the moved block origin to the active
grid.

### Select Blocks

Select blocks either by clicking a canvas rectangle or by selecting entries in
the block list.

### Align Blocks

Select at least two blocks in the list, then click `Align Bottom`.

The C++ engine updates the selected block locations. More align/distribute
operations are expected to follow the same model.

## Validation

Click `Validate` to check:

| Issue | Meaning |
|---|---|
| `OVERLAP` | Two blocks overlap |
| `OUTSIDE` | A block extends outside the die |
| `ERROR` | Invalid engine state, such as invalid die/grid settings |

The first few issues are shown in the left panel.

## Writing Back To BDB

Click `Write` to save current placements to the open or created BDB.

For Verilog-imported BDBs, components already exist with placeholder coordinates.
The floorplanner writes exact bounding boxes using `BDB.set_comp_bbox()`.

For new components, the floorplanner creates a simple cell name based on the leaf
instance path and inserts the component.

## Export HBundle Flow

Click `Export Flow` to write a `.buda` script for the current floorplan.

The generated script includes:

```buda
source <repo>/flow/tracks.buda
open_bdb <design.bdb>
derive_busterms <depth>
add_blocks_from_bdb 0
add_blocks_from_bdb <depth> skip
run_hier_bundler depth <depth>
dump_hbundles
generate_hier_topologies
run_planner hier 5
run_nuts
check_connectivity nuts
visualize
```

For `depth 0`, the depth-specific `add_blocks_from_bdb <depth> skip` line is
omitted.

## Run HBundle Flow

Click `Run Flow` to:

1. Write current placements to BDB.
2. Export a temporary HBundle flow script next to the BDB.
3. Run `src/buda_cli.py <script> --no-viz`.
4. Report completion or failure in the status bar.

This is the fastest feedback path for checking whether the current hierarchy and
placement produce HBundles and topology candidates.

## Verilog Pin Directions

`import_verilog()` still imports instance pins as `UNKNOWN`, preserving the
Verilog-only bootstrap behavior. During `run_hier_bundler`, BUDA now infers
instance pin directions from module port declarations recorded in `cell_pin`,
writes those directions back into BDB, and then groups HBundles.

Example:

```verilog
module producer(output y);
endmodule

module consumer(input a);
endmodule

module top();
  wire sig;
  producer u_src (.y(sig));
  consumer u_dst (.a(sig));
endmodule
```

After HBundle generation, BDB pin directions become:

| Pin | Direction |
|---|---|
| `u_src.y` | `OUTPUT` |
| `u_dst.a` | `INPUT` |

The resulting HBundle reason is:

```text
DRV:u_src|REC:u_dst,
```

## Current Limitations

- The GUI is a prototype, not a polished editor.
- Initial Verilog placement uses simple heuristics.
- Only depth switching is available; there is no full hierarchy tree browser yet.
- Resizing exists in the BDD contract but is not exposed as canvas handles yet.
- HBundle/NUTS results are not overlaid on the floorplanner canvas yet.
- `Run Flow` executes synchronously and blocks the GUI until the command exits.

## Recommended First Use

1. Run `./fp`.
2. Import Verilog.
3. Set die and grid.
4. Place depth-0 blocks.
5. Switch to depth 1 and adjust child blocks.
6. Click `Validate`.
7. Click `Write`.
8. Click `Run Flow`.
9. Use the generated BDB and `.buda` script as the starting point for deeper
   floorplan refinement.
