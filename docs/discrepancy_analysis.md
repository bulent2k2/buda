# Discrepancy Analysis: Original Paper and Talk vs. BUDA Implementation

**Original documents:**
- `docs/origin/paper.md` — *"Assisted and Auto Bus Planning in Full-Chip Layout"* (Ekici, Basaran et al., Intel Galaxy tool)
- `docs/origin/talk.pdf` / `docs/origin/talk_contents.md` — 25-slide conference presentation of the same work

**Implementation under review:** `buda_system_v2/`

The talk is the presentation version of the paper and largely covers the same concepts, but adds concrete performance benchmarks, explicit flow diagrams, a detailed hierarchy-depth case study (Nehalem slides 17–23), and some design intent statements that have implications for the implementation. Discrepancies unique to the talk are marked **[Talk]**.

---

## 1. Paper Features Not Implemented in BUDA

### 1.1 Busterm Generation Is Manual / Implicit

**Paper says:** Busterms are automatically generated from a hierarchy depth (`HBLOCKS`) with four distinct generation modes:
- Rectangular block → coincides with block boundary
- Non-rectangular block → one busterm per edge, marked as a **Terminal Equivalence Group (TEG)**
- I/O bus → optional top-cell edge busterm (closest edge to other busterms)
- Net terminal clustering → multiple busterms per block from physical pin locations, clustered by layer, location, connectivity and TEG

**BUDA does:** Busterms are derived implicitly from block names in the netlist — each named block becomes a single busterm whose bbox is the block's full bounding box (or margin-shrunk version). There is no:
- Hierarchy depth selection (`set_depth()` exists in `Bundler` but is not applied to busterm derivation)
- Multi-busterm generation per block
- I/O bus / top-cell edge terminal generation
- Net terminal clustering

**Impact:** BUDA cannot model buses that connect to sub-regions of a block, multiple pin clusters on the same block, or top-level I/O boundaries.

---

### 1.2 Terminal Equivalence Groups (TEGs)

**Paper says:** Among busterms belonging to a TEG, only one needs to be connected. Two modes:
- `over-the-block`: topology segments anchoring to different busterms in a TEG are explicitly connected to each other
- `thru-the-block`: assumes routing exists inside the block; bus segments are left disconnected

**BUDA does:** No concept of TEG whatsoever. `Topology`, `Busterm`, `ConnTopology` have no equivalence group field or logic. Every block in the netlist gets exactly one busterm and must be connected by a segment.

**Impact:** BUDA cannot model non-rectangular blocks (e.g., L-shaped partitions common in full-chip design) where the bus connects to only one face of a multi-face block. It also cannot model pass-through routing inside blocks.

---

### 1.3 Topology Generation Algorithms: Mixed Steiner Trees Missing

**Paper says:** Two distinct topology generation algorithms:
1. **Mixed Steiner / spanning trees** — for flexibility, general multi-pin connectivity
2. **Single-trunk and stub based Steiner trees** — for low-bend, high-predictability topologies

The paper explicitly says the algorithms handle rectangular nodes with TEG and direction properties, and provide options for "minimum possible jogs vs. largest possible flexibility."

**BUDA does:** Implements only single-trunk/stub approach — L (2-seg), Z (3-seg, intermediate trunk), U (3-seg, out-of-bbox trunk), UU (double-detour), plus `MST_HV`/`MST_VH` multicast shapes in `TopologyGenerator::add_mst_candidates()`. However, the MST in BUDA is used only for **multicast** (1-driver, N-receivers) topology generation, not as a general alternative to the trunk+stub approach for 2-pin buses.

**Impact:** For 2-pin buses, BUDA never generates a topology from a minimum spanning tree or general Steiner tree — only L/Z/U shapes. This limits topology diversity on designs where trunk+stub shapes are geometrically suboptimal.

---

### 1.4 Topology Sorting: Flexibility Criterion Missing

**Paper says:** After generation, topologies are sorted by four criteria:
1. Number of jogs/vias
2. Wirelength
3. **Flexibility** (size of segment ranges)
4. Congestion on their path

**BUDA does:** `Topology` stores `estimated_wirelength` and `trunk_location`. The GlobalRouter optimizes for congestion + span cost (`kCong`, `kSpan`, `base_cost_non_top`). No explicit sorting by:
- Jog/via count — not tracked per topology candidate
- Flexibility (range size) — not computed or stored
- A ranked sort presented to the user for interactive selection

