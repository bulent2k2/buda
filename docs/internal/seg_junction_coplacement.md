# Seg-to-seg junctions as first-class truth + hard co-placement in NUTS

Status: **Part A shipped** (as topo-truth **Phase 4**, `Topology::seg_conns` —
see `single_source_topo_truth.md`; note it retired the geometric scan outright
rather than demoting it to a fallback, with `annotate_seg_conns` as the explicit
re-derivation tool). **Phase 0 (FP-determinism stopgap) and the first Part B
slice (coverage as a maintained invariant) shipped together** on the follow-up
branch: `preferred_fit` + the pulled/free placement sorts use epsilon-hysteresis
(1e-6, first-candidate-wins — exact ties keep today's winner byte-identically;
sub-epsilon FP noise can no longer flip a track across machines), and
`do_span_adjustments`' coverage guarantee now consults ALL of a segment's
junction partners via `rev_conn_map`/`ts_ptr_map` (cross-layer), so a per-layer
call can no longer contract a trunk past a tap outside its slice — the
`tc3a_flat` bundle-48 mechanism. Verified digest-identical to the Phase 4
baseline on the flow corpus (mix/dogleg1/dogleg2/aligned/comprehensive); the
`flow/big_data_test/nuts-open.buda` canary stays clean. A "prefer lower
coordinate" tie key was tried first and rejected — it shifted real placements
(mix.buda DNUTS 60→138 unplaced); hysteresis preserves single-machine behavior
exactly. **Remaining:** full Part B (junction pins as placement constraints,
below) and Phase 3 persistence (`topology_seg_conn`, planned as topo-truth
Phase 5). Builds directly on
[`single_source_topo_truth.md`](single_source_topo_truth.md) (busterm truth,
Phases 1–3, done) and the abstract-NUTS placement core (`src/nuts.cpp`).

## TL;DR

The busterm half of a topology's connectivity — *which segment endpoint taps
which block face* — is already **authoritative** (`Topology::seg_busterms`,
persisted, geometric fallback retired). The **seg-to-seg** half — *which
perpendicular segment joins which, and at which endpoint* — is **not**: it is
re-inferred geometrically from nominal coordinates on every
`ConnTopology::build`, and NUTS then treats those joins only as a **soft
post-hoc span-extension**, never as a placement constraint. This proposal
finishes the single-source principle for seg-to-seg joins and, more importantly,
makes NUTS **co-place** joined endpoints so a join cannot be broken by track
selection. That structurally eliminates the "abstract-NUTS open that DetailedNUTS
happens to recover" class of bug (concrete instance: `tc3a_flat` bundle 48,
below), and removes one of the reasons routing results diverge across CPUs.

## Background — what is authoritative today

A `Topology` (`src/topology.h`) stores:

- `std::vector<Segment> segments` — pure geometry (start/end points, layer hint).
- `std::map<int, SegEndpoints> seg_busterms` — the **authoritative** record of
  which segment endpoint lands on which block face (`nullopt` = "internal
  junction, not a block face"). Populated at generation time; persisted logically
  to the BDB (`topology_seg_busterm`); the geometric fallback that used to guess
  busterms from `fp` geometry was **removed** (Phase 2 of
  `single_source_topo_truth.md`).

What a `Topology` does **not** store: an explicit **seg-to-seg join graph**.
Topo-gen knows it by construction — a `TRUNK_H` spine is split at each tap, an
MST edge joins two named blocks, `complete_relay_junctions` wires two stubs with
a jog — but that knowledge is discarded. Only the geometry survives.

`ConnTopology::infer_connections` (`src/conn_topology.cpp:131-164`) then
**re-derives** the join graph geometrically: for each segment endpoint `P`, it
scans the perpendicular segments and asks *"does `P` lie exactly on segment j's
nominal line?"* —

```cpp
bool on_j = ci.horiz
    ? (P.x == cj.perp_pos && in_range(P.y, cj.along_lo, cj.along_hi))
    : (P.y == cj.perp_pos && in_range(P.x, cj.along_lo, cj.along_hi));
...
c.is_endpoint = (at_i == ci.along_lo || at_i == ci.along_hi);   // exact ==
```

This is deterministic (integer, exact-equality on nominal coords), so it is *not*
itself a source of the cross-CPU divergence — but it is a **second connectivity
oracle**, exactly the kind of geometric re-derivation the busterm effort set out
to eliminate. It is asymmetric: busterm taps are first-class, seg-to-seg joins
are rebuilt from coordinates.

Downstream, **both** NUTS and DetailedNUTS consume `ConnTopology`'s joins (they do
**not** infer connectivity from physical overlap — the overlap machinery in
`nuts.cpp` is only for congestion/shorts between *different* bundles):

- NUTS: `build_nuts_maps` builds `rev_conn_map` from `cs.conns`
  (`src/nuts.cpp:116-133`); `do_span_adjustments` (`:301-374`) uses it.
- DetailedNUTS: `BusSegment.connections` is populated "from ConnTopology — the
  repo's canonical" adjacency (`src/buda_cli.py:450, 2545-2558`).
