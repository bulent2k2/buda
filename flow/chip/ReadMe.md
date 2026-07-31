# flow/chip — the next-level hier vehicle

Three instances each of **big2** (the flat `tc3b_flat_x5.buda` script as a
cell) and **mix2** (the hierarchical `flow/rnr/mix2.bdb.sql` imported as a BDB
cell — `build_hier_demo`'s one-level flatten), SA-placed with 25% bloat
channels, plus **100** random cross-instance top buses:
6 instances, 432 leaf blocks, 13320 nets — the largest corpus vehicle.

Generator (deterministic — fixed seed + iteration budget, no wall-clock)
===
```
python3 tools/build_hier_demo.py chip.bdb \
    --cells big2=flow/big_data_test/big2/tc3b_flat_x5.buda,mix2=flow/rnr/mix2.bdb.sql \
    --instances 3 --buses 100 --optimize sa --param iter=60k --bloat 25%
python3 tools/bdb_serialize.py dump chip.bdb chip.bdb.sql
```

Repro
===
```
buda flow/chip/chip_topdown.buda      # plain hier pipeline, no healers
buda flow/chip/chip_bottomup.buda     # bottom-up templates + align_bottom_up
```

Both flows run WITHOUT healers for fast experimentation — add
`negotiate_congestion` / `ripup_reroute` / `refine_selection` rounds manually
when studying healing at this scale.  `chip_tracks.buda` is the shared
6-layer stack + track patterns (same technology as big2's `tracks4top.buda`
and mix2's `mix_tracks.buda`, which are identical).

Baseline endpoints (2026-07-30, x86 reference build; both in the QoR corpus)
===
| Flow | overlaps | unplaced | viol_bundles | abstract WL | detailed WL | sec |
|---|---|---|---|---|---|---|
| chip_topdown | 255 | 3185 | 163 | 3036142 | 61969962 | 148 |
| chip_bottomup | 490 | 2285 | 110 | 2659098 | 46670260 | 130 |

(Refreshed 2026-07-31 after the `band_span_charge` default flip (#530) —
which helps this vehicle: topdown overlaps 452→255, bottomup unplaced
2610→2285 — and the planner-runtime fixes below, which cut the topdown
total 391s→148s.  The original 2026-07-30 baseline: topdown 452/3341/163
at 391s, bottomup 527/2610/136 at 187s.)

The pipeline profile at this scale: bundling 0.95s → 640 hbundles (100
top-bus + 540 cell-level), generation ~12s → 16792 candidates, and the
planner dominates (374s topdown / 148s bottomup at the 2026-07-30 baseline —
the bottom-up templates shrink the top-down problem 2.5x).

Planner-runtime study (2026-07-31)
===
Profiling the 374s planner (gdb stack sampling via the new `BUDA_PROFILE`
cmake option) found ~50% of it in `cuts_ = cuts_snapshot` band-vector
memcpy (plan_bundle's per-candidate rollback), the rest in the
for_each_band full-cut scan and blocks_cache_ string-set lookups.  Three
byte-identical fixes (candidate undo log, per-(layer,dir) sorted cut
index, leaf-only blocks cache) took chip_topdown's planner **374.5s →
109.7s (3.4x)** with the endpoint bit-identical (452/3341, the pre-#530
baseline); re-confirmed after rebasing onto the `band_span_charge` flip
(#530): **376.9s → 125.1s**, endpoint byte-equal (255/3185).  Five
planner-heavy corpus flows verified byte-identical against both mains.

**Occurrence-alignment twin** (`chip_aligned.bdb.sql` +
`chip_topdown_aligned.buda`, built with `--align-occurrences`): snapping
same-cell instances onto shared rows/columns collapses coincident block
edges — Hanan crossings **−33%** (242×262=63404 → 227×187=42449).  The
2×2 matrix:

| Fixture | old planner | new planner | endpoint (ovl/unplaced) |
|---|---|---|---|
| chip (unaligned) | 374.5s | 109.7s | 452 / 3341 |
| chip_aligned (−33% crossings) | 300.5s (−20%) | ~120s (+9%) | 452 / 3877 |

Verdict: grid reduction paid while memcpy dominated; after the algorithmic
fixes the planner cost is congestion-driven and the snap **costs QoR**
(+16% unplaced — it closes part of the SA bloat channels), so the
algorithmic path strictly dominates.  The aligned twin stays checked in as
the test-with-both study fixture (not in the QoR corpus).

chip3 — the TRUE 3-level variant (2026-07-31)
===
`chip3.bdb.sql` + `chip3_topdown.buda`: same composition and SA placement
as chip_topdown, but mix2 imported with `--nest-bdb-cells`, PRESERVING its
internal hierarchy — chip (d0) → i_big2_k / i_mix2_k (d1) → big2 blocks +
i_dnuts*/i_dogleg* (d2) → mix2 leaves (d3); 1+6+192+300 components,
13320 nets with deep names (`chip/i_mix2_0/i_dnuts1_0/bv1_0`).  The flow
derives busterms to depth 3, loads blocks at every depth, and bundles at
depth 3 — the maiden depth-3 run of the hier pipeline, and it worked
first try, including cross-level **D3→D2** bundles (a mix2 leaf three
levels down driving big2 blocks two levels down):

| | chip (2-level) | chip3 (3-level) |
|---|---|---|
| hbundles | 640 (100 D1 / 540 D2) | 640 (95 D1 / 311 D2 / 234 D3) |
| candidates | 16792 | 15073 |
| planner | 125.1s | **59.7s** |
| total | 148s | **76s** |
| overlaps / unplaced / viol_bundles | 255 / 3185 / 163 | 185 / 2925 / 180 |
| abstract / detailed WL | 3036142 / 61969962 | 2303254 / 53318203 |

The deeper templating HALVES the planner (the dnuts-level bundles solve
in tiny cell-local frames and expand 6-18x each) and improves most QoR
metrics too (overlaps −27%, unplaced −8%, detailed WL −14%, abstract WL
−24%) at slightly more violating bundles (163→180).  Same congestion
profile (90 ALLOW_OVERFLOW commits), healerless like its 2-level twin.  `align_bottom_up` (1.9s) gets both
cells to **ALIGNED** (3 instances each seeing identical signal-track pools;
508/496 windows compared), so DNUTS solves each cell once and copies.
Bottom-up trades +75 abstract overlaps for −22% unplaced bits, −17%
violating bundles, −10% abstract WL, −23% detailed WL, and 2.1x total
runtime — the genuine congestion (168 ALLOW_OVERFLOW commits topdown) is
the vehicle's point: plenty of headroom for healer / selection studies.
