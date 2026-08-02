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
**10-layer** stack + track patterns: M2/M3 LOW plus eight TOP layers M4–M11,
each pair coarser than the one below it (M10/M11 use 4-wide signal wires on
10-wide rails).  It extends big2's `tracks4top.buda` / mix2's
`mix_tracks.buda` (identical 6-layer stacks) by four layers — chip is where
a realistic top-metal count pays.  The 6 → 8 → 10 study below is worth
reading before copying this stack: the first extension is a clean win, the
second is the diminishing-returns knee, and it exposed that a cap band
written for one stack height is wrong on another.

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

Top-layer count study — 6 → 8 → 10 layers (2026-08-01)
===
`chip_tracks.buda` was extended twice: first to eight layers (M8 H / M9 V
TOP, 3-wide wires on 8-wide rails, 57.14% overhead), then to ten (M10 H /
M11 V TOP, 4-wide wires on 10-wide rails, 56.76%).  Each pair is coarser
than the one below, as real top metal is — which is exactly what makes the
second extension behave differently from the first.

| Flow | 6 layers (ov/unpl/viol) | 8 layers | 10 layers |
|---|---|---|---|
| chip_topdown | 266 / 3307 / 159 | **164 / 2227 / 120** | 188 / 2170 / 112 |
| chip_topdown_aligned | — | 206 / 1914 / 113 | 193 / 2027 / 112 |
| chip_bottomup | 482 / 2205 / 105 | **397 / 1921 / 107** | **353 / 1604 / 91** |
| chip_bottomup_caps | 458 / 3131 / 126 | 442 / 2996 / 116 | **358 / 1708 / 93** † |
| chip3_topdown | 121 / 1731 / 133 | **88 / 1687 / 123** | **63 / 1639 / 118** |
| chip3_bottomup | — | 365 / 1623 / 93 | **311 / 1544 / 87** |
| chip3a_bottomup | 447 / 2269 / 103 | **354 / 1698 / 84** | 297 / 1658 / 90 |

† `chip_bottomup_caps` reads 424/3192/124 at ten layers if its bands are
left at the 6-layer `M3 M5`; the row above is the rescaled `M5 M9` the
vehicle now ships with — see the band discussion below.

**6 → 8 was a clean win**: 5 better / 0 worse, every flow improving on all
three metrics, abstract WL −1..−5%, and the sweep 23% faster because fewer
bundles grind through the escalation ladder.  The layers carry real
traffic — `chip_bottomup` put ~20% of its metal on M8/M9.

**8 → 10 is the diminishing-returns knee**: 5 better / 2 worse.  The cause
is the per-bit cost of coarse metal.  Each pair's channel cost per bit is
`unit_pitch / n_signal_slots`:

| layers | M4/M5 | M6 | M7 | M8/M9 | M10/M11 |
|---|---|---|---|---|---|
| per-bit | 2.25 | 3.00 | 5.00 | 7.00 | **9.25** |

A 16-bit bus needs 148 units of M11 where it needs 78 on M7.  So the top
pair adds *width* but poor *bit density*, and whether that helps depends on
where the design is actually short of supply.

`chip_bottomup_caps` was the clearest case, and at its original bands it got
**worse** (unplaced 2996 → 3192).  Its `set_layer_caps_by_depth M3 M5` put
every cell template in [M2..M5], so the constraint sat at the BOTTOM of the
stack where new top layers cannot reach it.  All they did was spread the
top-level buses over coarser metal: top-level upper-layer WL was 9,577,640
at eight layers and 9,557,597 at ten — the same metal on more,
thinner-value layers.

**The band has to scale with the stack.**  `[..M5]` was written when the
stack ended at M7: it reserved the top *pair* for the top level.  On a
10-layer stack the identical text reserves *six* layers for a top level that
needs two.  Relaxing the cell band restores it — measured on this vehicle,
varying only the level-2 cap:

| cell band | overlaps | unplaced | viol | detailed WL |
|---|---|---|---|---|
| [..M5] (as written for 6 layers) | 424 | 3192 | 124 | 52,194,089 |
| [..M7] | 395 | 1898 | 104 | 48,034,999 |
| **[..M9] — now the vehicle default** | **358** | **1708** | **93** | **47,509,230** |
| no caps at all (`chip_bottomup`) | 353 | 1604 | 91 | 47,502,937 |

So reserving the top pair again costs ~6% in unplaced bits over routing
with no policy at all — versus ~99% for the stale band — and the separation
is total:

| context | M6 | M7 | M8 | M9 | M10 | M11 |
|---|---|---|---|---|---|---|
| big2 (capped, [..M9]) | 5,263,747 | 5,104,639 | 2,888,958 | 3,391,640 | **0** | **0** |
| TOP-LEVEL (capped run) | 1,102,668 | 1,035,758 | 480,722 | 518,646 | **2,007,678** | **2,257,736** |

The lesson, now measured from both sides: **more top layers help a design
whose congestion is at the top and do nothing for one capped at the bottom
— and a cap written for one stack is wrong on another.**  Relief for a
starved capped cell comes from widening its band (or a
`set_cell_layer_share` lease), not from metal above its ceiling.

This is why `chip_bottomup_caps` declares **`reserve_top_layers 2`** rather
than an absolute band: it derives the M9 cap from the stack itself, so the same
line stays correct if the stack grows again.  It is byte-identical to the
hand-computed `set_layer_caps_by_depth M5 M9` here (358/1708/93, WL equal to
the unit).

