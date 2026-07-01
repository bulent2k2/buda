# Wishlist / deferred follow-ups — index

Tracked-but-not-yet-done items, organized by subsystem. Each entry records: what,
why deferred, and where to start. This page is the **index**; the entries live in
per-subsystem files so each area stays scannable.

## History

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
  abstract NUTS bus-segments + bus-vias (next; feeds OA/GDS).
- **[wishlist-topo.md](wishlist-topo.md)** — Topology generation & connectivity.
  True along-flex trunk DOF (Stage C of the flexible-root re-arch).
- **[wishlist-planner.md](wishlist-planner.md)** — Congestion planner.
  Coverage gate (defense-in-depth); signal-track band capacity (Gap A part 2, ✅
  implemented); layer-assignment "instability" (✅ resolved, not a bug).
- **[wishlist-nuts.md](wishlist-nuts.md)** — Abstract & Detailed NUTS.
  Band-level repack for spread-fit overlap clusters; pull-repack test failure (✅
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
