# Unified Topology Tree Generation — Design Document

**Status:** Partially implemented — the unified entry point and all single-trunk /
MST / multi-rect machinery are built and shipping; feedthru, the flexibility score,
pull-balancing, candidate dedup, and general multi-level trunks are **not** yet built.
See **§16 Implementation Status** for the per-feature breakdown with code pointers.  
**Replaces:** ad-hoc `generate_candidates` (2-pin) + `generate_multicast_candidates` (N-pin)
— *done*: `TopologyGenerator::generate_candidates` (`topology.cpp:1550`) is now the single
entry point, dispatching to `generate_2pin` / `generate_npin`.  
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

> **Status — mostly [IMPLEMENTED], two gaps.** Phases 0–3 are built:
> `generate_npin` (`topology.cpp:1130`) runs the aligned-case pre-check, sweeps trunk
> positions via `add_trunk_h`/`add_trunk_v` (`:734`/`:918`), and falls back to
> `add_mst_candidates` (`:1298`). Phase 4 is only **partially** built —
> `add_multi_trunk_candidates` (`:1506`) emits a single `BITRUNK_H` split, not the
> general recursive multi-level trunk described here (no `BITRUNK_V`, no depth>1).
> Phase 5 `deduplicate` is **[MISSING]** (no candidate-level dedup exists) and
> `annotate_and_sort` (`:634`) sorts by raw `estimated_wirelength`, **not** `adjusted_wl`
> (the flexibility score of §10 is unbuilt). The signature here,
> `GenerateTopologies(busterms)`, is realized as `generate_candidates(src, dsts)`.

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
- **`deduplicate`**: remove candidates whose segment geometry is identical to an
  already-seen candidate.  Two candidates are geometric duplicates when their
  sorted sets of `(x1,y1,x2,y2)` segment tuples are equal — regardless of type
  string or which segment is called "trunk" vs. "stub".  The canonical duplicate
  arises when one block straddles the H-trunk y-line (making it Direct) AND
  another block straddles the V-trunk x-line (making it Direct): both
  `SingleHTrunk` and `SingleVTrunk` emit the same two segments.  Keep the first
  occurrence (shorter type string wins on tiebreak); discard the rest.
- `annotate_and_sort`: sort by `adjusted_wl`.

---

## 5. Feedthru Configuration

> **Status — [MISSING].** No `FeedthruConfig`, `Floorplan::feedthru_blocks`, or
> `add_feedthru` CLI command exists in the tree (`grep -ri feedthru src/` finds only
> `pass_through_count`). Today a trunk that crosses a block's bbox is recorded as a
> **pass-through** (`Topology::pass_through_count`, `topology.h:79`) but is never
> deliberately *configured* as routable; the connectivity verifier tolerates it. The
> opt-in/opt-out semantics below are unbuilt. `test/tests/test_feedthru.py` carries the
> xfail spec. Per the iteration-1 priorities, feedthru is **deferred** behind richer
> trunk shapes and dedup.

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

> **Status — [PARTIAL].** The trunk builders attach each leaf with a **direct/L-stub**
> only (`add_trunk_h`/`add_trunk_v`). The richer per-leaf connector choice described
> here — in particular dropping a **Z-stub** off the trunk for an offset leaf, or a
> recursive sub-trunk for a leaf that is itself a sub-tree — is **not** generated inside
> a trunk candidate. (Standalone `Z_HVH`/`Z_VHV` shapes exist only for the 2-pin case,
> `add_z_shapes` `:382`.) Per-leaf Z-stubs on trunks are the headline item of the
> **"richer trunk shapes"** workstream (iteration 2). The tc3a analysis
> ([topology_tc3a_findings.md](topology_tc3a_findings.md)) shows why: 70/80 bundles
> route trunks straight *through* intervening blocks rather than branching to them.

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

> **Status — [MISSING] (ranking) / [IMPLEMENTED] (the input metric).** The raw slide
> range this section builds on **is** computed and exposed:
> `ConnTopology::build` fills each `ConnSeg.perp_lo`/`perp_hi`
> (`conn_topology.cpp:210`, bound to Python in `bind_nuts.cpp:58`), and the
> `dump_topologies` CLI command reports the per-candidate `min_slide` from it. What is
> **unbuilt** is everything that *consumes* it for ranking: there is no `flex_score`,
> no `adjusted_wl`, no `kFlex` knob, and `annotate_and_sort` (`topology.cpp:634`) still
> sorts by raw `estimated_wirelength`. `test/tests/test_topology_flexibility.py` (16
> xfail markers) and `test_pull_preference.py` (§10.7, 10 xfail) hold the specs. The
> tc3a dump confirms the gap empirically: candidates with `min_slide=40` routinely sort
> *above* `min_slide=1100` siblings purely because they are a few units shorter.

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

