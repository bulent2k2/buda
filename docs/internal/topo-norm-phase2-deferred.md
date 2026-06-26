# Topo-norm Phase 2 (defects 2 & 5) — deferred, and why

**Status:** deferred. Phase 1 (PR #55) is merged; Phase 2 was investigated and
intentionally not implemented. This note records the findings so the decision is
not re-litigated from scratch.

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

## Defect 2 — staircase (collinear relay stubs)

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

Defects 2 & 5 are **deferred**. They do not affect routed output, and the safe
fixes the current architecture allows cannot express them. Revisiting either should
start with the enabling architectural change, scoped and tested on its own:

- **Defect 2:** teach `ConnTopology` to infer **collinear (end-to-end) joins**, then
  implement spec-a2 stub-combine in `complete_relay_junctions`.
- **Defect 5:** move the clamp to **NUTS placement time** (bound the placed
  position, not the slide window), with dogleg/pull-repack revalidation.

Until then, Phase 1 (honest WL → clean selected topologies) stands as the complete,
shipped outcome of topo-norm.
