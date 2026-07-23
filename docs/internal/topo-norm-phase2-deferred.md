# Topo-norm Phase 2 (defects 2 & 5) — resolved, and how

**Status:** Phase 1 (PR #55) is merged. **Defect 2 (issue #57) is FIXED**
(2026-07-22, see below). **Defect 5 (issue #58) is FIXED** (2026-07-23, see
below) — but NOT the way the issue first proposed. This note records the
findings so the decision is not re-litigated from scratch.

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

## Defect 5 — out-of-bbox trunk placement — ✅ FIXED (issue #58, 2026-07-23)

### The two dead ends (kept for the record)

A trunk (`>=2` SEG conns, no busterm of its own — the predicate in `nuts.cpp`) is
bounded by `compute_slide_ranges` Pass 2 only on sides where a stub pushes it out.
When every stub anchors on one side (an OOB trunk hugging the block cluster) the
other side stays at its `INT` sentinel — unbounded (`TRUNK_H_OOB@y905` had slide
`[-1073741824, 980]`). The issue proposed *clamping the slide window*. That was
tried twice and is a dead end:

- A Pass-3 clamp in `compute_slide_ranges` feeds the downstream
  `tighten_passthrough_ranges` / `pin_relay_tap_connectors` passes, creating
  *pinched* windows (`perp_lo == perp_hi`) that `filter_pinched` drops at
  **generation** — U-shape/detour trunks legitimately sit outside the bbox and
  got pinched away.
- A final "clamp only a genuinely-unbounded side" pass (PR #410,
  `clamp_sentinel_windows`) also failed: the analysis window is a **deliberate**
  free-DOF representation (serialized as `null`; `test_free_slide_windows_serialize_as_null`),
  and the planner + NUTS both *consume* `perp_lo/hi`, so bounding the stored
  value shifts selection and placement. Measured: 25 fast+mid failures and a QoR
  corpus regression (+103 opens on the `mix2` healer family) for the salvage —
  see the PR #410 discussion. **The analysis slide window is left unbounded on
  purpose.**

### What actually mattered — and the fix

The runaway slide window is a *symptom*, not the bug. The real defect surfaces
only when an OOB trunk is **selected** (an expert `select_topology`, or ripup
promotion under congestion): its nominal perp sits *below/beyond the block
bbox*, but the NUTS placement interval is seeded from the Hanan grid extent
(`get_hanan_grid` = blocks + keepouts only). So the interval collapses
(`[1000, 980]`, inverted), the trunk violates its interval, and detailed NUTS
strands every one of its bits — while abstract NUTS reports "0 overlaps". A
`detour_channel` *reserves* exactly the band such a trunk routes in, but it was
plumbed into topology **generation** only, never into the NUTS/DNUTS boundary.

The fix (in `nuts.cpp`) makes the NUTS design boundary detour-channel aware and
fails loud when a selected OOB trunk still has nowhere to sit:

1. **Detour-aware interval seed** (`extract_segments`): when an explicit
   `detour_channel` side is set, the interval seed reaches into it
   (`interval_lo -= south`, `interval_hi += north`, etc.). Bounded segments
   re-tighten to their slide window immediately after, so **only free-slide
   (sentinel) OOB trunks use the extra room** — a design with no explicit
   channel is byte-identical. The OOB trunk then gets a real window
   (`[548, 980]`) and seats; its bits place.
2. **Loud failure** (`derive_unseatable_trunks`, `NUTSResult::unseatable_trunks`):
   a selected OOB trunk whose nominal is *still* outside the routable boundary
   (Hanan extent + explicit channel) is reported per-trunk **and** on the NUTS
   headline — no more silent "0 overlaps" while DNUTS drops the bits. Widen the
   channel or re-select.

Both are gated on the geometry, so the default (no explicit `detour_channel`)
path is untouched: the **34-flow QoR corpus is byte-identical to `main`** on
overlaps / opens / abstract WL / detailed WL, with **zero** false-positive
unseatable flags. This is the "NUTS-placement-time" home the original deferral
predicted — realized as an *extend* (seat the legitimate detour route) plus a
*loud reject* (for the genuinely unseatable pin), not a slide-range clamp.

Repro: `test_topo_norm_phase2_repro.py::test_defect5_oob_trunk_seats_in_detour_channel`
(seats + all bits place) and `::test_defect5_oob_trunk_unseatable_without_channel_is_loud`
(flagged loud, bits strand).

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
- **Defect 5 (issue #58): ✅ FIXED (2026-07-23).** Resolved at NUTS placement
  time, as the deferral predicted — but as an **extend + loud reject**, not a
  clamp. The detour-aware interval seed lets a selected OOB trunk sit in the
  `detour_channel` band it routes in (so its bits place); an OOB trunk with no
  channel to sit in is flagged `NUTSResult::unseatable_trunks` loudly instead of
  silently stranding at DNUTS. Gated on geometry, so the QoR corpus is
  byte-identical to `main`. The runaway *analysis* slide window is left unbounded
  on purpose (a deliberate free-DOF/null contract that the planner and NUTS
  consume — bounding it cascades, see the two dead ends above).

Phase 1 (honest WL → clean selected topologies) plus the defect-2 and defect-5
fixes are the shipped outcome of topo-norm.
