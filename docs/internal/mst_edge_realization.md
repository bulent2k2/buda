# MST edge realization — trunk-tail tightening & per-edge L/Z DOF

Design notes for two coupled improvements to MST-based topology generation
(`src/topology.cpp`, `src/conn_topology.cpp`): (1) tighten the dangling
"trunk tail" left on `TRUNK+MST` hybrids, and (2) give each MST **edge** its own
choice of L/Z realization — resolved judiciously, without the 2ᴺ candidate
explosion. Index: [`wishlist-topo.md`](wishlist-topo.md).

Both are motivated by a measured fact (`tools/along_dof_probe.py`): across the
corpus's **4539 candidate topologies there are ~790 k units of removable dead
wire, concentrated entirely in `TRUNK+MST` / `TRUNK_OOB+MST` hybrids**, while the
**selected** topologies carry **zero**. The hybrids are loose, so the planner
never picks them — the dead wire inflates their wirelength out of contention. The
goal of both parts is to make MST candidates *honest and flexible* so the planner
can select them on their merits.

---

## Current state (as-built)

### MST construction
- `compute_mst` (`conn_topology.cpp:557-590`) — Kruskal over all-pairs
  `manhattan_nearest` (`:544-548`), returns `n-1` `MSTEdge`s sorted by distance.
- `MSTEdge` (`conn_topology.h:114-119`) carries **only** `u,v` (node indices),
  `dist`, and `u_name,v_name`. **No geometry** — no faces, no chosen bend, no
  orientation. All point/face/orientation selection happens later, at realization
  time in `topology.cpp`. *This is why a per-edge L/Z choice is not representable
  today: there is nowhere to record it.*
- Consumers (both from `generate_candidates`, `topology.cpp:2232-2233`):
  - `add_mst_candidates` (`:2275-2412`) — standalone `MST_HV` / `MST_VH`
    (`≥4` blocks); **two** candidates per bundle, one per global orientation.
  - `add_trunk_mst_candidates` (`:2558-2883`) — `TRUNK_H+MST` / `TRUNK_V+MST`,
    one hybrid per qualifying base trunk position (variable count, gated by
    `topology_is_clean_tree`, `:2426`).

### Edge → geometry: a WHOLE-CANDIDATE policy, never per-edge
A diagonal edge (two points differing in x and y) is realized as an L, and the
bend corner is fixed by the candidate's single `strategy`/trunk orientation,
applied uniformly to **every** edge:
- standalone MST diagonal legs (`topology.cpp:2374-2390`): `strategy==0` → H-then-V,
  `strategy==1` → V-then-H, same for all edges.
- `TRUNK+MST` hybrid legs (`realize_edges`, `:2756-2767`): orientation tied to the
  trunk's, not even a free strategy.
- corner-diagonal edges (`corner_diagonal_L`, `:1293-1310`): the two L's selected
  by a whole-candidate `strategy`; its own header says "the congestion planner
  picks whichever fits the rest of the topology" — i.e. picks between whole-
  candidate orientations, **never per edge**.
- **Z / dogleg realizations of an MST edge are never generated.** Z shapes appear
  only inside `complete_relay_junctions` when chaining relay stubs
  (`:1010-1060`), not as an edge alternative.

So alternatives exist only at whole-topology granularity: `MST_HV` vs `MST_VH`
flip *all* edges together. Within one candidate every edge has exactly one fixed
realization (agent-verified B4).

### Trunk-tail overshoot (root cause)
The `TRUNK+MST` hybrid copies the base trunk wholesale (`Topology tree =
trunk_topo;` `:2778`) — inheriting `x_lo/x_hi` set in `add_trunk_h/v` as
`min/max att_x` (`:1430-1433`). It then **drops** the child stubs the MST edges
replace (`:2783-2793`). If a dropped stub was what set the spine's extent, the
spine now dangles out to a now-unconnected landing.

`clip_spine_to_landings` (`:2494-2556`, called at `:2799`) is the fix — it
recomputes `[lo,hi]` from every kept junction **and** perpendicular MST-leg
crossing, extends only to cover straddling pass-through blocks, then clips
(never extends). **But it has three scope gaps:**
1. **Only the completed-tree branch calls it** (`:2775-2812`). The legacy/full-
   hybrid branch (`:2831-2860`) and the fallback pool get **no** re-clip.