The GlobalRouter *selects* topologies rather than sorting them for the user to choose from.

**Impact:** The interactive "assisted bus planning" experience of viewing topologies ranked by flexibility or via count is not reproducible in BUDA. The user sees all candidates in the topology explorer but without a meaningful quality ranking beyond wirelength.

---

### 1.5 Layer Assignment via Wirelength Lookup Table

**Paper says:** Layer assignment can be done via:
- A pair of H and V layers (manual), or
- **A layer look-up table that maps intervals of wirelength to layer pairs** (e.g., short wires → M3/M4, long wires → M5/M6)

**BUDA does:** The GlobalRouter assigns layers through iterative congestion optimization. There is no wirelength-interval-to-layer-pair mapping. The closest analog is `def_layer`'s `span_min`/`span_max` and `kSpan`, which penalize segments outside their preferred span range, but this is a soft cost, not a hard lookup table.

**Impact:** The paper's "short buses on lower metals, long buses on upper metals" design intent cannot be expressed directly in BUDA. Users must rely on the planner to infer it through span costs.

---

### 1.6 Congestion Query and Remediation Commands

**Paper says:** After dilution-based track sharing, designers get:
- Commands to **visualize congestion** (highlight congested areas)
- **Query which buses contribute to congestion** in a given area
- Reports on **number of additional tracks needed** to accommodate congested buses
- Commands to highlight contributing buses with dimmed colors in context

**BUDA does:** The visualizer (`buda_viz.py`) provides click-to-highlight per bundle and a `draw_nuts_tracks()` that shows interval bands. The `_write_nuts_log()` writes an overlap report with per-layer counts and exact overlap geometry. However, there is no:
- "How many additional tracks are needed here?" query
- "Which bundles cross this region and are they congested?" query
- Congestion heatmap with thresholded overlay (the `congestion_heatmap_logic.md` design doc exists but the feature is not yet implemented in the visualizer)

**Impact:** The interactive congestion-driven design loop described in the paper — where designers resize blocks or change topologies in response to congestion queries — is not fully supported.

---

### 1.7 Conversion to Real Layout (UdmWire / UdmBus)

**Paper says:** Once a topology is chosen, symbolic wires are converted to real layout wires (`UdmWire`) bundled in a physical bus (`UdmBus`). Range and pull information are preserved as bus properties for subsequent gridding.

**BUDA does:** BUDA ends at track assignment (abstract NUTS / detailed NUTS) and produces coordinate data in Python objects and visualizations. There is no layout database, no wire primitives, no GDS/DEF export. BUDA cannot produce tape-out-ready output.

**Impact:** This is the entire output side of the Galaxy flow. BUDA is a planning and verification tool only — it cannot feed a router or an extraction engine directly.

---

### 1.8 Obstacle-Aware Track Sharing as a Full Standalone Flow

**Paper/Talk say:** "Obstacle-aware track sharing" is a heavy-duty standalone flow for mid-to-late design that legalizes topologies against existing obstacles (power grid, KORs, pre-routes), distinct from the dilution-based flow. The talk (Slide 9) gives its speed as **1 bus per second** and (Slide 11) lists three use cases: planning at multiple hierarchy levels, planning when power/pre-routes exist, and late ECOs.

**BUDA does:** The two flows are staged sequentially: abstract NUTS (dilution-based) → detailed NUTS (snaps to routing grid signal tracks). Detailed NUTS in BUDA is always run as stage 9 after abstract NUTS — it cannot replace abstract NUTS as an alternative. Additionally, BUDA's obstacle-aware stage works against a `RoutingGridStack` (track patterns), not against actual pre-placed wires read from a layout database.

**Impact:** Cannot run obstacle-aware planning independently of the abstract stage. Cannot read real placed obstacles from a design database. The "1 bus/second" speed target is unverified — BUDA has no benchmark suite.

---

### 1.9 Hierarchy-Depth Planning [Talk]

**Talk says (Slides 17–23, Nehalem case study):** The same design is planned at successive hierarchy depths (Depth=0 through Depth=3). At each depth a different set of blocks participates. The IEXEC unit has two instances of ICLUST; ICLUST itself has 11 blocks and 4 levels of hierarchy. Planning proceeds depth-by-depth:
- Depth=0: only the top-level partition outlines are busterms
- Depth=1/2/3: progressively finer sub-blocks are used as busterms
- At Depth=3 for IEXEC, FUBs at depth=4 are still not shown (they would be the next level)

