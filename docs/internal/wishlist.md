# Wishlist / deferred follow-ups — index

Tracked-but-not-yet-done items, organized by subsystem. Each entry records: what,
why deferred, and where to start. This page is the **index**; the entries live in
per-subsystem files so each area stays scannable.

## History

- **2026-07-02** — Planner "coverage gate" resolved: superseded by a uniform
  generation-time gate (`TopologyGenerator::filter_uncovered` runs verify's
  `check_topo` on every candidate; drops `BUSTERM_OPEN` only, never-strand
  fallback) so `run_planner` stays focused on capacity/congestion.
- **2026-07-01** — Split the single flat `wishlist.md` into per-subsystem files
  (`wishlist-bdb`, `-topo`, `-planner`, `-nuts`, `-bundler`, `-ripup`); this page
  became the index. Added the BDB serialization / write-back items (`open_bdb`
  `*.sql` read-only → future write-back mode, schema versioning, provenance,
  routing write-back).
- **earlier** — A single flat list of deferred follow-ups accumulated across the
  topology / planner / NUTS / rip-up work (coverage gate, along-flex trunk DOF,
  signal-track band capacity, spread-fit repack, CONVERGENT fan-in, and the
  resolved layer-assignment-instability and pull-repack investigations).

## Subsystems

- **[wishlist-bdb.md](wishlist-bdb.md)** — BDB, test data & interchange.
  `open_bdb *.sql` write-back mode (✅), schema versioning (✅), provenance
  metadata (✅); persist the pipeline into the BDB — bundles (✅), topologies (✅),
  abstract-NUTS bus-segments + bus-vias (✅); detailed-NUTS net-segments,
  route-snapshot hash, and BDB→OA/GDS export remain.
- **[wishlist-topo.md](wishlist-topo.md)** — Topology generation & connectivity.
  True along-flex trunk DOF (Stage C of the flexible-root re-arch); incremental
  re-analysis (topo/conn unification Phase D, deferred by measurement); unify
  the 2-pin vs n-pin filter ordering (changes routing bytes — needs its own
  corpus review); resolve pre-planner hier slide columns (`mslide` / `wl[lo..hi]`)
  against the cell-local floorplan so a template dump shows finite slides without
  planning first (PR #215 made the sentinel honest via a `free` display). See
  [topo_conn_unification.md](topo_conn_unification.md).
- **[wishlist-planner.md](wishlist-planner.md)** — Congestion planner.
  Coverage gate (✅ resolved — superseded by the generation-time
  `filter_uncovered` gate); signal-track band capacity (Gap A part 2, ✅
  implemented); layer-assignment "instability" (✅ resolved, not a bug);
  selection basis — rank on measured routability, not the generation-time WL
  estimate (deferred; the planner-side of the BITRUNK/datapath-tree under-selection).
- **[wishlist-nuts.md](wishlist-nuts.md)** — Abstract & Detailed NUTS.
  Band-level repack for spread-fit overlap clusters (✅ implemented —
  `nuts_band_repack.md`); PlacedSegmentBase + first-class pre-routes (✅
  implemented — `placed_segment_preroutes.md`); pull-repack test failure (✅
  resolved, PR #69).
- **[wishlist-bundler.md](wishlist-bundler.md)** — Bundler.
  Multi-source (fan-in) topology to make CONVERGENT bundling sound.
- **[wishlist-ripup.md](wishlist-ripup.md)** — Rip-up & re-route.
  `ripup_reroute` v1 follow-ups (C++ band-injection; candidate-filter speedup;
  synthetic stage-b fixture; hier-mode ✅ resolved).

## Conventions

- A ✅-marked entry is kept for the record (resolution notes, or an implemented
  design worth referencing) rather than deleted.
- New items go in the matching subsystem file; add a new `wishlist-<area>.md` and
  link it here when an item doesn't fit an existing area, and note the change in
  the History section above.
