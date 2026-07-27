# Wishlist — Topology generation & connectivity

Deferred follow-ups for topology generation (`src/topology.cpp`) and the
connectivity model (`src/conn_topology.cpp`). Index: [`wishlist.md`](wishlist.md).

See also [`mst_edge_realization.md`](mst_edge_realization.md) — trunk-tail
tightening and the per-edge MST L/Z DOF (avoiding the 2ᴺ candidate explosion),
grounded in the current generator code with a measured prototype result.

## Star→spine relay completion (`set_spine_relays`) — SHIPPED opt-in, measured net-positive on viol_bundles

**Context (2026-07-27, pure-MST study on `flow/big_data_test/bigHalf.buda`).** A
pure-MST candidate's segment count is dominated NOT by per-edge shape but by
`complete_relay_junctions` connectors at high-degree hubs. Measured over the 54
bigHalf bundles that carry an MST candidate: every MST edge is an I (69%) or L
(31%) — never a per-edge Z — so the raw tree is always ≤ 2n−2 (n = busterms); the
overshoot to ~3n−1 is *entirely* relay connectors, and they explode at a degree-3
hub (mean connectors 1.1 → 5.5 from max-degree 2 → 3). The worst cases (b63: 6
raw legs + **10** connectors = 16 segs; b0: 6 + 9 = 15) are "double-stars": two
degree-3 hubs joined by one edge, each hub's three incident stubs chained by
over-the-cell brackets.

**The fix.** At a degree-≥3 relay whose incident stubs split as ≥2 PARALLEL
(majority) + exactly 1 PERPENDICULAR (minority), build a single collector SPINE
along the minority axis and let the majority stubs T-tap it independently:
- The minority stub is EXTENDED across the block to the face OPPOSITE its
  neighbour — that face landing is the block's single busterm-conn and keeps a
  bounded slide (a horizontal seg on a vertical face keeps its y-slide, and
  vice-versa; landing on the PARALLEL face would pin it to zero and `filter_pinched`
  would drop it). No dangling end, no lost tap.
- Each majority stub is repositioned to tap the spine at its OWN perpendicular
  coordinate. The parallels are NEVER merged onto one shared track — merging
  couples their slide to a single DOF (the intersection of their windows), while
  independent taps each keep the full spine span. This is the slide-DOF-preserving
  core of the design.

No new segments, no busterm tap lost. Conservative: fires only on the clean
single-rect 2-1 split whose spine provably covers every tap (`coverable` guard) and
whose repositioned endpoints don't sit on another block's boundary; anything else
(2-2, all-same-orientation, multi-rect, uncoverable) falls through to the existing
bracket chaining. Byte-identical when off.

**Measured (b63/b0 candidate):** 16 → **6** segs, 15 → **6** segs; 0 relay
connectors, `check_topo`-clean, un-pinched, taps on independent columns.

**Corpus A/B** (pin-free `tools/qor_nopin.py`, flag off vs `BUDA_SPINE_RELAYS=1`;
overlaps/unplaced/viol_bundles): 30/35 flows unchanged, and **viol_bundles — the
electrical-correctness metric — improves on 3 flows and regresses on 0**:
`slowdown_rnr` 2/8/1 → **0/0/0** (fully heals), `mix2_fast`(+aligned_sql)
34/241/14 → 31/235/13, `mix2_fast_topdown` viol 11 → **10** (overlaps 16→13,
unplaced 175→191). Two flows are raw-metric WORSE without a viol regression:
`06_multipin_stress` +1 overlap (viol 5→5) and `mix2_fast_topdown`'s
overlap/unplaced trade. Runtime: bigHalf's *endpoint* is unchanged (0/0/0) but its
wall-clock rose 5→30s — the cheaper MSTs get selected and the healers grind more
to the same result.

**Why opt-in, not default.** The correctness metric is net-better and the shape is
strictly cleaner, but the raw overlap/unplaced trade is mixed on stress flows and
bigHalf's healer cost is real, so — like `multi_trunk` / `set_dedup_loci` — it
ships default-off and is measured before any default flip. Tests:
`test/tests/test_topo_spine_relay.py`.

**Follow-ups:**