2. **Early-returns unless exactly one spine segment** (`n_spine != 1`, `:2503`) —
   feedthru-split trunks are skipped.
3. **Refuses the degenerate single-landing case** (`:2552`
   `if (new_lo >= new_hi) return;`). When a hybrid's spine has only ONE landing
   (all other stubs replaced by MST edges), `lo == hi`, so the clip cannot shorten
   and leaves the full stale extent. This is exactly the `four_blocks` bundle-2
   `TRUNK_H+MST@y125` case: seg0 spans x=[99,151] but connects only at x=99 — a
   52-unit dangling tail. Such a "trunk" with a single tap is really an L/straight
   shape, not a trunk.

Base plain-trunk candidates (`add_trunk_h/v`) are never re-clipped at all; their
`att_x`-driven extent (`:1430-1433`, with the partial pull-back at `:1407-1428`)
is the only tightening they get.

### Planner / ripup hooks (reusable for part 2's feedback)
- `CongestionPlanner` scores each candidate index and keeps the min as
  `selected_topology_index` (`congestion_planner.cpp:599-600`); a pinned bundle
  clamps the loop to one index (`:623-624`).
- `ripup_reroute` (`buda_cli.py:2738+`) already **pins a bundle to an alternate
  candidate index and re-runs** (`_rr_trial`, `:3081-3096`), ranking alternates by
  farness from measured contention (`_rr_candidate_order`, `:3050-3079`), accepting
  only on metric improvement (snapshot/restore).
- `negotiate_congestion` (`:3126+`) re-plans offending bundles unpinned after
  injecting measured band demand.
- `_adopt_doglegs` (`:2258-2299`) **appends a NUTS-produced variant as a new
  candidate at a fresh index** and pins to it, with rollback (`_reset_doglegs`).
  This is the precedent for materializing a runtime variant candidate.

**Assessment:** the selection/swap/rollback plumbing is fully reusable. What is
missing for a per-edge flip is only on the *generation* side: (a) an edge identity
on the emitted segments, and (b) a way to materialize the single-edge-flipped
variant.

---

## Part 1 — trunk-tail tightening

**Fix.** Close the three `clip_spine_to_landings` scope gaps:
1. Call it on the legacy/full-hybrid branch (`:2841`, after
   `complete_relay_junctions`) and consider a final pass over base trunk
   candidates.
2. Handle `n_spine != 1` (feedthru-split) by clipping each spine piece to its own
   landings.
3. Handle the degenerate single-landing case: a spine with one landing is not a
   trunk — either **drop the hybrid** (preferred: it is a malformed `TRUNK+MST`
   that a real L/straight candidate already covers) at the `topology_is_clean_tree`
   gate, or trim the tail to a minimal stub at the landing.

**Risk (measured, real).** Tightening an *unselected* candidate's wirelength makes
it cheaper, which can flip planner selections — the same churn class as the
always-on flexible-span experiment (which moved 15 goldens for no corpus WL gain,
see `wishlist-topo.md`). Because `selDEAD = 0`, there is **no** WL improvement on
currently-selected routes; the value is purely (a) fewer junk candidates
(efficiency) and (b) honest rankings that let good MST candidates compete — which
only pays off together with Part 2.

**Gate.** Any Part-1 change must keep the WL corpus (`tools/wl_corpus.py`)
equal-or-better and the fast+mid tiers green; a flipped selection is acceptable
only if the new route is equal-or-better (no new overlaps/opens/unplaced).

**Prototype result (measured — the "safe" drop is NOT safe).** A prototype that
made `clip_spine_to_landings` return "degenerate" for a single-landing spine and
dropped the hybrid removed **666 k of the 790 k dead wire** (corpus `allDEAD`
790 035 → 124 563, candidate count 4539 → 4308; the residual moved to the
unrelated `BITRUNK_H` family). But it broke two ways:
1. **Too blunt — it strands legitimate coverage.**
   `test_topo_keepout_mst::test_trunk_mst_candidate_generated_for_3_blocks` went to
   **zero** `TRUNK+MST` candidates. For a **3-block** bundle `add_mst_candidates`
   bails (needs ≥4), so the hybrid is the bundle's *only* MST-type coverage — and a
   3-block hybrid legitimately has one spine tap with the other two blocks reached
   by MST edges. A blanket single-landing drop removes that valid candidate. The
   drop must at minimum be gated on `blocks.size() >= 4` (a standalone MST exists),
   and even then the *tail should be trimmed*, not the candidate dropped.
