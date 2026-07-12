# Congestion Planner Design (Stage 3)

Stage 3 selects one topology per bundle and assigns a metal layer to each of its segments, minimizing congestion across all bundles simultaneously. **Capacity overflow is a hard constraint**: a plan that overflows a channel band cannot be physically placed by Stage 4 (NUTS) and is never traded against soft costs such as wirelength.

---

## Role in the Pipeline

```
Stage 2 (Topology Generator)
    BundleWrapper.candidates  ← L/Z/U/trunk topology candidates per bundle
          │
          ▼
Stage 3 (Congestion Planner)
    BundleAssignment per bundle:
      selected topology index + per-segment layer IDs
      + per-segment charged-band centres (seg_perp)
          │
          ▼
Stage 4 (Abstract NUTS)
    packs each segment into its Hanan-cell interval on the assigned layer,
    preferring the planner's charged band (seg_perp) for segments free of
    face semantics (no busterm/net_pull bound) — so buses land in the bands
    whose capacity the books actually reserved
```

The planner consults Stage 5 (`LayerStack`) for layer direction/type metadata and the honest per-layer bus width, and the `Floorplan` for blocks, keepouts, and the Hanan grid.

---

## Data Types

### `GlobalCut`

One Hanan-grid cut line subdivided into perpendicular bands (see [congestion_heatmap_logic.md](congestion_heatmap_logic.md) for the full cut/band geometry).

| Field | Description |
|---|---|
| `cut_coord` | `x_mid` of an X-channel (V-cut) or `y_mid` of a Y-channel (H-cut) |
| `dir` | `VERTICAL` cut counts H-segments crossing it; `HORIZONTAL` counts V-segments |
| `layer_id` | One cut exists per (channel, layer) pair |
| `band_cap` | Capacity per perpendicular Hanan band: band length minus blocked portions |
| `band_usage` | Accumulated demand per band from committed plans |

Band capacity subtracts **keepout zones** registered for the layer, and — for non-`TOP` layers only — **block footprints** (`band_available_length`, `congestion_planner.cpp`).

### `BundleWrapper`

| Field | Description |
|---|---|
| `candidates` | Topology candidates from Stage 2 |
| `selected_topology_index`, `topology_pinned` | Architect pin (sidecar `select_topology`): only that candidate is scored |
| `pinned_seg_layers` | Manual per-segment layer overrides (`-1` = planner decides) |
| `width` | Nominal bus width in layout units (processing order key) |
| `priority` | Higher routes first (set by `run_planner hier`) |

### `BundleAssignment` (output)

`bundle_id`, `topo_index`, per-segment `seg_layers`, and representative `v_layer_id`/`h_layer_id` for logging. Applied back onto the wrappers by the CLI.

### `PlanMode` / `PlanResult` (internal)

`plan_bundle(bw, mode)` is a **pure scoring function**: it evaluates every candidate against the current cut state, restores the state, and returns a `PlanResult` (`found`, `best_topo`, `score`, `overflow`, `seg_layers`, `seg_perp`). The caller applies or reverts it with `commit_plan(bw, plan, sign)` (`sign=+1` commits, `−1` rips up). This pure-score/commit split is what makes rip-up & replan possible.

`PlanMode` encodes admissibility, in decreasing strictness:

| Mode | Slide-window gate | Overflow gate |
|---|---|---|
| `STRICT` | yes | yes — overflowing layers/bands are not choices |
| `ALLOW_OVERFLOW` | yes | no — overflow priced softly |
| `BEST_EFFORT` | no | no |

---

## Cost Model

Per segment, for each direction-appropriate layer:

```
cong = kCong · max(0, usage + eff_width − cap) / cap      (0 when the segment fits)
span = kSpan(layer) · excess outside [span_min, span_max]
base = 0 if TOP layer else base_cost_non_top · min(1, seg_span / base_span_ref)
segment score = cong + span + base
```

```
topology score = max over segments (weakest link) + kWL · estimated_wirelength
```

