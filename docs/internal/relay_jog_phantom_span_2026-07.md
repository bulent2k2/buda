# Phantom dangling span on MST relay-jog connectors (2026-07-22)

A dangling abstract-NUTS segment on `flow/hbundles/06_multipin_stress.buda`
bundle 30 (bus `mp6_b`) — it renders as a long wire to nowhere. Root-caused
below; two solution paths (A analysis-side, B NUTS-side) are laid out, with
Option A already prototyped and measured (entangled — see its section) and
**Option B chosen** as the path forward.

## Problem

Bundle 30 (`mp6_b`, a cross-block 2-bit bundle) selects `TRUNK_H+MST@y135`, a
9-segment MST-hybrid. Segment **seg7** at abstract NUTS:

| | span (x) | length |
|---|---|---|
| abstract NUTS | `[120, 820]` | 700 |
| detailed NUTS bits | `[790.5, 821]` | ~30 |

A **670-unit phantom tail** from x=120 to x=790 connects to nothing. seg7's real
job is to connect seg2 (V @x≈797), seg8 (V @x≈811), seg6 (V @x≈820) — span
`[797, 820]`. Every other segment's abstract/detailed spans agree; only the jog
connectors dangle. It went unnoticed because **DetailedNUTS re-derives each
bit-wire from its real junctions**, so the tail collapses at the bit level and
never becomes an open.

## Root cause (confirmed by instrumenting `do_span_adjustments`)

1. **`complete_relay_junctions` (topology.cpp)** wires the MST relay with a jog
   of connectors — for bundle 30, `seg5`(H) / `seg6`(V) / `seg7`(H). These
   connect **only to other segments**, never to a block face.

2. **ConnTopology's slide derivation cannot anchor a segment with no block-face
   tap**, so it assigns these connectors the **±2³⁰ "unbounded" perp window**.
   Bundle 30's nominal windows:

   ```
   seg5 H (797,365)-(799,365) perp[137,+INF]
   seg6 V (799,365)-(799,415) perp[-INF,+INF]     <- fully unbounded
   seg7 H (797,415)-(820,415) perp[-INF,473]
   ```

   (seg0–seg4, seg8 tap a block face and are bounded normally.)

3. **Abstract NUTS clamps the unbounded window to the design extent**
   (`extract_segments`, nuts.cpp: `interval = [y_grid.front(), y_grid.back()]`),
   giving seg6 a huge legal X-interval (`[109.8, 980.2]`). During the
   sweep/repack it **transiently places seg6 far from its junctions** — the trace
   shows seg6's track roaming `…@545 … @120 … @775 … @820` across iterations.

4. **`do_span_adjustments`' coverage guarantee is extend-only** (nuts.cpp
   ~L413–425: "extend-only, so a legitimate jog contraction is never undone").
   When seg6 is transiently at x=120, seg7's span is extended to *cover* it, and
   because coverage never contracts, seg7 keeps `span_lo=120` permanently — even
   after seg6 settles back at x=820.

5. **DetailedNUTS re-derives each bit-wire from its real junctions**, so the tail
   collapses to ~30 units → no DNUTS open → the final QoR metric is clean.

## Impact — not cosmetic

Scanning all of hbundles/06 for the pattern (abstract span ≫ max detailed bit
span): **6 phantom segments across 4 bundles**.

| bundle | seg | abstract span | detailed max | phantom tail |
|--:|--:|--:|--:|--:|
| 3 | 1 | 119 | 0 | 119 |
| 3 | 2 | 119 | 0 | 119 |
| 3 | 3 | 119 | 0 | 119 |
| 18 | 0 | 668 | 0 | 668 |
| 26 | 0 | 110 | 0 | 110 |
| 30 | 7 | 700 | 30 | 670 |

`detailed_max = 0` means the real bit-wires are zero-length (a pure junction
point) while the abstract span is hundreds of units. And **bundle 18 seg0
(668-unit phantom) is an overlap participant** — it causes **2 of hbundles/06's 4
abstract-NUTS overlaps** (`bid 4 seg1 ↔ bid 18 seg0` and `bid 17 seg3 ↔ bid 18
seg0`, both layer 6). So the phantom tails inflate the abstract overlap count and
drive real (wasted) healing effort, even though DetailedNUTS later hides them.

## Reproduce

```bash
PYTHONPATH=build python3 src/buda_cli.py flow/hbundles/06_multipin_stress.buda --no-viz
# inspect bundle 30 seg7: nuts_result span [120,820] vs detailed bits [790.5,821]
```

The decisive trace was a temporary `std::getenv("BUDA_DBG_B30")` print in
`do_span_adjustments` showing `cover_partners: (30,6)@120` while seg6 roamed its
clamped interval.

## Solution paths

The defect is the **unbounded perp window on junction-only jog connectors** being
turned into a permanent phantom span by extend-only coverage. Two places to
intervene:

### Option A — bound the windows in the analysis (topology_analysis.cpp)