### 10.7 Pull Preference

#### What pull means

For every segment in a routed tree, each stub that hangs off it has a busterm at
its far end.  That busterm lives on one side of the segment's perpendicular axis —
either *below* (or to the left for a V segment) or *above* (or to the right).
The stub exerts a **pull** on the parent segment: it constrains the segment's
`perp_lo` (upward pressure) if the busterm is below, or `perp_hi` (downward
pressure) if the busterm is above.

```
H trunk at y = T
  V stub going DOWN to block B (B.top_face < T):
      pass-2 constraint:  T ≥ B.top_face + min_stub     ← B pushes perp_lo up
      B has a  lo_pull  on the trunk  (block is below, pulls trunk down toward it)

  V stub going UP to block C (C.bottom_face > T):
      pass-2 constraint:  T ≤ C.bottom_face − min_stub  ← C pushes perp_hi down
      C has a  hi_pull  on the trunk  (block is above, pulls trunk up toward it)
```

**lo_pull** = count of busterm stubs whose far end is on the *low* side of the
segment's current perp position (they constrain `perp_lo`).  
**hi_pull** = count on the *high* side (they constrain `perp_hi`).

These counts are computed directly from the `ConnSeg` data built by
`ConnTopology`:

```
for each stub T (V or H) with conn.kind == SEG attached to segment S:
    face_coord = T.conns[BUSTERM].face_coord
    if face_coord < S.perp_pos:  lo_pull[S]++
    if face_coord > S.perp_pos:  hi_pull[S]++
    // face_coord == S.perp_pos → pass-through (feedthru or direct), no pull
```

#### Pull factor and the L-topology

The **pull factor** of a segment is `lo_pull + hi_pull` — the total number of
busterm constraints it carries, directly or through one level of stubs.

An **L topology** is the fundamental unit case: each of its two segments has a
pull factor of exactly 1.

- H segment of L_HV: one attached V stub whose busterm is either above or below
  → `lo_pull=1, hi_pull=0` or `lo_pull=0, hi_pull=1`.
- V segment of L_HV: directly anchored at one busterm on its far end → same.

Every segment in an L topology is *unidirectionally constrained*: one block owns
the entire constraint, and the segment is free in the other direction (up to the
global layout boundary).  This is why L topologies have one of the largest
possible slide ranges when the unconstrained direction is open — but it also means
the segment has no "counterweight" to keep it from drifting away from the single
block that constrains it.

#### Pull balance

For a segment with `n = lo_pull + hi_pull > 0`:

```
pull_balance = (lo_pull − hi_pull) / n    ∈ [−1, +1]
```

| pull_balance | Meaning |
|---|---|
| +1.0 | All constraints push `perp_lo` up — segment can only move upward freely |
| 0.0 | Equal constraints on both sides — slide range is symmetric about current position |
| −1.0 | All constraints push `perp_hi` down — segment can only move downward freely |

A TRUNK_H with 3 blocks below and 2 blocks above:
- `lo_pull = 3`, `hi_pull = 2`, `pull_balance = (3−2)/5 = +0.2` (slight downward bias).

The same trunk with all 5 blocks below:
- `lo_pull = 5`, `hi_pull = 0`, `pull_balance = +1.0` (fully one-sided).

#### Pull balance and the slide range

Pull balance tells you the *direction* of freedom; slide range tells you the
*magnitude*.  A segment can be:

| pull_balance | slide range | Interpretation |
|---|---|---|
| 0.0 (balanced) | large | Best: symmetric freedom, trunk can dodge congestion in either direction |
| 0.0 (balanced) | small | Both sides are tightly constrained — a narrow channel surrounds the trunk |
| ±1.0 (one-sided) | large | The unconstrained side is open; useful for OOB trunks |
| ±1.0 (one-sided) | small | The single constraint is very tight; little room to move even in the free direction |

For NUTS, the optimal trunk is balanced with a large slide range.  A fully
one-sided trunk with a large slide range is second-best: NUTS can still shift it
significantly, but only in one direction, which limits its ability to resolve
conflicts on both sides of the routing channel.

