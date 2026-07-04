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

**Second prototype (measured — the ≥4-gated drop STILL regresses).** The refined
prototype gated the degenerate drop on `blocks.size() >= 4` (so the 3-block
coverage is kept) and returned a "degenerate" flag from `clip_spine_to_landings`.
This fixed the stranding — but the corpus still regressed: **`mix.buda` overlaps
1 → 3** (two new NUTS overlaps) even though its abstract WL *dropped* 66 656 →
64 947. Root cause: `mix.buda` runs both `negotiate_congestion` and
`ripup_reroute`, and dropping a candidate perturbs each — but by *different*
mechanisms:
- **`ripup_reroute` is genuinely index-dependent.** It walks the contender's
  candidates by index (`_rr_candidate_order` builds `range(n)`, `_rr_trial` pins
  by `tidx`; `src/buda_cli.py:3050-3084`) and commits the first improving trial,
  so removing/renumbering a candidate changes which trials are reached and in what
  order.
- **`negotiate_congestion` is candidate-*set*-dependent, not index-dependent.** It
  re-plans the offending bundles UNPINNED (`replan_bundle`/`replan_bundle_ripup`;
  `src/buda_cli.py:3127-3133`, `:3232-3246`), so the planner scores ALL remaining
  candidates in one pass; dropping one shrinks that set and can shift the planner's
  selection/tie-break, but there is no per-index trial walk.

Either way the coverage-safe drop is not side-effect-free — the sensitivity is to
the candidate *set/indexing*, not just planner tie-breaks.

**Conclusion: Part 1 is deferred as measured-not-worthwhile for now.** The dead
wire is confined to *never-selected* candidates (`selDEAD = 0`), and every
mechanism that removes it (drop, or trim that lowers the candidate's WL) perturbs
the ripup index walk and the negotiate candidate set into a worse route on
`mix.buda`. The
only genuinely side-effect-free option left is a topology **restructure** — delete
the vestigial single-tap spine segment and re-index (so the candidate stays,
tighter, without changing the candidate *count*) — which is substantially more
work than the payoff (honest WL on candidates that already correctly lose)
justifies, and its value is only realized once Part 2 lets good MST candidates win.
Revisit Part 1 **after** Part 2, if at all, with a restructure (not a drop/trim)
and the same overlap-and-ripup gate. Not pursued further in this pass.

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
   representable (the core gap from B4). **Shipped** as `Segment.edge_id` (PR #168).
   **Persistence prerequisite for step 4:** the BDB `topology_segment` path
   (`TopoSegRow` / `add_topology_segment` / `topology_segments` / `load_pipeline`)
   currently stores only x/y/layer/is_jog, so a candidate checkpointed to a BDB and
   reloaded loses `edge_id` (comes back `-1`). Harmless while the field is inert,
   but the flip needs it on resumed pipelines — so step 4's PR must add the
   `topology_segment.edge_id` column (schema + INSERT/SELECT + `TopoSegRow` +
   binding + `buda_cli` persist/reload) and a resume-then-flip round-trip test,
   regenerating the `*.bdb.sql` fixtures (Codex #168 P2).

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

### Shipped so far
- **Step 1** (`Segment.edge_id` identity) — PR #168.
- **Step 2** (keepout-aware per-edge default, `choose_edge_h_first`) — PR #168.
- **Step 4a** (`flip_mst_edge` primitive) — PR #169. Floorplan-validated: rejects a
  flip whose opposite bend lands inside a block or on a block corner (the
  `corner_diagonal_L` case). Involution; no-op for `edge_id < 0` / non-2-leg edges.
- **Part 1** (trunk-tail tightening) — measured **not worthwhile**, deferred (above).

### Step 4b implementation recipe (the ripup consumer — READY TO EXECUTE)

The hooks (`src/buda_cli.py`, verified): `_ripup_reroute` (loop, ~:3282) tries, per
contender, `_rr_candidate_order` (:3050) alternates via `_rr_trial` (:3081, pins an
index + `_rr_rerun`), keeping the first strict improvement; `_rr_snapshot`/
`_rr_restore` (:2856/:2894) capture **candidate COUNT + plan arrays, not candidate
geometry**, and trim appended candidates on restore (`while len(cands) > ncand`).

Key design decision — **flip in place, don't append a candidate.** An appended
flip-variant would be trimmed by `_rr_restore` (count-based) and its index would be
invalid at commit time (unlike a dogleg, which `_adopt_doglegs` *regenerates* every
re-solve). Since `flip_mst_edge` is an **involution**, the clean move is:

1. **Contended-edge detection.** For a contender whose selected candidate is an
   MST type, map each `overlap_details` entry touching this bundle → its
   `seg_idx` → `candidates[sel].segments[seg_idx].edge_id` (keep `>= 0`, dedup).
2. **Trial (per contended edge):** `flip_mst_edge(cands[sel], eid, h, v, fp)` in
   place → `annotate_topology(cands[sel], fp)` → **`_rr_trial(w, sel, stage,
   metric)`** → read the returned metric. Use `_rr_trial(w, sel, …)`, NOT a bare
   `_rr_rerun`: in the usual post-`run_planner` state the wrapper is *not*
   `topology_pinned`, and `CongestionPlanner::replan_bundle` → `plan_bundle`
   restricts the scored candidate range only when `topology_pinned` is true — so a
   bare rerun would let the planner re-score ALL candidates and a "flip improvement"
   could actually be a re-selection of a *different* topology (Codex #170 P2).
   `_rr_trial` pins `sel` (`topology_pinned=True`, `selected_topology_index=sel`)
   before the rerun, so the metric measures the flipped `sel` specifically. Because
   `_rr_snapshot` does **not** capture candidate geometry, a rejected flip is undone
   by flipping the SAME edge again (involution) + re-annotate; `_rr_restore(snap)`
   then restores selection/pin/plan arrays around it.
3. **Accept** the first flip with a strict metric improvement (keep it flipped in
   place — that IS the committed better route); the outer loop's next iteration
   then sees the flipped candidate as the new baseline. Fold this into the existing
   per-contender scan as an extra move source (try index alternates AND edge flips;
   commit whichever wins), so the loop's snapshot/commit structure is reused. A
   committed flip needs no index change — the commit is just `_rr_trial(w, sel, …)`
   with the geometry already flipped (re-pinning the same `sel`).
4. **Persistence (Codex #168 P2):** add the `topology_segment.edge_id` column
   (schema + INSERT/SELECT + `TopoSegRow` + binding + `buda_cli` persist/reload) so
   a resumed pipeline can still flip; regenerate the `*.bdb.sql` fixtures; add a
   resume-then-flip round-trip test.
5. **Measure** (the gate): `mix.buda` + big2 ripup — overlaps must be equal-or-
   **better** (this is where step 3 regressed 1→3, so watch it), no new opens,
   ripup runtime not blown up (each flip trial is ~one NUTS solve). Fast+mid green;
   a targeted test where an edge flip demonstrably clears an overlap.

This is a behavior-changing change to the ripup engine (the codebase's most
delicate module) and must be built + measured as its own focused PR, not rushed.

---

## Measurement harness (already in place)
- `tools/wl_corpus.py` — per-flow abstract+detailed WL, overlaps, unplaced.
- `tools/along_dof_probe.py` — per-candidate removable dead wire (selected vs
  all-candidate scopes). `allDEAD` is the direct metric for Part-1 progress; a
  Part-2 smart default should also lower per-candidate WL without raising overlaps.
