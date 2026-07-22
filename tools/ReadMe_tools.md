# Clustering, Converter, and DEF Visualization Tools

[Also see research doc](../docs/research.md)

## Standard Execution

From the repo root (after sourcing `bin/activate`):

```bash
# Visualize a BDB floorplan design
python3 tools/def_viz.py chip_designs/ariane136.bdb

# Visualize DEF/LEF pair
python3 tools/def_viz.py flow/lefdef/gcd/gcd.def flow/lefdef/gcd/Nangate45.lef

# Launch interactive Floorplanner GUI
bin/fp chip_designs/ariane136.bdb

# Unit-test to .buda converter + Topology Explorer
bin/u2b test_column_datapath_hvh
```

## Interactive IPC Test Sequence

`def_viz.py` can connect via Unix domain socket IPC (`/tmp/buda_ipc_<session>.sock`) to `buda_viz.py` for live cross-highlighting between the DEF/BDB physical placement view and BUDA's interconnect bundle view.

### 1. Small IPC Test

**Terminal 1 — BUDA routing CLI & visualizer:**
```bash
source bin/activate
buda flow/four_blocks.buda
# Session name → four_blocks, socket → /tmp/buda_ipc_four_blocks.sock
```

**Terminal 2 — DEF visualizer:**
```bash
source bin/activate
python3 tools/def_viz.py flow/lefdef/four_blocks/four_blocks.def flow/lefdef/four_blocks/four_blocks.lef
```

*Expected behavior*: Clicking a bundle in `buda_viz` highlights its connected driver/receiver instances in `def_viz`. Clicking an instance in `def_viz` highlights all bundles touching that instance in `buda_viz`.

### 2. ISPD19 Test

**Terminal 1:**
```bash
source bin/activate
buda demo/ispd19_test1.buda
```

**Terminal 2:**
```bash
source bin/activate
python3 tools/def_viz.py flow/lefdef/ispd19_test1/ispd19_test1.input.def flow/lefdef/ispd19_test1/ispd19_test1.input.lef
```

### IPC Verification Checklist
- **BUDA → DEF direction**: Click a bundle segment in `buda_viz`. Status bar in `def_viz` shows `[IPC] bundle N: X net(s) -> Y instance(s)`.
- **DEF → BUDA direction**: Click an instance box or pick from the instance listbox in `def_viz`. `buda_viz` highlights connected bundles.
- **Clear propagation**: Clicking background in `buda_viz` clears `def_viz` selection; clicking Clear in `def_viz` deselects `buda_viz`.
- **Session isolation**: Running multiple instances with different `.buda` scripts isolates IPC sockets by session name (`/tmp/buda_ipc_<session>.sock`).


✻ Cooked for 37s
