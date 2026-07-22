# Topo-norm Phase 2 (defects 2 & 5) — deferred, and why

**Status:** Phase 1 (PR #55) is merged. **Defect 2 (issue #57) is now FIXED**
(2026-07-22, see below). **Defect 5 (issue #58) remains deferred.** This note
records the findings so the decision is not re-litigated from scratch.

## Background

The "topo-norm" work split into two phases:

- **Phase 1 — generation normalization (done, PR #55).** Made trunk+MST
  wirelength *honest* (spine re-clip, clean-tree gate, bridge-segment WL). This
  fixed the `tc3a_flat` regression at its root and removed the visible defects in
  **selected** topologies: with honest WL the congestion planner now picks clean,
  low-segment, well-bounded candidates on its own (B36 → 7 segments, B61 →
  `mslide=180`, B23 → 0 unplaced bits, FEEDTHRU_RELAY 52 → 0).
- **Phase 2 — candidate-quality cleanup (this note).** Two remaining items from
  the user spec [`mst-stub-conn.md`](mst-stub-conn.md):
  - **Defect 2 — staircase:** spec-a2 *collinear-combine* in
    `complete_relay_junctions` (`topology.cpp`).
  - **Defect 5 — outlier slide:** cluster-bounded trunk slide in
    `ConnTopology::compute_slide_ranges` (`conn_topology.cpp`).

The decisive common fact: **both defects only affect *non-selected* candidates.**
With Phase 1's honest WL the planner never selects the staircase/outlier-slide
candidates, so neither defect changes any *routed* output. They are browsable-only
artifacts in the topology explorer.

## Defect 5 — outlier (runaway) trunk slide

A trunk (`>=2` SEG conns, no busterm of its own — the predicate in `nuts.cpp`) is
bounded by `compute_slide_ranges` Pass 2 only on sides where a stub pushes it out.
When every stub anchors on one side (e.g. an OOB trunk hugging the block cluster)
the other side stays at its `INT` sentinel — unbounded — so NUTS *could* slide the
trunk to the chip edge. Example measured: `TRUNK_H_OOB@y905` had slide
`[-1073741824, 980]`.

A Pass-3 clamp (bound a trunk's window to the union of its nominal position and its
stub-face cluster) does fix the geometry in isolation, **but it is too entangled to
ship:**

- Clamping in `compute_slide_ranges` feeds the downstream `tighten_passthrough_ranges`
  / `pin_relay_tap_connectors` passes, creating new *pinched* windows
  (`perp_lo == perp_hi`) that `filter_pinched` then drops at **generation time** —
  U-shape / detour trunks legitimately sit outside the bbox and got pinched away.
- Even a narrow "only clamp a genuinely-unbounded side, never pinch" version still
  shifted **NUTS placements on selected topologies** (`test_nuts_dogleg`,
  `test_nuts_pull_repack` failed). Deciding whether each shifted placement is an
  improvement or a regression needs per-test correctness review.

Two implementations broke **19–27 tests** for zero change to routed output. The
right home for this, if ever pursued, is a **NUTS-placement-time** clamp (bound the
*placed* position, not the slide window), with full dogleg/pull revalidation — not a
slide-range edit.

## Defect 2 — staircase (collinear relay stubs) — ✅ FIXED (issue #57, 2026-07-22)

**Fix as shipped.** The staircase came from the general chaining, which the
degenerate-collinear MERGE in `complete_relay_junctions` was *refusing* to
pre-empt whenever a stub's FAR endpoint tapped another block (dropping the stub
would strand that block's tap). The fix keeps the merge and, in that far-tap
case, **repoints the far block's landing-map entries** (`incident` / `all_land`)
from the erased stub onto the surviving merged wire — the tap transfers to the
straight pass-through, so no jog. Only a declared feedthru still refuses (it must
keep its two BUSTERM landings). Repro `test_defect2_no_collinear_staircase_jogs`
flipped xfail → pass; fast tier fully green.

**Not routed-neutral (measured, and why the fix is still correct).** The merge
makes the affected TRUNK+MST candidates ~4 units shorter (jog removed), which is
honest WL. Two intentional consequences: (1) on the column datapath multi_trunk
picks the cleaner TRUNK+MST over one BITRUNK_HVH while STILL improving QoR (WL
15563 ≤ plain 16600, overlaps equal — the `multi_trunk` mechanism assertion was
relaxed from "≥2 BITRUNK" to "≥1", the QoR-win assertions unchanged); (2) the
`big.buda` / `rnr/mix` topo_analysis digests change (`tools/topo_snapshot.py`
regenerated in place). The generation-stage snapshot is integer content (coords,
layers, integer WL, integer slide ranges), so the digests are **host-independent**
— verified: regenerating EVERY golden on this non-reference host changed ONLY the
~20 big / 5 mix bundles this merge touches, with every other bundle byte-identical
to the reference-host golden (had there been FP/ISA drift, unrelated bundles would
differ too). So the re-baseline did not need the reference host. The original
deferral note below
predicted "non-selected only" — that held when written but the candidate pool
has since grown (hanan-loci default flip), so a couple of close datapath
selections now flip; the flip is QoR-neutral-or-better, so the fix shipped.

### Original deferral analysis (kept for the record)

The staircase is tiny `len=2` jogs: two **collinear** relay stubs (e.g. blk_13: the
trunk stub and an MST edge, both H at `y=3610`) bridged by a connector offset to
`y=3612`.

Root cause is structural: `ConnTopology::infer_connections` only infers
**perpendicular** (T/L) junctions — it skips same-orientation segments
(`ci.horiz == cj.horiz → continue`). So two collinear stubs **cannot** be joined by
a collinear connector; completion is forced to offset the connector by 2 units to
create an inferrable perpendicular junction. That offset *is* the staircase.

Spec a2 ("combine the two stubs into one") therefore requires physically **merging**
collinear stubs into a single segment (extend one, erase the other, erase the
bridging jog). That means generalizing the verified de-overlap surgery to remove
segments that are *not* collinear-contained — which the code **deliberately avoids**.
From `complete_relay_junctions` (`topology.cpp`):

> A final de-overlap pass drops a connector only when it is *collinear-contained*
> within another wire (genuinely redundant) … it is deliberately NOT generalized to
> drop any "globally redundant" connector — the MST edges already span a tree, so
> every connector is globally redundant via the long tree path, and dropping a
> non-collinear one would re-open the feedthru relay it exists to prevent.

So the narrow fix the architecture allows cannot express a2, and the version that
can carries exactly the feedthru-reopening / cascade risk that blocked defect 5.

## Decision

- **Defect 2 (issue #57): ✅ FIXED (2026-07-22).** The enabling change turned out
  NOT to need the "teach `ConnTopology` collinear joins" route feared above:
  `complete_relay_junctions` already had the collinear stub-combine (the MERGE),
  it was just gated off when a far endpoint tapped a block. Doing the merge with
  a **tap transfer** (repoint the far block's landing onto the merged wire)
  expresses spec-a2 without generalizing the de-overlap surgery — so it does NOT
  carry the feedthru-reopening risk (a declared feedthru still refuses). It is
  not routed-neutral (see the defect-2 section), but the selection flips it does
  cause are QoR-neutral-or-better, and the two affected topo_analysis goldens
  (big / mix) were regenerated in place — verified host-independent, so no
  reference-host re-baseline was needed.
- **Defect 5 (issue #58): still deferred.** Move the clamp to **NUTS placement
  time** (bound the placed position, not the slide window), with dogleg/pull-repack
  revalidation — the slide-range edit cascades through `filter_pinched` and shifts
  selected placements (19–27 tests, zero output change). Tracked in issue #58.

Phase 1 (honest WL → clean selected topologies) plus the defect-2 fix are the
shipped outcome of topo-norm; only defect 5 remains.
