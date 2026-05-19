# Unified Topology Tree Generation — Design Document

**Status:** Proposal (pre-implementation)  
**Replaces:** ad-hoc `generate_candidates` (2-pin) + `generate_multicast_candidates` (N-pin)  
**Authors:** BUDA team  

---

## 1. Motivation and Goals

The current topology generator has two separate entry points with diverging code
paths:

| Entry point | Coverage | Shapes generated |
|---|---|---|
| `generate_candidates(src, dst)` | 2-pin only | I, L, Z, U, UU |
| `generate_multicast_candidates(src, dsts)` | N-pin (1 driver, N recv) | I, TRUNK_H/V, MST, BITRUNK |

There is no unified model for *how* a topology is a tree, *how* sub-trees connect
to a trunk, or *when* a block acts as a relay (feedthru) versus a terminal.  As a
result:

- The Z/U detour logic is duplicated across the two paths rather than composed.
- BITRUNK is a hard-coded special case rather than a natural multi-level result.
- The "feedthru" concept is implicit (`pass_through_count`) and has no
  configuration knob.
- There is no principled way to add a third tier (e.g. a spine that feeds two
  regional trunks that each feed several leaves).

**Goals of this redesign:**

1. **Unified tree model.** All generated topologies are instances of a routing
   tree whose leaves are busterms, whose internal nodes are trunk junctions, and
   whose root is the primary trunk or driver busterm.
2. **Composable connectors.** I/L/Z/U shapes remain as *connector primitives* that
   attach a leaf (or sub-tree) to a trunk at a junction point.
3. **Feedthru blocks.** A block can relay a bus across its interior; bus segments
   are physically disconnected at the feedthru face, and internal routing inside
   the feedthru block completes the connection later.  This is a first-class,
   configurable concept rather than a side-effect.
4. **Incremental complexity.** The 2-pin case is exactly a tree with two leaves and
   no trunk; it reuses all connector primitives.  N-pin cases grow by adding trunk
   nodes.

---

## 2. Vocabulary

| Term | Definition |
|---|---|
| **Busterm** | A leaf connection point: one block face + corner margin.  May be a driver or receiver. |
| **Trunk** | A long axis-aligned segment (H or V) that multiple busterms attach to. The trunk is an *internal node* of the routing tree. |
| **Stub** | A short segment connecting a busterm leaf to a trunk.  May be a single segment (direct), an L-shape, or a Z-shape. |
| **Connector** | The sub-topology that joins one leaf or sub-tree to a trunk.  Shapes: I (degenerate), L, Z, U (detour). |
| **Feedthru** | A block through which the trunk passes physically without connecting, relying on the block's internal routing to complete the electrical connection.  Opt-in per block and per layer. |
| **Root trunk** | The highest-level trunk in the tree.  All leaves are connected to it directly or via lower-level trunks. |
| **Sub-tree** | A sub-topology that is itself a routing tree; it attaches to a parent trunk via a connector. |
| **Hanan grid** | The set of x and y coordinates derived from all busterm bounding boxes.  Trunk candidates are placed at Hanan-grid midpoints. |
| **OOB (out-of-bbox)** | A trunk placed *outside* the bounding box of all busterms it serves.  Generates a U-shape detour for each leaf. |

---

## 3. Routing Tree Model

A routing tree is a directed acyclic graph (tree) where:

```
RootTrunk (H or V segment)
├── Connector₀ → Busterm₀  (leaf — direct I or L or Z stub)
├── Connector₁ → Busterm₁
├── Connector₂ → SubTree₀
│     └── SubTrunk (H or V, perpendicular to RootTrunk)
│           ├── Connector₃ → Busterm₂
│           └── Connector₄ → Busterm₃
└── ...
```

**Special cases:**

- **2-pin**: no trunk node; the root *is* the single connector from src to dst.
  Shapes: I, L, Z, U, UU.
- **All-aligned**: degenerate trunk with no stubs (I_H or I_V).
- **Single trunk**: all leaves attach directly to one trunk via stubs.  The
  existing `TRUNK_H` / `TRUNK_V` shapes are instances of this.
- **Two-level (BITRUNK)**: root is a vertical spine; two horizontal trunks hang
  off it; leaves attach to those trunks.

### 3.1 Node Types

