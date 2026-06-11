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
    (+ per-segment perp-band hints applied to the internal cut state)
          │
          ▼
Stage 4 (Abstract NUTS)
    packs each segment into its Hanan-cell interval on the assigned layer
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
base = 0 if TOP layer else base_cost_non_top
segment score = cong + span + base
```

```
topology score = max over segments (weakest link) + kWL · estimated_wirelength
```

- **Effective width** (`LayerStack::eff_bus_width`): `bits × unit_pitch / n_signal_slots` when the layer has a track pattern (`def_track_pattern`), else `width × dilution_factor`.
- **Slide-aware band lookup** (`best_band_perp`): the congestion charge goes to the cheapest Hanan band within the segment's ConnTopology slide window that can host the bus — not a point estimate at the window centre, which can land in an arbitrarily narrow band the bus would never use. Ties break toward the window centre.
- **Window-clamped capacity** (`usable_band_cap`): a segment confined to a sub-band window is priced against the window's overlap with the band, not the whole band.
- **Keepout rejection**: `cap ≤ 0` (band fully blocked) scores `kCong × 9999`, eliminating the band outright.

### Tuning knobs (`set_planner_param`)

| Knob | Default | Role under the hard-overflow design |
|---|---|---|
| `kCong` | 1.0 | Arbitrates among *unavoidable* overflows (`ALLOW_OVERFLOW` fallback) and prices residual soft pressure. It no longer decides overflow-vs-detour — that is a hard gate. |
| `kSpan` | 0.001 | Span-mismatch pressure; per-layer `kSpan K` override on `def_layer`. |
| `base_cost_non_top` | 0.5 | Flat preference for `TOP` layers. |
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

When no candidate is overflow-free against the current usage, earlier-committed bundles are tried as victims, **most recently committed first** (lowest priority / narrowest — cheapest to disturb):

```
for each victim P (reverse commit order):
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

Demo scripts:

| Script | Demonstrates |
|---|---|
| `flow/planner3.buda` | Three crossing 16-bit buses; all bands clean without detours |
| `flow/planner4.buda` | Same floorplan + `add_keepout … M6`: the keepout squeezes bundle 3 and the planner detours (`STRICT` gate) |
| `flow/ripup1.buda` | Wide bus parks in the only band a pinned narrow bus can use; rip-up & replan resolves it |

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

Planned extensions — multi-victim rip-up, smarter victim selection, PathFinder-style negotiated congestion using the reserved `run_planner <iterations>` argument, and raw-unit overflow pricing in the `ALLOW_OVERFLOW` fallback — are detailed in [future/planner_ripup_extensions.md](future/planner_ripup_extensions.md).
