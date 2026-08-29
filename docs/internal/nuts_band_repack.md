# Band-Level Repack for Spread-Fit Overlap Clusters — Plan

Status: **IMPLEMENTED** (this branch/PR; see §6 for the as-built
resolution and measurements).  Expands the
[`wishlist/wishlist-nuts.md`](wishlist/wishlist-nuts.md) item of the same name into an
implementable design.  Prerequisite work — the `LayerSolver` extraction that
makes the dense repack machinery reachable from the repair pass — landed in
the NUTS/DNUTS refactor (PR #205,
[`nuts_dnuts_refactor.md`](nuts_dnuts_refactor.md) Phase D).

## 1. Problem

**Baseline update (2026-07):** `big2.buda` moved from the plain pipeline to
`run_planner signal_tracks` + `negotiate_congestion` + `ripup_reroute`,
which clears every NUTS overlap on the full flow (the remaining DNUTS opens
are a different, planner-side class — see the "LOW-layer abutment
crossings" item in [`wishlist/wishlist-planner.md`](wishlist/wishlist-planner.md)).  The
band-repack target is therefore the **pre-negotiation residue**: the same
flow with the negotiation/ripup stages skipped leaves **8 overlaps in 3
spread-fit clusters** (x86-64 measurement: sizes 3 + 2 on M6, 6 on M7 —
the L7 cluster is B26/B45/B49/B79 + both segments of B22).  The value
proposition sharpens accordingly: the cluster repack clears this residue
deterministically *inside* `repair_overlaps`, so the expensive
measured-congestion negotiation and ripup grind are only needed for what a
correct packer genuinely cannot place.  All clusters below are
**spread-fit**: the shared Hanan band has room for every contender (sum
of widths ≤ interval), i.e. pure placement clustering, not over-capacity.
They survive because `NUTSEngine::repair_overlaps` (`src/nuts.cpp`) moves
**one victim per overlapping pair** into a gap that victim's own interval
still has free.  When 3+ buses share a band (big2 M7: B79's trunk collides
with B22, B26, B45 and B49), no single-victim move separates them — every gap a victim
could take is blocked by another cluster member, and the strict-improvement
guard (correctly) rejects each attempted move.  A plateau-move relaxation was
tried and measured useless: the victims' intervals are already full *given
the others' positions*.  The fix must move the cluster **as a set**.

The machinery already exists at placement time: `LayerSolver::try_repack`
(`src/nuts.cpp`) gathers every placed segment contending for a window and
re-places all of them earliest-deadline-first (pull-aware, then dense
low-edge fallback), committing only on full success.  It is unreachable from
`repair_overlaps` today only because it needs `LayerSolver`'s state (the
layer slice, the constrained set, keepouts, engine fits).

## 2. Design

### 2.1 Cluster discovery

In `repair_overlaps`, after the existing single-victim loop makes no further
progress and overlaps remain:

1. Build the **overlap graph** on the residual `find_overlaps(segments)`
   pairs (nodes = segment indices, edges = overlapping pairs).
2. Its connected components, split by layer (edges are same-layer by
   construction), are the candidate clusters.  Keep components of size ≥ 2
   (size-2 components reaching here mean both endpoints were pull-locked or
   mutually wedged — the multi-move can still help by re-seating both).