```
TreeNode (abstract)
├── LeafNode         — wraps a Busterm; no children
├── TrunkNode        — a H or V segment; has N children attached via connectors
└── ConnectorNode    — the H→V or V→H bend that attaches a child to its parent
```

### 3.2 Connector Shapes (refined)

| Shape | Segments | Used when |
|---|---|---|
| **Direct** (I-like) | 0 extra segs — child is already on the trunk's perp axis | Leaf falls on the trunk |
| **L** | 1 stub segment | Leaf is offset in ONE axis; one-bend connection |
| **Z** | 2 stub segments | Leaf is offset and the trunk midpoint is inside the busterm pair's bounding box |
| **U** | 1 stub + detour segment | Trunk is OOB on one side; leaf needs a wrap-around stub |

In the 2-pin case, the "trunk" is zero-width and the entire connector IS the
topology.  The I/L/Z/U/UU shapes remain exactly as implemented.

---

## 4. Unified Generation Algorithm

```
GenerateTopologies(busterms: List[Busterm]) → List[Topology]
```

### Phase 0 — Pre-check: aligned cases

If all busterms share the same x-center-range → emit `I_V`.  
If all busterms share the same y-center-range → emit `I_H`.  
Return early (these are always optimal; no trunk alternatives needed).

### Phase 1 — Candidate trunk positions

Build the Hanan grid from all busterm `orig_bbox` coordinates.  
Candidate trunk positions:

- **H trunks**: midpoints of adjacent Hanan y-intervals that fall *inside* the
  bounding box of the busterms' center y-range (in-bbox), *plus* OOB positions
  at `hanan_y[0] − margin` and `hanan_y.back() + margin`.
- **V trunks**: symmetric.

For each candidate position, call `try_trunk(direction, position, busterms)`.

### Phase 2 — `try_trunk`: attach all leaves to a trunk

```
try_trunk(dir, pos, busterms) → Topology or null
```

For each busterm `b`:

1. **Compute connection point** `face_coord` = `b.orig_bbox.face_{x|y}(pos)`.
2. **Check feedthru**: if `pos` falls *inside* `b.bbox` and feedthru is enabled
   for `b` (see Section 5), mark `b` as a feedthru node and skip stub
   generation.  The trunk segment passes through `b` unbroken.
3. **Check direct**: if `face_coord == pos`, the busterm touches the trunk — emit
   a zero-length connector (direct).
4. **Check stub length**: `stub_len = |face_coord − pos|`.  If
   `stub_len < min_stub_length(dir, layer)`, skip this trunk position for `b`
   (or, for OOB trunks, skip the entire candidate).
5. **Choose connector shape** for the stub to `b`:
   - Default: direct L-stub (one segment from `face_coord` to `pos` at
     `att_along` = `b.bbox.center.along`).
   - If `b` itself has sub-busterms (sub-tree case): recursively generate a
     sub-trunk and connect the sub-trunk's endpoint to the root trunk via a
     connector.

Emit the topology if at least one busterm connects.

### Phase 3 — MST fallback

If no single trunk covers all busterms, compute the MST over all busterms using
`compute_mst()` (Kruskal on Manhattan nearest-point distances).  For each MST
edge, generate both H-first (MST_HV) and V-first (MST_VH) connector shapes.

### Phase 4 — Multi-level trunks

For 4+ busterms, split the set into two halves (by sorted y or x coordinate) and
apply `try_trunk` independently to each half, then connect the two sub-trunks
with a perpendicular spine.  This generalises the current BITRUNK pattern to any
depth.

### Phase 5 — Post-processing

- `annotate_endpoints`: tag `seg_busterms` with leaf-node busterm info.
- `filter_pinched`: remove candidates where any segment collapses to a point.
- `annotate_and_sort`: sort by `estimated_wirelength`.

---

## 5. Feedthru Configuration

A **feedthru** is a block that a trunk is allowed to pass through electrically
disconnected.  The trunk segment spans across the block's face-to-face extent
without generating a stub.  The connection is completed later by physical
routing inside the block.

### 5.1 Feedthru vs. Pass-Through (current)

`pass_through_count` in `Topology` already tracks blocks whose bbox contains the
trunk.  The new feedthru model makes this:

- **Explicit**: a block must opt into feedthru; not all pass-throughs are feedthrus.
- **Configurable**: like `min_stub_length`, there is a hierarchy of defaults.
- **Recorded**: each segment that "enters" and "exits" a feedthru block records
  those crossing points in `seg_busterms` so the visualizer and DetailedNUTS can
  render and handle them correctly.

### 5.2 FeedthruConfig

```cpp
struct FeedthruConfig {
    bool global = false;                    // default: feedthru not allowed
    std::map<std::string, bool> per_block;  // block name → override
    std::map<int, bool> per_layer;          // layer_id  → override
};
```

Resolution order (most specific wins):

```
per_block(block_name)  >  per_layer(layer_id)  >  global
```

### 5.3 Floorplan additions

```cpp
void set_feedthru(bool val);
void set_feedthru_block(const std::string& name, bool val);
void set_feedthru_layer(int layer_id, bool val);
bool get_feedthru(const std::string& block_name, int layer_id) const;
```

### 5.4 CLI additions

```
set_feedthru [true|false]                     # global default
set_feedthru_block <name> [true|false]        # per-block override
set_feedthru_layer <layer_id> [true|false]    # per-layer override
```

### 5.5 Semantic rules

- Feedthru is ignored if `pos` is outside the block's bbox (i.e., a normal
  out-of-range projection generates a stub regardless of feedthru setting).
- When feedthru is enabled, the trunk does NOT add a stub segment for that block,
  but the block IS recorded in `Topology::feedthru_blocks` (new field) for
  downstream stages.
- In **Detailed NUTS** (Stage 9), a feedthru block's crossing positions must be
  included in the bit-wire span calculation — the wires must reach the feedthru
  faces on both sides so the block's internal router can make the connection.
- **Without feedthru**: every block that the trunk passes through requires a stub
  segment ending at its face (current behaviour).  This can produce very short
  stubs for large blocks that happen to straddle a trunk position.

---

## 6. Connector Shape Selection (detail)

The existing L/Z/U shapes map naturally onto the tree connector role.  The key
insight is that the choice of connector shape for a leaf depends on *both* the
trunk direction and the relative position of the leaf:

```
Leaf position relative to trunk
──────────────────────────────────────────────────────────────────────────────
           | same x-range        | same y-range        | neither
──────────────────────────────────────────────────────────────────────────────
H trunk    | Direct (no stub)    | L-stub (V segment)  | L-stub or Z-stub
V trunk    | L-stub (H segment)  | Direct (no stub)    | L-stub or Z-stub
──────────────────────────────────────────────────────────────────────────────
```

**Z-stub** is chosen when the L-bend point would place the bend inside another
block's bounding box — in that case an intermediate Hanan-grid position breaks the
Z shape.

**U-stub** applies when the trunk is OOB: every leaf gets a U-shaped detour
regardless of relative position.  The trunk's OOB position determines which side
the detour goes.

### 6.1 Connector quality metrics

Each connector carries:

- `stub_length`: physical length of stub segment(s).  Must be ≥ `min_stub_length`.
- `detour_length`: extra WL beyond the direct Manhattan distance (0 for L, positive
  for Z/U).
- `is_feedthru`: true when the leaf is connected via feedthru rather than a stub.

These feed into the `estimated_wirelength` of the enclosing `Topology`.

---

## 7. Shape Coverage Summary

| Shape | Tree interpretation | Connector type | Root trunk | Sub-tree |
|---|---|---|---|---|
| **I_H / I_V** | Degenerate trunk; all leaves direct | — | H or V trunk | no |
| **L_HV / L_VH** | 2-pin; no trunk; connector IS the topology | L | — | no |
| **Z_HVH / Z_VHV** | 2-pin; Z connector | Z | — | no |
| **U_HVH / U_VHV** | 2-pin; OOB trunk position; U connector | U | OOB trunk | no |
| **UU_VHV / UU_HVH** | 2-pin; src exits a side face → L exit + U detour | UU | OOB | no |
| **TRUNK_H / TRUNK_V** | N-pin; all leaves attach to one trunk | L or Direct | H or V | no |
| **TRUNK_H_OOB / V_OOB** | N-pin; OOB trunk | U | OOB H or V | no |
| **MST_HV / MST_VH** | N-pin; no trunk; L connectors on MST edges | L | MST edge | yes (implicit) |
| **BITRUNK_H** | N-pin (≥4); V spine + two H trunks | Direct/L | V spine | yes (H halves) |
| *(planned)* **TRUNK_H+Z** | N-pin; H trunk; some leaves use Z stubs | Z | H trunk | no |
| *(planned)* **MULTI_TRUNK** | N-pin; generalised k-level trunk tree | L/Z/U | k-level tree | yes |

