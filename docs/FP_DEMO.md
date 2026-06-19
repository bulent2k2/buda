# BUDA Floorplanner Demo Scenarios (`tools/fp_demo.py`)

`fp_demo.py` generates pre-wired BDB files for quickly exercising the
floorplanner and placement optimizer from the command line.  It is invoked
automatically by the `bfp` launcher but can also be run directly:

```bash
python3 tools/fp_demo.py tc1 [output.bdb]   # default: /tmp/bfp_tc1.bdb
python3 tools/fp_demo.py tc2 [output.bdb]   # default: /tmp/bfp_tc2.bdb
```

Both scenarios share the same physical canvas and connectivity density:

| Property | Value |
|---|---|
| Die | 3000 × 2400 µm |
| Grid | 10 µm |
| Total blocks | 40 |
| Bus nets | 80 (random pairs, undirected) |

---

## TC1 — Overlap Storm

**Seed:** 42

All 40 blocks are stacked at the origin `(0, 0)`.  Block sizes are varied
(width 60–300, height 40–200, snapped to grid), and 80 buses randomly
connect pairs of blocks.

**Purpose:** pure overlap-resolution stress test.  The optimizer must
simultaneously spread 40 fully-overlapping blocks across the die while
minimising wirelength.  There are no positional constraints — the optimizer
has maximum freedom but also maximum disorder to resolve.

### Suggested optimizer settings

- Algorithm: **SA**
- Iterations: **50 000**
- Wire-length weight: **0.0002** (≈ 1/n\_nets, keeps HPWL calibrated vs the overlap penalty)
- Overlap weight: **10** (default)
- No Fixed or Reshapeable constraints

### Expected result

After the run, blocks spread across the die with overlap ≈ 0 and a visible
topology driven by the 80 bus connections.

---

## TC2 — Fixed I/O

**Seed:** 99

One `io_pad` block (100 × 80) is placed at the origin and intended to be
held fixed throughout optimization.  The remaining 39 blocks (`blk_00` …
`blk_38`, width 80–300, height 50–200) start at the origin and must be
spread by the optimizer.  All 80 buses draw endpoints from the full 40-block
set, so `io_pad` participates in the connectivity and anchors one corner of
the wirelength objective.

**Purpose:** constrained placement test.  The optimizer must route the 39
free blocks around a physically fixed anchor, producing a realistic
I/O-driven floorplan.

### Suggested optimizer settings

- Algorithm: **SA**
- Iterations: **50 000**
- Wire-length weight: **0.0002**
- Overlap weight: **10** (default)
- `io_pad` → **Fixed** (check the Fixed column in the Optimize dialog)

### Expected result

`io_pad` stays at `(0, 0)`.  The 39 free blocks spread across the die,
pulled toward `io_pad` by the buses that connect to it.

---

---

## TC3 — Multi-Pad I/O

**Seed:** 13

Four `io_pad` blocks (100 × 80 each) are placed at the four die corners:

| Block | Position |
|---|---|
| `io_pad_bl` | (0, 0) — bottom-left |
| `io_pad_br` | (2900, 0) — bottom-right |
| `io_pad_tl` | (0, 2320) — top-left |
| `io_pad_tr` | (2900, 2320) — top-right |

40 free blocks (`blk_00` … `blk_39`, width 80–300, height 50–200) start at the
origin.  80 buses connect varying subsets of the 44-block set with:

- **Fanout 2–6** per bus, distribution roughly Normal(μ=4, σ=1.2), clamped.
  At least 10 buses have exactly 2 endpoints and at least 10 have exactly 6.
- **Bit width** per bus: a random multiple of 4 drawn from {4, 8, 12, …, 60}.
- Each io_pad appears in **≥ 4 buses**, each time paired with at least one free
  block.

Each bit of each bus is written as a separate `add_net_pins_undirected` call
(name pattern `bus_NNN_bBB`), giving a realistic multi-net workload.

**Purpose:** multi-anchor constrained placement with varied connectivity density.
The four fixed I/O pads anchor all four corners; free blocks must fill the die
interior while minimising HPWL across wide (up to 60-bit) buses.

### Suggested optimizer settings

- Algorithm: **SA**
- Iterations: **50 000**
- Wire-length weight: **0.0002**
- Overlap weight: **10** (default)
- Mark all four `io_pad_*` blocks as **Fixed**

### Expected result

All four io_pads remain at their corner positions.  The 40 free blocks spread
across the die interior, pulled by the buses that connect them to the anchored
I/O pads at all four corners.

---

## Comparison

| | TC1 | TC2 | TC3 |
|---|---|---|---|
| Starting state | All 40 at origin | All 40 at origin | All 44 at origin (io_pads pre-placed) |
| Fixed blocks | None | `io_pad` (1) | 4 × `io_pad_*` at corners |
| Free blocks | 40 (`blk_00`…`blk_39`) | 39 (`blk_00`…`blk_38`) | 40 (`blk_00`…`blk_39`) |
| Total blocks | 40 | 40 | 44 |
| Bus count | 80 | 80 | 80 |
| Fanout per bus | 2 (fixed) | 2 (fixed) | 2–6 (Normal μ=4) |
| Bits per bus | 1 | 1 | 4–60 (multiples of 4) |
| Total nets | 80 | 80 | ~2 000 (varies by seed) |
| What it tests | Pure overlap resolution | Single-anchor constrained | Multi-anchor + mixed bus widths |

---

## Launching via `bfp`

```bash
./bfp tc1              # create /tmp/bfp_tc1.bdb and open in floorplanner
./bfp tc2              # create /tmp/bfp_tc2.bdb and open in floorplanner
./bfp tc3              # create /tmp/bfp_tc3.bdb and open in floorplanner
./bfp tc1 my.bdb       # write to a custom path, then open
```

See [FLOORPLANNER_USER_GUIDE.md](FLOORPLANNER_USER_GUIDE.md) for the full
GUI workflow, and [FLOORPLANNER_REFERENCE_GUIDE.md](FLOORPLANNER_REFERENCE_GUIDE.md)
for keybindings and dialog options.