3. **Spread-fit precheck** per cluster: the union of the members' hard
   intervals, minus keepout extents, must have total free width
   ≥ Σ(width) + (n−1)·track_pitch when stacked — a cheap necessary condition
   that skips genuinely over-capacity clusters (those are the planner's
   problem, not the packer's; ALLOW_OVERFLOW already warned).
   *Measured nuance:* the precheck is deliberately conservative — a cluster
   whose members' spans only TOUCH (closed-span conflict) can exceed the
   union-interval budget yet still admit a span-aware pack (an L6 big2
   cluster measured exactly this way).  v1 skips those; a span-aware
   feasibility check is a possible v2 refinement.

### 2.2 Cluster repack

For each cluster (largest first — big clusters constrain small ones):

1. Instantiate a `LayerSolver` for the cluster's layer exactly as
   `solve_layer` does (same ctx, same `LayerConstraints` — **empty** here:
   the corner pass runs after and re-derives its own; a committed cross-layer
   `track_lo/hi_bound` on a member must be respected, see 2.4).
2. Call a new `LayerSolver::repack_cluster(members)` — a thin public wrapper
   over the existing `try_repack` body with the member set supplied by the
   caller instead of derived from one wedged segment.  Same
   earliest-deadline-first ordering, same pull-aware → dense two-phase pack,
   same all-or-nothing commit.  (The existing `try_repack(ts)` becomes
   `repack_cluster(members_of(ts))` internally — one body, two entry points.)
3. Guard exactly like every other repair move: `PlacementSnapshot` before,
   `settle_spans` after, accept only if the **global** overlap count strictly
   drops and violations do not rise; else restore.  The pass-level
   non-regression snapshot in `repair_overlaps` stays the outer safety net.

### 2.3 Ordering & determinism

- Clusters processed by (size desc, lowest member (bundle_id, seg_idx)) —
  a deterministic total order independent of map iteration.
- Members within a cluster keep `try_repack`'s `stable_sort` by
  (interval_hi, slack) — already FP-hardened by the quantized-key rationale
  (interval bounds are Hanan-derived integers).
- No RNG, no time — the placement goldens (`tools/nuts_snapshot.py`) stay
  the regression oracle, re-baselined once (see §4).

### 2.4 Interactions to respect

- **Pull-locked members** (net_pull ≠ 0): `try_repack`'s pull-aware phase
  already re-seats them at their pull target; the dense fallback bottom-packs
  them only when the window can't honor pulls — acceptable inside a cluster
  that is otherwise unroutable, and the strict-improvement guard rejects the
  result if the placement got worse overall.
- **Phase-0 / corner-constrained trunks**: `try_repack` already excludes
  `constrained` members; a cluster containing one keeps it fixed as an
  obstacle (may make the precheck fail — correct, the corner pass owns it).
  A member carrying committed `track_lo_bound/track_hi_bound` must keep its
  bounded side: clamp its per-member window to the bound inside the pack.
- **rerun_layer**: gets the cluster pass for free (it calls
  `repair_overlaps`); the single-layer contract holds because clusters are
  per-layer by construction.
- **ripup_reroute / negotiate_congestion** run *after* NUTS and read
  `overlap_details`; fewer residual overlaps simply means less work for them.
  No interface change.

## 3. Where to hook (file/function map)

| Piece | Location |
|---|---|
| Cluster discovery + loop | `NUTSEngine::repair_overlaps` (`src/nuts.cpp`), after the per-pair iteration plateaus |
| `repack_cluster` | `LayerSolver` (`src/nuts.cpp`) — public method; `try_repack` delegates to it |
| Spread-fit precheck | small static helper next to the discovery (uses `nuts_geom.h` accessors + `keepout_occupied`) |
| Guard | existing `PlacementSnapshot` + `settle_spans` + `find_overlaps`/`count_violations` |

No header/API change outside `nuts.cpp` internals; no binding change.

## 3.5 Repro (in place — `test/tests/test_big2_residuals.py`)

`test_big2_prenegotiation_spreadfit_residue` (mid tier, ~1s: big2 through
`run_nuts` with negotiation skipped is sub-second; the flow's minutes live
in negotiate/ripup/DNUTS) asserts the residue exists with ≥1 spread-fit
cluster of ≥3 members — the class single-victim repair provably cannot fix.
**When the cluster repack lands, flip this test to assert zero residue.**

Two extraction experiments established that **no smaller repro exists**:
re-running with only the involved buses (full floorplan kept, so the Hanan
grid and planner band structure are identical) packs cleanly both
*unpinned* (the planner spreads 10 bundles onto empty layers) and *pinned*
(full-run candidate + `seg_layers` + `seg_perp` forced onto every wrapper) —
the wedge comes from the other ~70 bundles' occupancy shaping every
`preferred_fit` decision, so the full-design run IS the minimal repro.
This also explains why a hand-crafted synthetic case is hard: placement-time
`try_repack` already resolves simple window fragmentation, so the residue
only materializes from post-placement span adjustments under real occupancy
— and it is why the fix belongs in `repair_overlaps` (which today never
repacks; it only moves single victims).

## 4. Gates & validation

This is a **behavioral improvement**, not a byte-identical refactor — the
gate discipline flips accordingly:

1. **Target metric:** the pre-negotiation residue (8 overlaps / 3 clusters
   on x86-64) → 0, with **no new DNUTS opens** (`num_unplaced` not worse)
   and the full flow (negotiate + ripup) still converging.  The gate test
   already exists — `test_big2_residuals.py::
   test_big2_prenegotiation_spreadfit_residue` — asserting today's failure;
   flip it to assert the clean state (the #203 host-robust pattern).
2. **No-regression corpus:** `tools/wl_corpus.py` A/B — wirelength within
   noise (a cluster repack can move WL slightly; assert overlaps/unplaced
   only, eyeball WL deltas), and the full fast+mid tiers.
3. **Placement goldens:** flows where the cluster pass fires will
   legitimately change → deliberate re-baseline of
   `test/tests/data/nuts_golden/*` with the diff reviewed in the PR (the
   documented `nuts_snapshot.py` workflow).  Flows with zero residual
   overlaps today (most of the corpus) must stay **byte-identical** — the
   pass must be a no-op when `find_overlaps` is empty after the single-victim
   loop.
4. **Unit tests:** a synthetic 3-bus spread-fit cluster (one band, sum of
   widths fits, centre-seeking placement wedges it) that single-victim repair
   provably cannot fix and the cluster pass must; a pull-locked-member
   cluster; an over-capacity cluster the precheck must skip.

## 5. Non-goals

- No planner-side changes: the residual is a packer problem
  ([`planner_low_layer_over_cell.md`](planner_low_layer_over_cell.md)
  measured that "balancing it away" regresses DNUTS).
- No change to the overflow ladder, `ALLOW_OVERFLOW`/`BEST_EFFORT` semantics,
  or `overlap_details` reporting.
- No cross-layer cluster moves (that is `resolve_corner_overlaps`' domain).

## 6. As-built resolution & measurements (implemented)

The landed implementation follows §2 with four deviations, each forced by a
measurement on the way to green gates:

1. **Best-effort pack for the cluster entry.**  The all-or-nothing contract
   was kept for the placement-time `try_repack`, but a repair-time cluster
   pack aborts far too often under it — one wedged neighbour vetoed whole
   clusters.  `repack_cluster` therefore packs best-effort (a member that
   cannot fit keeps its current track and becomes an obstacle), safe because
   every commit is guarded.
2. **Narrow → wide attempt ladder.**  Each cluster is tried members-only
   first (least collateral), then with try_repack's full contention sweep
   (every placed same-layer segment whose interval overlaps the union
   window) — the members' windows can be full *given* the neighbours'
   positions, so the neighbours must move with them.
3. **Guard: no global rise + STRICT in-cluster drop, integrated with the
   single-victim loop.**  Demanding a strict *global* drop per commit
   rejected every big-cluster repack: separating a cluster stretches
   follower spans, surfacing collateral overlaps elsewhere that the
   single-victim sweep can fix — so the cluster round is a fallback stage
   *inside* `repair_overlaps`' iteration (single-victim sweep first; when it
   plateaus, one cluster round; collateral cleaned next iteration), and the
   pass-level non-regression snapshot backstops the total.
4. **Corner constraints honored (PR #210 review).**  When
   `resolve_corner_overlaps` invokes `repair_overlaps` its `by_layer_cons`
   is not yet persisted onto the segments' track bounds, so the cluster
   pass receives the ACTIVE constraint map: constrained phase-0 trunks are
   dropped from the repack set (try_repack's gathering rule — they stay
   obstacles), preventing a commit that violates the derived ordering/split
   and then carries inconsistent bounds into DetailedNUTS.
5. **Charged-band placement preference (the critical one).**  A pull-free
   member's repack preference is its `pull_map` entry — for planner-managed
   segments the CHARGED band centre (`seg_perp`), the band whose
   signal-track supply the planner verified — falling back to its current
   position.  The first (minimal-movement) variant separated more abstract
   overlaps (big2 8 → 3) but drifted members onto bands without detailed
   supply: `rnr/mix` went from 0 to 16 DNUTS-open bits that stage-b ripup
   could not recover.  With the charged-band preference big2 lands at
   8 → 5 and mix keeps **0 opens** — the correct trade under the pipeline's
   lexicographic (opens, overlaps) metric.

**Measured outcomes (x86-64):**

| Flow | Metric | Before | After |
|---|---|---|---|
| big2 pre-negotiation | NUTS overlaps | 8 (3 clusters) | **5** (B79 star + 2 pairs) |
| big2 full flow | NUTS overlaps / DNUTS opens | 0 / 72 | 0 / 72 (unchanged; the 72 opens were resolved separately afterwards — LOW-layer abutment fix, wishlist/wishlist-planner.md → now 0 / 0) |
| big2 full flow | negotiate+ripup runtime | the flow's dominant cost | **~1.4s total** (residue mostly pre-cleared) |
| rnr/mix full flow | DNUTS opens / NUTS overlaps | 0 / 1 | **0** / 3 |
| rest of the golden corpus | placements | — | byte-identical (pass no-ops without residue) |

The B79 star (a 292-wide trunk whose window is genuinely full given
immovable neighbours) is re-pin territory — exactly what the flow's
negotiation clears — and the two remaining pairs are guard-rejected
(separating them costs more elsewhere).  Gate:
`test_big2_residuals.py::test_big2_prenegotiation_spreadfit_residue`
(bounds `<= 6`, tighten toward 0 with future improvements).
