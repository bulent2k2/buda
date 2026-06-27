# `build_hier_demo` — Hierarchical Demo BDB Builder

`tools/build_hier_demo.py` assembles a **hierarchical BDB from scratch** out of
existing flat `.buda` scripts. It defines each script as a reusable **cell**,
instantiates each cell **twice** inside a `top` cell, and adds top-level
**buses** (bit counts 4 … 16) that wire random subsets of the leaf blocks across
those instances — a quick, self-contained design to exercise the hierarchy-aware
flow and the Floorplanner.

---

## Usage

```bash
python3 tools/build_hier_demo.py [out.bdb] [--seed N] [--cells a.buda,b.buda,c.buda]
```

| Argument / option | Default | Description |
|---|---|---|
| `out.bdb` | `/tmp/hier_demo.bdb` | Output BDB path (overwritten if it exists) |
| `--seed N` | `1` | Seed for the random bus wiring (reproducible) |
| `--cells …` | `flow/dnuts1.buda,flow/dnuts2.buda,flow/channel_stress.buda` | Comma-separated flat scripts to use as leaf cells |

```bash
# Defaults
python3 tools/build_hier_demo.py

# Custom output, seed, and cells
python3 tools/build_hier_demo.py /tmp/my.bdb --seed 7 \
    --cells flow/two.buda,flow/dnuts1.buda,flow/channel_stress.buda
```

---

## What It Builds

```
chip                         (cell "top", depth 0)
├── i_dnuts1_0 / i_dnuts1_1  (cell "dnuts1")          → u0, u11, u12, v0
├── i_dnuts2_0 / i_dnuts2_1  (cell "dnuts2")          → u1 … u4
└── i_chan_0   / i_chan_1    (cell "channel_stress")  → u_b0 … u_t7
```

- **Leaf cells** are defined from each flat script via `buda2bdb`'s parser
  (`set_die` or the block bounding box becomes the cell size). Only block
  placement is taken from the scripts — their own internal nets are not
  replicated.
- **Two instances** of each cell are laid out in a row inside the `top` cell.
- **Top-level buses** — one per even bit width 4, 6, …, 16 (7 buses, 70 nets) —
  each wire one driver leaf block to 2–5 receiver leaf blocks chosen at random.
  At least one receiver is always in a *different* instance from the driver, so
  every bus is a genuine cross-instance net whose common ancestor is the top:
  `add_net_pins` propagates depth-1 interface pins onto the `chip/i_*`
  ancestors, exactly the shape the hier flow consumes.

With the defaults this yields `chip` → 6 instances → 48 leaf blocks, 7 buses,
70 nets.

---

## Trying It

```bash
./bb                                 # ensure the buda_db module is built
python3 tools/build_hier_demo.py /tmp/hier_demo.bdb
./fp /tmp/hier_demo.bdb              # open in the Floorplanner
```

From there you can derive busterms and run the hierarchy-aware routing flow
(`run_hier_bundler`, `generate_hier_topologies`, `run_planner hier`) — see the
[BDB Reference](BDB_REFERENCE.md) and [Hier Bundler](HIER_BUNDLER.md).

---

## How It Works

The script reuses `buda2bdb.parse_script` + `buda2bdb._cell_size_and_origin` to
read each flat script, then drives the BDB API directly:

1. `add_cell` + a synthetic leaf cell and `add_inst_to_cell` per block → each
   leaf cell's internal structure.
2. `add_cell("top", …)` + `add_inst_to_cell` for the six instances.
3. `add_inst("chip", "top", …)` materializes the whole hierarchy.
4. `add_net_pins` for each bus bit, with cross-instance endpoints.

Unlike `buda2bdb`, it does **not** create per-cell representative instances, so
the result is a single clean tree rooted at `chip`.

---

## See Also

- [`buda2bdb`](BUDA2BDB.md) — flat script → BDB cell (the parser this tool reuses)
- [`bdb2buda`](BDB2BUDA.md) — BDB → flat script (the reverse direction)