This depth-parameterised planning loop is the key mechanism for scaling to full-chip designs without needing bottom-up layout.

**BUDA does:** `Bundler::set_depth()` accepts an integer but `depth_` is never read inside `generate_signature()` (`bundler.cpp:20`). There is no mechanism in `Floorplan`, `TopologyGenerator`, or `BudaSession` that uses a depth parameter to filter which blocks participate in busterm generation or topology generation.

**Impact:** BUDA cannot reproduce the Nehalem flow at all. Every run uses all blocks in the floorplan as busterms at full resolution — there is no way to plan "only down to depth 2 today, add depth 3 tomorrow."

---

### 1.10 Multi-Pass Hierarchical Obstacle Flow [Talk]

**Talk says (Slide 23):** The Nehalem planning sequence is explicitly two-pass:
1. Plan all buses **local to ICLUST** first (using the ICLUST blocks as busterms)
2. Plan all **IEXEC buses** second, treating the already-placed ICLUST buses as physical obstacles

This is the primary use case for the obstacle-aware flow: buses planned at a lower hierarchy level become hard blockages for buses planned at a higher level.

**BUDA does:** All bundles are planned in a single `run_planner` / `run_nuts` pass. There is no way to take a previously computed `NUTSResult` and promote its track segments to obstacles for a second planning pass. `KeepoutZone` is the only obstacle primitive, but it is not auto-populated from prior NUTS results.

**Impact:** The most practically important use case for the obstacle-aware flow — bottom-up hierarchical planning where lower-level bus results feed back as constraints to upper-level planning — is entirely absent from BUDA.

---

### 1.11 "Add Wires" as a Distinct Interactive Step [Talk]

**Talk says (Slide 5, flowchart):** The assisted bus planning flow has an explicit user-interactive loop:
```
Bundling → Bus-term Generation → Bus Topology Generation
  → [user selects topology]
  → Add wires
  → [user approves: OK?]
    yes → Gridding → physical bus
    no  → back to topology selection
```
"Add wires" is a separate named step between topology selection and gridding, where symbolic wire positions are placed within ranges (driven by the pull/wirelength-minimization logic described in Figure 5 of the paper).

**BUDA does:** There is no "Add wires" step. After topology selection (via `visualize_topologies` or the optimizer), the flow jumps directly to `run_nuts` which performs track assignment. The wire addition is embedded inside NUTS — there is no stage where the user sees wires placed in ranges before approving them.

**Impact:** The iterative, interactive approval loop that Galaxy supports is collapsed into a single automated command in BUDA. A designer cannot preview "symbolic wires at nominal positions before legalization" as a distinct artifact.

---

### 1.12 Topology Has No Exact Coordinates [Talk]

**Talk says (Slide 4):** *"Topology is not exact layout. But, it is close.."* and (Slide 6): *"No exact layout or coords. No wires! Very fast!"* The intent is that a topology is a routing *intent* described by segment orientations and ranges, not a set of precise wire coordinates.

**BUDA does:** `Topology.segments` is a `vector<Segment>` where each `Segment` has `Point start, Point end` — integer (x, y) coordinates. Every topology in BUDA carries exact endpoint coordinates from the moment it is generated. The `Segment` struct has no range representation separate from its endpoint positions; range (`interval_lo/hi`) is derived later from the Hanan grid cell during NUTS extraction.

**Impact:** BUDA topologies carry coordinates earlier in the pipeline than the original design intends. The paper/talk model — topology as abstract intent, coordinates added only at track assignment — is not maintained. This makes the topology explorer show exact wires rather than range bands, which can be misleading if segments are later repositioned significantly by NUTS.

---

### 1.13 Performance Benchmarks [Talk]

**Talk says (Slides 3, 6, 9):** Explicit throughput targets:
- **~5,000 topologies per minute** (topology generation)
- **25,000 nets per minute** (dilution-based track sharing)
- **1 bus per second** (obstacle-aware track sharing)

**BUDA does:** No performance benchmarks are documented or tested anywhere in the repository. The test suite (`test/tests/`) covers correctness only, not throughput.

**Impact:** It is unknown whether BUDA meets, exceeds, or falls short of these targets. For a 6,000-net flat design like Manzano (~300 buses), the dilution flow should complete in under 1 second if the targets hold — this is plausible given the sweep-line algorithm complexity, but unvalidated.