2. **It churns selections.** `mix.buda` re-routed (abstract WL 66 656 → 65 047,
   detailed WL and per-layer split both moved) — dropping a candidate shifts
   candidate indices and the planner's tie-breaks even when the dropped candidate
   was never selected.

Conclusion: there is **no** quick, side-effect-free Part-1 change. Tail tightening
is genuinely behavior-changing (the same churn class as the always-on span flip)
and must be a deliberate, measurement-gated PR that (a) trims rather than drops,
(b) respects the `<4`-block "only MST coverage" rule, and (c) re-baselines the
candidate-count/type goldens it moves after confirming each moved route is
equal-or-better. It should not be attempted as an incidental cleanup.

---

## Part 2 — per-edge MST L/Z DOF (avoiding the 2ᴺ explosion)

**Problem.** N edges × {L-HV, L-VH, +Z variants} = ≥2ᴺ whole-topology
realizations. Enumerating them as candidates explodes; the planner cannot score an
exponential set, and most combinations are junk.

**Design principle — make each edge a per-edge VARIABLE, not a candidate axis.**
Mirror how layer assignment works (each *segment* independently picks a layer),
but for edge orientation. Keep the candidate count **linear** (one MST topology
per bundle, plus the existing global HV/VH pair) and get 2ᴺ expressiveness through
**local per-edge moves** resolved by the planner/ripup, only for edges that
actually contend.

**Three layers, each measurement-gated:**

1. **Edge-identity data model.** Tag each emitted segment with the `MSTEdge` it
   realizes (an `edge_id` on `Segment`, or an `edge → {segment indices}` map on
   `Topology`), and record, per edge, its alternate realization (the other L, or a
   Z at a Hanan line). `MSTEdge` (or a parallel per-edge struct) gains the chosen
   orientation + the alternate's leg geometry. Without this, no per-edge flip is
   representable (the core gap from B4).

2. **Smart generation default.** Instead of a single global `strategy`, pick each
   edge's L independently at emit time by a cheap local heuristic: layer-direction
   preference (put the longer leg on the trunk-preferred layer), nearest-face /
   min-detour, and avoid a leg that crosses a known keepout/pass-through. This
   alone (one candidate, locally-optimized edges) is likely most of the win and
   carries the least risk — it replaces the arbitrary global flip with per-edge
   sense.

3. **Planner / ripup per-edge flip (the "come back later" path).** After layer
   assignment / NUTS measures real congestion, flip an *individual* contended
   edge's realization as a local move and re-score — reusing the existing
   `ripup_reroute` pin-and-rerun loop and the `_adopt_doglegs` append-variant
   precedent. Because only contended edges are revisited, cost stays ~linear in
   the number of overflows, not 2ᴺ. The planner's cost model already scores band
   demand; a per-edge flip is a two-leg cost delta.

**Why this is judicious.** Linear candidates + local moves = 2ᴺ reach at polynomial
cost; the planner only "comes back" for the handful of edges that overflow, which
is exactly the user's ask ("possibly come back from the planner later and choose
the other … the possibilities explode with edge count").

**Staging.** (2) smart generation default is the highest-value / lowest-risk piece
and can ship first on its own (measured against the corpus). (1) edge identity is a
prerequisite for (3). (3) is the largest piece and reuses the most existing
machinery. Each stage gates on the WL corpus + tiers.

---

## Measurement harness (already in place)
- `tools/wl_corpus.py` — per-flow abstract+detailed WL, overlaps, unplaced.
- `tools/along_dof_probe.py` — per-candidate removable dead wire (selected vs
  all-candidate scopes). `allDEAD` is the direct metric for Part-1 progress; a
  Part-2 smart default should also lower per-candidate WL without raising overlaps.
