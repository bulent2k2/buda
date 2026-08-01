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
**8-layer** stack + track patterns: M2/M3 LOW plus six TOP layers M4–M9,
each pair coarser than the one below it (M8/M9 use 3-wide signal wires on
8-wide rails).  It extends big2's `tracks4top.buda` / mix2's
`mix_tracks.buda` (identical 6-layer stacks) by two layers — chip is where
a realistic top-metal count pays, and every chip flow improved on all three
endpoint metrics when M8/M9 were added (see the table below).

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

Two more top layers — M8/M9 (2026-08-01)
===
`chip_tracks.buda` grew from six layers to eight (M8 H TOP / M9 V TOP,
3-wide signal wires on 8-wide power rails, 57.14% overhead each).  All
seven chip flows improved, several substantially:

| Flow | 6 layers (ov/unpl/viol) | 8 layers | abstract WL |
|---|---|---|---|
| chip_topdown | 266 / 3307 / 159 | **164 / 2227 / 120** | −5.4% |
| chip_topdown_aligned | — | 206 / 1914 / 113 | — |
| chip_bottomup | 482 / 2205 / 105 | **397 / 1921 / 107** | −2.0% |
| chip_bottomup_caps | 458 / 3131 / 126 | **442 / 2996 / 116** | −1.1% |
| chip3_topdown | 121 / 1731 / 133 | **88 / 1687 / 123** | −3.2% |
| chip3_bottomup | — | 365 / 1623 / 93 | — |
| chip3a_bottomup | 447 / 2269 / 103 | **354 / 1698 / 84** | −2.2% |

The new layers are genuinely used, not decoration: `chip_bottomup` puts
4.2M + 5.1M detailed WL on M8/M9 (~20% of its total metal), and the extra
supply is what removes the stranded bits — the same congestion that was
overflowing six layers now spreads over eight.  Planner runtime drops with
it (total sweep 849s → 654s, −23%) because fewer bundles have to grind
through the escalation ladder.

Under `chip_bottomup_caps`'s `set_layer_caps_by_depth M3 M5`, the extra
layers land where the policy says they should — entirely at the top level:

| context | M6 | M7 | M8 | M9 |
|---|---|---|---|---|
| big2 (capped) | 0 | 0 | **0** | **0** |
| TOP-LEVEL (capped run) | 2,514,146 | 2,669,859 | **2,169,721** | **2,223,914** |
| big2 (uncapped run) | 5,263,851 | 5,104,846 | **2,888,334** | **3,391,496** |

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
| hbundles | 640 (100 D1 / 540 D2) | 640 (100 D1 / 306 D2 / 234 D3) |
| planner | 125.1s | 138s |
| overlaps / unplaced / viol_bundles | 255 / 3185 / 163 | **109 / 2088 / 143** |

(Re-baselined 2026-07-31 after the recursive-bottom-up bundler fixes —
the maiden-run numbers (95 D1 bundles, 185/2925/180 at a 59.7s planner)
were partly an artifact of five top buses silently LOSING their big2
receivers to the mixed-depth defects fixed in that arc: routing the
recovered endpoints costs planner time and pays QoR.  The 2-level chip
rows are byte-identical under the fixes.)

The deeper templating improves every headline QoR metric (overlaps −57%,
unplaced −34%, viol_bundles −12% vs the 2-level twin at comparable
planner time) — the dnuts- and mix2-level bundles solve in small
cell-local frames and expand per template.  Healerless like its 2-level
twin.

chip3 bottom-up — nested templates at TWO levels (2026-07-31)
===
`chip3_bottomup.buda` marks bottom-up at BOTH hierarchy levels — big2 +
mix2 (depth-1 cells) AND mix2's nested depth-2 classes (mix2__dnuts1/2,
mix2__dogleg1/2 with 18/18/12/12 congruent occurrences).  Two fixtures
tell the composed-alignment story:

- **chip3 (built from plain mix2.bdb.sql)**: `align_bottom_up` correctly
  refuses to move a nested instance independently ("inside a marked
  parent … not fixable by translation" — it would break the parent
  template's congruence), so only the first occurrence per parent lands
  on the class phase; `check_template_tracks` reports the four nested
  classes MISALIGNED and the `independent` policy solves those
  occurrences individually.  50.8s, 301 ovl / 2108 unplaced / 118
  viol_bundles.
- **chip3a (built from mix2_aligned.bdb.sql — `chip3a_bottomup.buda`,
  the QoR-corpus row)**: nested alignment MUST BE COMPOSED BOTTOM-UP —
  align inside the cell first (mix2's own `align_and_save` checkpoint),
  then across the cell's instances.  With the pre-aligned source, ZERO
  unfixable-phase warnings and every nested class reports **ALIGNED**
  (18/18/12/12 instances seeing identical signal tracks): each nested
  template plans locally, pins all its occurrences, and DNUTS solves the
  reference once.  With the recursive fixes: mix2 itself joins the
  bottom-up set (22 template bundles, 70 local segments solved once,
  copied ×3), 9512 reference bits copied to 24016 across 423 sibling
  instances, and the corpus row lands at 449 ovl / 2303 unplaced /
  **105 viol_bundles** — the best viol_bundles of any chip flow.

Recursive bottom-up (2026-07-31) — the open above is CLOSED: the
bundler's LCA cell-context generalization recognizes mix2's own buses
between its child instances as cell-local templates of mix2 (22
templates × 3 occurrences, busterm blocks = the dnuts/dogleg
instances), so marks at BOTH levels now compose fully — the nested
classes solve first, then mix2's frame solves ONCE around its locked
children and copies, then the top level plans around everything.  On
chip3a_bottomup every class reports ALIGNED including mix2 itself
(9512 reference bits DNUTS-solved once, 24016 copied across 423
sibling instances).  The same arc fixed two silent MIXED-DEPTH bundler
defects this vehicle exposed (five top buses reaching both depth-3
mix2 leaves and depth-2 big2 blocks had their big2 receivers DROPPED
from the block contract): receivers are now path-maximal pins rather
than globally-deepest, and is_cross compares per receiver.  Routing
the recovered endpoints costs planner time but pays QoR — see the
refreshed corpus numbers above; details in docs/HIER_BUNDLER.md and
docs/internal/hier_bottom_up_planning.md.  `align_bottom_up` (1.9s) gets both
cells to **ALIGNED** (3 instances each seeing identical signal-track pools;
508/496 windows compared), so DNUTS solves each cell once and copies.
Bottom-up trades +75 abstract overlaps for −22% unplaced bits, −17%
violating bundles, −10% abstract WL, −23% detailed WL, and 2.1x total
runtime — the genuine congestion (168 ALLOW_OVERFLOW commits topdown) is
the vehicle's point: plenty of headroom for healer / selection studies.
