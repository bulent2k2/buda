# Band-Level Repack for Spread-Fit Overlap Clusters — Plan

Status: **PLANNED** (not started).  Expands the
[`wishlist-nuts.md`](wishlist-nuts.md) item of the same name into an
implementable design.  Prerequisite work — the `LayerSolver` extraction that
makes the dense repack machinery reachable from the repair pass — landed in
the NUTS/DNUTS refactor (PR #205,
[`nuts_dnuts_refactor.md`](nuts_dnuts_refactor.md) Phase D).

## 1. Problem

After the planner's hard-overflow ladder and TOP-layer load balancing, big2
retains **9 abstract NUTS track overlaps** (M4×1, M6×4, M7×3, M2×1).  All 9
are **spread-fit**: the shared Hanan band has room for every contender (sum
of widths ≤ interval), i.e. pure placement clustering, not over-capacity.
They survive because `NUTSEngine::repair_overlaps` (`src/nuts.cpp`) moves
**one victim per overlapping pair** into a gap that victim's own interval
still has free.  When 3+ buses share a band (big2 M7: B79 collides with B65,
B26 *and* B45), no single-victim move separates them — every gap a victim
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

## 4. Gates & validation

This is a **behavioral improvement**, not a byte-identical refactor — the
gate discipline flips accordingly:

1. **Target metric:** `flow/big_data_test/big2/big2.buda` NUTS overlaps
   9 → 0 (or document the irreducible residue per cluster), with **no new
   DNUTS opens** (`num_unplaced` not worse) and `ripup_reroute` still
   converging.  Assert as bounds in a new mid/slow test (the #203 pattern:
   `<= base` everywhere, exact-zero only under `BUDA_REF_HOST`-style gating
   if host-sensitive).
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
