# `build_hier_demo` — Hierarchical Demo BDB Builder

`tools/build_hier_demo.py` assembles a **hierarchical BDB from scratch** out of
existing flat `.buda` scripts. It defines each script as a reusable **cell**,
instantiates each cell a configurable number of times (default **twice**) inside
a `top` cell, and adds a configurable number of top-level **buses** (default 7,
bit counts cycling 4 … 16) that wire random subsets of the leaf blocks across
those instances — a quick, self-contained design to exercise the hierarchy-aware
flow and the Floorplanner.  `--instances` and `--buses` scale it up into larger,
more realistic demos and test cases.

---

## Usage

```bash
python3 tools/build_hier_demo.py [out.bdb] [--seed N] [--cells a,b,c]
                                 [--path DIR] [--instances N] [--buses N]
                                 [--no-cell-nets] [--no-busterms]
                                 [--optimize sa|ga] [--param KEY=VALUE ...]
                                 [--bloat 20% | --bloat dx=50,dy=80]
```

| Argument / option | Default | Description |
|---|---|---|
| `out.bdb` | `/tmp/hier_demo.bdb` | Output BDB path (overwritten if it exists) |
| `--seed N` | `1` | Seed for the random bus wiring (reproducible) |
| `--cells …` | `dnuts1,dnuts2,channel_stress` | Comma-separated cells. Each entry is a flat `.buda` script **or an existing hierarchical BDB** (`.bdb` / `.bdb.sql` — see [BDB cells](#bdb-cells-instantiate-a-whole-design-as-a-cell)), optionally named `NAME=PATH` (`big2=flow/big_data_test/big2/tc3b_flat_x5.buda`). The `.buda` extension is inferred when omitted; a bare name is looked up in `--path` (default `flow/`), an absolute path is used as-is, and a directory-qualified entry (`flow/two.buda`) stays relative to the repo root |
| `--path DIR` | `flow/` | Directory holding the leaf `.buda` files for bare `--cells` names |
| `--instances SPEC` | `2` | Instances per cell: one int (all cells), a positional list in `--cells` order (`1,4,2`), or named (`dnuts1=3,channel_stress=1`; unlisted → 2). Each count ≥1 |
| `--buses N` | `7` | **Base** top-level cross-instance buses; bit widths cycle `[4,6,8,10,12,14,16]` (≥0). Extra buses are appended so every instance is wired to ≥3 top buses |
| `--layout row\|stacked` | `row` | Instance placement. `row` lays every instance in one row at y=0; **`stacked`** gives each cell type its own COLUMN in `--cells` order and stacks that cell's instances vertically. Incompatible with `--optimize` |
| `--channel V` | `200` | Minimum gap between stacked instances and between columns. The realized vertical gap is whatever `--grid` rounds the pitch up to, so it is ≥ V |
| `--grid Q` | *(off)* | Snap the vertical stack pitch UP to a multiple of Q (`--layout stacked` only) — see [on-grid stacking](#on-grid-stacking) |
| `--column-align bottom\|top\|center` | `bottom` | Where a column sits (`--layout stacked` only): `bottom` starts every column at y=0, `top` makes the tops flush with the tallest, `center` splits the slack equally above and below. Columns move as RIGID blocks, so `--grid` alignment survives; moving individual instances would not |
| `--mirror-upper` | *(off)* | Flip every instance whose centre is above the die centreline upside down, so the upper half mirrors the lower — contents included. Combine with `--column-align center`. Refuses (LOUD) if any instance *straddles* the centreline, since such an occurrence cannot mirror onto itself — use an **even** instance count per column |
| `--no-cell-nets` | *(off)* | Emit only the top-level buses (lean ~70-net demo) |
| `--no-busterms` | *(off)* | Skip busterm derivation |
| `--optimize sa\|ga` | *(off)* | Place the top cell's instances in 2D to shorten the top buses |
| `--param KEY=VALUE` | — | Optimizer knob (repeatable); see below |
| `--bloat …` | *(off)* | Inflate instances *for optimization only* to leave routing channels |

```bash
# Defaults
python3 tools/build_hier_demo.py

# Custom output, seed, and cells (extension inferred; looked up in flow/)
python3 tools/build_hier_demo.py /tmp/my.bdb --seed 7 \
    --cells two,dnuts1,channel_stress

# Leaf cells from a different directory
python3 tools/build_hier_demo.py /tmp/my.bdb --path ~/my_cells --cells alu,fifo,ctrl

# Bigger demo: 4 instances per cell and 16 base top-level buses
python3 tools/build_hier_demo.py /tmp/big.bdb --instances 4 --buses 16

# Per-cell instance counts (positional, in --cells order) — 1 dnuts1, 4 dnuts2, 2 chan
python3 tools/build_hier_demo.py /tmp/mix.bdb --instances 1,4,2

# Per-cell instance counts (named); unlisted cells default to 2
python3 tools/build_hier_demo.py /tmp/mix.bdb --instances dnuts2=4,channel_stress=1
```

## BDB cells — instantiate a whole design as a cell

A `--cells` entry ending in `.bdb` / `.bdb.sql` names an **existing
hierarchical BDB** to import as a cell — so a whole routed-design fixture
(mix2, a prior build of this very tool, an imported design) becomes a reusable
cell inside a bigger chip.  The source design is flattened **one structural
level deep**: every LEAF component becomes a child block of the new cell,
named by its root-relative path with `/` folded to `__` (so
multiply-instantiated sub-blocks stay unique: `chip/i_dnuts1_0/u0` →
`i_dnuts1_0__u0`), and every net with ≥2 leaf-pin endpoints is carried over
with the same folded names — the source's own *top-level* buses simply become
cell-internal nets of the imported cell.  Interface pins on intermediate
ancestors are skipped (they are the same logical connection propagated upward;
`add_net_pins` recreates them in the new hierarchy).  Driver selection and the
`unknown`/`inout` direction classes mirror `bdb2buda`'s export walk.

The chip-scale vehicle in `flow/chip/` is built exactly this way — three
instances each of big2 (flat script) and mix2 (BDB cell) with 100 top buses:

```bash
python3 tools/build_hier_demo.py chip.bdb \
    --cells big2=flow/big_data_test/big2/tc3b_flat_x5.buda,mix2=flow/rnr/mix2.bdb.sql \
    --instances 3 --buses 100 --optimize sa --param iter=60k --bloat 25%
```

`NAME=PATH` names a cell explicitly (the default is the basename minus its
extension); duplicate resolved cell names are a hard error.

**`--nest-bdb-cells`** switches BDB cell entries from the one-level flatten to
a **nested** import that PRESERVES the source's internal hierarchy: the
source's cell tree is reconstructed from its component tree (one
representative instance per cell type defines the cell — congruent replicas
required, a size mismatch is a loud error; non-root cell types are namespaced
`<cell>__<srccell>`), and nets keep root-relative *hierarchical* paths that
`add_net_pins` re-propagates through the deep tree.  Instantiating the
imported cell then materializes the source's own instances one level deeper —
a 2-level source becomes a 3-level chip (`chip/i_mix2_0/i_dnuts1_0/u0` at
depth 3).  The depth-3 vehicles `flow/chip/chip3*.buda` are built this way;
drive them with `derive_busterms 3`, `add_blocks_from_bdb 3 skip`, and
`run_hier_bundler depth 3`.

## Output is written atomically

The build writes to `<out>.part` and renames it into place **only on
completion**, and it ignores SIGPIPE.  A build that dies partway — the classic
being `... | head -N`, since this tool prints one line per bus — therefore
leaves no output BDB at all, rather than a plausible-looking one missing nets,
pins and every busterm.  That failure is worth designing against: such a file
reads as a valid design and routes fast and clean because most of the work is
simply absent, so it silently corrupts any measurement taken from it (it did
exactly that to `flow/chip/chip_stack` — see that ReadMe's correction note).

## On-grid stacking

`--layout stacked` places each cell type in its own **column** (left to right in
`--cells` order) and stacks that cell's instances **vertically**.  Two properties
follow, and together they make the instances *phase-aligned as placed*:

* every instance of a cell shares one **x**, so their vertical-layer track phase
  is identical by construction;
* with `--grid Q` the vertical pitch is snapped **up** to a multiple of Q, so
  their horizontal-layer phase is identical too.

Choose Q as the **LCM of the track pitches of the horizontal layers the cells
may use**.  The tool deliberately does not know the technology — the caller
does.  For `flow/chip/chip_tracks.buda` with `reserve_top_layers 2` (cells
capped at M9) the horizontal pitches are M2 18, M4 18, M6 24, M8 56, so
Q = 504.

```bash
python3 tools/build_hier_demo.py flow/chip/chip_stack.bdb \
    --cells big2=flow/big_data_test/big2/tc3b_flat_x5.buda,mix2=flow/rnr/mix2.bdb.sql \
    --instances big2=2,mix2=4 --buses 100 --layout stacked --channel 1000 --grid 504
```

```
  stack big2           2 x  pitch     7560 (cell 6300 + channel 1260), on a 504 grid
  stack mix2           4 x  pitch     3528 (cell 2360 + channel 1168), on a 504 grid
  top  top                9430 x  13860  (6 instances)
```

`--column-align` then shifts each column as a **rigid block** — `top` to make
the tops flush, `center` to split the slack equally above and below.  Only
whole-column shifts are safe: the slack is not generally a multiple of Q, so
moving just some instances would break the very phase alignment `--grid`
bought.  A column's *absolute* offset is free — only intra-column Δy matters.

`--mirror-upper` goes further and flips the instances above the centreline, so
the upper half mirrors the lower one, contents included.  An instance whose
interior *crosses* the centreline is refused rather than silently emitted: it
cannot map onto its own reflection unless its contents happen to be
self-symmetric, and flipping it would not help.  The usual trigger is an ODD
instance count in a centred column, which puts the middle occurrence exactly on
the centreline — use an even count per mirrored column.  **That costs the
phase**: a flipped instance aligns only if the track pattern is
reflection-symmetric about its centre, and with `flow/chip/chip_tracks.buda`
M2/M4/M8 admit only half-integer reflection axes (signal pitch 1.5 and 4.5),
which an integer placement can never hit.  `check_template_tracks` then reports
MISALIGNED and the flow must solve those instances individually.  Mirror
symmetry and solve-once-copy are not simultaneously achievable under such a
technology — see `flow/chip/ReadMe.md` for the measured comparison.

The payoff is that a bottom-up flow needs **no `align_bottom_up`**:
`check_template_tracks` reports `ALIGNED` straight from the placement (built
vehicle: `flow/chip/chip_stack_bottomup.buda`, big2 2 instances / mix2 4
instances, both ALIGNED), where an SA-placed design has to be nudged onto a
common phase first.

Pick Q against the layers the cells will actually use: the full 10-layer chip
stack would need LCM(18,18,24,56,74) = 18648, which is 12k of dead channel
around a 6300-tall cell.  Reserving the top pair for the top level is what
makes a tight on-grid pitch affordable.

## `--align-occurrences` — shared-row/column occurrence alignment

After placement (SA/GA or the default row), snap same-cell instances onto
SHARED coordinates: per cell class, each instance's y (then x) is snapped to
the class **median** — minimal total movement — unless the move would overlap
another instance's real rect (that instance keeps its position).  Congruent
instances sharing a row/column make their block edges **coincide** in the
flat Hanan grid, shrinking the planner's cut/band grid (the chip twin
measured −33% Hanan crossings).  A study knob: the measured verdict (see
`flow/chip/ReadMe.md`) is that the grid reduction helped the old
copy-dominated planner but is QoR-negative on dense placements (the snap
closes bloat channels), so it is off by default.

---

Whatever the instance count, **every instance is guaranteed to be wired to at
least three top-level buses**.  `--buses N` sets the count of *base* random
cross-instance buses; if that leaves any instance touched by fewer than three,
extra buses are appended (driven from the under-covered instance) until the
guarantee holds.  Each bus connects at the *instance* level — it reaches some
leaf block inside the instance, not necessarily every lower-level block.

---

## Optimizing the Top-Cell Placement

By default the instances are laid out in a fixed row, so the random
cross-instance top buses are long.  `--optimize sa|ga` runs the
`PlacementOptimizer` (simulated annealing or genetic algorithm) over the
**depth-1 instances**, placing them in 2D to minimize HPWL of the top buses (plus
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

While the optimizer runs the build prints a live progress line — `10% 20% …
100%` — followed by the optimizer result (`hpwl / area / overlap / iterations`,
**total runtime**) and the resulting top-cell size.  A timed run (`--param
time=…`) that converges early stops before 100% — the iteration count and
runtime then show how far it got.  The printed hier-flow steps are unchanged.

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
- **`--instances` instances** of each cell (default 2; a single count, a
  positional per-cell list, or named per-cell counts) are laid out in a row
  inside the `top` cell (or placed in 2D by the optimizer — see
  [Optimizing the Top-Cell Placement](#optimizing-the-top-cell-placement)).
- **Cell-internal buses** are replicated into every instance with
  instance-qualified, globally-unique names (e.g. `chip/i_dnuts1_0/n11_0` and
  `chip/i_dnuts1_1/n11_0`) — the same representation `import_verilog` produces.
  Each stays inside its instance (a cell-local net). The hier bundler then
  **templates** the two occurrences of each cell into one cell-level bundle
  (keyed on the component hierarchy + cell type), solved once and expanded per
  instance — so multi-instance cells are handled correctly.
- **Top-level buses** — `--buses` *base* buses (default 7), bit widths cycling
  the palette `[4,6,8,10,12,14,16]` — each wire one driver leaf block to 2–5
  receiver leaf blocks chosen at random.  When more than one instance exists, at
  least one receiver is always in a *different* instance from the driver, so every
  bus is a genuine cross-instance net whose common ancestor is the top:
  `add_net_pins` propagates depth-1 interface pins onto the `chip/i_*` ancestors,
  exactly the shape the hier flow consumes.  (With a single instance the buses
  fall back to intra-instance receivers.)  **Extra buses are appended** as needed
  so every instance is wired to **at least three** top buses — driven from any
  instance the base set left under-covered.
- **Busterms** are derived (`BustermGen.derive(2)`) so the BDB is immediately
  ready for `run_hier_bundler` / `generate_hier_topologies`.

With the defaults this yields `chip` → 6 instances → 48 leaf blocks, and **all**
buses present: 7 base top buses + 2 coverage buses (80 nets) + 688 replicated
cell-internal nets = 768 nets. `--no-cell-nets` restores the lean top-buses-only
version. (The exact coverage-bus count depends on the seed and instance counts.)

---

## Trying It

```bash
bin/bb                                 # ensure the buda_db module is built
python3 tools/build_hier_demo.py /tmp/hier_demo.bdb
bin/fp /tmp/hier_demo.bdb              # open in the Floorplanner
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
2. `add_cell("top", …)` + `add_inst_to_cell` for each cell's instances (the
   per-cell `--instances` count).
3. `add_inst("chip", "top", …)` materializes the whole hierarchy.
4. Replicate each cell's internal nets into every instance via `add_net_pins`
   (`chip/<inst>/<block>.<port>` endpoints, `chip/<inst>/<net>` names).
5. `add_net_pins` for each top bus bit, with cross-instance endpoints — including
   the extra buses appended to guarantee ≥3 top buses per instance.
6. `BustermGen.derive(2)` so the BDB is plan-ready.

Unlike `buda2bdb`, it does **not** create per-cell representative instances, so
the result is a single clean tree rooted at `chip`.  Net replication mirrors how
`import_verilog` represents a multiply-instantiated module's internal nets, which
is what lets the hier bundler template the instances.

---

## See Also

- [`buda2bdb`](BUDA2BDB.md) — flat script → BDB cell (the parser this tool reuses)
- [`bdb2buda`](BDB2BUDA.md) — BDB → flat script (the reverse direction)