- Verify: `check_nuts` / `check_dnuts` test whether the logically-joined segments
  physically reach each other (`src/verify.cpp:303-345`).

## The two gaps

### Gap 1 — seg-to-seg joins are re-inferred, not carried

The join graph is reconstructed geometrically rather than recorded by the
generator that already knows it. This keeps two connectivity oracles alive (the
generator's intent vs. the geometric inference) and relies on exact-equality
endpoint tests over nominal coordinates.

### Gap 2 — NUTS maintains joins *softly*, not as a constraint

This is the load-bearing gap. NUTS places segments by **overlap-avoidance
packing** (each segment is a rectangle to fit into a track), and only *afterward*
tries to keep joined segments touching by **extending spans** to follow each
other's placed track (`do_span_adjustments`, incl. the "coverage guarantee" at
`nuts.cpp:356-374`). A join is therefore never a hard constraint on *where a
segment is placed* — it is a repair applied to the *result*. When placement moves
one side far enough, or a later pass (`tighten_pulls`, `repair_overlaps`,
`resolve_corner_overlaps`) re-touches a span after the coverage extension, the
repair can fail and the join opens.

## Evidence — `tc3a_flat` bundle 48

Bundle 48 is a V trunk (`seg0`) tapped by two H segments (`seg1`, `seg2`) and one
V segment (`seg4`), forming a chain of corners. On the **reference host** it
routes clean, but only at **zero margin**:

```
seg0 V L5 track_pos=9965.0  span(Y)=[2869.5, 7990.0]   ← span_lo = 2869.5
seg1 H L4 track_pos(Y)=2869.5                            ← its track = 2869.5
seg2 H L6 track_pos(Y)=3063.0
```

`seg1.track_pos (2869.5)` **exactly equals** `seg0.span_lo (2869.5)` — the corner
holds only because span-adjustment pulled seg0's end down to precisely meet seg1.

On another host (a Codex CI run) the same solve produced:

```
seg0 span(Y)=[3063, 7990]      seg1 track_pos(Y)=2622.5   →  2622.5 ∉ [3063,7990]  (open)
```

Two facts combine: (a) `seg1`'s H-track placement is **CPU-dependent** —
`2869.5` vs `2622.5`, both legal in its interval `[2310,3480]` — because NUTS
track selection has untoleranced FP tie-breaks (`preferred_fit`'s
`dist < best_dist` over an unsorted candidate list, `nuts.cpp:~1240`; the
`stable_sort`s on FP `span_lo` / window-width keys at `:1611` / `:1599`), and
under `-O3 -march=native` a vector-reduction rounding difference flips the near
tie; (b) when `seg1` drops to 2622.5, `seg0`'s span follows the *endpoint* tap
(`seg2` at 3063) and the coverage guarantee does not re-extend it down to the
interior tap (`seg1`), so the corner opens. **DetailedNUTS** then re-snaps the
bits to concrete signal tracks and re-derives per-bit reach, reconnecting them —
which is why the open never reaches the final layout and only `check_connectivity
nuts` sees it.

Key insight: **if the seg0↔seg1 join were a hard co-placement constraint, (b)
could not happen** — seg0 would be *required* to reach seg1 wherever seg1 landed,
so seg1's CPU-dependent flip would be harmless. Hard co-placement fixes the
*consequence* of the FP non-determinism without having to make every FP
comparison bit-reproducible.

## Proposal

### Part A — make seg-to-seg joins first-class (finish single-source)

Record the join graph at generation time, the symmetric completion of the
`seg_busterms` work:

