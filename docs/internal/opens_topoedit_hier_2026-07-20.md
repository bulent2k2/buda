# Open items — topology editing & hierarchical flows (snapshot 2026-07-20)

A focused snapshot of what remains open in two areas, verified against
`main` on 2026-07-20 (post PR #344, the kSegsRel compiled-default flip).
Sources: [`opens.md`](opens.md) (the ranked cross-subsystem view),
[`wishlist-topoedit.md`](wishlist-topoedit.md),
[`wishlist-bundler.md`](wishlist-bundler.md),
[`wishlist-topo.md`](wishlist-topo.md).  When an item lands, mark it ✅
here AND in its wishlist file, per the `opens.md` convention.

## Topology editing (TopoEdit)

The big items of the TopoEdit arc are all closed — `edit_set_slide` CLI
parity, USER-topo persistence in the BDB for hier flows (opens item 12),
explorer hier frames, the gridded `W` refine (snap-to-Hanan with `enter`
toggling `[free]`), and block/face coordinate references.  Remaining,
deliberately deferred:

1. **Op-log provenance in the BDB** (~a day, mostly tests).  A committed
   USER candidate persists as geometry only; the sidecar's `user_topo`
   op-log (base uid + `edit_*` sequence — the human-readable design
   intent) isn't mirrored into BDB `meta`.  Restore works perfectly
   without it, so this is forensics/documentation value: auditing a
   checkpoint months later, or replaying ops against a *changed*
   floorplan where raw geometry can't adapt.  No schema bump needed if
   it rides the `meta` key-value table.
2. **Per-instance candidate pools.**  Post-expansion, an instance
   wrapper persists only its *selected* topology; an unpinned
   `edit_commit` on an expanded instance is session-only (loudly noted).
   Recommendation stands: don't build until a real workflow demands it —
   it would multiply topology rows by instance count and blur
   template-vs-instance provenance.
3. **`W` echo marker** — ✅ RESOLVED (this snapshot's companion PR).  The
   last residue of the input-precision item: a transient marker at the
   first captured slide bound so the user sees where it landed before
   committing the second.  Variants 1 (snap-to-grid) and 3 (precise
   entry via `edit_set_slide`) had already shipped; the wishlist file's
   input-precision entry is refreshed accordingly.

## Hierarchical flows

The hier pipeline is largely feature-complete (bundler modes across all
four strategies, bottom-up planning with all 8 orientations,
resume/rehydrate, USER templates replicating to instances).  Genuinely
open:

1. **Cross-level fan-in grouping** (opens item 8,
   [`wishlist-bundler.md`](wishlist-bundler.md)).  Cross-level nets
   still keep STRICT/BIDIRECTIONAL grouping because their single
   `drv_spec_path` metadata cannot describe a multi-driver group — needs
   a per-net endpoint record like the same-level `net_drivers` /
   `net_receivers`.  Fails conservative (never silent) today.
2. **Hier `set_max_bundle_bits`** (same item).  The balanced split pass
   is flat-only; a hier version must propagate splits through the
   template↔replica linkage so every instance splits identically.  Also
   fails LOUD today.
3. **Re-planning a resumed post-expansion session** is unsupported —
   `run_planner hier` on a `load_pipeline expanded` checkpoint would
   double-expand.  Pre-existing, noted in the resume work; no workflow
   has needed it yet.
4. **Min-stub lengths on depth-projection / cross-level frames** —
   deliberately NOT retrofitted during the dogleg-template work (it
   measurably regressed tuned hier flows); parked pending a golden
   review.
5. **The rnr/mix hanan-loci regression** — the `hanan_loci` default flip
   regressed mix's healed endpoint (0/0 → 0 ov / 42 unplaced); the flow
   is pinned out with `no_hanan_loci` by owner decision, and the
   mix–loci root cause is an open follow-on in
   [`wishlist-topo.md`](wishlist-topo.md) piece (a).

## Standing big-ticket items (outside these two areas)

- **OA bridge** — gated on the proprietary Si2 OA libraries.
- **True along-flex trunk DOF (Stage C)** — blocked on the upstream
  far-face-traversal WL-inflation investigation.
- **2026-07 audit report-only findings** — 59 confirmed defects awaiting
  fixes ([`audit_2026-07.md`](audit_2026-07.md)).