- **A + E — anchor pick + overstretch removal — ✅ IMPLEMENTED (PR #460).**
  Merged as a single *min-of-two* choice: for the minority-anchor stub, compute
  BOTH the old M-anchor cost (`|m_opp − bus_along|`, the opposite-face extend) and
  the P-anchor cost (`|anchor_far − spine_perp|`, tapping the outermost majority
  stub's far end) and take the cheaper. That both picks the anchor that gives up the
  least reach (A) and removes the dead opposite-face overhang when the P-anchor wins
  (E) — measured never worse than the old WL (b64 hub 19 back to nomWL 23160 from the
  overstretched 23880), all candidates `check_topo`-clean. The anchor MUST end on the
  outermost tap so the spine terminates in a T-junction ConnTopology can infer (an
  interior anchor is X-crossed by the spine → stranded block → dropped candidate).
- **B — the all-same-orientation split — ✅ IMPLEMENTED (this PR).** A degree-≥3
  hub whose incident stubs are ALL one orientation has no perpendicular minority to
  serve as the spine, so it fell back to bracket chaining (worst exactly there — a
  3-seg Z per stub pair). Now it ADDS a dedicated collector: a new segment
  perpendicular to the stubs that every stub T-taps at its own coordinate
  (independent slide) and STOPS there. When every stub lands on the SAME block face
  (the common comb — all leaves on one side), the collector **rides that near face**
  at the taps' own line: the stubs tap the face directly (their MST landing), so the
  collector spans exactly `[t_min..t_max]` with **no along-overhang** and the busterm
  tap stays on the outermost stub. When the taps straddle faces, it falls back to an
  interior collector extended along-axis to the NEARER face only (never both — a
  face-to-face span would feed the receiver through), the busterm on that face
  landing. This is the follow-up-E overstretch fix applied to the all-same case: an
  earlier scheme spiked the outermost stub across the block to the FAR face (a
  perpendicular spike), then a second cut extended the collector sideways to a face
  (a small along-overhang past the outermost tap); the near-face-ride removes even
  that. Measured on `big.buda` (generated
  MST-family pools): pure `MST_HV/VH` connectors **329 → 127 (−61%)**, total
  MST-family segments 10387 → 9425 (**−9.3%**), all 1573 MST candidates stay
  `check_topo`-clean and un-pinched. all-same is the common unhandled case (73 relays
  vs 2 for the 2-2 split, still on chaining as rare). Corpus A/B (pin-free): 3 flows
  better / 2 worse; `mix2_fast_topdown` viol_bundles regresses **11 → 12** (near-noise,
  flagged to revisit) vs A/E's 11 → 10 — hence B stays opt-in with the others.
- **C — per-command `.buda` `spine_relays` token — ✅ IMPLEMENTED (this PR).** The
  opt-in now also has a script-level token on `generate_topologies` /
  `generate_hier_topologies` (and the per-bundle / `generate_more_topologies`
  variants), threaded through `_parse_gen_flags` + the ~6 call sites and recorded in
  the v15 per-bundle knob memo (index 4 of the knob tuple), so it round-trips a bulk
  regeneration exactly like `multi_trunk`. The Python `set_spine_relays` setter and
  `BUDA_SPINE_RELAYS` env remain. Since there is NO opt-OUT token (spine is opt-in
  only, unlike the default-on `hanan_loci` with its `no_hanan_loci`), `_make_topo_gen`
  stamps the flag ONLY when opting in — a tokenless flow keeps the constructor's
  env-derived default, so the `BUDA_SPINE_RELAYS` global opt-in still enables the
  spine for an un-edited corpus. Precedence: **token > `BUDA_SPINE_RELAYS` > compiled
  default (off)**.
- **D — default-flip measurement — OPEN.** Re-measure a corpus A/B toward flipping
  the default on (gated like `kSegsRel` / `multi_trunk` if it lands mixed). The B
  measurement above (3 better / 2 worse, one near-noise viol regression) keeps the
  default off for now.
- **F — TRUNK+MST hybrids — MEASURED, hypothesis REFUTED.** The same
  `complete_relay_junctions` runs on the trunk+MST hybrid path, so the spine already
  fires there under the flag (big.buda GENERATED hybrid-pool connectors 6271 → 5585,
  −11%). Note the corpus A/B above is pin-FREE (`qor_nopin` removes every
  `select_topology`, so the planner draws from the full pool) — it therefore already
  exercised these hybrid changes end-to-end; it just never ISOLATED or attributed
  them. The *hypothesis* was that hybrids — the planner's genuine choice far more
  often than pure MST — are where the real QoR win lives. The isolating end-to-end A/B on
  `big.buda` (2026-07-27, spine off vs on) refutes it:
  - **Natural flow** (planner free): abstract WL 767,207 → 767,613 (**+0.05%, flat**),
    QoR 0/0/0 both — a near-total no-op (only 13-14 of 80 selected are MST-family).
  - **Hybrid-pinned** (all 70 forced to their cheapest hybrid): selected-hybrid
    connectors 194 → 184 (**−5.2%**), abstract WL 801,339 → 801,179 (**−0.02%, flat**),
    QoR 7/356/10 **identical**.

  Why: the **trunk already IS the collector** — it absorbs the fan-out, leaving the
  MST portion with only ~2.8 connectors/hybrid (194/70) and few high-degree hubs, so
  the spine's collapse is marginal and WL-invisible (the trunk dominates WL). The
  spine's real win is concentrated in the *pure-MST-selected* case (16→6 segs on
  b63/b0), which is rare; on hybrids it is **largely redundant with the trunk**. Net:
  this WEAKENS the default-flip case (D), not strengthens it — the feature is narrow
  (fixes the pure-MST pathology) and there is little global upside to turning it on.

## Coverage by zero-length abutment (`seg_spans_rect` inclusive bounds) — NOTED

**Found 2026-07-17 while fixing the passthru report scope (big2 b61):** verify's
`seg_spans_rect` (`verify.cpp:77`) uses inclusive bounds, so a segment that
merely touches a bundle block at a SINGLE POINT — a trunk endpoint landing on
the block's corner, or riding a face line — counts the block as "covered" in
`check_topo`'s coverage check (suppressing `BUSTERM_OPEN`) and in generation's
coverage gate (`filter_uncovered` keeps the candidate).  Full-edge abutment is
load-bearing (the ABUT shared-edge candidates depend on it), but a zero-length
corner touch granting coverage is dubious: no realizable wire connects through
a point.  Not observed misfiring on the corpus (the report's floorplan-wide
scan was the only site listing point-touches, fixed by requiring interior
overlap there); if a real strand traces back here, the fix is a positive-length
along-overlap requirement in `seg_spans_rect` with the perp-face inclusive
bounds kept (face landings are real).  The display predicate
(`reports.py::_seg_crosses_rect`) is deliberately stricter — see its docstring.

## Slide-aware pass-through DECISION (proactive stub, not reactive drop) — EXPERIMENT / DEFERRED

**Context (2026-07-26, follow-on to #438/#443's receiver-graze coverage gate).**
PR #443 (`fix(#438): slide-aware block-coverage gate`, `verify.cpp:381`) made
`check_topo`'s coverage test — and therefore generation's `filter_uncovered`
gate — *slide-aware*: a nominal pass-through counts as covering a block only if
the covering segment's slide window `[perp_lo, perp_hi]` actually reaches the
block's perp-extent. A trunk/edge whose nominal locus lands ON a block's face
but whose window is pushed entirely off it (a min-stub / junction constraint) is
an **unreachable graze**: NUTS seats the wire off the face and the block's bits
open. The gate now **drops** such candidates (a robust interior-pass-through
sibling wins; `filter_uncovered` keeps the pool only if EVERY candidate is
broken, so a bundle is never stranded).

That fix is *reactive* — it detects the false pass-through and discards the
candidate. This entry scopes the *proactive* alternative the owner asked about:
instead of dropping, **make the pass-through decision itself slide-aware at
generation** so a **real min-stub** is emitted up front (with correct MST
rooting) whenever the covering segment's window cannot reach the face.

### The reference graze (measured)

`flow/rnr/mix.buda`, HBundle **11** (`top_bus9_w8`, 8-bit), candidate 16
`TRUNK_V+MST@x1775` — the candidate #443 drops (`BUSTERM_OPEN`), which is why
the mix topo golden moved (see #445). Extracted geometry (global coords):

- Grazed block **`chip/i_dnuts1_0/u12`** = `x[1200..1600] y[830..880]`.
- The covering segment is the **horizontal MST edge** `(1060,830)→(1775,830)`
  (layer 6). Its nominal perp sits at **y=830 = u12's bottom face**, and its
  x-span [1060,1775] ⊇ u12's [1200,1600], so the *nominal* `seg_spans_rect`
  counts it as a pass-through cover.
- But its **slide window is `y∈[350,820]`** — the nominal (830) lies OUTSIDE its
  own window, which tops out at **820, a 10-unit gap below the face**. NUTS can
  never seat it at 830 → u12 opens. (`min_stub` on this V layer = 20.)
- The edge is *load-bearing in the MST*: it carries **top3's column** (x≈1010–1060,
  left) across to the **V trunk** at x=1775 (right); u12 is an incidental graze
  underneath it. A robust sibling (candidate 22 `TRUNK_V_OOB+MST@x1924`) taps u12
  with its window actually reaching the face, which is what the gate lets win.

### Where the retrofit lives, and what it actually costs

1. **NUTS never synthesizes segments** — it only places the frozen segment set
   within its windows. "Add a stub" is a *generation-stage* transformation
   (`complete_relay_junctions`, `topology.cpp`), the same stage the #443 gate
   runs in. This is the one hard structural constraint: the fix is proactive
   generation, not a placement patch.
2. **The graze exists because the pass-through optimization SUPPRESSED the stub**
   — flying an edge across a face is treated as covering the block precisely to
   save the stub wire. Here that coverage was a false positive (on-face nominal,
   off-face window).
3. **The added stub is a leaf; it does NOT re-root the edge.** A perpendicular
   stub whose base endpoint lands anywhere in the covering edge's inclusive span
   is recorded as a reciprocal T-junction by `annotate_seg_conns`
   (`topology.cpp:281-323`, `P.x∈[alo,ahi]`), so the load-bearing edge stays one
   whole segment and the new stub is a leaf — no split, and there is no retained
   MST-root structure to re-root after realization. *(Corrected from an earlier
   draft that framed this as MST surgery — thanks @codex.)*
4. **`min_stub` is a relaxable PREFERENCE, not a hard floor** — so the 10-unit
   gap need not pin the edge 20 below the face. `filter_pinched`
   (`topology.cpp:4463-4483`) drops only a *zero-slide* segment (`perp_lo ==
   perp_hi`); `derive_slide_ranges` (`topology_analysis.cpp:397-405`) explicitly
   relaxes the `f±m` clearance to the face `f` itself when the full-clearance
   window would empty against a pass-1 busterm bound ("connectivity wins over the
   stub-length floor"), and `complete_relay_junctions` already permits short
   completion connectors. So a face-reaching stub as short as the gap is
   *accepted*; the real cost is the added stub wire + one new junction (plus
   whatever slide slack the new busterm bound consumes), not a mandatory ≥20-unit
   edge pin. The experiment must therefore *measure* the cheap relaxed-stub case,
   not rule it out.

### The experiment

Fold #443's robust-cover predicate into the pass-through *decision* in
`complete_relay_junctions` (and the relay-tap logic feeding it), gated behind a
new opt-in knob (proposed `set_slide_aware_passthru on`, default off →
byte-identical). At the point the generator decides "this block is covered by a
pass-through, emit no stub", additionally require that the covering ConnSeg's
window `[perp_lo, perp_hi]` reach the block's perp-extent (the exact
`verify.cpp:381` test). When it does not, emit a perpendicular **leaf stub** from
the covering segment to the face at an `x` inside the block's along-extent, and
register it as a real tap (busterm landing) so the block is covered by
construction. This is ONE path for every edge, load-bearing or not (obstacle 3):

- **Stub length is preference-then-relax**, mirroring `derive_slide_ranges`:
  prefer full `min_stub` clearance, but relax to the face when the full-clearance
  window would empty — so a sub-`min_stub` gap yields a short (down to
  face-reaching) stub rather than a rejection or an edge pin (obstacle 4). The
  edge's slide window is re-derived under the new busterm bound; `filter_pinched`
  still culls only if the result is genuinely zero-slide.
- **Fallback (rare):** only if the block's along-extent offers no legal interior
  tap `x` on the edge at all does the candidate stay broken and fall to the #443
  drop — the existing safety net.

### Measurement plan & decision criteria

- Baseline vs knob-on through `tools/qor_corpus.py` (the `(overlaps, unplaced,
  viol_bundles)` triple), plus the mix repro's DNUTS opens and detWL, and a
  `topo_snapshot` golden diff to see which bundles' pools change.
- **Ship only if** it strictly helps a bundle that has *no robust sibling* (the
  all-broken case #443 keeps flagged) without regressing detWL/opens elsewhere;
  the mix-b11 graze itself is already dominated by a robust sibling, so it is
  **not** the justification — it is the reproducible vehicle. The retrofit is
  cheaper than first estimated (a relaxable leaf stub, no re-root — obstacles
  3/4), so the added stub WL is the only real cost; the deferral is about *when
  it's needed* (a no-sibling case), not about the fix being invasive.
- Fresh-generation only (like every generation knob): accretion via
  `generate_more_topologies` and the persisted knob memo (v15) must carry the
  polarity, mirroring `no_hanan_loci` / `multi_trunk`.

**Status: DEFERRED.** The reactive drop (#443) is the right default; this proactive
form is a measured follow-up to run when a bundle with no robust pass-through
sibling surfaces. The graze geometry above is fully reproducible from the mix
hier flow (generate up through `generate_hier_topologies no_hanan_loci`, then
inspect `s.bundles[10].input.candidates[16]` and its `ConnTopology.segs()` slide
windows against the u12 component rect).

**Sibling problem — the endpoint face-tap mis-assignment (big2 b25).** A related,
UPSTREAM facet of the same "fragile graze → DNUTS open" family is
`annotate_endpoints` handing an endpoint face-tap to a block the segment *crosses*
instead of the receiver that *abuts* the same face from outside (a `viol_bundles=1`
open on `big2.buda`; see
[`big2_b25_abutment_tap_dnuts_2026-07.md`](big2_b25_abutment_tap_dnuts_2026-07.md)).
That case wants a REAL face-tap (not a stub), so its fix is interior-side
discrimination in `annotate_endpoints`, not the stub emission above — but both
chase the same symptom. **SHIPPED 2026-07-26** (PR #448): the two-pass
abut-vs-cross rule heals b25 (`big2.buda` `0/0/1 → 0/0/0`). The first sweep looked
net-negative but was a stale-`select_topology`-pin artifact (index pins renumber
when the pool shifts — caught by @codex); pin-free, only the big2/tc3b_flat_x5
circuit moves, and only its RAW no-healer residue rises (healer-equipped
`big2.buda` recovers). The residue rise is because the fix also unlocks the
pinch-dropped `x≈5772` pure-pass-through trunks; two raw-packing guard tests were
re-baselined. A **surgical** follow-up (redistribute only when the crossed block is
the bundle's DRIVER) was **prototyped and measured net-negative 2026-07-26 — NOT
landed**: it heals b25 identically to the blanket fix but, being strictly more
conservative, makes the two raw big2/tc3b siblings *worse* (`8/40/3 → 9/140/4`), not
better — the raw no-healer residue is not a monotone function of annotation churn.
Reverted; the blanket fix stands. See the b25 doc's "Follow-up (measured,
net-negative)" section.

## Nominal-WL comparability across shape families (the b44 root causes) — (a)+(b)+(c) SHIPPED

**Context (2026-07-16, `flow/big_data_test/b44.buda`; deep-dive after the
`kWLSpread` ship, see wishlist-planner "Realization-risk WL"):** the b44
mis-ranking traced to THREE generation-policy facts that make the nominal
`estimated_wirelength` non-comparable across shape families:

1. **The 3510 nominal is the geometric floor, and several families sit ON it
   while others sit above it.** b44's floor = the L1 distance between the two
   far blocks' nearest corners (io_pad_tl (1200,12000) ↔ blk_07 (2960,10250) =
   1760 H + 1750 V; the middle block is covered by crossing/pass-through for
   free). The `TRUNK_H+MST` hybrid's nominal is a 6-segment **monotone
   staircase** between those corners (H components sum exactly 1760, V exactly
   1750 — zero overshoot **by construction**: nearest-face MST joins + relay
   completion place every segment at its locally-minimal nominal), so its
   nominal ALWAYS equals its envelope bottom `wl_lo`. Plain trunks only hit
   the floor when a sampled locus happens to align.

2. **Trunk loci are sampled at Hanan-channel MIDPOINTS only**
   (`topology.cpp` multicast locus loop: `mid = (hanan[i]+hanan[i+1])/2`,
   strictly inside the bundle bbox) — never ON a Hanan line. b44's V loci are
   700/1950/2830/3810; the WL-optimal x=1200 (io_pad_tl's right edge) is
   structurally unsampled, so the tight 2-seg `TRUNK_V@x700` carries a +500
   nominal overshoot its own slide window could remove (NUTS in fact slides
   it to ~1073, the bus-width-clearance limit). A locus ON the Hanan line
   would nominal at the floor with the narrowest envelope of the pool.

3. **WL ties break ALPHABETICALLY, then by index.** `annotate_and_sort`
   orders candidates by `(wl, type)` and ASCII `'+' < '@'` sorts
   `TRUNK_H+MST@…` before `TRUNK_H@…`/`TRUNK_V@…`; the planner's soft costs
   tie (~0.01 across the 3510 group) and its equal-score tie-break keeps the
   LOWEST index — so the 6-junction staircase won the 3-way 3510 tie **by
   alphabet**, and NUTS's greedy per-segment placement then realized it at
   4510/bit vs the 3-seg tie-sibling's 3625/bit.

**Measured and rejected: "assign every I/L/Z (= trunk + ≤2 MST edges) its
deterministic minimum WL".** The reduction is conceptually right — a monotone
2-pin I/L/Z is already AT the floor (a Z's WL is locus-independent:
Δx+Δy for any interior trunk), and for k≥3 trunk shapes the constrained
minimum is computable (the envelope `wl_lo`). But assigning it as the
estimate was tested literally on b44 (stamp `estimated_wirelength = wl_lo`
on all 23 candidates, plan knob-off): **11/23 candidates collapse onto the
same 3510 floor**, the WL term differentiates nothing, the alphabetical/
lowest-index tie-break decides again, and the planner re-picks the 6-seg
staircase → 4510/bit — the original mis-pick, reproduced exactly. Two
floor-tied candidates also genuinely differ in what a real BUS can attain:
an interior-optimum window centers the bus at the optimum
(`TRUNK_V@x1950` → 3625/bit) while a window-EDGE optimum can only bring the
bus center to half-bus-width from the edge (`TRUNK_V@x700`'s ideal x=1200,
center stops at ~1073 → 3719/bit) — equal under min-assignment, ~2.6% apart
in reality. The minimum is the wrong statistic not because it is ill-defined
but because NUTS does not deliver it: realization ≈ lo + fill·spread, so the
estimate must carry BOTH the floor and the risk — hence the (nominal,
spread) pair the shipped `kWLSpread` scores. The clean salvage of the
min-assignment idea is **dominance pruning**: a candidate whose `wl_lo`
exceeds another's `wl_hi` is WL-dominated deterministically — but
WL-dominance alone is NOT overall dominance (Codex #313): the planner
scores congestion/span/layer/balance/peak BEFORE weighted WL, and a
longer candidate can be the only overflow-free / window-feasible option
(the escalation ladder and ripup's OOB-trunk promotion exist for exactly
that), so an unconditional generation-time drop could strand the only
routable topology. A prune must be gated on non-WL routing equivalence
(same layers/corridors/slide windows) — or the comparison deferred to
planner scoring where the other cost terms already arbitrate.

**Shipped mitigation:** the planner-side `kWLSpread` knob (opt-in) prices the
envelope spread, which resolves exactly these ties toward the tightest
realization (b44: picks `TRUNK_V@x1950`, detailed 188513 = 3625/bit, −19.6%,
better than the flow's hand-pin). **Generation-side follow-ons (this item):**
(a) **SHIPPED (2026-07-17, opt-in `hanan_loci` generation knob)** — also
sample n-pin trunk loci ON the in-bbox Hanan lines
(`TopologyGenerator::allow_hanan_loci_`, gating an extra insert into
`generate_npin`'s `x_set`/`y_set` beside the channel midpoints; the whole
TRUNK_H/V family inherits it — `+MST` hybrids derive from the trunk results,
BITRUNK positions are quartile/cluster-data-driven not channel-sampled, the
2-pin Z is WL-locus-independent, and OOB loci are beyond the floor by
definition).  On b44 the knob emits `TRUNK_V@x1200` (io_pad_tl's right edge)
AT the 3510 floor — `test_topo_hanan_loci.py` guards both that and the
knob-off default pool staying byte-identical.  **Measured:** pool growth is
~1.3–1.6×, NOT the predicted ~2× (b44 23→35 (+52%), comprehensive_demo
154→245 (+59%), rnr/mix hier 1237→1688 (+36%), big2/b4_bus_077 17→22
(+29%)); QoR endpoints byte-identical knob-on vs knob-off on b44 unpinned /
comprehensive_demo / b4_bus_077 (measured pre-(b): the then-alphabetical
tie-break still picked the same `TRUNK_H+MST` at the 3510 tie — the
combined `hanan_loci` + structural-tiebreak corpus measurement is the
pending default-flip decision point), mix shifts to
1 overlap / 32 dnuts-unplaced (from 0/0) with detailed WL −0.5% — the
default-on QoR blocker, together with 16 fast-tier failures from index
renumbering incl. `topo_analysis` goldens (CONTENT shifts from new candidates —
order-only shifts are now absorbed by (b)'s canonical comparison; the
content regen stays reference-host-owned), is WHY it shipped opt-in; generation runtime +0.12s on mix
(0.41→0.53s).  **Interplay caution (measured):** `hanan_loci` +
`kWLSpread 0.125` on b44 picks the edge-aligned `TRUNK_V@x1200` (floor
nominal, narrowest envelope) but realizes 3719/bit (193376) — the
window-EDGE effect quoted above (the bus centre cannot reach an
edge-aligned optimum), vs `TRUNK_V@x1950`'s interior-optimum 3625/bit
(188513) that `kWLSpread` alone picks; an envelope term that prices
edge-vs-interior optima is the (b)/(c)-adjacent follow-on.
**Stressed-corpus measurement (default-flip gate, 2026-07-18, merged main
post-(b)):** the four-flow combined measurement on merged main was
byte-identical everywhere, so the flip's QoR case was re-measured on the
stressed corpus, where a richer pool can feed the planner's escalation
ladder and ripup's index-window alternates.  Per flow, baseline vs
`hanan_loci` appended to every generation command ("plain" = the pipeline
endpoint with the flow's own `negotiate_congestion` lines skipped;
"healed" = `negotiate_congestion` + `ripup_reroute` run after
`run_detailed_nuts`; opens = DNUTS unplaced bits, with keepout-culled
bits in parens; DISC = check_design `DISCONNECTED` bundles in the
endpoint's SELECTED routing):

| flow (variant) | side | pool | gen s | NUTS ov | opens (ko) | det WL | DISC |
|---|---|---:|---:|---:|---:|---:|---:|
| big2_noviz (checked-in = plain) | base | 1617 | 0.12 | 4 | 108 (0) | 11.47M | 0 |
| | hanan | 2326 (+44%) | 0.16 | 1 | 80 (0) | 11.33M | **4** |
| big2_noviz healed (0.15s / 0.17s) | base | | | 1 | 0 | 11.95M | 0 |
| | hanan | | | 0 | 0 | 11.43M | **4** |
| bigHalf as checked in (2× negotiate) | base | 2488 | 0.14 | 3 | 593 (301) | 14.78M | 0 |
| | hanan | 3954 (+59%) | 0.21 | 3 | 270 (54) | 14.04M | **5** |
| bigHalf plain | base | 2488 | 0.22 | 6 | 626 (198) | 14.36M | 0 |
| | hanan | 3954 | 0.23 | 3 | 270 (54) | 14.04M | **5** |
| bigHalf healed (51.4s / 3.3s) | base | | | 6 | 64 | 15.32M | 0 |
| | hanan | | | 0 | 0 | 14.31M | **7** |
| mempool_tile (as checked in) | base | 94 | 0.01 | 61 | 2971 (507) | 529756 | 0 |
| | hanan | 99 | 0.01 | 61 | 2971 (507) | 529756 | 0 |
| channel_stress (as checked in) | base | 461 | 0.01 | 0 | 3 (3) | 102052 | 0 |
| | hanan | 461 | 0.01 | 0 | 3 (3) | 102052 | 0 |
| hbundles/10 (as checked in) | base | 901 | 0.18 | 0 | 0 | 364252 | 0 |
| | hanan | 901 | 0.16 | 0 | 0 | 364252 | 0 |

**Verdict: the flip's QoR case is NOT made — and the gate found a
soundness blocker, not a neutral.**  On the metrics the healers optimize,
the richer pool *looks* like a clear win on both big flows (bigHalf healed
6 ov/64 opens → 0/0 with det WL −6.6% and a 15× faster heal loop; big2
healed 1 ov → 0/0 at −4.3%), and honestly neutral everywhere else
(mempool_tile +5 candidates, endpoint byte-identical; channel_stress and
hbundles/10 have ZERO pool growth — 2-pin/no-eligible-loci corpora — and
byte-identical endpoints).  But every big-flow "win" is disqualified by
the same discovery: the edge-aligned loci emit **electrically incomplete
candidates** — `check_design topo all` counts 92 island-split
(`DISCONNECTED`) candidates in bigHalf's knob-on pool vs 0 in baseline's
(e.g. b1 topo 1 `TRUNK_H@y3880`, a trunk ON a block edge whose seg graph
splits) — and nothing downstream refuses them: the coverage gate only
drops `BUSTERM_OPEN`/relay candidates, `check_design` is report-only, and
neither the planner score nor negotiate/ripup's `(opens, overlaps)`
metrics price completeness, so the flow converges ONTO them (missing
island = missing wire = fewer opens and less WL): bigHalf selects 5
`DISCONNECTED` bundles at the plain endpoint and ripup grows that to 7 in
its "0/0" healed state; big2's "0/0" healed state carries 4.  The
baseline pool has zero such candidates at every endpoint, so the opens/WL
columns above are not comparable across sides on big2/bigHalf.  Flip
prerequisite, ahead of any re-measurement: extend generation's coverage
gate (`filter_uncovered`) to drop island-split candidates (the
`DISCONNECTED` wire-graph check it already has access to), then re-run
this table — the honest part of the knob-on pool may still help (the
big-flow deltas are large), but no number here can support the flip until
the pool is sound.  Gen-runtime cost of the flip is a non-issue
(+0.04–0.08s worst, on big2/bigHalf).
✅ **(b) SHIPPED 2026-07-17: the WL tie-break is
structural** — `annotate_and_sort` (and the Python pool-merge resort
`_resort_pool_preserving_selection`, its replica for
`generate_more_topologies`/knob-memo replays) orders by `(wl, nsegs, type)`:
equal-WL candidates by ascending segment count (TEG-over bridge segments
counted — real wires, a junction each), type only as the final determinism
anchor.  b44's 3510 tie group now ranks its two 3-seg plain trunks ahead of
the 6-seg `TRUNK_H+MST` staircase, so the planner's equal-score lowest-index
pick is the structurally-tight candidate.  Spec + tests:
`features/structural_tiebreak.feature` / `test_topo_structural_tiebreak.py`.
  - *Pin index audit (the shipping CAUTION):* the sort defines the 1-based
    `select_topology` indices.  All 33 checked-in `.buda` flows with active
    pins (112 pin records) were executed before/after and each pinned
    candidate compared by `topo_uid`: **109 unchanged, 3 remapped** to
    preserve the original candidate — `demo/quickstart.buda` b1 3→2
    (TRUNK_V@x350@wl1200, 5 segs, now ahead of the 6-seg TRUNK_H+MST tie
    sibling), `flow/big_data_test/b44.buda` b1 10→8 (TRUNK_V@x700@wl4010,
    2 segs, moved to the head of its 4010 tie), and
    `flow/big_data_test/big_3bundles_sel_trunk+mst_topo.buda` b2 4→5
    (TRUNK_H+MST@y2340@wl6970 — its 4-seg tie sibling TRUNK_V+MST@x5690 now
    sorts first).  Sidecar JSON selections resolve `topo_uid` →
    `(type, wl)` → warned index hint in `_apply_selections`, so they are
    order-insensitive by construction; BDB `load_pipeline` resumes restore
    candidates from the persisted rows themselves with `is_selected`/
    `is_pinned` flags ON the row (uid-verified, v14) — never an index into a
    regenerated pool — so pre-change checkpoints resume identically.
    Four fast-tier b44-fixture tests that relied on the MST staircase
    sorting first now pin it by content.  The `topo_analysis` golden
    COMPARISON was made order-canonical (per-candidate blocks sorted by
    content on both sides — `topo_snapshot.canonicalize`) so a pure
    tie-order permutation needs no golden change: the text goldens stay
    byte-identical to the reference host's, candidate ranking is guarded by
    `test_topo_structural_tiebreak.py`, and only the two per-bundle DIGEST
    goldens (big, rnr_mix) were recomputed under the now
    order-independent-by-construction canonical hash — proven safe by
    computing the canonical digests with main's C++ and the branch C++ and
    getting byte-identical results.
  - *Measured (QoR endpoints, before → after):* b44 unpinned 188682 →
    188695 detailed WL (+0.007%, picks `TRUNK_H@y11330` over the staircase
    at the 3510 tie), comprehensive_demo 40868 → 40868,
    big2/b4_bus_077 191669 → 191669, rnr/mix (hier) 799419 → 799419; NUTS
    overlaps and DNUTS unplaced 0/0 on all four, both sides.  The change is
    an ordering policy, not a generator change: pools are content-identical.
CAUTION on future re-keys: any further tie-order change renumbers pools the
same way and needs a fresh pin audit.

**Default-flip prep for (a) — DONE (2026-07-18):** the INDEX/TEST audit gate
for flipping `hanan_loci` default-on is prepared in
[hanan_loci_flip_audit.md](hanan_loci_flip_audit.md): the full pin remap
table (22 of 112 pins across 15 flows shift, identity checked by `topo_uid`,
none go missing), the golden regen kit (6 files: four_blocks + dogleg2 fast,
all four mid flows), 10 index-sensitive fast-tier tests made CONTENT-based
(dogleg, alignment, tap-edge, charge_pull — green under both defaults), and
the sidecar/BDB order-insensitivity verification (uid-first resolution;
zero `(type, wl)` fallback collisions across all audited pools).  The degenerate-loci
blocker the audit surfaced is FIXED — next paragraph.

**Degenerate face/abutment-coincident loci — ✅ FIXED (2026-07-18, branch
`claude/hanan-loci-degenerate-gate`; the flip-blocker gate from
[hanan_loci_flip_audit.md](hanan_loci_flip_audit.md)).**  The extra loci are
exactly the block-face/abutment Hanan lines, and a trunk sampled ON such a
line degenerated three ways: (1) an aligned column's shared face line made
every stub's TRUNK-side endpoint land exactly on a face-riding block's face,
so `annotate_endpoints` tagged it a busterm TAP and the tap-wins-over-junction
precedence swallowed the stub↔spine junction — a `DISCONNECTED` all-tap tree
at the WL floor that sorts FIRST and gets auto-selected (a missing island =
less wire + fewer opens, so optimization CONVERGES onto them; pre-gate bigHalf
carried 92 such candidates and shipped 7 broken selected bundles at a healed
"0/0"); (2) the connected-but-junction-less variant defeated the fan-in
taper's driver→sink derivation; (3) an abutment-line spine whose slide window
only collapses under the block contract's pass-through clamps (b34
`TRUNK_H@y4615`: pre-contract [3365,4615], post-contract [4615,4615], taps
{blk_15,blk_15}) slipped past the pre-contract `filter_pinched`.  Fixed at the
root + gated:

- *Generation correctness* — `restore_face_graze_junctions` (topology.cpp):
  a stub trunk-side endpoint ON a spine segment whose tapped block's face IS
  the trunk locus is a GRAZE, not a landing (the spine itself rides that face,
  the same load-bearing inclusive overlap the ABUT candidates use), so the
  graze tap is cleared and the real junction is derived — the face-line trunk
  becomes a VALID candidate (classes 1–2 fixed structurally, taper works).
- *Loci pinch gate* — `generate_npin` re-checks loci-ONLY candidates (and
  their `+MST` hybrids) against the FINAL post-contract analysis and drops any
  with a zero-slide segment (class 3).  Scoped to the loci-only trunk
  positions so default-off pools are untouched by construction (moving the
  contract stamp above `filter_pinched` for everyone was measured to drop
  pre-existing zero-slide candidates from the DEFAULT rnr/mix pool — see the
  NOTE in `finalize_candidates`).
- *Defense-in-depth* — `filter_uncovered` now also drops `DISCONNECTED`
  candidates (the same island computation as `check_topo`'s
  `detect_disconnected`; declared-feedthru candidates exempt — their split-gap
  islands are bridged by the fed-through block).  This surfaced and led to a
  root-cause fix of a pre-existing default-off TEG-over bug: the gap-stub pair
  is emitted at rect CENTRES while the spine span came from the pulled
  `att[]`, so an extreme TEG block's stub pair floated off a too-short trunk —
  the spine span now extends to the gap-stub positions.

Default-off measured bit-identical: fast+mid 100% green (1485 passed),
topo goldens untouched.  Guards: `test_topo_hanan_loci_degenerate.py`
(repro fixtures for all three classes + the default-off superset property);
the four audit category-(c) tests pass under a scratch default-on.

*Pool effect of the fix (knob-on, generation stage):* bigHalf 3954 → 3950
candidates with DISCONNECTED 92 → 0; big2_noviz 2326 → 2233 with 41 → 0 —
i.e. most face-line trunks are REPAIRED in place (junctions restored, the
candidate stays and competes), and only the unplaceable remainder is gated.

*Stressed-corpus re-measurement (2026-07-18, post-gate; healed =
negotiate_congestion + ripup_reroute after both run_nuts and
run_detailed_nuts; opens = DNUTS unplaced; DISC = DISCONNECTED candidates in
pool / among selected):*

| flow / endpoint | side | pool | ov | opens | det WL | DISC pool/sel |
|---|---|---|---|---|---|---|
| big2_noviz plain | base | 1617 | 4 | 108 | 11465635 | 0 / 0 |
| big2_noviz plain | loci | 2233 | 2 | 28 | 11608386 (+1.2%) | 0 / 0 |
| big2_noviz healed | base | 1617 | 0 | 0 | 11842590 | 0 / 0 |
| big2_noviz healed | loci | 2233 | 0 | 0 | 11722521 (−1.0%) | 0 / 0 |
| bigHalf plain | base | 2488 | 6 | 626 | 14364154 | 0 / 0 |
| bigHalf plain | loci | 3950 | 5 | 283 | 15187090 (+5.7%) | 0 / 0 |
| bigHalf healed | base | 2488 | 0 | 80 | 15408926 | 0 / 0 |
| bigHalf healed | loci | 3950 | 0 | 63 | 14716562 (−4.5%) | 0 / 0 |

**FLIPPED — pending goldens (2026-07-19, branch
`claude/hanan-loci-default-flip`).**  `allow_hanan_loci_ = true`
(src/topology.h); every generation command grew a `no_hanan_loci` opt-out
(the legacy `hanan_loci` flag stays accepted as a keep-on no-op, and the
v15 knob memo round-trips the opt-out — encoding documented at
`_record_gen_knob_memo`, src/buda_cmds/topologies_cmds.py); the 20-pin
remap was re-collected against the gated pools and applied (all 112 pins
uid-verified identical — [hanan_loci_flip_audit.md](hanan_loci_flip_audit.md),
now APPLIED); the two opt-in spec tests were inverted.  Remaining: the
6-golden regen on the reference host
([hanan_loci_golden_regen.md](hanan_loci_golden_regen.md)).

*Final flip table (default-on vs `no_hanan_loci`, endpoints as checked in
incl. remapped pins; ov = NUTS overlaps, opens = DNUTS unplaced bits):*

| flow / endpoint | side | pool | ov | opens | det WL |
|---|---|---:|---:|---:|---:|
| b44 pinned (as checked in) | loci | 35 | 0 | 0 | 193376 |
| | no_loci (pre-flip pin 8) | 23 | 0 | 0 | 193376 |
| b44 planner-free (no pin tail) | loci | 35 | 0 | 0 | 193376 (picks TRUNK_V@x1200, the floor locus) |
| | no_loci | 23 | 0 | 0 | 188695 (picks TRUNK_H@y11330) |
| comprehensive_demo | loci | 243 | 0 | 0 | 40888 (+0.05%) |
| | no_loci | 154 | 0 | 0 | 40868 |
| big2/b4_bus_077 | loci | 21 | 0 | 0 | 191669 (byte-identical) |
| | no_loci | 17 | 0 | 0 | 191669 |
| **rnr/mix (hier, healed)** | loci (measured pre-pin-out) | 1675 | 0 | **42** | 800426 (+0.13%) |
| | no_loci (**= as checked in, PINNED-OUT**) | 1237 | 0 | 0 | 799419 |

**⚠ rnr/mix REGRESSED under default-on → PINNED-OUT by owner decision
(2026-07-19); regression numbers kept above for the record:** the audit's
QoR caveat was CONFIRMED post-gate.  Under the default-on pool mix's
fully-healed endpoint (2× negotiate_congestion + ripup_reroute) went from
a clean 0 overlaps / 0 opens to **42 dnuts-unplaced bits (2 bundles /
3 groups)** with detailed WL +0.13% and a ~3× slower heal loop (58s vs
21s end-to-end).  The richer pool steers mix's planner/healers onto
realizations whose real signal-track supply falls short; no DISCONNECTED
candidates are involved (0 in pool and selection on both sides — the gate
holds).  The stressed big2/bigHalf wins above still stand.  Resolution:
`flow/rnr/mix.buda` now generates with `no_hanan_loci` (flow-level bulk
flag, non-sticky — no knob memo involved), restoring the 0/0 healed
endpoint at det WL 799419 as checked in; a side benefit is that the
rnr_mix topo_analysis digest golden (and its slow-tier NUTS placement
digest) no longer shift, shrinking the reference-host regen to **5**
topo goldens.  Root-causing the interaction is a follow-on — next item.
b44's planner-free +2.5% detWL is the known window-EDGE effect (the floor
locus x=1200 nominal beats y11330's, but the bus centre cannot reach an
edge-aligned optimum) — the (b)/(c)-adjacent envelope follow-on above.
Two smaller default-on notes from the full-corpus smoke: `flow/sel_topos.buda`
(healer-less R&D flow) shifts 0 ov / 0 opens → 1 / 16 (its unpinned bundles
pick loci candidates; its b2 pin is uid-preserved), and three mid-tier
planner/NUTS measurement tests (`test_nuts_pull_repack` big fixture,
`test_planner_nontop_dead_span`, `test_planner_signal_tracks` mix repro —
where the signal_tracks benefit inverts on the loci pool, 300→332;
`test_datapath_multi_trunk_qor`, where the loci-enriched PLAIN pool beats
the BITRUNK two-level trees on the synthetic column datapath, 17947 vs
18332 — the multi_trunk QoR claim is now relative to the midpoint corpus;
and `flow/planner3.buda` itself, whose double-booked-trunk contention
scenario dissolves when the planner auto-picks a loci trunk) now pin
their generation corpus pre-flip via `no_hanan_loci` to keep their measured
scenarios; `flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda`'s
12 opens are pre-existing (identical both sides).

**Follow-on — bigHalf rr slow-tier endpoint under the loci default (OPEN,
accepted at flip time):** `test_bighalf_rr_reaches_clean_endpoint` (slow
tier — the ReadMe_bigHalf row-6 both-rr-lines config) fails to reach the
clean 0/0 endpoint on the reference host under the default-on pool; every
fast+mid tier test passes and the checked-in bigHalf.buda (row-7 config)
is unaffected.  Owner decision at flip time: ship and debug later — same
family as the mix–loci interaction below (the richer pool shifts what the
heal loop converges to), and the first debugging step is the same
occupancy/trial trace on the loci pool.

**Follow-on — mix–loci interaction ROOT-CAUSED (2026-07-19, still PINNED-OUT):**
on `flow/rnr/mix.buda` the RICHER default-on pool degrades the fully-healed
endpoint (0 ov / 0 opens → 0 / 42, det WL +0.13%, heal loop ~3× slower)
with **zero DISCONNECTED candidates** in pool or selection.  Reproduces
identically on today's kSegsRel-0.02-default planner (the flip predates the
kSegsRel default; re-measured to confirm the default did not change it).

**Root cause — LOW-layer dead-span escalation, NOT a WL / selection-envelope
bug.**  The 42 stranded bits are exactly `check_design` bundles **b61 seg1
(16) + seg6 (16)** and **b90 seg0 (10)** — "no track in DetailedNUTS".
Tracing their abstract layer assignments across the two pools is decisive:
b90 keeps the SAME selection (`TRUNK_V+MST@x1790`) yet its seg0 flips from
**M5 (TOP)** on the baseline pool to **M3 (LOW)** on the loci pool; b61's
strand segments land on **M2/M3** (LOW) vs M4/M5 baseline.  Mechanism: the
richer floor-tied loci pool shifts ~48 OTHER bundles' picks, which crowd
the TOP-layer bands around b61/b90's region; the STRICT escalation ladder
then pushes the contended segments DOWN to LOW layers whose per-cut band
capacity PASSES but whose FULL span sits over a leaf-footprint keepout with
ZERO DNUTS-placeable signal tracks → strand.  This is precisely the
**`nontop_dead_span_gate` blind spot** (wishlist-planner) — the loci pool
is only the TRIGGER (extra TOP contention), not a new failure mode.  The
regression is already present PRE-healer (loci plain 18 ov / 332 unpl vs
baseline 12 / 209), so it is a planner LAYER-ASSIGNMENT fault the healers
inherit, not a ripup blindness.

**The three doc-sketch suspects, resolved by measurement (loci-on, injected
before `run_planner`):**
- `kWLSpread 0.125` → **RULED OUT**: makes it WORSE (42 → 48).  The strand
  is not a WL-envelope mis-pricing of edge-aligned optima.
- `kPeak 0.1` → **PARTIAL** (42 → 32): its absolute-supply floor catches
  some supply-short LOW bands — the right FAMILY (supply), incomplete.
- ripup's lexicographic metric → **not primary**: the strand exists
  pre-healer; ripup cannot fix it because once contention pushes those
  segments off TOP, the only legal LOW layers are dead-span.
- `nontop_dead_span_gate 1` → **strongest, but over-conservative** (loci
  42 → 18 but +5 ov; and it regresses the BASELINE 0/0 → 0/48).  Exactly
  the gate's own documented open problem: `span_pool == 0` over the
  conservative abstract span can't tell a genuine cull from a survivor.

**Conclusion.**  mix–loci is the SAME open item as the `nontop_dead_span_gate`
**always-on discriminator** (wishlist-planner): a post-placement-aware
predictor — does the keepout cover the WHOLE routed extent (→ escalate to
TOP) or only part (→ leave on LOW)? — would fix both bigHalf (the gate's
original target) AND mix-loci cleanly, letting mix un-pin from
`no_hanan_loci`.  The `signal_tracks` inversion the earlier note cited (width
300 vs signal_tracks 332 opens, `test_planner_signal_tracks`) is the same
mechanism: signal-track capacity surfaces the LOW-band shortfall as more
overflow, which escalation then routes into the dead spans.

**✅ RESOLVED (2026-07-19).**  Re-measured on today's real CLI config
(kSegsRel-0.02 default + the merged healer dead-span fold, both gated on
`_healers_in_flow`) with a FRESH build: `rnr/mix` with `hanan_loci` ON is
**0 ov / 0 opens** — already clean, on par with the pinned `no_hanan_loci`
config.  The earlier 42/32/16 figures were an **artifact of a scriptless
measurement harness** that never set `script_path`, so `_healers_in_flow`
returned False and the kSegsRel default (which is gated on it) silently did
not apply — i.e. they measured a planner that does not exist in the real
flow.  With kSegsRel active the loci-shifted LOW-band crowding the dead-span
story described never materializes.  The "PINNED-OUT / LOW-supply ~2/3"
follow-on is therefore closed; mix may un-pin whenever the flip checklist
above is executed.  A companion planner change (escalate dead-LOW spans at
`run_nuts`, before the healers — wishlist-planner "dead-span discriminator")
additionally cleaned the as-checked-in `rnr/mix` (1/16 → 0/0) and cut
bigHalf's no-rr opens (190 → 94), with mix2/mix2_fast/big2 unchanged.

**Pre-flip verdict record (2026-07-18, post-gate stressed corpus):** the
wins SURVIVE the gate — and are now honest
(pre-gate, part of the loci "win" was missing islands).  With the gated pools,
`hanan_loci` strictly improves every stressed endpoint that matters: plain
opens −74% (big2 108→28) / −55% (bigHalf 626→283), healed endpoints equal or
better on BOTH metrics (big2 0/0 at −1.0% det WL; bigHalf opens 80→63 at
−4.5% det WL), zero DISCONNECTED anywhere on either side.  The remaining flip
blockers are purely mechanical: re-collect the on-side pin indices against the
GATED pools (the audit's remap table is stale — its CAUTION now says so),
regenerate the 6 shifted goldens on the reference host, invert the two opt-in
spec tests, and re-measure rnr/mix hier (the audit's QoR caveat) — see the
flip-commit checklist in hanan_loci_flip_audit.md.

### Piece (c) — gated WL-dominance pruning — ✅ SHIPPED (opt-in `set_prune_dominated`)

**As-built.**  `set_prune_dominated on` (default OFF — bit-identical flows
without it) makes every generation command (`generate_[hier_]topologies` and
the per-bundle regen variants; NOT the additive `generate_more_topologies`)
run a prune pass after the pool is final and before sidecar restore / BDB
persistence (`BudaSession._prune_dominated_pools`,
`src/buda_session/edit.py`; command in `src/buda_cmds/topologies_cmds.py`).
A candidate is dropped, with a printed note, when its envelope bottom
`wl_lo` STRICTLY exceeds a survivor's envelope top `wl_hi` (the deterministic
dominance salvaged from the rejected min-WL assignment above) **and** the
survivor passes the non-WL routing-equivalence gate (the Codex #313
condition):

- same `connected_block_names` and `feedthru_blocks` (as sets);
- same segment count, with a one-to-one (bipartite) segment matching where
  each pair has the same orientation and the same `layer_hint`;
- the survivor's perpendicular slide window **covers** the dominated
  segment's (the safe containment direction: every band/track placement
  reachable by the dominated candidate is reachable by the survivor —
  STRICT feasibility, the rip-up ladder, and DNUTS track supply are all
  window-scoped);
- the survivor's along-span lies **inside** the dominated segment's (the
  survivor crosses a subset of the planner cuts at the same bus width, so an
  overflow-free assignment for the dominated candidate maps onto one for the
  survivor with no greater demand on any band);
- the survivor's *nominal* WL ≤ the dominated one's (the nominal carries
  dangling wire the connection-based envelope excludes, and the planner's
  kWL term scores the nominal).

Non-participants (either side): TEG `bridge_segments` (wire outside
`segments`), fan-in `seg_bits` taper (per-segment demand differs per bit),
adopted dogleg jogs, U_OVL `perp_clamp` segments (a NUTS constraint the
window model does not carry), and underivable envelopes/connectivity.
`USER` candidates take no part at all — never pruned AND never a survivor
(`edit_commit` accepts a not-clean hand edit with only a warning, so an
invalid-but-shorter USER candidate must not evict the valid generated
alternative — Codex on PR #329); the selected/pinned candidate is never
pruned either, and a shrunk pool remaps the selection index by `topo_uid`.
Dominance + the gate compose transitively, so a pruned survivor may still
prune others soundly.

**Measured (ON vs OFF; OFF is bit-identical by construction and fast+mid
green):** on the four reference flows — `flow/big_data_test/b44.buda`
truncated before its hand-pin tail, `demo/comprehensive_demo.buda`,
`flow/rnr/mix.buda` (hier), `flow/big_data_test/big2/b4_bus_077.buda` —
the prune fires **0 times** with 11 / 459 / 3010 / 30 dominated pairs
refused by the gate respectively, and every QoR endpoint is identical
(b44 0 ov / 0 unpl / detailed 188682; demo 0/0; b4 0/0; mix heals to 0/0
with detailed 799419 in both runs — flow logs diff clean modulo the prune
summary).  A refusal-reason tally on comprehensive_demo: 338/459 different
segment count, 121/459 direction/corridor mismatches (window AND span both
fail) — i.e. every WL-dominated pair on the corpus differs in shape family
or corridor, exactly the ones the escalation ladder may need, confirming
the gate's necessity rather than its looseness.  Cost: envelope computation
makes generation slower when opted in (demo 0.01→0.21 s, mix hier
0.42→1.04 s); planner runtime unchanged.  The prune DOES fire on genuinely
redundant strictly-worse candidates (same corridors, far-face taps — see
`test/tests/test_topo_prune_dominated.py`), so it is safety infrastructure
for future generation changes (e.g. piece (a)'s ~2× pool growth) rather
than a corpus QoR lever today.  Indices renumber under the prune, so
`select_topology` pins must come from an opted-in run — the reason it is a
setup command, not a default.

## Dangling-topo handling — further investigation (`set_drop_dangling` modes) — OPEN

**Context.**  `set_drop_dangling` (opt-in, default `off`) now has three
non-trivial modes — `clamp` (bound every unbounded slide window to the design
extent, drop nothing), `clamp_drop` (clamp + drop only the truly-dangling
candidates), and `drop`/`on` (drop any dangling/unbounded candidate).  The
mode expansion (PR #391) and its full corpus sweep are recorded in
[`drop_dangling_modes_2026-07.md`](drop_dangling_modes_2026-07.md).  The sweep's
verdict: `clamp` is the low-risk bound (bit-identical to base on 32/34 flows,
pin-safe), `drop` is a high-variance per-flow lever, and `clamp_drop` is not a
good default (its truly-dangling drops perturb more than they save).  All three
stay opt-in.

**Open questions the sweep surfaced, none yet resolved:**
1. **Why does a pure `clamp` perturb two healer-heavy flows at all?**  `rnr/mix`
   0/0→1/2 and `rnr/slowdown_rnr` 0/32→1/36 are the only non-identical `clamp`
   rows.  Bounding a slide window a healer leaned on being wide changes which
   track a segment lands on — i.e. the clamp extent (blocks-bbox + candidate
   nominals + margin) is TIGHTER than the window a converged healer actually
   used.  Worth checking whether a looser clamp bound (e.g. the design extent
   grown by the widest converged healer slide, or simply the OOB detour-channel
   band when one is reserved) makes `clamp` a true no-op on these two.
2. **Is the unbounded window itself ever load-bearing?**  The ±2³⁰ sentinel is
   a "no-clamp" marker; the sweep shows clamping it is nearly always inert, but
   the two perturbations mean SOME realization used the unbounded reach.
   Understand what NUTS does with an unclamped window today (does
   `preferred_fit` ever place beyond the design extent?) — if never, the
   perturbation is purely a re-derivation-order artifact and the clamp could be
   made provably inert.
3. **Should the clamp bound come from the planner/NUTS instead of geometry?**
   The current extent is a generation-time geometric guess.  A placement-aware
   bound (the actual band reservations / track supply for the segment's layer)
   would be tighter where safe and looser where the healer needs room — the
   same "predict the realized extent" theme as the `nontop_dead_span_gate`
   discriminator (wishlist-planner).
4. **`clamp_drop` truly-dangling predicate.**  It currently drops a ConnSeg
   with a single non-block connection.  The sweep shows these are load-bearing
   more often than not (`rnr/mix` 0/0→0/32).  Investigate whether a stricter
   predicate (single non-block connection AND no downstream junction consumes
   the segment) would drop only the genuinely-useless tails, recovering the
   MST-hybrid trunk-tail case the along-DOF probe flagged (§"True along-flex
   trunk DOF", ~790k units of dead wire in never-selected `TRUNK+MST` hybrids).
   That overlaps the "tighten the MST-hybrid trunk endpoints at generation"
   follow-up noted there — a generation-time tail trim may be the better fix
   than a post-hoc drop.

See [`drop_dangling_modes_2026-07.md`](drop_dangling_modes_2026-07.md) for the
per-flow numbers behind each of these.

## Promote `set_dedup_loci` to default-on in generation — MEASURED, keep opt-in

**Full root-cause + measurement:
[`dedup_default_2026-07.md`](dedup_default_2026-07.md).**

`set_dedup_loci` (opt-in, default `off`) collapses candidates that are the same
topological choice differing only in a nominal trunk locus WITHIN a shared slide
window, keeping the best-estimated representative — the LOSSY sibling of the
sound `set_prune_dominated` (piece (c) above).

The [modes corpus sweep](drop_dangling_modes_2026-07.md) suggested dedup as a
runtime win at neutral-to-better QoR (`bigHalf` 84s→20s, `slowdown_rnr`
0/32→0/0, `mix2` 73→13 opens) — a strong default-on case. **Investigated and
rejected:**

1. **The two `hbundles` regressions are not a representative-choice problem.**
   On `hbundles/06` the base-selected candidate SURVIVES the dedup pool in every
   flipped bundle — the planner has it and chooses differently. Cause: the
   planner charges bands by NOMINAL perp, but dedup's equivalence is the SLIDE
   WINDOW, so collapsing different-nominal locus variants removes the planner's
   band-spreading options. Dedup's *value* is exactly that collapse, so it
   cannot be both valuable and planner-neutral (`charge_pull_target` does not
   cure it). A realization-aware representative would NOT help.
2. **The sweep's wins were a `kSegsRel` confound.** The sweep harness runs
   scriptless, so the `kSegsRel` 0.02 default (and dead-span escalation) were
   suppressed on BOTH its base and dedup sides — a planner regime that does not
   ship. Re-measuring with `kSegsRel` active on both sides (the real config),
   dedup is endpoint-neutral on 4/5 healer flows, **mixed** on `mix2` (opens ↓,
   overlaps ↑), and its runtime effect FLIPS SIGN: `mix2`/`mix` −83%/−56% but
   **`bigHalf` +510% (6× slower)** — `kSegsRel` already cleans bigHalf, and the
   deduped pool makes the healer thrash. dedup and `kSegsRel` chase the same
   congestion headroom, so stacked on the shipped default dedup is mostly
   redundant and sometimes counterproductive.

**Verdict: keep opt-in** (like the `multi_trunk` "measured, keep opt-in"
decision). A future flip would need a NEW justification measured *with*
`kSegsRel` active — not the scriptless sweep — and would still face the `mix2`
mixed change, the `bigHalf` slowdown, and the usual pin-remap / golden-regen
tail (dedup renumbers indices). If the real motivation is pool-persist size for
large hier designs, measure that against the BDB persist path
([`wishlist-bdb.md`](wishlist-bdb.md)) — it is independent of the planner regime
and may justify a persist-time (not generation-time) dedup instead.

## True along-flex trunk DOF (Stage C of the flexible-root re-arch)

**Context.** The coverage-driven flexible trunk span (PR on `claude/topo-gen-b4`)
makes a trunk's endpoints span exactly from the lowest busterm it taps to the
topmost stub centerline — minimal, no dead wire — but only **under
`double_detour`**, and the minimisation is computed at GENERATION (stub centerline
+ near-face coverage of pass-through blocks).  A stub's slide still comes only from
its busterm face intersected with the *generated* spine extent; NUTS
`do_span_adjustments` contracts/extends a spine end **only where the extreme
connection sits at the endpoint** (it SETs there) — a stub at a mid/T-junction is
extend-only, and there is no along-direction pull.  So the generated span is the
binding one (we place stubs at centerlines specifically so the extreme stub keeps
a positive slide window), and the behaviour is gated off by default to avoid
disturbing candidate rankings (always-on far-face traversal inflated V-trunk WL
and flipped planner selections).

**Wish.** A first-class **along-flex DOF** so a trunk spine's endpoints are a
*range* resolved by pull, not a fixed generated coordinate:
- Add along-endpoint flex/anchored flags + a coverage floor (+ an `along_pull`) to
  `ConnSeg` (`src/conn_topology.h`) — **DONE in Stage A** as
  `along_flex_lo/hi` + `along_cover_lo/hi` + `along_pull`, computed in a new
  `compute_along_pull()` (the `along_lo`/`along_hi` names were already taken by the
  segment's current extent, and a spine's two endpoints move independently, so a
  single signed pull like `net_pull` could not model them).
- Teach NUTS `do_span_adjustments` / `tighten_pulls` (`src/nuts.cpp`) to contract a
  spine end toward the pull-optimal coordinate even at a mid-junction, never past a
  busterm-face anchor or a pass-through coverage requirement.  **Blocked** — see the
  measurement verdict below: the regressions the flip introduces are upstream of
  NUTS, so this NUTS-only step does not unblock always-on on its own.

**Payoff.** The flexible-root span could then be **always-on** (not just
`double_detour`): trunks would generate tight, gain slide room from the DOF, and
contract to minimal honest wirelength — eliminating the ranking-inflation that
forced the `double_detour` gate, and letting the planner prefer the region-4
pass-through trunk on its merits. Also unlocks always-on generation of the
"region-4" pass-through trunk (e.g. `TRUNK_V@x5772` in
`flow/big_data_test/big2/b4_bus_077.buda`) instead of only under `double_detour`.

### Stage A — SHIPPED (inert ConnSeg data model)

The along-flex DOF is now a first-class field set on `ConnSeg`
(`src/conn_topology.h`): per-endpoint flex/anchored flags (`along_flex_lo/hi`), the
nominal along-coverage floor (`along_cover_lo/hi`) a flex end may contract down to,
and a signed `along_pull` WL hint — all computed by `compute_along_pull()`
(`src/conn_topology.cpp`).  It is deliberately **inert** (no NUTS consumer yet):
the WL corpus (`tools/wl_corpus.py`) is byte-identical to baseline across all 10
representative flows, and the fast tier is green.  This is the foundation for
whichever of the paths below is taken next.

### Measurement verdict (2026-07): the always-on flip is NOT ready, and the NUTS-only DOF is insufficient to make it so

Before building the Stage-B NUTS contraction, we ran the decisive experiment: flip
both generation gates (`topology.cpp:1442/1842`) to **always-on** and measure.  The
result contradicts the premise that the flip is a clean, DOF-fixable win:

- **Zero wirelength benefit.**  The 10-flow WL corpus (`tools/wl_corpus.py`) is
  **byte-identical** to baseline with always-on generation.  The real routed
  designs do not get tighter — the only concrete payoff is *enabling* the region-4
  trunk without the `double_detour` keyword (which already routes cleanly *with*
  it), not better interconnect.
- **3 genuine routing regressions** (fast+mid tier: 15 tests move — 1 pure-gate
  assertion, 11 clean-but-changed selection goldens, **3 real regressions**):
  1. `test_planner4_keepout_overflow_forces_detour` — planner **overflow 0→27 / 0→17**
     and a **new NUTS overlap** (M6, B1×B3): the tighter always-on trunk spans no
     longer fit the planner's reserved bands.
  2. `test_nuts_busterm_face_anchor::test_big2_b4_b24_routes_cleanly` — **48 bits
     unplaced** (was 0) + a new interval violation + 96 connectivity opens.
  3. `test_planner_low_over_cell::test_big2_no_low_layer_over_cell_dumping` —
     **2 LOW-over-cell dumps** (was 0): a bus dumped onto M3 with 0 signal tracks
     (the "Gap A" symptom returns).
- **The wishlist's proposed DOF cannot fix these.**  All 3 regressions are
  **planner-time / detailed-NUTS-time** effects of the tighter *generated* span
  (band overflow, track shortage).  The Stage-B DOF lives in NUTS
  `do_span_adjustments` — a **post-selection, post-planning** span contraction.  It
  cannot undo a planner overflow that already occurred, nor add signal tracks to a
  starved band.  Contracting the placed span at NUTS does not make the *generated*
  tight span fit the planner in the first place.

**The DOF saves 0 WL on any *selected* route — the removable dead wire lives only in
never-selected candidates.**  Using the Stage-A fields we probed every bundle's
topologies across the corpus (`tools/along_dof_probe.py`, `--verbose` for per-hit
detail) for a flex end whose along-coverage floor (`along_cover_lo/hi`) sits strictly
INSIDE the generated extent — i.e. genuinely removable "dead wire" (pass-through
coverage excluded).  Two scopes:
- **Selected topologies: 0 dead wire, every flow, gated and under the always-on
  experiment.**  Generation already lands the *winning* candidate's spine endpoints
  exactly on their extreme stub/coverage, so a NUTS-time DOF that contracts a span to
  its own coverage has nothing to remove on what actually routes.
- **All 4539 candidate topologies: ~790 k units of dead wire — concentrated entirely
  in `TRUNK+MST` / `TRUNK_OOB+MST` hybrids.**  These carry a genuine dangling trunk
  overshoot (e.g. `four_blocks` b2 `TRUNK_H+MST@y125` seg0 spans x=[99,151] but
  connects only at x=99 — 52 units of tail attached to nothing).  It is connected at
  one end (not an open), just wasteful — and that waste inflates the candidate's
  wirelength, which is precisely *why* the planner ranks these hybrids below the tight
  candidates and never selects them.

So the DOF's real leverage is **not** "save WL on the committed route" (there is none
to save) but "de-inflate loose MST-hybrid candidates so they could compete" — a
**ranking / selection** effect (the churn risk), not a wirelength saving on committed
routes.  And a NUTS-time DOF cannot even do that: ranking uses the *generation-time*
WL estimate, before NUTS runs.  A simpler, safer alternative surfaced by the probe is
to **tighten the MST-hybrid trunk endpoints at generation** (drop the dangling
overshoot at emit time) — independent of any DOF — which would make their WL honest
without a NUTS mechanism.  That is a scoped follow-up, noted here, not taken in this PR
(it touches exactly the candidates the always-on experiment showed cause selection
churn, so it needs the same WL-corpus gate).

**Re-scoped blocker.**  Making the flexible span always-on safely is therefore not
about a NUTS along-contraction — it needs either (a) honest generation-time trunk-tail
tightening (above), and/or (b) a **planner-aware** flex span: the congestion planner
reserving the trunk's minimal/contracted extent (endpoints as a range) rather than the
wide generated span, so a flex trunk stops overflowing bands it does not need
(`rebuild_cuts_` / demand charging).  Both are well beyond the ConnSeg+NUTS scope the
original wish assumed.  Until then the `double_detour` gate stays — it is a correct
guard, not an accident.  Stage A's data model, the ConnSeg python bindings, the WL
corpus harness (`tools/wl_corpus.py`) and the dead-wire probe
(`tools/along_dof_probe.py`, selected + all-candidate scopes) are all in place so that
larger effort can be measured from its first commit.

## `multi_trunk` as a default — MEASURED, keep opt-in

**What.** `generate_topologies multi_trunk` (opt-in) emits two-level
`BITRUNK_HVH/VHV` datapath trees. On column/row-aligned datapaths the planner
selects them and QoR improves substantially (col 3×5×6 WL −7.5 %, ov 3→1; col
4×5×8 WL −31.9 %, ov 11→1; row 3×5×6 WL −17.7 %, ov 1→0; see
[`mst_edge_realization.md`](mst_edge_realization.md)). Question: flip it on by
default?

**Measured (default off vs on, over flows that do NOT already opt in).** The
corpus is every flat flow + non-datapath demo + comprehensive_demo, each run with
its `generate_topologies` line as written (no keyword) vs with `multi_trunk`
forced on — so the comparison actually isolates the *default*.
- **QoR-neutral everywhere on the corpus** — identical abstract WL, overlaps and
  unplaced on tc3a_flat, channel_stress, four_blocks(+_3_bundles), dogleg1/2,
  b4_bus_077, and comprehensive_demo. **Zero regressions.**
- **The two datapath demos are excluded from this on/off measurement**: they
  hardcode `generate_topologies multi_trunk` (`flow/datapath_multi_trunk.buda`,
  `flow/datapath_row_vhv.buda`), so they run multi_trunk in *both* configs and
  measure nothing about the default. They are the *win* case (their headers
  document the plain-vs-multi improvement), not a neutral data point — see the
  substantial QoR gains quoted under **What** above.
- **Runtime cost negligible** on tc3a (`generate_topologies` 0.16 s both ways).
- **But zero corpus BENEFIT** (none of these are datapaths) and a real
  **candidate-count cost**: tc3a_flat 2571 → 2797 candidates (+8.8 %), b4 17 → 20,
  four_blocks 60 → 64. That compounds with the BDB candidate-topology persist
  path (see [`wishlist-bdb.md`](wishlist-bdb.md) — persistence, not generation,
  is the large-design bottleneck), so default-on taxes every big hier design for
  benefit only datapaths see.
- The earlier size sweep also found a couple of datapath shapes where multi_trunk
  *loses* (sparse `row 4×5×8` +7.7 % WL; saturated `col 2×6×6` +3.2 % WL) — so
  default-on is not universally safe even on its target class.

**Decision: keep it opt-in.** The benefit is real but confined to datapaths,
where the flag is the right mechanism; default-on would add candidate-count /
persist cost to every design for no corpus gain, with residual loss risk on some
datapath shapes. Revisit only if a datapath becomes a common default workload.

## Incremental re-analysis (topo/conn unification Phase D) — DEFERRED BY MEASUREMENT

**Context.**  The topo/conn unification
([`topo_conn_unification.md`](topo_conn_unification.md), all other phases
implemented) cached the six-pass derived analysis on the `Topology` itself,
validated by content fingerprint.  A mutation therefore costs exactly ONE full
recompute of ONE candidate on its next build — measured at ~10µs for small
topologies — and interactive editing (the TopoEdit ops) performs one mutation
at a time, so the planned dirty-set machinery would optimize a cost that is
already negligible.

**Wish.**  Scoped re-analysis on top of the cache: mutators report a dirty set
of segment indices; the per-neighborhood passes (`derive_conn_segs`,
`derive_net_pull`, `derive_along_flex`) re-run on the dirty closure and the
fixpoint slide passes on the dirty segments' connected component, gated by a
fuzz property test (incremental == full rebuild, field-for-field) and shipped
behind a flag.  Full design: `topo_conn_unification.md` §7.

**Trigger.**  Revisit only if TopoEdit profiling on very large candidates
(hundreds of segments) shows the recompute in an interactive loop; the Phase 0
byte-identity harness (`tools/topo_snapshot.py` goldens +
`test_topo_analysis_golden.py`) is the acceptance gate.

## Unify the 2-pin vs n-pin filter ordering — ✅ IMPLEMENTED

**As-built.**  `TopologyGenerator::finalize_candidates` (`topology.cpp`) is
the shared post-emission pipeline both dispatch targets now flow through:
seg-conn annotation → sort → **keepout cull** → pinch filter →
`connected_block_names` fill (then the caller's `filter_uncovered` coverage
gate, unchanged).  For `generate_2pin` this is the exact stage order it
always ran (byte-identical by construction); `generate_npin` **gains the
post-emission keepout cull** — its trunk-locus pre-gates remain as the
emission-side efficiency measure, but stubs and MST/BITRUNK edges are now
culled when fully blocked on all same-direction layers.

**Measured.**  Corpus: BOTH golden corpora byte-identical (no corpus n-pin
bundle has a fully-blocked segment — the loci pre-gates already steer
trunks).  The latent bug the cull closes, demonstrated on a 3-block
scenario (keepout over B's stub corridor on all V layers): pre-unification
`TRUNK_H_OOB@y250` survived with a dead V stub, routed **0 overlaps / 0
violations at abstract NUTS, then stranded 4/4 bits at DetailedNUTS** — and
was reachable without a pin via stage-a `ripup_reroute` (metric counts only
overlaps; a dead stub's band is empty).  Post-unification the candidate is
culled and the scenario routes fully clean.  Semantic note: a bundle whose
EVERY candidate is keepout-dead now surfaces as UNROUTED (zero-candidate
warning) instead of routing phantom wires — the 2-pin cull's long-standing
behaviour, now shared.  Tests: `test/tests/test_topo_filter_unify.py`.
The original item follows.

**Context.**  The two generation paths order their post-emission stages
differently (mapped in [`topo_conn_unification.md`](topo_conn_unification.md)
§1 and called out as a deliberate non-goal in its §12): `generate_2pin` culls
keepout-blocked candidates POST-emission (after sorting, before
`filter_pinched`), while `generate_npin` pre-filters trunk LOCI and has no
post-emission cull at all — MST/BITRUNK segments see keepouts only via the
per-edge `choose_edge_h_first`.  Annotation timing is split the same way
(batch `annotate_endpoints` for trunk/L/Z/U vs self-seeding inside the MST/
hybrid/BITRUNK builders), and `connected_block_names` fills at different
points relative to `filter_pinched` — the ordering accident the PR #194
review traced through the abutment fallback.

**Wish.**  One shared post-emission pipeline (emit → annotate → keepout cull →
pinch → coverage) both paths flow through, so a filter fix or a new gate lands
once instead of per-path.

**Cost/risk.**  This CHANGES ROUTING BYTES (candidates culled at different
stages survive differently), so unlike the unification's phases it needs its
own deliberate before/after review — the corpus diff is mechanical now:
re-baseline `tools/topo_snapshot.py` + `tools/wl_corpus.py` and review the
golden diff bundle by bundle.

## Resolve pre-planner hier slide columns against the cell-local floorplan — ✅ RESOLVED

**Resolution.**  `dump_topologies` now resolves each hier bundle's
generation-time floorplan via `_make_topo_fp_resolver`
(`src/buda_session/hier.py`) — a per-call wrapper→Floorplan resolver built on
the SAME 3-case `_floorplan_for_hbundle` that `check_connectivity` already
used (cell-local / endpoint-depth / cross-level custom; expanded per-instance
wrappers and flat bundles keep `self.fp`).  The floorplan is threaded through
`_topo_min_slide`, `_topology_wl_interval` (+ `_seg_slide_box`/`_fp_extent`
for the sentinel clamp), and `_dump_conn_detail`, so a cell-level template
shows real finite `mslide`/`wl[lo..hi]`/`--conn` slides the moment candidates
exist — measured: the PR #215 repro's I_H goes `free` → `mslide=80`,
`slide=[60..140]` (cell-local), and after `run_planner hier` the expanded
wrapper reports the SAME magnitudes in absolute coords (`slide=[110..190]`).
`free` remains the display for a genuinely unresolvable slide.  Test:
`test_dump_topologies_conn.py::test_mslide_resolves_cell_local_before_planner`
(the inverse of the old `…_prints_free_not_sentinel`, plus the pre/post-planner
consistency check).  The original item follows.

**Context.**  `dump_topologies` slide-derived columns — `mslide`
(`_topo_min_slide`, `src/buda_session/reports.py`) and the `wl[lo..hi]`
envelope's upper bound (`_topology_wl_interval`, `src/buda_session/nutsflow.py`)
— build each candidate's `ConnTopology` against `self.fp`, the **absolute**
floorplan.  A **cell-level HBundle template** dumped *before* `run_planner hier`
is still in cell-local coordinates, so its block faces don't resolve against
`self.fp`; ConnTopology leaves every segment's perpendicular slide unbounded
(the ~2e9 sentinel).  PR #215 made that honest at the display layer — the column
prints `free` instead of the raw sentinel, and the doc tells the reader to dump
*after* `run_planner hier` for real slide/envelope numbers — but the underlying
value is still unresolved until the planner expands the template into
per-instance absolute wrappers.

**Wish.**  Build the cell-level template's `ConnTopology` against its
**cell-local floorplan** — the same floorplan `generate_hier_topologies` already
constructs when it generates those candidates — rather than `self.fp`, so a
pre-planner hier dump shows correct finite slides (and a real envelope `hi`)
without needing to plan first.  `mslide` and `wl[lo..hi]` would then be
meaningful the moment candidates exist, matching the flat flow.

**Why deferred / cost.**  Read-only reporting affordance, not a routing
correctness issue — the `free` display + doc note (PR #215) already prevent the
misread, and the numbers are correct post-planner regardless.  The real work is
plumbing the per-template cell-local floorplan (or a way to reconstruct it from
the bundle's `cell_context`) out to the reporting path, which today only holds
the absolute `self.fp`.  `generate_hier_topologies` builds that local floorplan
transiently during generation; making it available at dump time means either
persisting it per cell-level bundle or rebuilding it on demand from the BDB cell
definition.

**Where to start.**  `generate_hier_topologies` (the cell-local case, hier
topology generation) is where the local floorplan is built — capture/rebuild it
keyed by `cell_context`; then have `_topo_min_slide` / `_topology_wl_interval`
pick the cell-local floorplan for a cell-level template and `self.fp` for an
already-absolute bundle.  Gate: the flat flow and post-`run_planner hier`
numbers must be byte-identical (this only *adds* resolution to the pre-planner
hier case), plus a test that a pre-planner hier template dump now shows finite
`mslide` instead of `free` (the inverse of
`test_mslide_unbounded_prints_free_not_sentinel`).


## Corner-margin default `dx=dy=0` — MEASURED, keep 0 (corner-touch gap ✅ resolved)

**Question.**  The global corner margin (`BlockCornerMargin`, `topology.h`)
defaults to `{0,0}` — "no constraint beyond the face extent".  Nothing recorded
*why* 0 is the default (only what it means).  Two experiments settle it.

**Experiment 1 — global `corner_margin dx 1 dy 1` over the `wl_corpus` corpus.**
Baseline = flows as written; experiment = strip any standalone `corner_margin`
and force the global default to `1 1` (per-block `add_block … corner_margin`
overrides kept).

| Flow | abstract WL | detailed WL | overlaps | unplaced |
|---|---|---|---|---|
| tc3a_flat | +0.6% | +0.4% | 0→0 | 0→0 |
| comprehensive_demo | +0.1% | — | 0→0 | — |
| channel_stress | +0.3% | +0.3% | 0→0 | 0→0 |
| four_blocks * | 0.0% | 0.0% | 0→0 | 0→0 |
| four_blocks_3_bundles | +8.8% (430→468) | — | 0→0 | — |
| dogleg1 ** | −0.5% | −0.7% | 0→0 | 0→0 |
| dogleg2 ** | −0.5% | −0.3% | 0→0 | 0→0 |
| double_detour | (flow runs no `run_nuts`) | — | — | — |
| b4_bus_077 | +0.0% (3221→3222) | 0.0% | 0→0 | 0→0 |
| mix (hier) | +1.6% | +2.6% | **3→2** | 0→0 |

\* four_blocks already sets `corner_margin 1 1` — an identical control.
\** dogleg1/2 baseline is `2 2`, so this row is a `2→1` *reduction* (less margin
   → slightly shorter), not a clean `0→1` — not representative of the default.

- **Safe:** no new overlaps and no new unplaced bits anywhere; the hier `mix`
  flow even *improved* by one overlap (3→2) as the 1-unit inset nudged endpoints
  off a contended band.
- **But not free:** a small, consistent WL increase on the genuine `0→1` flows
  (~+0.1 %..+0.6 % flat, +1.6 %/+2.6 % on hier `mix`) — endpoints pulled a unit
  off block corners make stubs/trunks a hair longer.  (`four_blocks_3_bundles`
  +8.8 % is 38 units on a 430-unit toy design — a small-design artifact.)
- **No broad benefit:** the single `mix` overlap relief is the only upside, and
  it came *with* a WL cost.  Everywhere else it is neutral-to-slightly-worse.

**Decision: keep the default `0`.**  It is the permissive identity; a margin is
an opt-in tightening for a *specific* design where corner congestion is real (as
`dogleg1/2` do at `2 2`), not a global default worth a WL tax for no general win.

**Experiment 2 — corner-only touching blocks are a real generation gap at `0`.**
Two blocks that meet at a single corner:

```
source tracks/tracks.buda
add_block u1   0   0 100 100
add_block u2 100 100 200 200      # shares only the point (100,100) with u1
add_bus b[8] u1 u2
run_bundler
generate_topologies               # dx=dy=0 → ZERO candidates (bus unrouted)
# corner_margin dx 1 dy 1         # → 5 candidates, routes cleanly
```

- **`dx=dy=0` → 0 candidates.**  At margin 0 the busterm faces meet only at the
  corner point, so every generated L/U segment is degenerate/pinched and dropped
  by `filter_pinched` / the coverage gate — the bus is left unrouted.
- **`dx=dy=1` → 5 candidates** (`L_HV`, four `U`), routing cleanly (0 overlaps,
  16 bit-wires placed, 0 unplaced).  Shrinking the bboxes (u1→`[1,1,99,99]`,
  u2→`[101,101,199,199]`) opens a 2-unit gap so the faces no longer share a
  point and positive-length segments survive.

So the margin knob is not merely cosmetic: a tiny inset *rescues* a degenerate
placement the default cannot route.

**Follow-up — ✅ RESOLVED.**  Corner-only-touching blocks are now rescued at
generation *independent of the `corner_margin` knob* — the same way a fully shared
edge is (`ABUT_H`/`ABUT_V` + `kAbutmentSpanEpsilon`, PR #197).  The 2-pin fallback
(`generate_candidates`, `topology.cpp`) now detects a single-corner touch and emits
two `CORNER_HV`/`CORNER_VH` candidates by reusing the MST path's `corner_diagonal_L`
(an L routed *around* the shared corner, each leg tapping a real face with slide
room), so the bus routes at the default `corner_margin 0`.  Fully-coincident /
overlapping-with-no-channel blocks correctly stay candidate-free (the zero-candidate
warning fires — the intended flag for a degenerate placement).  Gated `wl_corpus`
byte-identical across all 10 flows (the branch only fires when a bundle would
otherwise have *zero* candidates) + regressions in `test_topo_abutment.py`
(`test_corner_touch_rescued_by_diagonal_L`, `test_corner_touch_bus_routes_to_completion`,
`test_fully_coincident_blocks_produce_no_candidate`).

## Persist the overlap-U perp clamps (`Segment::perp_clamp_lo/hi`) — ✅ RESOLVED

The corner-wrapping overlap U's (`U_OVL_*`/`UU_OVL_*`, PR #224) carry
generation-supplied per-segment perpendicular slide clamps
(`Segment::perp_clamp_lo/hi`) that pin each face-tap arm to its exclusive band and
each detour arm outside the union bbox.  They are **load-bearing for correctness**
(without them NUTS collapses a wrap through the overlapped block); Codex on #224
flagged that a U_OVL persisted to a BDB and resumed via `load_pipeline` **before**
NUTS would reload unclamped and could collapse again.

**Resolved (option 1 — persist):** `perp_clamp_lo/hi` are now `topology_segment`
columns (**BDB v16**), written by all three persist sites (flat + hier + regen) and
restored by `load_pipeline`, mirroring the `edge_id` (v14) round-trip exactly.  They
stay **out of the topology fingerprint** (deterministic from geometry, so an
identical-geometry cache hit already implies an identical clamp — no uid churn), and
pre-v16 rows migrate to the INT_MIN/INT_MAX unclamped sentinels (correct for every
non-U_OVL segment).  Round-trip + resume-before-NUTS regressions live in
`test_bdb_topology_persist.py`
(`test_overlap_u_perp_clamp_persists_and_roundtrips`,
`test_overlap_u_perp_clamp_survives_load_pipeline_resume`).

While here, confirmed `edge_id` persistence (v14) was already complete end-to-end
(schema + all persist sites + `load_pipeline` + `test_topo_resume_analysis.py`); the
stale "NOT YET PERSISTED" note on `topology.h Segment::edge_id` was refreshed.

Option 2 (re-derive on load, no schema change) was considered and set aside: the
persist path is a direct mirror of the existing `edge_id` machinery, so it is the
lower-risk, self-documenting choice.

## Audit 2026-07: stub_suppressed TEG-over blocks skip the spine pre-extension — OPEN (pool loss, not an open)

Finding C4-01 of [audit_2026-07.md](audit_2026-07.md), refuted as a
correctness bug on HEAD: the trunk generator's TEG-over pre-pass (which
folds a gap block's near/far stub positions into the spine extent) skips
blocks whose normal stub is `stub_suppressed`, while the emission still
emits their gap stubs — off-spine, so the candidate's wire graph splits and
the generation coverage/islands gates (#335) DROP it. Net effect today is a
lost candidate (pool completeness), not a silent open. Fix shape: extend
the pre-pass to suppressed TEG-over gap blocks too (their gap stubs are the
real connection; the suppression logic models only the single-stub form),
then re-measure the pool on the TEG flows.