#### 10.8 Pull-Balanced Centroid as a Trunk Candidate

The trunk position that minimises the sum of stub lengths (L1 wirelength) is the
**median** of the busterms' face coordinates toward the trunk.  For N busterms
with face positions `f₁ … fₙ`:

```
y_centroid = median(f₁, f₂, …, fₙ)
```

This position also maximises pull balance (at the median, ≈ N/2 blocks pull from
each side).  It is therefore both the lowest-WL and the most-flexible trunk
position — a unique sweet spot.

The current generator only produces trunks at Hanan-grid *midpoints*.  The
centroid will generally not fall on a Hanan midpoint.  **Phase 1 of the unified
algorithm should add the pull-balanced centroid as an explicit additional trunk
candidate** for every set of busterms, distinct from the Hanan-grid sweep:

```
Phase 1 (extended):
  for each H trunk direction:
    candidates ← Hanan-midpoint sweep (existing)
    y_cen ← median of all busterm top/bottom face y positions
    if y_cen is not already in candidates: add try_trunk(H, y_cen, busterms)

  for each V trunk direction:
    x_cen ← median of all busterm left/right face x positions
    if x_cen not already in candidates: add try_trunk(V, x_cen, busterms)
```

The centroid candidate is labelled `TRUNK_H@y<cen>~CEN` (or `~CEN` suffix) in the
type string so the visualiser can highlight it distinctly.

For the 2-pin case the centroid is unambiguous: the median of two values is their
midpoint, so the centroid candidate is the Z-shape with the trunk at the midpoint
of the two block faces — matching the default Z position from `add_z_shapes`.

#### 10.9 Updated Topology Fields and Sort Key

Extend `Topology`:

```cpp
struct Topology {
    // ... existing fields ...
    int   min_slide      = 0;   // min(perp_hi − perp_lo) across all segments
    int   adjusted_wl    = 0;   // estimated_wl × (1 − flex_score)
    int   lo_pull        = 0;   // sum of lo_pull counts across all segments
    int   hi_pull        = 0;   // sum of hi_pull counts across all segments
    // pull_balance per topology = (lo_pull − hi_pull) / max(1, lo_pull + hi_pull)
};
```

Extend `ConnSeg`:

```cpp
struct ConnSeg {
    // ... existing fields ...
    int lo_pull = 0;   // # busterm stubs constraining from below/left
    int hi_pull = 0;   // # busterm stubs constraining from above/right
};
```

Updated sort key for `annotate_and_sort` (lower = better):

```
primary:    adjusted_wl                              (lower WL after flex discount)
secondary:  −min_slide                               (larger slide first on WL tie)
tertiary:   |lo_pull − hi_pull| / (lo_pull+hi_pull)  (more balanced pull wins)
```

The tertiary key resolves the rare case where two topologies have identical
adjusted_wl and min_slide but differ in pull balance — the more balanced one (net
pull closer to 0) is preferred because it gives NUTS symmetric freedom.

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

## 12. Topology Type Naming Convention (extended)

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

## 13. Implementation Plan

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

## 14. BDD Feature Outline (Gherkin stubs)

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

# pull_preference.feature
Feature: Pull preference and pull-balanced trunk generation
  Scenario: L topology each segment has pull factor 1 (lo_pull+hi_pull == 1)
  Scenario: H trunk with 3 blocks below and 2 above has pull_balance = +0.2
  Scenario: H trunk with all blocks on one side has pull_balance = ±1.0
  Scenario: Pull-balanced centroid candidate generated for every trunk direction
  Scenario: Centroid candidate labelled with ~CEN suffix in topology type string
  Scenario: For 2-pin case centroid trunk matches midpoint Z-shape position
  Scenario: More balanced pull breaks tie between equal adjusted_wl/min_slide candidates
  Scenario: lo_pull and hi_pull counts stored on ConnSeg after ConnTopology::build
