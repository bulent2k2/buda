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

## Comparison

| | TC1 | TC2 |
|---|---|---|
| Starting state | All 40 blocks at origin | All 40 blocks at origin |
| Fixed blocks | None | `io_pad` (100 × 80) |
| Block naming | `blk_00` … `blk_39` | `io_pad`, `blk_00` … `blk_38` |
| Block size range | w: 60–300, h: 40–200 | free: w: 80–300, h: 50–200 |
| Net endpoints | Random pairs from 40 blocks | Random pairs including `io_pad` |
| Optimizer constraint | None | Fixed `io_pad` |
| What it tests | Pure overlap resolution | Constrained placement with anchor |

**TC1** is the harder degenerate-start test: the optimizer must do all the
work of untangling a pile.  **TC2** is a more realistic scenario where an
anchor is already positioned correctly and the rest of the design routes
around it.

---

## Launching via `bfp`

```bash
./bfp tc1              # create /tmp/bfp_tc1.bdb and open in floorplanner
./bfp tc2              # create /tmp/bfp_tc2.bdb and open in floorplanner
./bfp tc1 my.bdb       # write to a custom path, then open
```

See [FLOORPLANNER_USER_GUIDE.md](FLOORPLANNER_USER_GUIDE.md) for the full
GUI workflow, and [FLOORPLANNER_REFERENCE_GUIDE.md](FLOORPLANNER_REFERENCE_GUIDE.md)
for keybindings and dialog options.