- Add an explicit join record to `Topology`, e.g.
  `std::vector<SegJoin> seg_joins;` where
  `SegJoin { int seg_a; Endpoint end_a; int seg_b; bool b_is_endpoint; }` — "endpoint
  `end_a` of `seg_a` lands on `seg_b` (at `seg_b`'s endpoint or interior)." The
  generator emits one per junction it *builds* (trunk split, MST edge,
  relay-junction jog, dogleg split), where the intent is unambiguous.
- `ConnTopology::infer_connections` consumes `seg_joins` for the SEG kind exactly
  as it now consumes `seg_busterms` for the BUSTERM kind, and the geometric
  T-junction scan (`conn_topology.cpp:131-164`) is retired to a *fallback for
  unannotated / hand-built topologies* — mirroring how the busterm fallback was
  handled (kept only as an explicit `annotate_topology`-style tool, never the
  silent default). A missing join then means "no join," not "go scan geometry."
- Persist logically alongside `topology_seg_busterm` (a `topology_seg_join`
  table), so a reloaded topology restores the graph without re-derivation — the
  same guarantee Phase 3 gives busterms.

Benefit on its own: removes the second connectivity oracle and the exact-equality
nominal-coordinate matching; a coincidental T-touch can no longer invent (or a
margin/rounding gap drop) a join.

### Part B — hard co-placement in NUTS (the load-bearing change)

Treat a `SegJoin` as a **placement constraint**, not a post-hoc span repair:

- When two segments join at an endpoint, their shared coordinate is **one degree
  of freedom**, not two. Concretely: the perpendicular position of the trunk and
  the along-endpoint of the tap are the same point; co-place them so the tap's
  track *is* on the trunk's span by construction, and the trunk's span is
  *defined to include* every tap it carries (not extended to chase them).
- Mechanically this can be staged into the existing solver: after placing a
  trunk, its taps' endpoint coordinate is **pinned** to the trunk's track (within
  the tap's own interval); a tap that cannot honor the pin is a genuine
  overflow/DNUTS signal, surfaced at NUTS time rather than silently opened. The
  span "coverage guarantee" loop (`nuts.cpp:356-374`) becomes an **invariant the
  placement maintains**, not a repair it applies afterward — and cannot be undone
  by a later `tighten_pulls` / `repair_overlaps` pass, because those passes would
  see the join as a constraint too.
- `resolve_corner_overlaps` / `do_span_adjustments` are refactored to *read* the
  join graph rather than reconstruct lo/hi endpoint roles from nominal geometry
  (`sc.lo_end` is currently keyed on nominal `at_pos <= mid`,
  `nuts.cpp:132`) — the stale-label problem the coverage guarantee exists to
  paper over goes away when the role is authoritative.

### Relationship to the FP-determinism work

These are complementary, not alternatives:

- The **FP-determinism** fix (tolerance + deterministic tie-breaks in
  `preferred_fit` and the sort comparators) makes the *same* track get chosen on
  every CPU — it addresses the *cause* of divergence but is a game of
  whack-a-mole across every FP comparison, and identical goldens are only as
  durable as the next comparison added.
- **Hard co-placement** makes join breakage *structurally impossible* regardless
  of which track a free segment lands on — it addresses the *consequence*. Even
  if a segment's track still varies across CPUs, the route stays connected.

Recommended order: land a small FP-determinism stopgap first (cheap, unblocks the
brittle tests — see the relaxed baselines already in `test_planner_signal_tracks`
/ `test_ripup_reroute` / `test_tc3a_flat_no_perp_range_inversion`), then pursue
Part A + Part B as the durable fix.

## Roadmap (proposed)

- **Phase 0 — FP-determinism stopgap.** Tolerance + deterministic secondary key
  (`bundle_id`, `seg_idx`) in `preferred_fit` (`nuts.cpp:~1240`) and the two
  `stable_sort` comparators (`:1611`, `:1599`). Validate the mid-tier goldens do
  not shift; if they stabilize across CPUs, the relaxed baselines can be
  re-tightened. *Small, self-contained.*
- **Phase 1 — `seg_joins` on `Topology` (in-memory).** Emit from every generator
  path (audit table like the `seg_busterms` coverage table); `ConnTopology`
  consumes it; geometric T-scan demoted to explicit fallback. Prove
  `ConnTopology`/`check_topo` identical to today on the full flow corpus.
- **Phase 2 — hard co-placement in NUTS.** Pin joined endpoints during placement;
  turn the coverage guarantee into a maintained invariant. Validate: bundle 48
  (and the big2 spread-fit residuals, `wishlist-nuts.md`) show **no abstract-NUTS
  open** regardless of host; DNUTS opens unchanged or better.
- **Phase 3 — persist `seg_joins` logically** (`topology_seg_join`), completing
  the single-source guarantee for reload-and-route.

## Risks / open questions

- **Over-constraining.** A hard pin could make a genuinely-feasible layout look
  infeasible if the tap's interval is tighter than the trunk's chosen track. The
  pin must be *within the tap's interval*; when it cannot be, that is real
  overflow to surface (the planner/ripup already handle overflow) — but the
  interaction with `ripup_reroute` needs care so a pin failure re-routes rather
  than dead-ends.
- **Multi-tap trunks.** A trunk carrying several taps at different perpendicular
  positions defines its span as the union of their pins; ordering/among-tap
  overlap on the trunk layer must still be packed. This is where Part B does real
  work vs. today's extend-and-hope.
- **Scope.** Part B touches the placement core (`nuts.cpp`) — the highest-risk
  area in the repo. It should land behind thorough before/after golden diffs on
  the flow corpus, ideally after Phase 0 has made those goldens CPU-stable so the
  diffs are trustworthy.
- **Generator completeness.** Part A is only as good as its coverage; an emission
  path that forgets to record a join reintroduces the fallback. The
  `seg_busterms` effort hit exactly this (BITRUNK gap, Phase 1 there) — the same
  audit-every-path discipline applies.

## Alternatives considered

- **Only fix FP determinism.** Keeps the soft-repair architecture; the join can
  still open if a *future* placement change (not just a CPU difference) moves a
  segment past the coverage window. Treats the symptom.
- **Smarter geometric inference** (bend-aware seg-to-seg scan). Same objection the
  busterm effort raised against a smarter busterm fallback: it keeps two oracles.
  One oracle, set at generation, is the durable design.
- **Do nothing** (rely on DetailedNUTS to recover). Works today for bundle 48, but
  leaves an abstract stage reporting opens that are actually connected — noise
  that masks real opens, and a latent correctness gap if a future design isn't
  recoverable at the detailed stage.
