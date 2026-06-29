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
                                 [--optimize sa|ga] [--param KEY=VALUE ...]
                                 [--bloat 20% | --bloat dx=50,dy=80]
```

| Argument / option | Default | Description |
|---|---|---|
| `out.bdb` | `/tmp/hier_demo.bdb` | Output BDB path (overwritten if it exists) |
| `--seed N` | `1` | Seed for the random bus wiring (reproducible) |
| `--cells …` | `flow/dnuts1.buda,flow/dnuts2.buda,flow/channel_stress.buda` | Comma-separated flat scripts to use as leaf cells |
| `--no-cell-nets` | *(off)* | Emit only the top-level buses (lean ~70-net demo) |
| `--no-busterms` | *(off)* | Skip busterm derivation |
| `--optimize sa\|ga` | *(off)* | Place the top cell's six instances in 2D to shorten the top buses |
| `--param KEY=VALUE` | — | Optimizer knob (repeatable); see below |
| `--bloat …` | *(off)* | Inflate instances *for optimization only* to leave routing channels |

```bash
# Defaults
python3 tools/build_hier_demo.py

# Custom output, seed, and cells
python3 tools/build_hier_demo.py /tmp/my.bdb --seed 7 \
    --cells flow/two.buda,flow/dnuts1.buda,flow/channel_stress.buda
```

---

## Optimizing the Top-Cell Placement

By default the six instances are laid out in a fixed row, so the random
cross-instance top buses are long.  `--optimize sa|ga` runs the
`PlacementOptimizer` (simulated annealing or genetic algorithm) over the **six
depth-1 instances**, placing them in 2D to minimize HPWL of the top buses (plus
area and overlap).  The cells' internal contents follow their instance
automatically.

```bash
# SA, 20k iterations, weight wire-length 2×, 25% bloat for routing channels
python3 tools/build_hier_demo.py /tmp/opt.bdb --optimize sa \
    --param iter=20k --param wl=2.0 --bloat 25%
```

**`--param KEY=VALUE`** (repeatable) tunes the optimizer.  Values accept `k`/`m`
suffixes (`iter=20k` → 20000).  Friendly keys:

| Key | SA (`run_sa`) | GA (`run_ga`) |
|---|---|---|
| `iter` | `max_iter` (default 20000) | `generations` (default 200) |
| `time` / `runtime` | runtime budget (`5s`/`2m`/`1h`); see below | same |
| `patience` | early-stop after N non-improving checks | same |
| `wl` / `area` / `ovlp` | `w_wl` / `w_area` / `w_ovlp` | same |
| `seed` | `seed` (defaults to `--seed`) | same |
| `pop` / `mutation` / `crossover` | — | `population` / `mutation_rate` / `crossover_rate` |
| `t_init` / `t_min` / `alpha` | same | — |

Any raw `run_sa`/`run_ga` argument name also works; an argument invalid for the
chosen method reports a clear error.

**Runtime budget** — `--param time=2m` (or `30s`, `1h`) runs the optimizer for a
soft wall-clock budget instead of a fixed iteration count.  The optimizer
calibrates per-iteration cost on the current machine, sizes the run to fit (and,
for SA, anneals the cooling schedule over that estimate), and stops when the
budget elapses.  Combine with `iter` for a hard ceiling under the soft cap
(`--param time=10s --param iter=50k`).  Timed runs also **stop early on
convergence** by default (`patience=10`) — no meaningful improvement for ~10
checkpoints; pass `--param patience=0` to disable, or a larger value to run
longer.  `patience` also works in iteration mode (off by default there).

```bash
# Run GA for one minute, or stop sooner if it converges
python3 tools/build_hier_demo.py /tmp/opt.bdb --optimize ga --param time=1m
```

**`--bloat`** reduces utilization so the optimizer leaves channel space for
routing.  Each instance is inflated **only during optimization** — `--bloat 20%`
scales both dimensions ×1.2, `--bloat dx=50,dy=80` (or `--bloat 50`) adds an
absolute margin.  The real-sized instance is then centered in its bloated slot,
leaving channels on all sides.  More bloat → lower density → fewer track overlaps
(at the cost of a larger die).

The build prints the optimizer result (`hpwl / area / overlap / iterations`) and
the resulting top-cell size.  The printed hier-flow steps are unchanged.

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
- **Two instances** of each cell are laid out in a row inside the `top` cell
  (or placed in 2D by the optimizer — see
  [Optimizing the Top-Cell Placement](#optimizing-the-top-cell-placement)).
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
def_layer 4 M4 H TOP 44.44     # TOP routing layers (the BDB carries no tech)
def_layer 5 M5 V TOP 50.00
add_blocks_from_bdb 0          # chip (top container)
add_blocks_from_bdb 1 skip     # the 6 instances
add_blocks_from_bdb 2 skip     # the 48 leaf blocks
run_hier_bundler depth 2
generate_hier_topologies
run_planner hier
run_nuts
check_connectivity nuts
EOF
```

Two setup steps the BDB can't carry on its own:

- **`def_layer`** registers the TOP routing layers (M4 horizontal, M5 vertical)
  the planner and NUTS use.  Without them `check_connectivity` reports every
  segment as on an *undefined* layer (`unbuildable`).
- **`add_blocks_from_bdb`** loads the BDB components into the flat floorplan at
  each routing level so NUTS (and topology generation) build their Hanan grid and
  keepouts from the **real block edges** (`skip` = only that exact depth).
  Omitting them leaves the floorplan empty (NUTS then falls back to a coarse grid
  derived from the routes themselves).

With both in place the dnuts1/dnuts2 cell bundles appear as templates with two
instances each, expanded and planned alongside the top-level buses, and
`check_connectivity nuts` reports **no opens**.  (The dense random demo will
still show track overlaps — genuine congestion, not a connectivity break.)  See
the [BDB Reference](BDB_REFERENCE.md) and [Hier Bundler](HIER_BUNDLER.md).

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