- **Effective width** (`LayerStack::eff_bus_width`): `bits × unit_pitch / n_signal_slots` when the layer has a track pattern (`def_track_pattern`), else `width × dilution_factor`.
- **Slide-aware band lookup** (`best_band_perp`): the congestion charge goes to the cheapest Hanan band within the segment's ConnTopology slide window that can host the bus — not a point estimate at the window centre, which can land in an arbitrarily narrow band the bus would never use. Ties break toward the window centre.
- **Window-clamped capacity** (`usable_band_cap`): a segment confined to a sub-band window is priced against the window's overlap with the band, not the whole band.
- **Keepout rejection**: `cap ≤ 0` (band fully blocked) scores `kCong × 9999`, eliminating the band outright.
- **Endpoint-block face clamping** (`for_each_band`): segments run centre-to-centre, but on block-obstructed (non-`TOP`) layers the portion inside an *endpoint* block is not routed there — the connection lands on the block face. Cut crossings are therefore clamped to the endpoint-block faces on non-TOP layers; without this, every block-attached segment scored `cap=0` on every lower layer and the whole non-TOP stack was unusable for stubs. Blocks merely crossed mid-span still block normally.

### Span-scaled non-TOP penalty

`base_cost_non_top` is scaled by `min(1, seg_span / base_span_ref)`: a segment of `base_span_ref` or longer pays the full penalty, shorter ones proportionally less. Because the congestion term is overflow-based (zero until a band is full), the penalty's real effect is the **drop-to-lower-layer vs detour-on-TOP** tradeoff once TOP bands saturate: a short local stub now offloads to a lower layer (cheap at short span) instead of detouring on TOP — preserving TOP capacity for the long-haul trunks that pay the penalty in full. This is the main mechanism separating local/short bundles from global/long ones on a shared layer stack without per-design `span_min`/`span_max` tuning. Demonstrated by `flow/planner5_span_drop.buda`.

### Tuning knobs (`set_planner_param`)

| Knob | Default | Role under the hard-overflow design |
|---|---|---|
| `kCong` | 1.0 | Arbitrates among *unavoidable* overflows (`ALLOW_OVERFLOW` fallback) and prices residual soft pressure. It no longer decides overflow-vs-detour — that is a hard gate. |
| `kSpan` | 0.001 | Span-mismatch pressure; per-layer `kSpan K` override on `def_layer`. |
| `base_cost_non_top` | 0.5 | Preference for `TOP` layers, scaled by segment span (see above). |
| `base_span_ref` | 25% of the larger Hanan grid extent | Span at which a segment pays the full non-TOP penalty; shorter segments pay proportionally less. |
| `kWL` | 0.001 | Tie-breaker among overflow-free candidates: a detour must buy real relief to be worth its length. |

---

## Why Overflow Is a Hard Constraint

The soft-cost-only planner traded physical infeasibility against wirelength. Motivating failure (`flow/planner4.buda`): a keepout on M6 blocked bundle 3's preferred trunk band, and the two remaining usable bands were held by pinned bundles 1 and 2. The cheapest band left bundle 3 with a **16-unit overflow**, priced at `kCong·16/120 ≈ 0.13` — cheaper than the overflow-free detour's extra `kWL·ΔWL ≈ 0.22`. The planner committed the overflow, and NUTS, which cannot place 136 units of bus into a 120-unit band, emitted a real B2×B3 track overlap.

Two properties of the soft pricing made this systematic:

