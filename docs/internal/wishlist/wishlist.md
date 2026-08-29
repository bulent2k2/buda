# Wishlist / deferred follow-ups — index

Tracked-but-not-yet-done items, organized by subsystem. Each entry records: what,
why deferred, and where to start. This page is the **index**; the entries live in
per-subsystem files so each area stays scannable. For the **cross-subsystem
priority view** — what's actually open right now, ranked by value/effort — see
[`opens.md`](../opens.md).

## History

- **2026-08-28** — Moved the wishlist set into its own folder
  (`docs/internal/wishlist/`) and added [`wishlist-ux.md`](wishlist-ux.md),
  the first entry about DRIVING BUDA rather than about what it computes.
  Filenames were kept, so every prose mention of `wishlist-topo.md` and its
  siblings stays true; a link guard (`test_doc_links.py`) now walks every
  markdown link in the repo, which is what makes a move of this size
  verifiable rather than hopeful.
- **2026-07-20** — Added
  [`opens_topoedit_hier_2026-07-20.md`](../opens_topoedit_hier_2026-07-20.md):
  a focused snapshot of what remains open in topology editing and the
  hierarchical flows (verified against main post-#344), incl. the
  TopoEdit follow-on ranking and the hier bundler corners.
- **2026-07-14** — Added [`wishlist-topoedit.md`](wishlist-topoedit.md) (topology
  editor): 'W' slide-refine input-precision options (snap-to-grid variant, echo
  marker at the marked bound, precise text entry) deferred until the raw
  cursor-float capture proves too loose in practice, plus `edit_set_slide` CLI
  parity.
- **2026-07-13** — Added the **non-TOP pin-access stub span-stretched onto its
  endpoint leaf** open item ([`wishlist-nuts.md`](wishlist-nuts.md) +
  [`opens.md`](../opens.md) #4 + `../future/nuts_packing_gaps.md` §4): flow 10's
  host-sensitive `x_t*` DNUTS opens, diagnosed as a NUTS span-stretch (not a
  planner) fix. Refreshed `opens.md` verified date to 2026-07-13.
- **2026-07-09** — Added [`opens.md`](../opens.md), the ranked open-items snapshot
  (verified against main: corner-touch + partial-overlap topologies, abutment
  Gap A, and `kHeight` all landed; refreshed the stale BDB/topo index lines here).
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
  planner output (✅), abstract-NUTS bus-segments + bus-vias (✅), detailed-NUTS
  net-segments + per-bit vias (✅), route-snapshot hash (✅), resume/rehydrate
  (✅); GDS round-trip ✅ — only the **OA bridge** remains (gated on the
  proprietary Si2 OA libraries).
- **[wishlist-topo.md](wishlist-topo.md)** — Topology generation & connectivity.
  True along-flex trunk DOF (Stage C of the flexible-root re-arch); incremental
  re-analysis (topo/conn unification Phase D, deferred by measurement); unify
  the 2-pin vs n-pin filter ordering (changes routing bytes — needs its own
  corpus review); resolve pre-planner hier slide columns (`mslide` / `wl[lo..hi]`)
  against the cell-local floorplan so a template dump shows finite slides without
  planning first (PR #215 made the sentinel honest via a `free` display);
  corner-margin default `dx=dy=0` (MEASURED — keep 0; the corner-touch
  generation gap is ✅ resolved via `CORNER_HV`/`CORNER_VH` diagonal L's).
  See [topo_conn_unification.md](../topo_conn_unification.md).
- **[wishlist-topoedit.md](wishlist-topoedit.md)** — Topology editor (TopoEdit).
  Slide-window refine ('W') input precision — snap-to-grid variant, echo
  marker at the marked bound, precise text entry — if the raw cursor-float
  capture proves too loose in practice; CLI parity for the slide refine
  (`edit_set_slide`, mirroring `edit_set_span`).
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
  resolved, PR #69); corner/repair at-scale residual after the 2026-07
  runtime arc (open, three items: span-indexed repack occupancy,
  marginal-yield round stop, over-capacity classification — the
  responsibility-boundary one); pairwise-overlap stub alignment (heal ✅
  shipped opt-in as `set_pair_align_heal`, PR #557 — the unconditional form
  measured 0 better / 7 worse, the accept makes it 0/0/37; the DEFAULT-FLIP
  is measured and REFUSED 2026-08-02 on the absence of benefit — 1 of 37 flows
  accepts at −0.02% WL; the cost is small (one solve on each of 31 eligible
  flows, 0.05–2.1% of wall) but buys nothing, and the "cheap pre-check"
  prerequisite is refuted — it does not discriminate and costs 2.5–48× the
  solve it would skip).
- **[wishlist-bundler.md](wishlist-bundler.md)** — Bundler.
  Multi-source (fan-in) topology to make CONVERGENT bundling sound.
- **[wishlist-ux.md](wishlist-ux.md)** — UX: the web client and the command
  surface.  How a person DRIVES BUDA rather than what it computes — a
  palette/marker legend derived from the live CSS (never a fourth hand-kept
  copy of the palette), click-to-focus a bundle in the nuts/dnuts views (the
  state, the bar and the stepper all exist; only the pointer path is
  missing), and `check_design` violations as clickable markers (medium: the
  design-stage audit reaches the browser as TEXT, so the server half — typed
  violations as data — has to come first).  The per-user rc file and
  user-remappable key-bindings are POINTED AT, not restated:
  [`opens_ux.md`](../opens_ux.md) carries them.
- **[track_density_doubling.md](../track_density_doubling.md)** — the `_2x` clone
  vehicles: doubling a flow's track density as an ADDED twin rather than an
  in-place edit, so the original keeps the congestion its healer and
  doomed-seat coverage depends on.
- **[wishlist-healer.md](wishlist-healer.md)** — Healers (rip-up, negotiate,
  refine).  Rounds 1–5 all landed (incremental trials → negotiation →
  global-occupant pass → fast trials + place-abort → fixed-context screen →
  batched screen; warm-start re-solve shipped as a measured opt-in): bigHalf's
  clean 0/0 endpoint ~49s → ~12.4s.  Remaining levers are trigger-gated
  (fixed-bits DNUTS, stage-b opens-proxy, warm-default flip) with the bars
  recorded — plus the CAPABILITY gap of class-level TRACK negotiation (the
  #536 b61 residual).

## Cross-cutting (no subsystem file)

- **Bound-container element views (the C7-04 hazard class)** — pybind's stl
  caster propagates `def_readwrite`'s `reference_internal` policy to
  container ELEMENTS, so registered-class members like `Topology.segments`
  and `seg_busterms` hand out non-owning views: held across a reassignment
  they dangle, and their stale instance-registry entries can alias later
  casts at recycled heap addresses. `BundleInput.candidates` and
  `NUTSResult.dogleg_topologies` were converted to by-value getters after
  an actual intermittent use-after-free segfault
  ([audit_2026-07.md](../audit_2026-07.md), C7-04); sweeping the remaining
  container members is a binding-breaking change to do deliberately, with
  a perf pass on the hot accessors.
- **2026-07 audit report-only findings** — the deferred adversarial pass
  (2026-07-19) CONFIRMED 59 of the 60 tabled leads (27 silent-corruption,
  4 crash, 12 wrong-but-loud, 4 leak-perf, 12 cosmetic; P3-05 refuted) —
  see [audit_2026-07.md](../audit_2026-07.md). These are verified defects
  awaiting fixes, executed repros archived per finding.

## Conventions

- A ✅-marked entry is kept for the record (resolution notes, or an implemented
  design worth referencing) rather than deleted.
- New items go in the matching subsystem file; add a new `wishlist-<area>.md` and
  link it here when an item doesn't fit an existing area, and note the change in
  the History section above.
