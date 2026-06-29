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
                                 [--no-cell-nets] [--no-busterms]
```

| Argument / option | Default | Description |
|---|---|---|
| `out.bdb` | `/tmp/hier_demo.bdb` | Output BDB path (overwritten if it exists) |
| `--seed N` | `1` | Seed for the random bus wiring (reproducible) |
| `--cells …` | `flow/dnuts1.buda,flow/dnuts2.buda,flow/channel_stress.buda` | Comma-separated flat scripts to use as leaf cells |
| `--no-cell-nets` | *(off)* | Emit only the top-level buses (lean ~70-net demo) |
| `--no-busterms` | *(off)* | Skip busterm derivation |

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
  (`set_die` or the block bounding box becomes the cell size), including the
  blocks **and** the cell's own `add_net`/`add_bus` buses (and any nets pulled in
  by a `source`d file).
- **Two instances** of each cell are laid out in a row inside the `top` cell.
- **Cell-internal buses** are replicated into every instance with
  instance-qualified, globally-unique names (e.g. `chip/i_dnuts1_0/n11_0` and
  `chip/i_dnuts1_1/n11_0`) — the same representation `import_verilog` produces.
  Each stays inside its instance (a cell-local net). The hier bundler then
  **templates** the two occurrences of each cell into one cell-level bundle
  (keyed on the component hierarchy + cell type), solved once and expanded per
  instance — so multi-instance cells are handled correctly.
- **Top-level buses** — one per even bit width 4, 6, …, 16 (7 buses, 70 nets) —
  each wire one driver leaf block to 2–5 receiver leaf blocks chosen at random.
  At least one receiver is always in a *different* instance from the driver, so
  every bus is a genuine cross-instance net whose common ancestor is the top:
  `add_net_pins` propagates depth-1 interface pins onto the `chip/i_*`
  ancestors, exactly the shape the hier flow consumes.
- **Busterms** are derived (`BustermGen.derive(2)`) so the BDB is immediately
  ready for `run_hier_bundler` / `generate_hier_topologies`.

With the defaults this yields `chip` → 6 instances → 48 leaf blocks, and **all**
buses present: 7 top buses (70 nets) + 688 replicated cell-internal nets = 758
nets. `--no-cell-nets` restores the lean 70-net version (top buses only).

---

## Trying It

```bash
./bb                                 # ensure the buda_db module is built
python3 tools/build_hier_demo.py /tmp/hier_demo.bdb
./fp /tmp/hier_demo.bdb              # open in the Floorplanner
```

Busterms are already derived by the builder, so you can plan **all** buses
together — cell-internal and top-level — straight away with the hier flow.  Use
**`depth 2`**: the cell-internal buses connect leaf blocks (depth 2), so the
bundler must reach that depth to form (and template) the cell-level bundles:

```bash
PYTHONPATH=build python3 src/buda_cli.py <<'EOF'
open_bdb /tmp/hier_demo.bdb
add_blocks_from_bdb 0          # chip (top container)
add_blocks_from_bdb 1 skip     # the 6 instances
add_blocks_from_bdb 2 skip     # the 48 leaf blocks
run_hier_bundler depth 2
generate_hier_topologies
run_planner hier
run_nuts
dump_hbundles expanded
EOF
```

`add_blocks_from_bdb` loads the BDB components into the flat floorplan at each
routing level so NUTS (and topology generation) build their Hanan grid and
keepouts from the **real block edges** — `skip` adds only components at that
exact depth.  Omitting them leaves the floorplan empty (NUTS then falls back to a
coarse grid derived from the routes themselves).  The dnuts1/dnuts2 cell bundles
appear as templates with two instances each, expanded and planned alongside the
top-level buses.  See the [BDB Reference](BDB_REFERENCE.md) and
[Hier Bundler](HIER_BUNDLER.md).

---

## How It Works

The script reuses `buda2bdb.parse_script` + `buda2bdb._cell_size_and_origin` to
read each flat script, then drives the BDB API directly:

1. `add_cell` + a synthetic leaf cell and `add_inst_to_cell` per block → each
   leaf cell's internal structure.
2. `add_cell("top", …)` + `add_inst_to_cell` for the six instances.
3. `add_inst("chip", "top", …)` materializes the whole hierarchy.
4. Replicate each cell's internal nets into every instance via `add_net_pins`
   (`chip/<inst>/<block>.<port>` endpoints, `chip/<inst>/<net>` names).
5. `add_net_pins` for each top bus bit, with cross-instance endpoints.
6. `BustermGen.derive(2)` so the BDB is plan-ready.

Unlike `buda2bdb`, it does **not** create per-cell representative instances, so
the result is a single clean tree rooted at `chip`.  Net replication mirrors how
`import_verilog` represents a multiply-instantiated module's internal nets, which
is what lets the hier bundler template the instances.

---

## See Also

- [`buda2bdb`](BUDA2BDB.md) — flat script → BDB cell (the parser this tool reuses)
- [`bdb2buda`](BDB2BUDA.md) — BDB → flat script (the reverse direction)