One quirk worth knowing: in this design the *leaf* argument of the by-depth
spelling is a free choice — `M5 M9` and `M7 M9` are byte-identical, as are
`M3 M7`/`M5 M7`.  The level-1 cells are true leaves with no cell-local
bundles (`HierBundler: 640 hbundles (D1: 100, D2: 540)`), so only the
level-2 band ever bites.

The pipeline profile at this scale: bundling 0.95s → 640 hbundles (100
top-bus + 540 cell-level), generation ~12s → 16792 candidates, and the
planner dominates (374s topdown / 148s bottomup at the 2026-07-30 baseline —
the bottom-up templates shrink the top-down problem 2.5x).

chip_stack — the on-grid stacked variant (2026-08-02)
===
A deliberately *placed* sibling of chip, built to remove instance-phase noise
from the bottom-up flow:

```
  LEFT  column  x=[0..6100]      2 x big2, pitch 7560   y = 0, 7560
  RIGHT column  x=[7100..9430]   4 x mix2, pitch 3528   y = 458, 3986, 7514, 11042
  die 9430 x 13860, 100 top-level cross-hierarchy buses, 11750 nets
```

![chip_stack placement](chip_stack_placement.png)

Every instance of a cell shares one **x**, so their vertical-layer track phase
is identical by construction; the vertical pitches are multiples of
**504 = LCM(18, 18, 24, 56)** — the unit pitches of M2/M4/M6/M8, the horizontal
layers a cell may use under `reserve_top_layers 2` — so the horizontal phase is
identical too.  Δy contributes **zero** track offset between stacked instances.

The payoff: `chip_stack_bottomup.buda` never calls `align_bottom_up`, and
`check_template_tracks` still reports **ALIGNED** for both cells straight from
the placement (big2: 2 instances, 249 windows compared; mix2: 4 instances, 774
windows) — where the SA-placed `chip_bottomup.buda` has to nudge instances onto
a common phase first.

**Mirror-symmetric, on grid.**  `--column-align center` splits each column's
slack equally above and below (mix2 gets 458 top and bottom — and 458 is
forced: 4x2360 plus three channels ≡ 160 mod 504 cannot fill 13860 with zero
margins).  `--mirror-upper` then flips the three instances above y=6930
(i_big2_1, i_mix2_2, i_mix2_3), so all **488 leaf blocks map exactly onto
their reflections** about the centreline — verified as a set comparison, not
by eye.  Column origins are no longer multiples of 504 in absolute terms and
need not be: `check_template_tracks` asks that a cell's instances agree with
*each other*.

**The mirror costs the track phase, structurally.**  A flipped instance needs
`2*y1 + h ≡ d (mod p)` on every H layer, and the reflection-valid `d` are
{0.5, 9.5} mod 18 (M2), {7.5, 16.5} mod 18 (M4), {7, 19} mod 24 (M6),
{4.5, 32.5} mod 56 (M8).  `2*y1 + h` is an integer, but M2/M4/M8 admit only
HALF-INTEGER axes — their signal pitch is 1+0.5 = 1.5 and 3+1.5 = 4.5 — so no
integer placement can satisfy them.  Under this technology mirroring and
solve-once-copy are mutually exclusive: `check_template_tracks` reports
MISALIGNED for the flipped instances and the flow falls back to
`on_mismatch independent`.

Endpoints, all four geometries measured back-to-back in one session state:

| geometry | overlaps | unplaced | viol | abstract WL | sec |
|---|---|---|---|---|---|
| bottom-aligned columns | 661 | 2271 | 106 | 2,359,622 | 155 |
| top-flush columns | 619 | 2253 | 111 | 2,363,777 | 123 |
| centred columns | 586 | 2212 | 115 | 2,350,369 | 124 |
| **centred + mirrored upper half** | **26** | **278** | **35** | **973,233** | **23** |

The three unmirrored variants are within noise of each other — where a column
sits does not much matter.  The mirror is what moves the needle, and it does
so while *losing* solve-once-copy, which is the surprise worth following up.

> **Measurement note (2026-08-02).**  Earlier revisions of this file and of the
> flow headers quoted much better endpoints for the unmirrored variants
> (64/364 bottom-aligned, 24/288 top-flush).  Those do NOT reproduce: rebuilt
> from the same generator arguments and re-measured they give the 661 and 619
> above, and the good runs also completed in ~30s against ~120-155s now, so
> they were doing substantially less work.  The cause has not been isolated,
> so those numbers are RETRACTED rather than explained.  The table above is one
> self-consistent set, each row re-measured at least twice.

The top-down twin of the mirrored build measures 49/1056/65 — the bottom-up
gap is still large (278 vs 1056 unplaced) even though the flipped instances
are solved individually rather than copied.

The layer policy holds as designed: `big2` and `mix2` place **zero** metal on
M10/M11, which carry top-level wiring only.

The picture above is `tools/render_stack_placement.py <design.bdb> <out.png>
[grid]` — die, instances by cell type, leaf blocks, channels, and each origin
as a multiple of the grid quantum.

Generated with `build_hier_demo.py --layout stacked --channel 1000 --grid 504
--column-align center --mirror-upper`
(see [BUILD_HIER_DEMO](../../docs/BUILD_HIER_DEMO.md#on-grid-stacking)); the
regeneration command is in the flow header.  Note the 504 grid is valid only
while the cells stay at or below M9 — the full 10-layer stack would need
LCM(18,18,24,56,74) = 18648, i.e. 12k of dead channel around a 6300-tall cell.
Reserving the top pair is what makes a tight on-grid pitch affordable.

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