```

---

---

## 15. Multi-Rect Blocks and Equivalent Busterms

> **Status — [IMPLEMENTED].** `add_block <name> rect …` parses into a multi-rect
> busterm (`Busterm::rects`, `topology.h:67`); the trunk builders pick the best-fit rect
> per trunk position via `best_rect_for_h`/`best_rect_for_v` (`topology.cpp:669`/`:681`);
> and `teg_mode thru|over` drives the bridge logic (`Topology::bridge_segments`). The
> `.buda` syntax and TEG behaviour are documented in CLAUDE.md ("Stage 2"). Covered by
> the multi-rect tests under `test/tests/`.

### 15.1 Motivation

Two real-world layout situations require connecting to more than one candidate
face on a single logical block:

**Equivalent busterms** — a bus can exit the block from either of two physical
ports (e.g., a left-side port and a right-side port that are internally
equivalent).  The topology should connect to whichever port produces the shorter
stub for the current trunk position, not always the same face.

**Rectilinear blocks** — a block occupies an L-shaped, T-shaped, or other
non-rectangular region.  Different topology candidates will naturally want to
approach the block from different faces (the top of the tall arm vs. the right
end of the wide base).  A single bounding rectangle misrepresents the available
faces.

Both problems share one solution: allow a block to carry **multiple rectangles**,
where each rectangle is an independent candidate connection region (an
"equivalence group").  The topology generator tries all rectangles and picks the
one that minimises stub cost for the current trunk position.

### 15.2 Syntax (`add_block` extension)

```
# Existing (backward-compatible): single rect, no parentheses
add_block <name> <x1> <y1> <x2> <y2> [corner_margin dx N [dy N]]

# New: multi-rect, each rect in parentheses
add_block <name> (<x1> <y1> <x2> <y2>) (<x1> <y1> <x2> <y2>) ... \
    [corner_margin dx N [dy N]]
