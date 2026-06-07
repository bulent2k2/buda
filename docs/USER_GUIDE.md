# BUDA User Guide

Welcome to BUDA! This guide is designed to help you get started with interconnect planning. BUDA automates the grouping of nets into buses (bundling), generates physical routing candidates (topologies), and resolves congestion through planning and track assignment.

---

## 1. The Standard Execution Flow

To successfully run a BUDA script, you should follow this sequence:

1.  **Environment Setup**: Define your metal layers and track patterns.
2.  **Floorplan & Netlist**: Add blocks and the connections between them.
3.  **Bundling**: Group individual nets into manageable bundles.
4.  **Topology Generation**: Enumerate possible routing shapes for each bundle.
5.  **Global Planning**: Choose the best topology for each bundle to minimize congestion.
6.  **Track Assignment (NUTS)**: Solve for the specific track positions of every wire.

---

## 2. Necessary Conditions (Prerequisites)

BUDA commands depend on each other. If you skip a setup step, the engine may use incorrect defaults or fail.

### `generate_topologies`
*   **Prerequisite**: Use `def_layer` first.
*   **Why?**: The generator needs to know which layers are Horizontal (H) and which are Vertical (V). If no layers are defined, it will default to Layer 4 (H) and Layer 5 (V).

### `run_nuts`
*   **Prerequisite**: You must have run `run_planner` (or manually pinned topologies).
*   **Why?**: NUTS assigns tracks to the *selected* topologies. Without a plan, there is nothing to assign.

### `run_detailed_nuts`
*   **Prerequisite 1**: Run `run_nuts` first.
*   **Prerequisite 2**: Use `def_track_pattern` for every used layer.
*   **Why?**: Detailed NUTS performs bit-level placement. It needs to know the exact width and spacing of tracks (the "pattern") to ensure the layout is physically legal.

---

## 3. Sidecar Selections (`.json` files)

When you run BUDA, it automatically looks for a "sidecar" JSON file with the same name as your script (e.g., `quickstart.json` for `quickstart.buda`).

*   **Persistence**: This file stores manual topology selections you've made in the visualizer.
*   **Stability**: If you run `run_planner`, it will prioritize the "pinned" selections found in this file over its own calculated defaults. 
*   **Reproducibility**: You can share your design by providing both the `.buda` script and the `.json` sidecar.

If the engine finds a conflict (e.g., you changed the floorplan and the pinned topology no longer exists), it will print a warning and fall back to the best available candidate.

---

## 4. Example Script: `quickstart.buda`

Save this as a `.buda` file and run it from the repository root using `python3 src/buda_cli.py your_file.buda`.

```python
# ── Step 1: Define Technology ──
# Layer ID, Name, Direction (H/V), Type (TOP/LOW), Capacity per unit
def_layer 4 M4 H TOP 1.0
def_layer 5 M5 V TOP 1.0

# Track patterns are REQUIRED for Detailed NUTS.
# Format: <layer_id> <origin> [<type> <width> <spacing>] ...
def_track_pattern 4 0.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  POWER 2.0 1.0  GROUND 2.0 1.0  CLK 1.5 1.0
def_track_pattern 5 0.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  POWER 2.0 1.0  GROUND 2.0 1.0  CLK 1.5 1.0

# ── Step 2: Define Floorplan ──
add_block CPU  100 100 300 300
add_block MEM  700 500 900 700
add_block GPU  400 800 600 1000

# Optional: Add margins to keep wires away from block corners
corner_margin dx 20 dy 20

# ── Step 3: Define Netlist ──
# Single control net
add_net cntr CPU.tx MEM.lx

# 8-bit bus: MEM -> GPU
add_bus mem_to_gpu[8] MEM.tx GPU.rx

# ── Step 4: Execute Planning ──
run_bundler strict
generate_topologies

# run_planner will load any existing selections from quickstart.json
run_planner 5

run_nuts
run_detailed_nuts

# ── Step 5: Visualize ──
# Use the GUI to click on bundles and choose different topologies.
# Your choices will be saved to the sidecar JSON file automatically.
visualize
```

---

## 5. Key Concepts for Novices

### BUSTERM Mode
By default, BUDA connects wires to the **faces** of your blocks (Busterm mode). This is more realistic than connecting to the exact center of a block.

### Minimum Stub Length
To ensure your layout isn't too "pinched," you can set a minimum stub length:
`set_min_stub_length 25`
This ensures that every wire segment connecting to a block has a healthy physical length.

### Keepout Zones
You can prohibit routing in specific rectangular regions for one or more layers:
`add_keepout 300 100 500 300 M4`
*   **Planning**: `run_planner` will automatically choose topologies that detour around these zones.
*   **Detailed Routing**: `run_detailed_nuts` will skip any tracks that pass through a keepout zone for the assigned layer.
*   **Visualization**: Keepout zones appear as red hatched rectangles in the visualizer.

---

## 6. Getting Help
*   Check `docs/BUDA_SCRIPT_REFERENCE.md` for a full list of commands.
*   Use `visualize` at different stages of your script to see what BUDA is doing!
