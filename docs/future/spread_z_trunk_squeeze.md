# Future Enhancement: Spread-Z Trunk Squeezing

## Problem

When two blocks share the same perpendicular centre (e.g. both centred at `y=500`), the topology generator produces **spread-Z** candidates — a Z-shape where the trunk connects the top edge of one block to the bottom edge of the other. The trunk currently spans the full block height (`[y1, y2]`) regardless of where the two H stubs are actually placed.

### Concrete example (`two_b7.buda`)

```
u0:   x=[200,300]  y=[400,600]  (shrunken: y=[420,580])
u22:  x=[-100,-50] y=[400,600]  (shrunken: y=[420,580])
```

The spread-Z candidate `Z_HVH@x75` has:
- seg0 (H stub, M4): slide range `[420, 580]`
- seg1 (V trunk, M5): fixed topological span `[420, 580]` = 160 units
- seg2 (H stub, M4): slide range `[420, 580]`

Because seg0 and seg2 are on the same layer with **non-overlapping routing-direction spans** (`[-50,75]` and `[75,200]`), NUTS's first-fit naturally places both at `y=420`. The trunk's **effective length** collapses to zero — but the `TrackSegment` still reports `span=[420,580]`, inflating:

1. The V-layer congestion accounting by 160 phantom units.
2. The planner's wirelength estimate for the spread-Z topology, making it look more expensive than it is relative to `I_H`.

### General case (partially-overlapping intervals)

The problem is more acute when the two stubs have **partially overlapping** but not identical slide ranges:

```
stub A interval: [100, 300]   first-fit → 100
stub B interval: [200, 400]   first-fit → 200
trunk effective length:  100  (|100 - 200|)
optimal placement:       both at 200 → trunk = 0
```

Here first-fit places the stubs at their respective interval bottoms, which are 100 units apart, even though placing both at 200 (the overlap region) would eliminate the trunk entirely.

---

## Proposed Enhancement

### Stage: Topology Generation + Abstract NUTS (Stages 2 and 4)

### Part A — Topology generator: correct wirelength estimate

In `add_z_shapes`, after generating a spread-Z candidate, estimate the trunk's wirelength as the **minimum possible span** — i.e., the length of the interval **intersection** of the two stubs' slide ranges — rather than their full union:

```
stub_overlap = max(0, min(stub_A.slide_hi, stub_B.slide_hi)
                      - max(stub_A.slide_lo, stub_B.slide_lo))
effective_trunk_length = max(0, nominal_trunk_span - stub_overlap)
```

This feeds into `estimated_wirelength`, which the planner uses for topology ranking.

### Part B — NUTS engine: trunk-squeeze post-pass

After the per-layer first-fit placement in `NUTSEngine::run()`, add an optional **trunk-squeeze pass** over connected stub–trunk–stub triples:

1. **Identify triples.** Use the topology's `seg_busterms` annotation or a `coupled_seg_indices: pair<int,int>` field on `TrackSegment` (populated by the CLI from `ConnTopology`) to find which two stubs define each trunk's effective span.

2. **Compute target.** For each triple, the target position is `midpoint = (stub_A.track_pos + stub_B.track_pos) / 2`, clamped to the intersection of both stubs' intervals (or the nearest feasible point if the intersection is empty).

3. **Slide each stub toward target.** For each stub, check whether the new position conflicts with any already-placed segment on the same layer whose routing-direction span overlaps. Accept any conflict-free move that reduces `|stub_A.track_pos - stub_B.track_pos|`.

4. **Iterate to convergence** (typically 1–2 rounds for tree-shaped topologies).

5. **Update trunk's reported span.** After the pass, set `trunk.span_lo = min(stub_A.track_pos, stub_B.track_pos)` and `trunk.span_hi = max(...)` so that congestion accounting and visualisation reflect the actual routing.

### Coupling signal between stages

The cleanest carrier is a new optional field in `TrackSegment`:

```cpp
// Index pair into the same NUTSResult::segments list.
// When set, this trunk's effective span is [min(segs[a].track_pos,
// segs[b].track_pos), max(...)].  Used by the trunk-squeeze pass.
std::optional<std::pair<int,int>> coupled_stubs;
```

The CLI populates this when converting `NUTSResult` segments into NUTS input by inspecting `ConnTopology`.

---

## Non-Goals

- This enhancement does **not** affect segment ordering within a layer (first-fit is still the primary placer; the squeeze pass only improves).
- It does **not** require changes to the planner (Stage 3), the routing grid (Stage 8), or detailed NUTS (Stage 9).
- The trunk-squeeze pass is **optional** — omitting it leaves NUTS behaviour unchanged, it just means trunk lengths may be longer than necessary.

---

## Related

- `docs/topology_generation.md` — background on Z and spread-Z candidate shapes.
- `docs/routing_grid.md` — Stage 8 track patterns (dilution factor is the separate but related mechanism that adjusts segment widths for power-grid overhead).
- The spread-Z connectivity bug (wrong `ovlp` offset breaking T-junction detection) was fixed in commit `ccd03aa`. This enhancement builds on that fix.