---

## 2. BUDA Extensions Not in the Paper

These are features implemented in BUDA that go beyond what the original paper describes:

### 2.1 Keepout Zones (KeepoutZone)
`topology.h:70` — Layer-specific keepout rectangles that the topology generator and visualizer respect. The paper mentions KORs only as "layout obstacles" with no structural detail.

### 2.2 Corner Margins (BlockCornerMargin)
`topology.h:58` — Per-block and global margin that shrinks the busterm bbox before topology generation, keeping routes away from block corners. Not described in the paper.

### 2.3 Minimum Stub Length (MinStubLength)
`topology.h:64` — Per-direction and per-layer minimum stub length constraints. Not described in the paper.

### 2.4 Multi-Rect Blocks
`Floorplan::add_block_rects()` — Blocks with multiple component rectangles; topology generator picks the best-fit rect. Not described in the paper.

### 2.5 Preferred-Fit Placement
`nuts.cpp:363` — Assigns track positions closest to a "preferred" coordinate (median busterm position) rather than lowest-valid (first-fit). The paper's assisted bus planning describes pulling wires to minimize wirelength (Figure 5), but the NUTS paper (cited as Ekici, Basaran, Keskinocak 2009) is the algorithmic source, not the Galaxy paper.

### 2.6 Span-Adjustment via ConnTopology
`nuts.cpp:187` — After NUTS placement, segment span endpoints are adjusted to align with the actual placed track positions of connected segments. This closes the geometric loop between placement and topology shape. Not described in the paper.

### 2.7 Post-NUTS Stub Layer Reassignment
CLI: `run_planner post_nuts` — Short/long stubs on V or H layers are moved to cheaper (non-TOP) layers after NUTS. Not described in the paper.

### 2.8 Segment-Level Layer Pinning
`BundleWrapper::pinned_seg_layers` — Individual segments within a topology can have their layer pinned independently. The paper only describes H/V layer pair assignment per bus.