1. **Normalization by `cap`** means the same absolute overflow costs *less* in a wider band — the opposite of the physics (16 units of bus that don't fit are 16 units of overlap regardless of band height).
2. **Scale mismatch**: `kCong` multiplies a ratio ≤ ~1 while `kWL` multiplies hundreds of layout units, so any detour longer than `(kCong/kWL)·(ov/cap)` units beat avoiding a genuine overflow.

An overflow is not a cost — it is a guarantee of failure downstream. It is therefore gated, not priced.

---

## The Escalation Ladder

Bundles are processed in `priority` order (depth-0 before depth-1), widest-first within a priority. Each bundle walks down the ladder until a plan is found:

### 1. `STRICT` — overflow-free or nothing

Only candidates that are **slide-feasible** (the bus fits each segment's ConnTopology window) **and overflow-free** compete on the soft costs. Inside the per-segment layer loop, any layer whose best band still overflows (`ov > 1e-6`) is skipped; if every layer for some segment overflows, the candidate is infeasible. A pinned segment layer (`pinned_seg_layers`) that overflows makes the candidate infeasible too.

### 2. Rip-up & replan — make room by moving an earlier bundle

When no candidate is overflow-free, the failed STRICT pass also returns the **contended bands** — the (cut, band) pairs whose overflow disqualified candidates. Committed bundles are ranked as victims by the demand they hold on those bands (`plan_band_overlap`), most relief first; **zero-overlap victims are skipped** (ripping them cannot help), and ties break toward the most recently committed. This finds the actual blocker directly — e.g. the one global trunk crossing a cell whose local bundle just failed — instead of walking back through unrelated bundles:

```
contended = bands whose overflow disqualified B's candidates
for each victim P (by overlap with contended, descending; overlap 0 skipped):
    commit_plan(P, plan_P, −1)                 # rip up
    mine = plan_bundle(B, STRICT)
    if mine.found:
        commit_plan(B, mine)
        theirs = plan_bundle(P, STRICT)        # P rescored against B's usage
        if theirs.found:                       # accept: BOTH overflow-free
            commit_plan(P, theirs)
            overwrite P's BundleAssignment in place; log "[replanned]"
            done
        commit_plan(B, mine, −1)               # P can't recover: undo B
    commit_plan(P, plan_P)                     # restore victim exactly
```

Properties:
- The pair is accepted **only if both bundles end up overflow-free**; any failure restores the victim's exact prior plan (commit/rip-up are exact inverses).
- A **pinned** victim keeps its pinned topology (`plan_bundle` honors `topology_pinned`) but may move to different layers or perp bands.
- Single-victim and non-recursive: relief requiring two earlier bundles to move simultaneously is not found (see [future/planner_ripup_extensions.md](future/planner_ripup_extensions.md)).

### 3. `ALLOW_OVERFLOW` — overflow is genuinely unavoidable

The slide-window gate stays on, but overflow reverts to soft pricing and the least-cost candidate is committed with an explicit warning. Demand routinely exceeding capacity is a floorplan/stack problem the user must see, not silently absorb.

### 4. `BEST_EFFORT` — slide windows themselves are too narrow

No candidate fits its slide windows at all (e.g. sidecar pins saved under an older width model). The bundle is committed without gates rather than dropped — committing an empty `seg_layers` used to index out of bounds and crash (`flow/channel_stress.buda` regression).

---

## Hierarchical Planning (run_planner hier)

`run_planner hier` expands cell-level HBundles to per-instance wrappers and feeds them to the same optimizer with `priority = -(level·10000 + n_candidates)` — depth-0 globals route first, constrained bundles first within a level (see [HIER_PLANNER.md](HIER_PLANNER.md)). Two mechanisms manage the resulting local/global competition:

### Cell-interior demand reservation

Globals are planned first by design, but TOP layers ignore block footprints, so global trunks fly over cell interiors — the only place the later-planned, tightly-windowed locals can route. To stop early globals from eating that capacity, each expanded cell-local wrapper carries a **reservation** (`has_reservation` + the parent instance bbox `res_x1..res_y2`, stamped by `_expand_hier_bundles` at expansion time): before planning starts, its effective bus width is parked as *virtual usage* on every TOP-layer band inside the region (`apply_reservation`), and released right before the bundle's own turn.

The recipe — `apply_reservation(bw, sign)`, `sign=+1` parks, `-1` releases the identical charge:

1. No-op unless `bw.hier.has_reservation` (only expanded cell-local wrappers carry one; flat-flow and cross-block bundles never do).
2. Resolve the two TOP layers. The bundle's eventual H segment consumes V-cut capacity on the TOP **H** layer and its V segment H-cut capacity on the TOP **V** layer, so only cuts living on those two layers are touched — LOW layers are never reserved (locals reach them cheaply via the span-scaled non-TOP discount anyway).
3. Keep only cuts whose coordinate lies inside the region along the cut axis (the wire would have to cross that cut to exist inside the cell).
4. Compute the parked width exactly as a real segment would be charged: `eff_bus_width(bits, width, layer) + track_pitch` — pattern-aware per-bit footprint when the layer has a track pattern, plus one pitch of inter-bus spacing (Gap 1).
5. Charge that width on **every band overlapping the region's perpendicular range**. The local bundle will eventually occupy *one* of those bands, but until it plans, any of them could be its home — so each must individually leave room. This deliberate over-counting is what makes reservations *pessimistic*: a region spanning k bands parks k× the real demand.
6. `plan_all` parks all reservations up front (`sign=+1` in processing order) and releases each bundle's own (`sign=-1`) at the top of its turn — from then on its demand is real, not reserved.

Because congestion cost is overflow-based, the virtual usage repels a global from a region band **only when the band cannot hold both** of them — it is a "leave room" constraint, not a keep-out. A global that fits alongside the local still routes straight over the cell. When contention is real, the global detours on its first pass and no rip-up is needed (`flow/hbundles/09_local_global_compete.buda`).

Limitation: a reservation is not a committed plan, so a bundle blocked *only* by reservations cannot rip them up — it falls through the ladder and the conflict resolves when the reserved bundle itself plans (possibly via rip-up then).

### Level ordering: top-down vs deep-first (`BUDA_HIER_DEEP_FIRST`)

The level key of the processing order is a design choice, not a law. Top-down (the default) reasons "widest, longest buses claim TOP first; locals are protected by reservations and offload to LOW cheaply". The opposite reading — deeper cells are smaller and have fewer resources, so most-constrained-first argues they should commit **real** usage before globals plan — is testable: `BUDA_HIER_DEEP_FIRST=1` inverts *only* the level key (deepest level first; fewest-candidates-first within a level preserved; unset is bit-identical to the historical formula).

A/B on the full hier corpus (2026-07-11, `flow/hbundles/01–10` + `flow/rnr/mix2_fast`):

| Flow | Detailed WL (base → deep) | NUTS overlaps | DNUTS unplaced | Verdict |
|---|---|---|---|---|
| 01_pipeline_hier | 3360 → 2640 (−21%) | 0 → 0 | 0 → 0 | win |
| 02_two_procs | 7816 → 5280 (−32%) | 0 → 0 | 0 → 0 | win |
| 03, 04, 08, 09 | identical | identical | identical | neutral |
| 05_stress_grid | 31626 → 26025 (−18%) | 0 → 1 | 47 → 8 | win |
| 06_multipin_stress | 51995 → 53305 (+2.5%) | 2 → 0 | 34 → 26 | win (defects) |
| 07_wide_fan_stress | 39942 → 47980 (+20%) | 1 → 6 | 0 → 9 | regression |
| 10_chip_units_blocks_leaf | 369313 → 360138 (−2.5%) | 1 → 1 | 7 → 17 | regression (defects) |
| mix2_fast | identical | 28 → 28 | 259 → 259 | control (all wrappers locked) |

Reading the wins: reservations over-count (recipe step 5), so top-down globals sometimes detour around phantom congestion — in 01/02 the D0 buses pick 3-segment Z shapes over a straight `I_H` that deep-first proves fits (all four bundles end up as single straight wires). Reading the losses: deep-first has **no symmetric protection for global demand** — locals plan blind to globals and squat on TOP bands (07: D1 takes 48 segments on M6 vs 32, two D0 buses then commit WITH overflow; 10: D0 squeezed onto bands whose real signal-track supply falls short, +10 DNUTS opens). Each order protects one side by priority and the other by an approximation; neither dominates (4 improved / 2 regressed / 4 neutral), so the default stays top-down.

**The synthesis SHIPPED (2026-07-12) as opt-in refinement passes**
(`set_planner_param refine_passes <n>`, default 0 = skipped entirely,
existing flows bit-identical). The two-pass reading won: pass 1 stays
top-down exactly as above (so nothing ever plans blind — the deep-first
failure mode is structurally excluded), then each refinement pass revisits
every committed, unlocked, un-pinned bundle DEEPEST-FIRST (ascending
priority, the reverse of commit order — the widest globals re-decide last,
seeing everything) against the now-REAL usage of everyone else, all
reservations long released. Acceptance is the
**strictly-better-than-keeping** rule: rip the bundle up, score the best
plan KEEPING its old topology (a temporary pin probe) and the unrestricted
STRICT best against the same state, and adopt only when leaving the old
topology is strictly better by the planner's own score — otherwise restore
the original plan exactly. That strictness is measured, not stylistic:
adopting any found replan accepted 23 score-equal lateral moves on
hbundles/10 and reshuffled NUTS packing (7 → 78 DNUTS opens); with the
strict rule the same flow makes 4 real moves and heals instead. A
fixpoint early-out stops when a pass changes nothing.

A/B with the strict rule (same corpus as above):

| Flow | base → refine 1 | refine 2 |
|---|---|---|
| 01_pipeline_hier | WL 3360 → **2640** (−21%), clean | fixpoint |
| 02_two_procs | WL 7816 → **5280** (−32%), clean | fixpoint |
| 05_stress_grid | opens 47 → 32, WL −7% | opens **8**, 1 overlap, WL −16% |
| 10_chip_units_blocks_leaf | 1 ovl / 7 opens → **0 / 0**, WL −0.6% | one further small move, same 0 / 0 |
| 03/04/06/07/08/09, mix2_fast | unchanged (0 moves) | unchanged |

Every deep-first win is captured or exceeded (01/02/05; 06's win is not
reachable by strictly-better moves), 10 improves instead of regressing,
and the two deep-first regressions (07/10) cannot recur. The knob also
works in the flat `run_planner` (the pass runs over whatever was
committed). **Defaults (decided 2026-07-12,
[internal/refine_passes_default.md](internal/refine_passes_default.md)):
hier planning defaults to 1 pass** — the flat+demo measurement campaign
showed exact no-ops everywhere except `big2_noviz` (0 → 60 opens, the
pre-charge-horizon class), which keeps the FLAT default at 0, while the
hier side added `mix` healing 1/0 → 0/0 with its heal loops converging
9× faster (ripup trials inherit the refinement configuration by design
— fidelity over gating). An explicit `set_planner_param refine_passes
<n>`, including 0, always wins. Tests:
`test/tests/test_planner_refine.py`.

### Per-level summary

When the bundle set spans hierarchy levels, the planner prints which ladder stage each level's bundles ended at and their layer mix:

```
[Planner] Level summary:
  D0: 6 bundles  strict:6  layers{M5:2 M6:6}
  D1: 5 bundles  strict:5  layers{M5:4 M6:3}
  D2: 10 bundles  strict:10  layers{M5:1 M6:10}
```

Stages: `strict` / `ripup` / `overflow` (ALLOW_OVERFLOW) / `best_effort`; `max_overflow` is appended when non-zero. Anything other than `strict` on a level is the signal that local/global competition (or genuine under-capacity) needs attention.

---

## Console Output Reference

Tests grep these strings — keep them stable.

```
[Planner] Bundle 3 (24 units wide) -> topo 8 of 14: TRUNK_H@y560  [H→M6 V→M5 V→M5]  overflow=0
```
Per-bundle selection: topology index/type, optional ` [pinned]`/` [replanned]` tags, per-segment layers, and the **raw** overflow in layout units (the gate guarantees 0 unless a fallback mode committed).

```
[Planner] Rip-up: replanned bundle 1 to free capacity for bundle 2:
[Planner] Bundle 1 (36 units wide) -> topo 1 of 5: I_H [replanned]  [H→M6]  overflow=0
```

```
[Planner] WARNING: Bundle 7: no overflow-free candidate (even after rip-up); committing least-cost with overflow=16.
```

```
[Planner] WARNING: Bundle 7: no candidate fits its slide windows (bus width exceeds them); committing best-effort I_H [pinned].
```

---

## Testing

Flow-level regressions in `test/tests/test_flow_scripts.py`:

| Test | What it checks |
|---|---|
| `test_planner4_keepout_overflow_forces_detour` | Keepout + pinned neighbours: bundle 3 must detour to an overflow-free trunk instead of committing 16 units of overflow (was a B2×B3 NUTS overlap) |
| `test_ripup1_replans_earlier_bundle_to_free_capacity` | Pinned bundle forces rip-up: bundle 1 is replanned out of the only band bundle 2 can use; both end overflow-free |
| `test_planner3_window_capacity_avoids_double_booked_trunk` | Two bundles must not double-book one trunk window; the hard gate also lets bundle 3 take the shortest clean trunk |
| `test_channel_stress_pinned_infeasible_does_not_crash` | `BEST_EFFORT` fallback: infeasible pinned candidates warn instead of crashing |
| `test_ripup2_targets_actual_blocker` | Victim ranking rips the actual blocker directly; zero-overlap victims are never replanned |
| `test_planner5_span_scaled_penalty_drops_short_stub` | Span-scaled penalty: short stub drops to M4 when TOP saturates; flat penalty would detour on TOP |
| `test_09_local_global_compete_reservation_avoids_ripup` | Reservation steers the global off the cell-interior band on the first pass — no rip-up |

Demo scripts:

| Script | Demonstrates |
|---|---|
| `flow/planner3.buda` | Three crossing 16-bit buses; all bands clean without detours |
| `flow/planner4.buda` | Same floorplan + `add_keepout … M6`: the keepout squeezes bundle 3 and the planner detours (`STRICT` gate) |
| `flow/ripup1.buda` | Wide bus parks in the only band a pinned narrow bus can use; rip-up & replan resolves it |
| `flow/ripup2.buda` | ripup1 plus an unrelated committed bundle: contended-band ranking targets the real blocker |
| `flow/planner5_span_drop.buda` | Span-scaled vs flat non-TOP penalty (two `run_planner` runs with different `base_span_ref`) |
| `flow/hbundles/09_local_global_compete.buda` | Cell-local demand reservation vs a global trunk crossing the cell |

---

## Implementation Files

| File | Contents |
|---|---|
| `src/congestion_planner.h` | `GlobalCut`, `BundleWrapper`, `BundleAssignment`, `PlanMode`, `PlanResult`, `CongestionPlanner` declarations |
| `src/congestion_planner.cpp` | Cut construction, cost model, `plan_bundle` / `commit_plan`, escalation ladder in `optimize_topologies` |
| `src/conn_topology.h/cpp` | Authoritative per-segment slide windows (`perp_lo`/`perp_hi`) consumed by the feasibility gate and band lookup |
| `src/layering.h/cpp` | `eff_bus_width`, layer direction/type/span metadata |

---

## Future Work

Residual planner→NUTS packing gaps observed at scale
(`flow/hbundles/10_chip_units_blocks_leaf.buda`) are catalogued in
[future/nuts_packing_gaps.md](future/nuts_packing_gaps.md): pitch-blind band
accounting, ancestor-block face clamping, and post-placement span stretching.


Planned extensions — multi-victim rip-up, PathFinder-style negotiated congestion using the reserved `run_planner <iterations>` argument, and raw-unit overflow pricing in the `ALLOW_OVERFLOW` fallback — are detailed in [future/planner_ripup_extensions.md](future/planner_ripup_extensions.md). (Contended-band victim selection, item 2 there, is implemented.)
