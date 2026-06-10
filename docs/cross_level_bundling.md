# Cross-Level Hierarchical Bundling

## Overview

This document covers the work done to extend BUDA's hierarchical bundler to correctly handle **cross-level buses** — buses where the driver endpoint and receiver endpoint are at different hierarchy depths (e.g., a depth-1 blk drives a depth-2 leaf, or a depth-0 chip drives a depth-2 leaf).

---

## Problem Statement

The `HierarchicalBundler` groups nets into HBundles by looking at component names at each hierarchy depth. `BDB::add_net_pins` propagates ancestor pins for every endpoint — a bus with driver `left/top` (depth 1) and receiver `right/top/lo` (depth 2) also gets ancestor pins at `left` (depth 0) and `right` (depth 0).

This meant a cross-level bus like `xl_b2l_ht` (left/top D1 → right/top/lo D2) ended up with the same depth-0 signature `DRV:left|REC:right` as the same-level bus `x_top` (left/top/hi D2 → right/top/lo D2). They were incorrectly merged into the same HBundle.

The same collision happened at depth 1: both got signature `DRV:left/top|REC:right/top`, so they merged at D1 as well.

---

## Test Vehicle: `flow/hbundles/08_cross_level.buda`

Clones the three-level hierarchy from `04_deep_hierarchy.buda` (chip→blk→leaf) and adds eight cross-level buses:

| Bus | Driver | Receiver | Type |
|-----|--------|----------|------|
| `xl_b2l_ht` | `left/top` (D1) | `right/top/lo` (D2) | blk→leaf, cross-chip, H top |
| `xl_b2l_hb` | `left/bot` (D1) | `right/bot/lo` (D2) | blk→leaf, cross-chip, H bot |
| `xl_l2b_dt` | `right/top/hi` (D2) | `left/bot` (D1) | leaf→blk, cross-chip, diagonal |
| `xl_l2b_db` | `right/bot/hi` (D2) | `left/top` (D1) | leaf→blk, cross-chip, anti-diag |
| `xl_ic_b2l` | `left/top` (D1) | `left/bot/lo` (D2) | blk→leaf, intra-chip, downward |
| `xl_ic_l2b` | `left/bot/hi` (D2) | `left/top` (D1) | leaf→blk, intra-chip, upward |
| `xl_c2l` | `left` (D0) | `right/bot/hi` (D2) | chip→leaf, cross-chip |
| `xl_l2c` | `right/top/hi` (D2) | `right` (D0) | leaf→chip (degenerate — skipped) |

Before the fix, the bundler produced 15 HBundles (D0:2 D1:6 D2:7) with cross-level buses incorrectly merged alongside same-level buses.

---

## Fix

### 1. HBundle: new cross-level fields (`src/bundler.h`)

Four new fields distinguish cross-level bundles from same-level ones:

```cpp
int drv_spec_depth = -1;           // depth of the specified driver endpoint (-1 = same-level)
int rcv_spec_depth = -1;           // depth of the deepest specified receiver endpoint
std::string drv_spec_path;         // component path of driver (e.g. "left/top")
std::vector<std::string> rcv_spec_paths;  // component paths of receivers (e.g. ["right/top/lo"])
```

`drv_spec_depth >= 0` is the flag that distinguishes a cross-level bundle from a same-level one.

### 2. Pre-computation of leaf endpoints (`src/bundler.cpp`)

Before the per-depth bundling loop, `HierarchicalBundler::run()` now pre-computes per-net "leaf info":

- **Driver spec**: the deepest OUTPUT pin in the BDB pin table for each net → this is the actual specified driver endpoint (not a propagated ancestor).
- **Receiver spec**: the deepest INPUT pin(s) → actual receiver endpoints.
- **Cross-level detection**: `drv_spec_depth != rcv_spec_depth`.
- **Bundle depth**: the number of common leading path segments between driver and receiver paths. This is where the bundle will be placed in the hierarchy.
  - `path_common("left/top", "right/top/lo") = 0` → D0 bundle (cross-chip)
  - `path_common("left/top", "left/bot/lo") = 1` → D1 bundle (intra-chip)
- **Degenerate detection**: skips nets where one endpoint is an ancestor of the other (e.g., `right/top/hi → right`).

### 3. Modified depth loop (`src/bundler.cpp`)

The depth loop now has two sub-passes:

**Pass 2a — cross-level nets at their bundle_depth:**  
Cross-level nets whose `bundle_depth == depth` are grouped using the actual leaf paths as the signature key (e.g., `DRV:left/top|REC:right/top/lo,`). This produces a signature distinct from any same-level bundle at the same depth.

**Pass 2b — same-level nets:**  
Cross-level nets are erased from `ep_map` before the existing grouping logic runs. This prevents them from accidentally merging with same-level bundles.

### 4. Python bindings (`src/bindings.cpp`)

The four new HBundle fields are exposed to Python:
```python
b.drv_spec_depth   # int
b.rcv_spec_depth   # int
b.drv_spec_path    # str
b.rcv_spec_paths   # list[str]
```

### 5. Topology generation: Case (c) (`src/buda_cli.py`)

`generate_hier_topologies` had two cases (a: cell-local, b: BDB depth-D). A third case was added:

**Case (c)** — cross-level bundles (`b.drv_spec_depth >= 0`):  
Builds a custom `Floorplan` containing exactly the two actual endpoint blocks at their absolute coordinates, regardless of depth. The topology generator then routes between those two specific blocks, producing geometrically correct candidates.

```python
elif b.drv_spec_depth >= 0:
    fp = buda.Floorplan()
    fp.add_block(b.drv_spec_path, ...)   # e.g. left/top at its D1 bbox
    fp.add_block(rcv_path, ...)          # e.g. right/top/lo at its D2 bbox
    tg = self._make_topo_gen(fp, ...)
    w.candidates = tg.generate_candidates(b.drv_spec_path, b.rcv_spec_paths)
```

`_clone_hbundle_with_id` was also updated to copy the four new fields.

---

## Results

After the fix, `08_cross_level.buda` produces:

```
18 HBundles (D0:6  D1:5  D2:7)
  D0: xl_b2l_ht, xl_b2l_hb, xl_l2b_dt, xl_l2b_db, xl_c2l  [5 cross-level]
      x_top + x_bot merged                                    [1 same-level]
  D1: xl_ic_b2l, xl_ic_l2b                                   [2 cross-level]
      l_tb (cell-level), left/bot→right/bot, left/top→right/top [3 same-level]
  D2: b_lohi × 4 instances (cell-level), l_tb D2, x_top/x_bot D2 descendants
98 candidates, 21 expanded wrappers
26 segments placed; 0 NUTS overlaps
check_connectivity: Success — no opens found
```

Each cross-level bus now has its own dedicated HBundle and its topology routes between the correct endpoint blocks.

**Known limitation**: `xl_l2c` (`right/top/hi → right`) is a degenerate case where the receiver (`right`, D0) is a direct ancestor of the driver (`right/top/hi`, D2). This case is silently dropped with a note; supporting it would require routing from inside a block to its own boundary, which needs a different routing model.

---

## Additional Test Vehicle: `flow/hbundles/07_wide_fan_stress.buda`

Also added in this session: a stress test for 7–12 pin wide-fan multicast buses on a 3×2 block grid (same layout as 05/06). Tests the HBundle pipeline at the widest multicast fan sizes:

- D0: `mp7_a/b` through `mp12_a/b` — 12 cross-chip multicast buses
- D1: `d1_mp7` (6-receiver intra-blk fan), `d1_mp8` (7-receiver = full intra-blk max)
- Bit widths: [4] for 7–8 pin, [2] for 9–10 pin, [1] for 11–12 pin

Pipeline result: 51 HBundles (D0:9 D1:18 D2:24), 855 candidates, connectivity passes.

---

## Files Changed

| File | Change |
|------|--------|
| `src/bundler.h` | Added `drv_spec_depth`, `rcv_spec_depth`, `drv_spec_path`, `rcv_spec_paths` to `HBundle` |
| `src/bundler.cpp` | Added `#include <climits>`; rewrote `HierarchicalBundler::run()` with pre-computation pass and two-phase depth loop |
| `src/bindings.cpp` | Exposed 4 new HBundle fields; updated `_clone_hbundle_with_id` |
| `src/buda_cli.py` | Added Case (c) to `generate_hier_topologies`; updated `_clone_hbundle_with_id` |
| `flow/hbundles/07_wide_fan_stress.buda` | New stress test: 7–12 pin wide-fan multicast buses |
| `flow/hbundles/08_cross_level.buda` | New test: cross-level buses; comment updated to reflect correct pipeline result |