```

The parenthesised form is recognised when the first token after `<name>` starts
with `(`.  At least two rects are expected; corner margin applies uniformly to
all rects.  The single-rect form continues to parse without parentheses for full
backward compatibility.

**Rectilinear block example** — L-shaped SRAM:

```
#      +----+
#      | A  |   tall left arm
#      |    +--------+
#      |    B  wide  |
#      +--------------+

add_block SRAM_L (0 0 100 200) (0 0 300 100)
```

**Equivalent busterms example** — CPU block with left and right ports:

```
add_block CPU (0 100 20 300) (480 100 500 300)
```

### 15.3 Best-Rect Selection

For each block `b` and each candidate trunk, the generator tries all of `b`'s
rects and selects the one with minimum stub cost:

```
best_rect(b, trunk_dir, trunk_pos) =
    argmin over b.rects r of:
        stub_cost(r, trunk_dir, trunk_pos)

stub_cost(r, H, y) = |nearest_y_face(r, y) − y|
stub_cost(r, V, x) = |nearest_x_face(r, x) − x|
```

Where `nearest_y_face(r, y) = r.y2 if r.y2 < y else r.y1` (whichever face of
`r` points toward the trunk).

If the trunk passes through `r` (i.e. `r.y1 ≤ y ≤ r.y2` for an H trunk),
`stub_cost = 0` and the connection is Direct (or feedthru if enabled).

The rect selection is **per-candidate**: the same block may use different rects
in different topology candidates.  This is expected and correct.

### 15.4 Hanan Grid with Multi-Rect Blocks

The Hanan grid is built from **all rect edges of all blocks**:

```
hx = sorted unique {r.x1, r.x2  for all blocks b, all rects r ∈ b.rects}
hy = sorted unique {r.y1, r.y2  for all blocks b, all rects r ∈ b.rects}
```

This ensures that trunk positions exist at every natural connection boundary,
regardless of which rect a block ends up connecting through.

### 15.5 Slide Range with the Selected Rect

After a topology is built (Phase 5), the slide-range computation in
`ConnTopology::compute_slide_ranges` uses the **selected rect's geometry** for
each block — not the union bounding box of all rects.

The Pass-2 constraint (min-stub-length enforcement) is derived from
`selected_rect.face_coord` toward the trunk, not from the block's overall
extreme face.  Using the union bbox would over-constrain the slide range for
blocks whose selected rect is far from the opposite extreme.

### 15.6 Obstacle vs. Connection Semantics

The multi-rect data serves two distinct purposes:

| Query | Which rects to use |
|---|---|
| **Connection point**: where does a stub attach? | Selected rect only (cheapest for this trunk) |
| **Obstacle / feedthru check**: does the trunk pass through the block? | Union of **all** rects — any rect that straddles the trunk marks the block as a pass-through |
| **Congestion / area accounting** | Union of all rects (conservative approximation) |

For feedthru, the check is: does **any** of `b.rects` straddle the trunk
position?  If so, the block is potentially a feedthru candidate; the designer
must have opted in via `set_feedthru_block`.

### 15.7 Rectilinear Block Decomposition

For an L-shaped block, the two rects need not be non-overlapping.  They
represent **face groups**, not a partition of area:

```
Tall arm:   (x1, y_base, x_narrow, y_top)    — exposes top face and left/right of arm
Wide base:  (x1, y_base, x_wide,   y_bottom) — exposes right face and bottom of base
```

Rects may overlap in their shared interior region.  The topology generator only
cares about external faces; overlap in the interior has no effect on stub
computation.

For congestion accounting, the actual occupied area is the union (computed via
inclusion-exclusion or a polygon union if a geometry library is available; the
bounding box is an acceptable conservative estimate for stage-2 coarse planning).

### 15.8 C++ API Changes

```cpp
// Current
void Floorplan::add_block(const string& name, int x1, int y1, int x2, int y2);

// New (primary overload)
void Floorplan::add_block(const string& name, const vector<Rect>& rects);

// Backward-compat thin wrapper (calls new overload with single-element vector)
void Floorplan::add_block(const string& name, int x1, int y1, int x2, int y2) {
    add_block(name, {Rect{x1, y1, x2, y2}});
}

// Existing — returns union bbox of all rects (unchanged for all callers)
Rect Floorplan::get_block_bounds(const string& name) const;

// New — returns all rects for this block
const vector<Rect>& Floorplan::get_block_rects(const string& name) const;
```

The `Busterm` struct gains a `rect_index` field that records which rect was
selected when the busterm was created:

```cpp
struct Busterm {
    // ... existing fields ...
    int rect_index = 0;   // index into block's rect list; 0 for legacy single-rect blocks
};
```

### 15.9 Corner Margin with Multi-Rect Blocks

The `corner_margin` in `add_block` applies uniformly to **all** rects.  Each
rect is shrunk independently:

```
effective_rect[i] = rects[i].shrink(dx, dy)
```

The existing `Rect::shrink` guard (skip shrink if `2*margin ≥ face_extent`)
applies per-rect, per-axis, so small rects are protected independently.

Per-block margin overrides (via `corner_margin dx N dy N` in the `add_block`
line) apply the same override to all rects of that block.

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

---

## 16. Implementation Status

Snapshot of this design vs. the code on the analysis branch
(`claude/claude-md-docs-c72lbp`, off `main@f125a49`). Verified by reading the cited
symbols and by the `dump_topologies` inspection of `flow/big_data_test/tc3a_flat.buda`.

| § | Feature | Status | Code / evidence |
|---|---|---|---|
| 4 | Unified entry point (`generate_candidates` → 2pin/Npin) | **DONE** | `topology.cpp:1550` / `:1563` / `:1130` |
| 4 / 6 | I/L/Z/U/UU 2-pin shapes | **DONE** | `add_l/z/u/uu_shapes` `:206/:382/:485/:543` |
| 4.1–4.2 | Single H/V trunk sweep + OOB + pass-through | **DONE** | `add_trunk_h/v` `:734/:918`; `pass_through_count` `topology.h:79` |
| 4.3 | MST + trunk+MST fallback | **DONE** | `add_mst_candidates` `:1298`, `add_trunk_mst_candidates` `:1400` |
| 4.3 | **MST feedthrough completion** (standalone MST self-connected; single busterm tap + SEG junctions) | **DONE** | `complete_relay_junctions` (single-tap, PR #43); `FEEDTHRU_RELAY` in `verify.cpp::check_topo` |
| 4.3 | trunk+MST completion (MST edge replaces a stub; avoid cycles) | **DONE** | `add_trunk_mst_candidates`: root MST at trunk-nearest stub-owner, drop child stubs, `complete_relay_junctions`; emit only `topology_is_clean_tree` (connected + acyclic) hybrids, else legacy/drop |
| 4.4 | **General multi-level trunks** (recursive, V-split, depth>1) | **PARTIAL** | only `add_multi_trunk_candidates`→`BITRUNK_H` `:1506` |
| 4.5 | Per-leaf **Z-stub / sub-trunk connectors** on a trunk | **MISSING** | trunk builders emit direct/L stubs only |
| 4.5 | **Candidate dedup** (`deduplicate`) | **MISSING** | no candidate-level dedup; `annotate_and_sort` `:634` |
| 15 | Multi-rect blocks + TEG bridge | **DONE** | `Busterm::rects` `topology.h:67`, `best_rect_for_h/v` `:669/:681` |
| — | Slide range (`perp_lo/hi`), `net_pull` | **DONE** | `conn_topology.cpp:210/:499`, `bind_nuts.cpp:53–60` |
| 10 | **Flexibility score** (`flex_score`/`adjusted_wl`/`kFlex`), flex-aware sort | **MISSING** | sort still by `estimated_wirelength`; 16 xfail in `test_topology_flexibility.py` |
| 10.7 | **Pull-balanced centroid** (`lo_pull`/`hi_pull`, `~CEN`) | **MISSING** | 10 xfail in `test_pull_preference.py` |
| 5 | **Feedthru** (`FeedthruConfig`, `add_feedthru`) | **MISSING** | `grep -ri feedthru src/` → none; 3 xfail in `test_feedthru.py` |

### 16.1 Naming reconciliation (doc ↔ code)

| This document | In the code |
|---|---|
| `GenerateTopologies(busterms)` | `TopologyGenerator::generate_candidates(src, dsts)` |
| `add_block(... rects ...)` | `add_block <name> rect …` → `Busterm::rects` |
| `deduplicate` (Phase 5) | *not present* |
| `adjusted_wl` sort key | `estimated_wirelength` (raw) |
| `feedthru_blocks` | *not present* (`pass_through_count` is the nearest geometric analogue) |

### 16.2 Prioritized backlog (set in iteration 1)

The empirical driver is the tc3a candidate explosion — see
[topology_tc3a_findings.md](topology_tc3a_findings.md). On `tc3a_flat` the 80 bundles
generate **2780 candidates (avg 35/bundle)**, of which **~91 % are straight-trunk
sweeps** (`TRUNK_H/V` ± `+MST` ± `OOB`) while genuinely distinct shapes (L/Z/U/I/MST)
are a small minority. Ordered next steps:

1. **Richer trunk shapes** *(NEXT)* — §4.4 general multi-level trunks and §4.5/§6
   per-leaf Z-stubs/sub-trunks. Replaces dozens of straight-trunk-through-block
   candidates with a few branching ones that actually drop off at each leaf (addresses
   the 70/80 pass-through bundles).
2. **Dedup + noise reduction** *(NEXT)* — §4.5 `deduplicate`, plus snapping trunk
   positions to a meaningful subset of Hanan lines and suppressing redundant `+MST`
   twins. Directly cuts the ~91 % trunk-sweep noise and the 11/80 geometric duplicates.
3. **Flexibility score** *(LATER)* — §10. The slide metric already exists; only the
   ranking consumer is missing.
4. **Pull-balanced centroid** *(LATER)* — §10.7.
5. **Feedthru** *(LATER)* — §5.

> **Inspection tooling:** the `dump_topologies [hint] [--problems]` CLI command
> (added in iteration 1) prints the per-bundle candidate table — type, wirelength,
> segment count, pass-through count, `min_slide`, selected/pinned marker — and flags
> duplicate/pinched/single-candidate/pass-through bundles plus an aggregate summary.
> Use it to re-measure after each backlog item lands.

---

## 17. Deferred Work & Follow-ups (post single-tap completion, 2026-06-23)

Captured after PR #43 merged the MST feedthrough completion **and** its single-tap
refinement: `complete_relay_junctions` now keeps a busterm tap on exactly **one**
incident stub per relay (the most slide-flexible one) and demotes every other
landing — plus both endpoints of each appended connector — to a `nullopt`
annotation, so `infer_connections` wires them as real **SEG** junctions (previously
they were BUSTERM-only, so NUTS/detailed-NUTS still saw a through-block feedthrough
and could slide the pieces apart). `connect()` also gained an off-line detour for
degree-3 same-row/column orthogonal landings (which used to collapse into a
zero-length leg + a collinear, non-inferrable join). Verified on STAIRCASE / PLUS /
GRID (`MST_HV` + `MST_VH`): single tap per block, one SEG-connected component, no
zero-length segments, no `check_topo` violations.

The known remainders, in priority order:

### Deferred by design

1. **Trunk+MST hybrid completion** — **DONE** (see
   [trunk_mst_and_feedthru_plan.md](trunk_mst_and_feedthru_plan.md) §1).
   `add_trunk_mst_candidates` now roots the branch MST at the trunk-nearest
   **stub-owning** block, drops every other branch block's trunk stub (the MST edge
   *replaces* it), and runs `complete_relay_junctions` on the resulting cycle-free
   trunk-rooted tree. A completed hybrid is emitted only if it verifies as a clean
   tree (`topology_is_clean_tree`: one SEG component **and** acyclic) — single-rect
   hybrids that cannot be cleanly completed (a stub collinear with an incident MST
   edge, or a pass-through crossing that re-closes a loop) are dropped; multi-rect /
   un-rootable cases keep the legacy (still `FEEDTHRU_RELAY`-flagged) form.
   `test_trunk_mst_completed_no_feedthru` pins the new invariant. Side effect: the
   completed hybrids have honest (often lower) wirelength, so they sort earlier in
   the WL-ordered candidate list — index-pinned flows (e.g. `dogleg2.buda`) were
   re-pinned accordingly.

2. **Feedthru as an opt-in option** *(planned — see
   [trunk_mst_and_feedthru_plan.md](trunk_mst_and_feedthru_plan.md) §2; design in
   §5 above)*. A genuine feedthru — a block that deliberately relays a bus across
   its interior via its own lower-level routing — is still unmodelled: no
   `FeedthruConfig`, no `Floorplan::feedthru_blocks`, no `add_feedthru`
   (`grep -ri feedthru src/` → only `pass_through_count`; 3 xfail in
   `test_feedthru.py`). Today every topology must be physically self-connected and
   `FEEDTHRU_RELAY` flags any block that is not. Feedthru turns that hard error into
   a per-block/per-layer opt-in.

### Exposed / observed, not yet resolved

3. **`tc3a_flat`: 40 unplaced detailed-NUTS bits.** Noted in PR #43 as a
   routing-quality interaction (honest, footprint-crossing MST topologies cut
   abstract-NUTS overlaps 8→2 but left one bundle with 40 unplaced bits) — measured
   **before** the single-tap fix. Re-run `flow/big_data_test/tc3a_flat.buda` through
   detailed NUTS now that connectors are real SEG junctions, then decide whether the
   residue is a detailed-NUTS packing issue to chase separately. Not a completion
   bug; connectivity still verifies clean ("no opens").

4. **Mid-tier viz failures (environmental).** `matplotlib.cm.get_cmap` was removed
   in matplotlib 3.9+; the mid-tier visualization tests fail on that
   (`buda_viz.py:2916`), unrelated to topology. A small compatibility shim
   (`matplotlib.colormaps[...]`) clears it.

5. **Standalone-MST cycle at high-degree relays** — **FIXED**. Surfaced by the
   acyclicity check added for trunk+MST: `complete_relay_junctions`, when chaining
   the landings of a **degree-≥4** relay (e.g. the centre block of the PLUS
   arrangement), laid one dogleg connector's return leg collinear on top of the next
   connector, an overlap that closed a redundant wire **loop** (`MST_HV`/`MST_VH` for
   PLUS were cyclic on `main`; PR #43's completion tests checked SEG-connectivity but
   not acyclicity). Fixed with a de-overlap pass in `complete_relay_junctions`: any
   connector segment collinear-contained within another segment carries no unique
   junction but creates the parallel path, so it is dropped (connectivity preserved
   by the covering segment). Tests: `test_completion_seg_connected_downstream` now
   also asserts acyclicity, plus `test_high_degree_relay_has_no_overlapping_connectors`.

### Open question (owner decision)

6. **Dogleg tap placement.** Single-tap currently taps the most-flexible **stub**
   in all relay cases. The original suggestion was to tap the *middle dogleg
   connector* segment; that was not adopted because a busterm on an along-face
   connector lets it slide into the block **interior** (wrong side), whereas a stub
   slides cleanly *along* its face. Revisit only if a configuration needs the
   connector to hold the tap.

### Optional refinements (low priority)

7. **Tap-selection metric.** "Most slide flexibility" uses a geometric proxy (the
   block face extent in the slide direction) rather than `ConnTopology`'s computed
   `perp_lo/hi` slide ranges (which are derived downstream). Exact enough in
   practice; tighten only if a misranked tap ever shows up.

8. **Detour offset robustness in `connect()`.** The degree-3 same-row/column detour
   uses a fixed `±2` off-line offset (`ym = a.p.y - 2`, `xm = a.p.x - 2`). Near
   coordinate 0 it can go slightly negative and is not chosen to avoid the block.
   Harmless for the abstract topology (all tests pass); a direction-aware offset
   (toward open space, magnitude ≥ `min_stub`) would be tidier.