### 2.9 Sidecar JSON Topology Selections
`.json` sidecar files — Topology and layer selections persist across runs via JSON, identified by bundle hint + topology type + wirelength. Not in the paper (the paper's GUI-driven selection flow is in-tool only).

### 2.10 Detailed NUTS Bit Ordering and Timing-Critical Mode
`detailed_nuts.h:19` — `bit_order` (LO_HI/HI_LO) and `timing_critical` (requires contiguous signal tracks with equal spacing for RC uniformity). The paper's gridding step just snaps to nearest tracks in range order with no bit-level timing control.

### 2.11 Band-Subdivided Congestion Cuts
`global_router.h:7` — `GlobalCut` subdivides each Hanan cutline into perpendicular bands with independent capacity and usage. The paper's "Cutline module" describes scalar cutline congestion values, not banded capacity.

---

## 3. Implementation Differences (Same Concept, Different Approach)

### 3.1 Dilution Formula

**Paper:** "In any large enough region, no more routing tracks are used than are available." Specified as "reserve X% per layer." Uses abstract dilution without formal formula.

**BUDA:** `effective_width = raw_width × (100.0 / (100.0 − overhead_percent))` — `global_router.cpp:15`. Additionally, the `RoutingGrid`/`TrackPattern` provide an explicit `dilution_factor = unit_pitch / signal_width_sum`, computed from the physical track pattern. Two dilution models coexist: the overhead-percentage model (GlobalRouter) and the track-density model (RoutingGridStack).

**Discrepancy:** The paper uses a single unified dilution model. BUDA has two: a fast percentage-based model for abstract planning and a physics-based model derived from track patterns for detailed planning. These two can produce different effective widths for the same bus on the same layer if both are active.

---

### 3.2 Bundling Strategies

**Paper:** Bundles by "similar signal name or similar connectivity." No formal taxonomy.

**BUDA:** Formalizes as `STRICT` (driver instance + sorted receiver instances) and `CONVERGENT` (sorted receivers only). The `set_depth()` method exists but does not act on hierarchy depth — it stores an integer but the `generate_signature()` function in `bundler.cpp:20` does not use the `depth_` field.

**Discrepancy:** The `depth_` field is dead code — it has no effect on bundling. The paper's hierarchy-depth concept (which blocks participate at depth N) is not implemented despite the API suggesting it is.

---

### 3.3 Congestion Analysis: Scalar Cutlines vs. Banded Cuts

**Paper:** "Cutline module computes the congestion on each natural cutline on the floorplan. Then each topology is assigned a fitness value based on the congestion it sees on its segments."

**BUDA:** `GlobalRouter::build_congestion_map()` creates `GlobalCut` objects at Hanan grid midpoints (not at block edges themselves), each with `band_cap` and `band_usage` vectors. Capacity is computed as available length within the Hanan cell band, adjusted for keepouts and dilution.

**Discrepancy:** The paper's cutlines are at block edges; BUDA's cuts are at Hanan grid midpoints (midpoints between adjacent grid lines). For designs where congestion concentrates exactly at block edges, this off-by-half-cell placement could mislocate the congested cut.

---

### 3.4 Layer Assignment: Manual vs. Automated

**Paper:** Layer assignment is driven by the designer specifying H/V layer pairs or a lookup table, with optional congestion-based *sorting* of pre-generated topology candidates.

**BUDA:** Layer assignment is fully automated via `GlobalRouter::optimize_topologies()` — an iterative optimizer that assigns both topology and layer simultaneously to minimize `kCong × congestion_cost + kSpan × span_cost + base_cost_non_top`. The designer has no explicit "assign these layers" command separate from the optimizer.

**Discrepancy:** BUDA eliminates the manual layer assignment step. While more automated, this means the designer cannot override with a simple H=M4/V=M5 assignment without running the full optimizer. (Pinning via sidecar JSON partially addresses this for topologies but not for layers specifically.)

---

### 3.5 Topology Type Naming

**Paper:** Uses generic descriptions ("L-shape with two bends," "Z-shape," "U-shape for over-obstacle routing"). No named taxonomy.

**BUDA:** Formally named topology types: `L_HV`, `L_VH`, `Z_trunk_x`, `Z_trunk_y`, `U_top`, `U_bot`, `U_left`, `U_right`, `UU_VHV`, `UU_HVH`, `MST_HV`, `MST_VH`. These names are BUDA-specific and do not map to any standardized taxonomy.

---

### 3.6 Gridding vs. Detailed NUTS

**Paper:** "Gridding" is a step after layout conversion (UdmWire/UdmBus) that snaps physical wires to routing tracks, using range and pull information to preserve connectivity. It explicitly "does not change the relative ordering of bus segments."

**BUDA:** Detailed NUTS (Stage 9) operates on abstract `BusSegment` objects (before any layout conversion) and snaps to signal tracks in `RoutingGridStack`. It enforces LO_HI or HI_LO bit ordering and contiguous-track selection for timing-critical buses.

**Discrepancy:** Relative ordering is enforced differently. The paper's gridder works post-layout and preserves physical ordering from track sharing. BUDA's detailed NUTS works pre-layout and enforces bit order semantically via `bit_order`. If abstract NUTS places bus A below bus B in a channel, detailed NUTS does not guarantee their NetSegments maintain that relative ordering — each bus is expanded independently.

---

## 4. Algorithm Fidelity: NUTS

The NUTS algorithm referenced in `nuts.h:43` — "Ekici, Basaran & Keskinocak 2009" — is a separate publication from the Galaxy paper. The Galaxy paper says track sharing details "will be presented elsewhere." BUDA's NUTS is an implementation of that "elsewhere" paper.

### Fidelity Issues:

**4.1 Event ordering tie-breaking:** BUDA's sweep sorts events by `(pos, type)` with type descending, meaning END events at the same position as START events are processed *after* the START. This means a segment whose span_hi equals another segment's span_lo will be in the active set during the later segment's placement — potentially creating a phantom conflict. The paper would likely process END before START at the same coordinate (the standard sweep convention).

**4.2 Fallback to interval center:** When first/preferred-fit returns -1 (infeasible), BUDA places the segment at `(interval_lo + interval_hi) / 2` and counts it as a violation. The paper's algorithm description in the 2009 work likely has a different fallback strategy (e.g., best-effort force-place at nearest feasible position). The interval center is not necessarily the least-bad placement.

**4.3 First-fit is replaced by preferred-fit everywhere:** BUDA's `solve_layer()` always calls `preferred_fit()` — `first_fit()` is a dead function path in the current code (it exists but is never called by the solve path). The NUTS paper's core algorithm is first-fit; preferred-fit is a BUDA extension to minimize wirelength via pull.

---

## 5. Terminology Mapping

| Paper term | BUDA equivalent | Notes |
|---|---|---|
| Bus | Bundle | BUDA uses "bundle" for the logical grouping |
| Busterm | `Busterm` (struct) | Implemented, but generation is manual/simplified |
| Topo / bus topology | `Topology` | ✓ Matches |
| Segment range [ymin, ymax] | `interval_lo / interval_hi` | ✓ Matches semantically |
| Anchor | `SegConn::BUSTERM` | ✓ Implemented in ConnTopology |
| Connection | `SegConn::SEG` | ✓ Implemented in ConnTopology |
| Floating segment | Segment with no BUSTERM conns | Implicit, not a first-class concept |
| TEG | Not implemented | Missing |
| Over-the-block | Not implemented | Missing |
| Thru-the-block | `pass_through_count` in Topology | Partial — tracks count, not connectivity |
| Cutline | `GlobalCut` | Extended to banded capacity model |
| Dilution | `layer_dilution_factors_` + `dilution_factor()` | Two models coexist |
| Track sharing | Abstract NUTS (Stage 4) | ✓ Matches conceptually |
| Gridding | Detailed NUTS (Stage 9) | Different abstraction level |
| UdmWire / UdmBus | Not implemented | No layout output |
| Pull | `pull_map` in NUTS | ✓ Matches Figure 5 intent |
| HBLOCKS | Not implemented | Hierarchy depth bundling absent |
| Flexibility | Not tracked | Missing metric |
| Depth=N planning [Talk] | Not implemented | No hierarchy depth infrastructure |
| GRBUS [Talk] | Detailed NUTS (Stage 9) | BUDA analog; not a standalone named tool |
| DRV (Design Rule Violation) | `num_violations` + `num_overlaps` | ✓ Tracked; not called DRV in BUDA |
| 25k nets/min [Talk] | Unverified | No throughput benchmarks |

---

## 6. Summary Table

| Area | Paper / Talk | BUDA | Gap |
|---|---|---|---|
| Bundling | Name + connectivity similarity | STRICT / CONVERGENT strategies | Simplified taxonomy; depth param is dead code |
| Busterm generation | 4 automatic modes + manual | Implicit from block names | No TEG, no I/O bus, no multi-busterm per block |
| TEG support | Full (over-block / thru-block) | None | Not implemented |
| Topology algorithms | Mixed Steiner + trunk/stub | Trunk/stub (L/Z/U/UU) + MST for multicast only | Mixed Steiner for 2-pin buses absent |
| Topology coordinates | Ranges only — no exact coords [Talk] | Exact integer Point endpoints | Coordinates added too early in pipeline |
| "Add wires" step | Explicit interactive step [Talk] | Embedded in NUTS, not exposed | Interactive approval loop absent |
| Topology sorting | 4 criteria incl. flexibility | Congestion + span cost only | Flexibility and jog-count missing |
| Layer assignment | Manual pair / lookup table | Automated optimizer | No manual override without optimizer |
| Track sharing (fast) | Dilution-based; 25k nets/min [Talk] | Abstract NUTS with overhead% | ✓ Implemented; throughput unverified |
| Track sharing (heavy) | Obstacle-aware (standalone); 1 bus/sec [Talk] | Detailed NUTS (sequential only) | Cannot run standalone; pre-placed wire obstacles not read |
| Hierarchy-depth planning | Depth=0..N, per-depth busterm sets [Talk] | None (dead `depth_` code) | Entirely absent |
| Multi-pass hierarchy | ICLUST first, then IEXEC with ICLUST as obstacles [Talk] | Single pass only | Entirely absent |
| Congestion queries | Rich interactive queries | Overlap log + visualizer bands | "How many tracks needed" query absent |
| Layout output | UdmWire / UdmBus | None | No layout export |
| Gridding | Post-layout track snapping | Detailed NUTS (pre-layout) | Different abstraction; relative order not guaranteed |
| Keepout zones | KORs (mentioned only) | `KeepoutZone` with layer filtering | BUDA more detailed |
| Corner margins | Not described | Full per-block and global margins | BUDA extension |
| Bit ordering | Not described | LO_HI / HI_LO + timing-critical | BUDA extension |
| Span preferences | Not described | `span_min` / `span_max` per layer | BUDA extension |
| Performance benchmarks | 5k topos/min, 25k nets/min, 1 bus/sec [Talk] | None measured | Unvalidated |
