# Clustering, Converter, and DEF Visualization Tools

[Also see research doc](../docs/research.md)

## Standard Execution

From the repo root (after sourcing `bin/activate`):

```bash
# Visualize a BDB floorplan design
python3 tools/bdb_serialize.py load flow/rnr/mix2_aligned.bdb.sql fp.bdb
bin/viz fp.bdb

# Launch interactive Floorplanner GUI
bin/fp fp.bdb

# Unit-test to .buda converter + Topology Explorer
bin/u2b test_column_datapath_hvh

# Visualize a DEF/LEF pair — the tiny BUDA-original toy design ships in-repo:
bin/viz tools/data/four_blocks.def tools/data/four_blocks.lef

# For a realistic design, use the standard OpenROAD "gcd" example on the
# Nangate45 open library. These are third-party files with their own licenses
# and are NOT committed here — download them and point viz at your local copies:
#   Nangate45 LEF  → OpenROAD-flow-scripts, flow/platforms/nangate45/lef
#       https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
#   gcd design DEF → OpenROAD test suite (test/gcd*.def) or the flow designs
#       https://github.com/The-OpenROAD-Project/OpenROAD
# bin/viz /path/to/gcd.def /path/to/Nangate45.lef
```

See [`tools/data/README.md`](data/README.md) for what ships in `tools/data/`
and where to fetch the larger third-party benchmarks.

## Interactive IPC Test Sequence

`viz` can connect via Unix domain socket IPC (`/tmp/buda_ipc_<session>.sock`) to `buda_viz.py` for live cross-highlighting between the DEF/BDB physical placement view and BUDA's interconnect bundle view.

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
viz tools/data/four_blocks.def tools/data/four_blocks.lef
```

*Expected behavior*: Clicking a bundle in `buda_viz` highlights its connected driver/receiver instances in `viz`. Clicking an instance in `viz` highlights all bundles touching that instance in `buda_viz`.

### 2. ISPD19 Test

The ISPD 2019 contest benchmarks are third-party and NOT committed here; download
them from the contest site and point the tools at your **local** copies (adjust
the paths below to wherever you unpacked them):
<https://www.ispd.cc/contests/19/>

**Terminal 1:**
```bash
source bin/activate
buda demo/ispd19_test1.buda        # your own .buda wrapper for the design
```

**Terminal 2:**
```bash
source bin/activate
python3 tools/def_viz.py /path/to/ispd19_test1.input.def /path/to/ispd19_test1.input.lef
```

### IPC Verification Checklist
- **BUDA → DEF direction**: Click a bundle segment in `buda_viz`. Status bar in `def_viz` shows `[IPC] bundle N: X net(s) -> Y instance(s)`.
- **DEF → BUDA direction**: Click an instance box or pick from the instance listbox in `def_viz`. `buda_viz` highlights connected bundles.
- **Clear propagation**: Clicking background in `buda_viz` clears `def_viz` selection; clicking Clear in `def_viz` deselects `buda_viz`.
- **Session isolation**: Running multiple instances with different `.buda` scripts isolates IPC sockets by session name (`/tmp/buda_ipc_<session>.sock`).
