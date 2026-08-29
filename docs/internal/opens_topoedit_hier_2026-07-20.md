# Open items — topology editing & hierarchical flows (snapshot 2026-07-20)

A focused snapshot of what remains open in two areas, verified against
`main` on 2026-07-20 (refreshed end-of-day, post the TopoEdit closure
batch #355/#357/#358/#363/#368/#371/#375).
Sources: [`opens.md`](opens.md) (the ranked cross-subsystem view),
[`wishlist/wishlist-topoedit.md`](wishlist/wishlist-topoedit.md),
[`wishlist/wishlist-bundler.md`](wishlist/wishlist-bundler.md),
[`wishlist/wishlist-topo.md`](wishlist/wishlist-topo.md).  When an item lands, mark it ✅
here AND in its wishlist file, per the `opens.md` convention.

## Topology editing (TopoEdit) — ✅ NO TRACKED OPENS

Every item in [`wishlist/wishlist-topoedit.md`](wishlist/wishlist-topoedit.md) is now
RESOLVED; the arc is effectively feature-complete.  What shipped:

- `edit_set_slide` CLI parity, USER-topo persistence in the BDB for hier
  flows (opens item 12), explorer hier frames, the gridded `W` refine
  (snap-to-Hanan with `enter` toggling `[free]`) + its **echo marker**
  (#355), and block/face coordinate references.
- **Op-log provenance in the BDB** (#357): `edit_commit` (CLI + GUI sink)
  stores `user_ops:<bundle_id>:<topo_uid>` → {base uid, applied `edit_*`
  command lines} in BDB `meta`; `load_pipeline` prints a pointer per
  restored USER candidate, `dump_user_ops` prints the replayable ops.
- **Per-instance candidate pools** (#358): expanded instances persist
  their selection plus their USER candidates only — an unpinned instance
  commit survives `load_pipeline expanded`, un-edited instances still
  persist one row.

Bugs found + fixed on top of the arc this session:

- **GUI USER topo dropped at the planner on a flat sidecar re-run**
  (#363): a script `select_topology` used to override the GUI-pinned
  hand-built USER topo; a `user_topo` sidecar entry now wins (gated on
  resolve-by-uid so a stale sidecar can't hijack a script pin).
- **Per-instance busterm links named the reference instance** (#368):
  replicated instances now name their own occurrence.
- **Inline-comment leak into the op-log** (#371): `_edit_record` strips
  the `# comment` before recording.
- **`S` picked the parent container, not the leaf busterm** (#375):
  `_block_at` now prefers the bundle's busterm/leaf block (fixes the
  wrong stub target AND the spurious OTC pass-through rejection).

Nothing tracked remains open here.

## Hierarchical flows

The hier pipeline is largely feature-complete (bundler modes across all
four strategies, bottom-up planning with all 8 orientations,
resume/rehydrate, USER templates replicating to instances).  Genuinely
open:

1. ✅ **Cross-level fan-in grouping** (opens item 8,
   [`wishlist/wishlist-bundler.md`](wishlist/wishlist-bundler.md)) — LANDED.
   CONVERGENT/COMBINED now group cross-level nets by their shared receiver
   set into one fan-in bundle (per-net `net_drivers`/`net_receivers` +
   a persisted `FANIN:root|FROM:leaves` reason); generation roots the tree
   at the shared sink with each deep driver as a per-bit tapered leaf, and
   a resumed session recovers the endpoints from the reason.
   `test_hier_cross_level_fanin.py`.
2. ✅ **Hier `set_max_bundle_bits`** (same item) — LANDED.  The balanced
   split now runs at `run_hier_bundler` on the TEMPLATE bundles, before
   per-instance expansion, so each part is its own template and the split
   propagates identically to every occurrence through the template↔replica
   linkage.  Every HBundle hier field is preserved per part; the AUTO
   (busterm-edge) cap resolves a cell-local leaf name to a congruent
   instance's child footprint; a fan-in part re-scopes its per-net
   `net_drivers`/`net_receivers` + FANIN reason to the leaves its bits
   touch.  See `test_hier_max_bundle_bits.py`.
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
   [`wishlist/wishlist-topo.md`](wishlist/wishlist-topo.md) piece (a).

## Standing big-ticket items (outside these two areas)

- **OA bridge** — gated on the proprietary Si2 OA libraries.
- **True along-flex trunk DOF (Stage C)** — blocked on the upstream
  far-face-traversal WL-inflation investigation.
- **2026-07 audit** — ✅ CLOSED (2026-07-20).  All 59 confirmed
  report-only findings are fixed (second-wave slices + the C9-04/P1-03 and
  C6-09/P7-05 follow-ups); only the two refutations (C4-01, P3-05) stand,
  and those are not bugs.  See [`audit_2026-07.md`](audit_2026-07.md).
