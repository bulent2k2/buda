# Single source of topo-truth: `seg_busterms` should drive every stage

## Principle

A BUDA topology candidate must carry **one authoritative description of its
connectivity** — which segment endpoint lands on which block face (a busterm),
and which endpoints are internal bends/junctions. Every downstream stage
(planner, NUTS, detailed-NUTS, ripup-reroute, verify) then reads that one truth
rather than re-deriving connectivity from raw geometry. Re-deriving invites
divergence: two stages can disagree, and a coincidence in the coordinates (a wire
grazing a block corner) can be read as a connection that was never intended.

That authoritative description is **`Topology::seg_busterms`**
(`topology.h`): a `map<seg_index, (optional<Busterm> start, optional<Busterm>
end)>`. `nullopt` at an endpoint means "internal junction, not a block face."
`annotate_endpoints` (`topology.cpp`) populates it at generation time.

## The leak: ConnTopology's geometric fallback

`ConnTopology::infer_connections` (`conn_topology.cpp`) has two paths per
endpoint:

1. **Authoritative** — if `seg_busterms` has an entry for the segment index, use
   it. A `nullopt` endpoint is correctly treated as a SEG junction; a coincidental
   block-face touch there is ignored.
2. **Geometric fallback** — if the segment index has **no** entry, scan
   `fp.get_all_blocks()` and tap any face the endpoint lies on. This path can
   mis-tap a block whose corner grazes an L/Z bend, inventing a feedthru that the
   net never had. (`if (found) continue;` then also suppresses the real SEG
   junction there.)

The fallback **was** the only place connectivity was guessed from geometry — the
one violation of the principle, **removed in Phase 2** (below). Two real bugs
traced to it:

- **Hier per-instance offset** dropped `seg_busterms`, so every offset candidate
  was unannotated → fallback → corner feedthru (fixed by carrying the annotation
  through `offset_topology`; see `hier_offset_feedthru.md`).
- **BITRUNK generation** (`add_multi_trunk_candidates`) annotated only leaf stubs,
  leaving the root spine + branch trunks unannotated → fallback (Phase 1 below).

## Goal

Make `seg_busterms` **complete for every generated candidate**, then retire the
geometric fallback so a missing annotation is treated as "no busterm" (not a cue
to go guessing). After that, the corner-graze class of bug is *structurally*
impossible, not fixed case by case.

## Coverage audit (as of Phase 1)

Every emission path calls `annotate_endpoints` (which default-inserts an entry
for **every** segment, so the shape is fully covered):

| Shape | Annotated? |
|---|---|
| L / Z / U / UU / I_H / I_V (2-pin) | ✅ `topology.cpp` generate_2pin |
| TRUNK_H/V(_OOB) + all npin | ✅ generate_npin |
| MST_HV/VH | ✅ `add_mst_candidates` |
| trunk+MST hybrids | ✅ `add_trunk_mst_candidates` |
| `complete_relay_junctions` connectors | ✅ explicit `nullopt` seeding |
| **BITRUNK_H / HVH / VHV** | ✅ **Phase 1** (was: leaf stubs only) |

## Roadmap

- **Phase 1 — close the BITRUNK gap (done).** `add_multi_trunk_candidates` now
  gives **every** segment a `seg_busterms` entry before the clean-tree gate, so
  ConnTopology uses the authoritative path for all of them. The leaf stubs are
  seeded with their block tap; the root spine + branch trunks are completed as
  **`null`/`null`** (they tap no block — their endpoints are branch↔root /
  stub↔branch wire junctions or free ends, inferred as SEG). It deliberately does
  **not** call `annotate_endpoints` here: that geometric annotator would fill a
  trunk endpoint that coincidentally grazes a neighbour block face, turning a
  junction into a spurious feedthru busterm (a Codex P1 catch — the very bug this
  effort removes). Default candidate set unchanged (legacy BITRUNK_H still
  emitted; annotation is metadata, not geometry). Regressions:
  `test_multi_trunk_units.py::test_bitrunk_candidates_are_fully_annotated` and
  `::test_bitrunk_trunk_endpoint_grazing_a_face_stays_a_junction`.

  Note: `annotate_endpoints` is safe for the other shapes because 2-pin shapes
  pass it only `{src, dst}` and trunk/MST shapes place their spine outside block
  faces; BITRUNK is the case where a trunk endpoint can graze a *terminal*
  sibling's face, so it seeds structurally instead of geometrically.

- **Phase 2 — retire the fallback (done).** The geometric BUSTERM search in
  `ConnTopology::infer_connections` is deleted: `seg_busterms` is now the *only*
  busterm source, and a segment with no entry (or a `nullopt` endpoint) is a wire
  junction that flows to the SEG inference — ConnTopology never re-derives
  connectivity from geometry. `buda.annotate_topology(topo, fp)` (C++
  `annotate_topology`) is the explicit, one-time entry point for annotating a
  hand-built or reloaded topology before `ConnTopology::build`. The tests that
  built segments-only topologies were migrated to annotate explicitly
  (`test_corner_margin.py` via a persisting whole-map assign — the old
  `seg_busterms[i] = …` item-assign never persisted, so those were *silently*
  on the fallback; `test_passthrough_slide.py`, `test_relay_tap_slide.py`,
  `test_span_layer_assignment.py` via `annotate_topology`), and the
  fallback-sanity test now asserts an unannotated topology taps **nothing**.

  Caveat worth knowing: `annotate_topology` is itself *geometric*, so on a
  corner-graze layout it re-taps the grazed block just as the old fallback did.
  It is a re-derivation tool for clean / well-formed topologies, **not** a
  substitute for the generator's graze-safe *structural* seeding (which is what
  `offset_topology` carries). Generation remains the authoritative source; the
  fallback removal just stops ConnTopology from silently guessing when the
  annotation is absent.

- **Phase 3 — BDB reload.** `seg_busterms` is in-memory only (`bdb.cpp`
  `topology_segment` stores geometry; deferral in `wishlist-bdb.md`). Today every
  session regenerates candidates via `generate_candidates`, so the annotation is
  never lost. If a future path reconstructs a `Topology` from persisted rows, give
  it one explicit re-annotation (`annotate_endpoints(topo, fp)` on load) — or
  persist `seg_busterms` — so the fallback stays unnecessary in every path.

## Why not just make the fallback smarter?

A bend-aware fallback (skip a BUSTERM at an endpoint shared with a perpendicular
segment) would patch the corner-graze symptom, but it keeps *two* connectivity
oracles and can still mis-handle a real terminal that coincides with a junction.
One oracle, set at generation, is the durable design.