Add a pass to `derive_slide_ranges`: for a connector that taps no block face and
still carries the ±INT_MAX/2 sentinel, intersect its perp window with the
**envelope of its perpendicular partners' nominal along-ranges** (union not
intersection, so a connector whose partners meet at one point does not collapse
to zero slide and get dropped by `filter_pinched`). Fixes the root — seg6 can no
longer roam.

**PROTOTYPED AND MEASURED — works but entangled, NOT taken.** On hbundles/06:

| predicate | bundle 30 phantom | overlaps | opens |
|---|---|---|---|
| baseline | present | 4 | 48 |
| bound any-unbounded-end | gone | **1** | **20** |
| bound both-ends-unbounded only | gone | 3 | 48 |

But slide windows are consumed far beyond NUTS placement, so bounding them shifts
**real behavior**, not just goldens. Fast+mid failures: 24 (broad predicate) /
21 (narrow). The broad predicate broke `test_topo_keepout_mst` (OOB trunks, MST
spine extent), the U-VHV trunk-outside-bbox test, `set_drop_dangling`
clamp/drop tests (which *depend* on unbounded windows existing); the narrow one
shifted planner/realization tests (`test_b44_mst_realization_no_overshoot`,
`test_datapath_multi_trunk_qor`, 6× `test_planner_ksegs`,
`test_planner_charge_pull_target`, `test_planner_signal_tracks`) because a
bounded window changes placement → planner selection → QoR. Both variants break
`test_free_slide_windows_serialize_as_null` — the tell that a "free" (unbounded)
window is a **recognized, load-bearing state** across the stack (web
serialization, MST realization, planner scoring), not merely a derivation gap.
So Option A is a corpus-wide change (planner selection moves), not a local
repair.

### Option B — re-tighten the span in NUTS after convergence (CHOSEN)

A single post-convergence pass that re-derives each abstract span as the **tight
min/max of its final placed junction partners + busterm faces**, collapsing any
extend-only-inflated phantom while still reaching every junction (so it can only
shrink to the exact envelope of what the segment must reach — it cannot open).

Runs **after the planner and after all NUTS placement**, so it **does not touch
analysis windows and cannot shift planner selection** — dodging Option A's worst
ripple (the planner tests). Exact location: the finalize tail of the `solve`
lambda in `NUTSEngine::run` (`nuts.cpp`), in the `tighten_pulls` slot at
**nuts.cpp:2277** — after `resolve_corner_overlaps` (2270), immediately before
`compute_metrics` (2285), so metrics count overlaps on honest spans.

Known caveats to handle in the prototype:
- **Not one call site.** The same finalize sequence is duplicated on the
  incremental/warm paths (`resolve_corner_overlaps` at 2270 main, 2543
  `rerun_bundle_warm`, and the `rerun_layer` path) — the tighten must run in each
  (a shared helper), or ripup's incremental re-solves keep producing phantoms.
- **Must be the last span mutation** before `compute_metrics`: `settle_spans` /
  `repair_overlaps` are extend-only and would re-inflate if run after it.
- **Overlap counts move** (that is the point — bundle 18's phantom overlap
  disappears), so `test_nuts_placement_golden` and overlap-count assertions
  shift even though the planner is untouched — a smaller, more defensible surface
  than Option A.

## Prototype + measurement (Option B, chosen — SHIPPED)

Implemented `tighten_spans_to_reach` (nuts.cpp) and called it as the last span
mutation before `compute_metrics` on all three finalize paths — the main
`run()` solve (after `tighten_pulls`), `rerun_bundle_warm`, and `rerun_layer`.

**Result — surgical and clean:**

- **Bundle 30 seg7 fixed:** abstract span `[120,820]` → **`[797.5, 820]`**, the
  honest extent between its junctions. Detailed bits unchanged (DNUTS already
  re-derived them).
- **QoR corpus neutral-to-better.** b44, big, big2_noviz, mix, mix2, hbundles/06,
  /07, /10, bigHalf, b4_bus_077 all **byte-identical** overlaps/opens; `tc3a`
  **improves** (overlaps 870 → **867** — three phantom overlaps removed). No
  regression anywhere.
- **Fast+mid tier: 1674 passed, 0 failures** — not even a golden shifted (the
  golden flows carry no extend-only phantom, so the tighten is a no-op on them).
  No reference-host golden regen needed.
- **No planner ripple** by construction (the pass runs after the planner and
  after all placement).

Why hbundles/06 overlaps stayed 4 (not the Option-A broad 1): the other flagged
segments there are **not** phantoms — bundle 18 seg0 is a real fan-in trunk
(zero-length bits are a per-bit taper artifact, its overlaps are genuine), bundle
3 seg0 is a real OOB trunk spanning its stubs. Option B correctly touches only
the genuine extend-only over-extension (bundle 30 seg7), leaving real congestion
for the healer — where Option A's 4→1 was an unprincipled placement shuffle from
moving analysis windows.

This is the shipped fix.