---

## 8. Relationship to Existing Code

### 8.1 What to keep as-is

- `Busterm`, `Topology`, `Segment`, `Rect`, `Point` — unchanged.
- `ConnTopology` (connectivity inference and slide-range computation) — unchanged.
- `compute_mst` (Kruskal's) — unchanged.
- `add_l_shapes`, `add_z_shapes`, `add_u_shapes`, `add_uu_shapes` — these become
  **connector-shape generators** called by the unified algorithm.  Their
  signatures may be generalised to accept a `TrunkNode*` argument instead of
  always operating on a src/dst Busterm pair.
- `add_trunk_h`, `add_trunk_v` — become `try_trunk_h` / `try_trunk_v` inside the
  unified Phase 2, extended with feedthru handling.
- `min_stub_length` config and all its checks — unchanged.

### 8.2 New additions

| New component | Purpose |
|---|---|
| `FeedthruConfig` | Configuration struct (Section 5.2) |
| `Floorplan::set/get_feedthru` | Config API (Section 5.3) |
| `TreeNode` / `TrunkNode` / `LeafNode` | Optional typed tree IR; may be implicitly represented in `Topology::seg_busterms` instead |
| `Topology::feedthru_blocks` | `vector<string>` — blocks connected via feedthru |
| `generate_topologies(busterms)` | New unified entry point replacing both old entry points |
| `try_trunk(dir, pos, busterms, config)` | Phase 2 trunk attachment |
| `choose_connector(leaf, trunk)` | Returns Direct / L / Z / U based on geometry |
| `add_multi_level_trunks(busterms)` | Phase 4 generalised BITRUNK |

### 8.3 What changes

- `generate_candidates(src, dst)` becomes a thin wrapper: calls `generate_topologies({src, dst})`.
- `generate_multicast_candidates(src, dsts)` becomes a thin wrapper: calls
  `generate_topologies({src} ∪ dsts)`.
- `add_mst_candidates` continues to exist but is called from Phase 3 of the
  unified algorithm.
- `add_multi_trunk_candidates` is subsumed by Phase 4.

---

## 9. Key Design Decisions and Trade-offs

### 9.1 Should I and L-shapes be generated for the multi-pin case?

**Yes.**  When N=2 (2-pin), the unified algorithm must produce the same set as the
current `generate_candidates`.  This requires Phase 0 and the connector shape
selection to match.  The regression test suite anchors this.

### 9.2 Feedthru: opt-in vs. opt-out

**Opt-in (default=false).**  Feedthru is a semantic promise from the chip designer
that a specific block has the internal routing capability to relay the bus.  Opt-in
prevents silent correctness errors where the trunk skips a block that actually
needed a tap.  An alternative "auto-detect" mode (enable feedthru for any block
bigger than a threshold) can be added later as a heuristic.

### 9.3 When does a leaf use a Z-stub vs. an L-stub?

The criterion is whether the direct L-bend falls inside another busterm's bounding
box.  If it does, we shift the bend to the nearest Hanan-grid x (or y) that avoids
it — this becomes a Z-stub.  When there is no such grid line available (all Hanan
positions are blocked), the leaf is unconnectable to this trunk and the trunk
candidate is discarded for this leaf.

### 9.4 Should "feedthru-only" topologies be ranked lower?

**Yes.**  A topology that uses feedthru for some leaves is valid only if the
chip designer has configured those blocks as feedthru-capable.  When both a
feedthru topology and a stub topology are feasible, the stub topology should
rank first (lower WL equivalent), because feedthru adds implicit internal routing
cost.  A `feedthru_penalty` weight (similar to `kCong`) is added to
`estimated_wirelength` for each feedthru hop.

### 9.5 Multi-level depth limit

Unlimited recursion would generate exponentially many candidates.  The practical
limit is depth = 2 (root trunk + one level of sub-trunks), which covers the
BITRUNK pattern.  Depth = 3 is reserved for future work.  The `max_trunk_depth`
parameter controls this.

---

## 10. Topology Ranking — Routing Flexibility Score

### 10.1 Motivation

The current `annotate_and_sort` sorts candidates purely by `estimated_wirelength`.
This is correct as a first approximation, but wirelength alone does not capture
*how much freedom the layout optimiser has* after a topology is selected.

Consider two 2-pin topologies that connect the same block faces:

```
Block A [0,0,100,100]           Block B [180,60,280,160]
gap between right face of A and left face of B = 80 units
```

**L_HV@x180@y80** — H stub from A.right (x=100) to B.left (x=180) at y=80;
V stub from x=180 down to B.bottom (y=60).  
Nominal WL = 80 + 20 = 100 units.

`ConnTopology` gives the V stub a slide range:
- Pass 1 (busterm constraint): perp (x) ∈ [B.x1, B.x2] = [180, 280].
- Pass 2 (via H stub from A): perp ≥ A.x2 + m_h = 100 + 20 = 120.
- **Effective slide = [180, 280] → 100 units** (the full dst x-extent, as long as
  the lower bound 120 < 180).

**Z_HVH@x140@y80** — H stub from A.right (x=100) to trunk x=140; V trunk at
x=140 from y=80 to y=60; H stub from x=140 to B.left (x=180).  
Nominal WL = 40 + 20 + 40 = 100 units (same).

`ConnTopology` gives the V trunk a slide range:
- No direct busterm constraint (trunk is not on any block face).
- Pass 2 from A's H stub: perp ≥ A.x2 + m_h = 120.
- Pass 2 from B's H stub: perp ≤ B.x1 − m_h = 160.
- **Effective slide = [120, 160] → 40 units** (constrained to the inter-block gap).

In this configuration the L wins on flexibility: its V segment can traverse B's
full x-width, while Z's trunk is boxed into the 40-unit gap.  

Now extend the gap to 200 units (B at x=[300,400,…]):

| | L_HV | Z_HVH@x200 |
|---|---|---|
| Nominal WL | 220 | 220 |
| V-seg slide (pass 1+2) | min(B.width=100, 200−20=180) = 100 | 200 − 2×20 = 160 |

Z wins.  The cross-over depends on the ratio of gap to destination block width.
In general, Z is more flexible when the routing channel is wide relative to the
destination block; L is more flexible when the destination block is wide relative
to the channel.

A second important case: when blocks are vertically *misaligned*, the L-bend
point is constrained by BOTH blocks simultaneously (its y must satisfy src's face
AND dst's extent, which may produce a narrow intersection).  The Z trunk has no
direct busterm constraint — only pass-2 indirect constraints — so its slide range
is the full channel width, independent of both blocks' extents.

The practical consequence: a Z topology whose nominal WL is 10–20% longer than a
competing L may still be preferable because the wider slide range lets NUTS place
the trunk without a violation, reducing the chance of an overflow that forces
rip-up-and-reroute.  The flexibility score makes this trade-off explicit and
tunable.

### 10.2 Slide Range Computation

`ConnTopology::compute_slide_ranges` already computes `perp_lo` and `perp_hi` for
every segment after a two-pass constraint propagation (§ 8.1 of the current code).
The slide range of segment *i* is:

```
slide[i] = perp_hi[i] − perp_lo[i]    (layout units; 0 = pinched)
```

For a multi-segment topology the **bottleneck slide** is:

```
min_slide = min over all i of slide[i]
```

The **trunk-weighted slide** adds importance weighting for longer segments:

```
weighted_slide = Σ(slide[i] × segment_length[i]) / Σ(segment_length[i])
```

Where `segment_length[i] = along_hi[i] − along_lo[i]`.

`min_slide` is the primary metric: a single pinched segment forces NUTS into a
fixed position and eliminates all routing freedom regardless of what the other
segments can do.  `weighted_slide` is a secondary tiebreaker between candidates
that share the same `min_slide`.

Topologies with `min_slide = 0` (pinched) are already discarded by
`filter_pinched`.  The flexibility score therefore operates on the range
`(0, ∞)`.

### 10.3 Flexibility Score and Adjusted Wirelength

The flexibility score converts a slide range to a WL discount factor:

```
flex_score(s) = kFlex × min(s / ref_slide, max_flex_ratio)
```

Where:
- `s` = `min_slide` in layout units.
- `kFlex` ∈ [0, 1] controls the maximum discount (default **0.20**).
- `ref_slide` is the "comfortable" reference slide range (default
  **3 × min_stub_length** = 60 units with the default stub of 20).
- `max_flex_ratio` caps the multiplier at `s/ref_slide` = 1 (so the maximum
  discount is exactly `kFlex`; extra flexibility beyond `ref_slide` gives no
  further reward).

The **adjusted wirelength** used for sorting is:

```
adjusted_wl = estimated_wl × (1 − flex_score(min_slide))
```

Lower `adjusted_wl` = higher rank.  Parameterised examples with `kFlex=0.20`
and `ref_slide=60`:

| min_slide (units) | flex_score | Effective WL discount |
|---|---|---|
| 0 (pinched, filtered) | — | filtered |
| 10 | 0.033 | 3.3% |
| 20 (= min_stub_length) | 0.067 | 6.7% |
| 60 (= ref_slide) | 0.200 | 20% |
| 90 | 0.200 (capped) | 20% |
| 120 | 0.200 (capped) | 20% |

So a topology with `min_slide ≥ ref_slide` gets the full 20% discount; one with
`min_slide = min_stub_length` (barely acceptable) gets ~7%; and anything pinched
is filtered out first.

**Using `weighted_slide` as a secondary tiebreaker:** when two topologies have
the same `adjusted_wl` (after rounding to integer WL units), the one with higher
`weighted_slide` ranks first.

### 10.4 Worked Example: Z vs L re-compared

Gap = 80, B.width = 100 (from §10.1):

| Topology | Nominal WL | min_slide | flex_score | adjusted_wl |
|---|---|---|---|---|
| L_HV | 100 | 100 | 0.200 | 80.0 |
| Z_HVH@x140 | 100 | 40 | 0.133 | 86.7 |

L still wins when `gap < B.width + m_h` (80 < 120).

Gap = 200, B.width = 100:

| Topology | Nominal WL | min_slide | flex_score | adjusted_wl |
|---|---|---|---|---|
| L_HV | 220 | 100 | 0.200 | 176.0 |
| Z_HVH@x200 | 220 | 160 (capped) | 0.200 | 176.0 |

Tie on adjusted_wl; `weighted_slide` breaks the tie (Z wins if its trunk is
longer than L's pinned V segment).

Gap = 200, B.width = 50 (a narrow destination block):

| Topology | Nominal WL | min_slide | flex_score | adjusted_wl |
|---|---|---|---|---|
| L_HV | 220 | 50 | 0.167 | 183.3 |
| Z_HVH@x200 | 220 | 160 (capped) | 0.200 | 176.0 |

**Z wins by 4%** — the flexibility bonus overcomes the equal nominal WL because
the inter-block channel is larger than the narrow destination.

### 10.5 Interaction with the Planner (Stage 3)

The planner already has its own cost function (`kCong`, `kSpan`, congestion map).
The flexibility-adjusted ranking from Stage 2 affects *which topologies appear
near the top of the candidate list* that the planner iterates over.  The effects
are complementary:

- **Stage 2 ranking** ensures the planner sees flexible options first, reducing
  the number of iterations needed to find a good global solution.
- **Stage 3 cost** captures cross-bundle congestion that Stage 2 cannot see
  (since Stage 2 generates per-bundle, not globally).

When `kFlex` is set to 0, Stage 2 ranking degenerates to pure WL order (current
behaviour); setting it higher (0.15–0.25) is the recommended operating range.

### 10.6 Storing and Exposing Flexibility Metadata

Two new fields are added to `Topology`:

```cpp
struct Topology {
    // ... existing fields ...
    int   min_slide     = 0;   // min perp_hi − perp_lo across all segments (0 = unknown)
    int   adjusted_wl  = 0;   // estimated_wl × (1 − flex_score), used for sorting
};
```

`annotate_and_sort` is updated to:
1. For each candidate, run `ConnTopology::build(cand, floorplan_)` to get slide ranges.
2. Set `cand.min_slide = min over segs of (perp_hi − perp_lo)`.
3. Compute `flex_score = kFlex × min(min_slide / ref_slide, 1.0)`.
4. Set `cand.adjusted_wl = (int)(estimated_wl × (1.0 − flex_score))`.
5. Sort by `adjusted_wl` ascending (secondary key: `−min_slide` to prefer
   flexible candidates when adjusted WL ties).

The `kFlex` and `ref_slide` values are read from `Floorplan` at sort time so the
generator is stateless and tests can override them.

---

## 11. Configuration Summary

All new parameters follow the same resolution hierarchy as `min_stub_length`:
`per_layer > per_block/per_dir > global`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `feedthru` | bool | `false` | Global feedthru enable |
| `feedthru_block.<name>` | bool | (inherit) | Per-block feedthru enable |
| `feedthru_layer.<id>` | bool | (inherit) | Per-layer feedthru enable |
| `feedthru_penalty` | float | `1.5×` | WL multiplier for feedthru hops |
| `max_trunk_depth` | int | `2` | Maximum trunk hierarchy depth |
| `min_stub_length` | int | `20` | Already implemented; unchanged |
| `kFlex` | float | `0.20` | Max WL discount for routing flexibility (0 = disable) |
| `flex_ref_slide` | int | `3 × min_stub_length` | Slide range considered "comfortable" (full discount threshold) |

---

## 11. Topology Type Naming Convention (extended)

To keep the `Topology::type` string human-readable, the unified generator will
use a structured naming scheme:

```
<root_shape>[@<pos>][+<connector>]*[~FT]

Examples:
  TRUNK_H@y450                        — single H trunk, stubs are L-type
  TRUNK_H@y450+Z@b2                   — H trunk; block b2 uses a Z-stub
  TRUNK_H@y450~FT@b3                  — H trunk; block b3 is a feedthru
  BITRUNK_V@x200+H@y100+H@y300        — V spine, two H trunks
  MST_HV                              — MST edges with H-first bends
  L_HV@x150@y80                       — 2-pin L shape (unchanged)
  U_HVH@x-30                         — 2-pin U shape (unchanged)
```

The `~FT` suffix flags that at least one feedthru hop is present.  This allows
the planner and visualizer to treat feedthru-containing topologies distinctly.

---

## 12. Implementation Plan

### Phase A — Feedthru config (no algorithmic change)
1. Add `FeedthruConfig` to `Floorplan`.
2. Add `Topology::feedthru_blocks` field.
3. Extend `add_trunk_h` / `add_trunk_v` to skip stub generation for feedthru blocks
   and record them in `feedthru_blocks`.
4. Extend `min_stub_length_exhaustive` test with feedthru scenarios.
5. Add `.buda` CLI commands `set_feedthru`, `set_feedthru_block`, `set_feedthru_layer`.

### Phase B — Unified entry point (2-pin parity)
1. Implement `generate_topologies(busterms)`.
2. Route `generate_candidates(src, dst)` through it.
3. Verify all 2-pin tests pass unchanged.

### Phase C — Unified N-pin parity
1. Route `generate_multicast_candidates` through `generate_topologies`.
2. Verify all N-pin tests pass unchanged (TRUNK_H/V, MST, BITRUNK coverage).

### Phase D — Connector shape generalisation
1. Move Z-stub and U-stub selection into `choose_connector`.
2. Add Z-stub support to `add_trunk_h` / `add_trunk_v` for individual leaves that
   need a Z detour rather than a direct L-stub.
3. Add `TRUNK_H+Z` and `TRUNK_V+Z` topology types.

### Phase E — Multi-level trunks
1. Implement `add_multi_level_trunks` (depth ≤ 2).
2. Replace hard-coded BITRUNK with this generalised version.
3. Add BDD feature `multi_level_trunk.feature`.

### Phase F — Routing flexibility score
1. Add `min_slide` and `adjusted_wl` fields to `Topology`.
2. Add `kFlex` and `flex_ref_slide` to `Floorplan`.
3. Update `annotate_and_sort` to run `ConnTopology::build` per candidate and
   populate `min_slide` and `adjusted_wl`.
4. Change sort key from `estimated_wirelength` to `adjusted_wl` (secondary:
   `-min_slide`).
5. Add `.buda` CLI commands `set_planner_param kFlex <v>` and
   `set_planner_param flex_ref_slide <v>`.
6. Add BDD feature `topology_flexibility.feature`.

---

## 13. BDD Feature Outline (Gherkin stubs)

The following features will be fleshed out in `test/tests/features/`:

```gherkin
# feedthru.feature
Feature: Feedthru-enabled trunk generation
  Scenario: Trunk passes through a feedthru-enabled block without a stub
  Scenario: Trunk generates a stub when feedthru is disabled (default)
  Scenario: Per-block feedthru overrides global setting
  Scenario: Per-layer feedthru overrides global setting
  Scenario: Feedthru block crossing recorded in Topology.feedthru_blocks
  Scenario: Feedthru topology ranked lower than stub topology (WL penalty)

# unified_topology.feature
Feature: Unified 2-pin topology generation parity
  Scenario: generate_topologies({src, dst}) produces same set as generate_candidates
  Scenario: I, L, Z, U, UU shapes all present for typical block pairs
  Scenario: min_stub_length filter respected by all shapes

# multicast_topology.feature
Feature: Unified N-pin topology generation parity
  Scenario: generate_topologies({src, dst1, dst2}) produces TRUNK_H/V candidates
  Scenario: MST topology generated as fallback when no single trunk covers all
  Scenario: BITRUNK generated for 4+ blocks
  Scenario: OOB trunk candidates generated on both sides

# connector_shape.feature
Feature: Connector shape selection
  Scenario: Leaf on same y-range as H trunk → direct connection (no stub)
  Scenario: Leaf below H trunk → L-stub (vertical)
  Scenario: Leaf at Hanan-grid intermediate position → Z-stub
  Scenario: OOB trunk → U-stub for each leaf

# multi_level_trunk.feature
Feature: Multi-level trunk trees
  Scenario: 4 blocks split into two H-trunk halves connected by V spine
  Scenario: Depth-1 (single trunk) still generated alongside depth-2
  Scenario: max_trunk_depth=1 disables multi-level generation

# topology_flexibility.feature
Feature: Routing flexibility score
  Scenario: Topology.min_slide equals minimum perp slide range across all segments
  Scenario: Topology.adjusted_wl reflects WL discount proportional to min_slide
  Scenario: kFlex=0 disables the discount and sorts by estimated_wl only
  Scenario: Z topology with large inter-block gap ranks above equal-WL L topology
  Scenario: L topology with wide destination block ranks above equal-WL Z topology
  Scenario: flex_ref_slide controls the slide threshold for full discount
  Scenario: Candidate with same adjusted_wl but higher min_slide ranks first
  Scenario: Two-segment topology: min_slide is the bottleneck (smaller) segment
```

---

## Appendix A: Current Code Map

```
topology.h          — Rect, Busterm, Topology, Floorplan, TopologyGenerator
topology.cpp        — add_l_shapes, add_z_shapes, add_u_shapes, add_uu_shapes,
                      add_trunk_h, add_trunk_v, add_mst_candidates,
                      add_multi_trunk_candidates,
                      generate_candidates, generate_multicast_candidates
conn_topology.h     — SegConn, ConnSeg, MSTEdge, ConnTopology
conn_topology.cpp   — ConnTopology::build, infer_connections, compute_slide_ranges,
                      compute_mst, trunk_mst, manhattan_nearest
```

## Appendix B: `pass_through_count` vs. Feedthru

`pass_through_count` is incremented in `add_trunk_h` / `add_trunk_v` for any block
whose bbox straddles the trunk (i.e., `has_stub[i] == false`).  It is currently
displayed in the visualizer as a label.  In the feedthru model:

- `pass_through_count` continues to count blocks *geometrically* straddled by the
  trunk.
- `feedthru_blocks` records the *intentional* feedthru subset (only when
  `get_feedthru(block, layer) == true`).
- A block in `pass_through_count` but NOT in `feedthru_blocks` means the trunk
  happens to pass through the block but the designer has NOT declared it a relay
  — this may be a routing error and should be flagged as a DRC warning.

## Appendix C: Why Not Just MST?

MST minimises total WL but ignores:
- Trunk reuse (one H segment can serve all busterms in a horizontal band).
- Layer assignment (a horizontal trunk spans fewer layers than MST star routes).
- Feedthru opportunity (a single spanning segment through a feedthru block is
  cheaper than an MST detour around it).

The trunk-first approach (Phase 1 and 2) generates fewer total segments and
aligns naturally with the layer-assignment model in Stage 3.  MST is kept as a
fallback (Phase 3) when no single trunk covers all busterms.
