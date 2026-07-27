# Topology candidate generators: shared machinery and code reuse

*Scope:* the N-pin (bundle) candidate generators in `src/topology.cpp` — how
`add_trunk_mst_candidates` relates to the pure-trunk and pure-MST paths, what
`annotate_endpoints` does for all of them, and how `add_multi_trunk_candidates`
(the BITRUNK family) is built. Written as an anatomy + reuse audit, ahead of any
refactor.

All line numbers are against `src/topology.cpp` / `src/topology.h` as of this
writing (branch off `main` @ `ac9ad93`); treat them as signposts, not anchors.

---

## 0. Where the generators are called

`TopologyGenerator::generate_candidates` fills a single `std::vector<Topology>&
results` by running the shape generators in sequence, then a shared
post-emission pipeline (`topology.cpp:2917–2929`):

```cpp
for (auto& t : results) annotate_endpoints(t, blocks);        // 2917
for (auto& t : results) restore_face_graze_junctions(t);      // 2918
add_trunk_mst_candidates(blocks, results);                    // 2919
add_mst_candidates(blocks, results);                          // 2920
add_multi_trunk_candidates(pins, blocks, results);            // 2921
...
finalize_candidates(results, block_names);                    // 2929
```

Two things about this ordering matter for the reuse story:

1. **The trunk shapes are already in `results`** (emitted by the `add_trunk_h` /
   `add_trunk_v` loci loop above line 2917) and have already been through
   `annotate_endpoints` by the time `add_trunk_mst_candidates` runs. The hybrid
   therefore consumes *finished trunk topology objects*, not the trunk-gen code.
2. **`add_mst_candidates` runs after the hybrid.** The hybrid explicitly leans on
   this: for ≥4-block bundles it can *drop* a non-beneficial hybrid because "the
   plain trunk plus the standalone MST already cover the bundle"
   (`topology.cpp:3581–3587`), knowing that standalone MST is about to be added.

`finalize_candidates` (`topology.cpp:3895`) is the common tail: it runs
`annotate_seg_conns` once per candidate (deriving the SEG-junction records every
downstream `ConnTopology` build reads), then the slide-aware keepout cull, the
pinch gate, and the coverage fill. Every generator's output flows through it —
this is the single largest piece of genuinely shared machinery.

---

## 1. `annotate_endpoints` (`topology.cpp:1350–1398`)

### What it computes

Given a `Topology` whose `segments` are laid out geometrically, it fills
`topo.seg_busterms[i] = {first, second}` — for each segment `i`, which block
(if any) each of its two endpoints **taps** on a block face. A filled slot is an
electrical landing (a BUSTERM tap); a null slot means the endpoint is a free end
or a wire junction (a SEG join, inferred later by `ConnTopology`). It is the
bridge from raw `Segment` geometry to the connectivity model the planner, NUTS,
and `verify` all consume.

### The two-pass rule (why it exists)

For each endpoint `P` of a segment (with `other` = the segment's far endpoint),
`assign()` (`1384–1393`) tries two predicates **in priority order**:

1. **`abuts_rect(P, other, r)`** (`1367–1378`) — `P` lies on the face of `r`
   perpendicular to the segment's travel **and the body `P→other` points away
   from the block interior**. This is a block the segment docks against *from
   outside*.
2. **`on_face_rect(P, r)`** (`1379–1383`) — the weaker legacy rule: `P` merely
   lies on some face of `r`, regardless of which way the wire continues.

Pass 1 (abutment) is tried for *all* blocks before pass 2 (bare face-coincidence)
is tried for any. The priority is the fix from
[`big2_b25_abutment_tap_dnuts_2026-07.md`](big2_b25_abutment_tap_dnuts_2026-07.md):
a block the trunk passes **through** (its face happens to coincide with a trunk
endpoint) must not steal the face-tap from a *receiver* that genuinely abuts that
same endpoint from outside. Without the ordering, `on_face_rect` would match the
pass-through block first and mis-assign the tap, producing a DNUTS open.

Multi-rect blocks are handled by `rects_of` (`1363–1366`): the predicates run
against each physical rect (or `{orig_bbox, bbox}` for single-rect blocks), so a
tap lands on an actual face, never in a union-bbox interior.

### The critical caller-side contract: **not everyone calls it**

`annotate_endpoints` is *geometric* — it will fill any endpoint that lands on a
face. That is correct for stub-based shapes (L/Z/U/trunk stubs genuinely dock on
faces) but **wrong for trunk/branch spine endpoints**, which are junctions or
free ends that may *graze* a neighbour's face by coincidence. Filling those would
manufacture a spurious feedthru busterm — the exact corner-feedthru defect this
line of work removed. So the callers split into two disciplines:

- **Stub shapes and the completed hybrids** call it (`2917`, and after building
  MST/hybrid stubs at `3138`, `3554`, `3607`) — then `complete_relay_junctions`
  rewrites the relay taps that annotation got wrong.
- **The BITRUNK generators deliberately do NOT call it.** Instead they seed
  `seg_busterms` for their stub segments via `emit_tap_segment` and leave every
  trunk/branch/backbone entry explicitly null (`for (…) (void)t.seg_busterms[i];`
  at `3742`, `3872`), so `annotate_seg_conns` infers those joins as SEG rather
  than promoting a grazing endpoint to a busterm. The comments at `3733–3742` and
  `3861–3872` spell this out.

`restore_face_graze_junctions` (`2918`) is the complement for the *stub* path: it
clears graze-only taps at a shared face line so the stub↔spine junction is
derived rather than left as a false tap.

---

## 2. `add_trunk_mst_candidates` (`topology.cpp:3300–3667`) — the hybrid

### Reuse of the pure-trunk path: **strong, via the object**

The hybrid never re-runs trunk generation. It iterates the already-emitted
`TRUNK_H` / `TRUNK_V` topologies in `results` (`3328–3334`, skipping anything
already `+MST`) and, for each, does `Topology tree = trunk_topo;` (`3532`) —
copying the whole object: `segments`, `trunk_location`, `seg_busterms`, spine
geometry. It then:

- classifies each block as **spine** (straddles `trunk_location`, needs no stub)
  vs **branch** from the copied `trunk_location` (`3336–3346`);
- roots an MST at the trunk-nearest **stub-owning** branch block and **replaces**
  a non-root branch's trunk stub with a shorter MST parent edge only when the
  edge beats the stub (`3409–3458`), so the result stays a cycle-free
  **trunk-rooted tree**;
- drops the replaced stubs (`3537–3547`) and re-clips the spine to the surviving
  landings (`clip_spine_to_landings`, `3553`).

So the hybrid's identity is literally "a copied trunk candidate with some stubs
swapped for MST edges." Its type string is the trunk's with `+MST` spliced around
the `@locus` suffix (`3460–3466`).

### Reuse of the pure-MST path: **absent at the routine level** *(as audited — since fixed)*

> This subsection is the **pre-refactor** picture. Both duplications below have
> since been removed (byte-identical by construction, measured QoR-neutral) —
> see [§4.1](#41-status-both-mst-duplications-deduplicated-landed).

It does **not** call `add_mst_candidates`. The MST logic is duplicated in two
layers:

1. **Tree computation differs.** The hybrid calls the shared helper
   `compute_mst(nodes)` (`3370`; defined in `conn_topology.cpp:63`). But
   `add_mst_candidates` rolls its **own inline Kruskal** — a local `RawEdge`
   struct + sort + union-find (`3053–3073`) — and never calls `compute_mst`. Two
   independent MST implementations coexist.
2. **Edge realization is copy-pasted.** The hybrid's `realize_edges` lambda
   (`3471–3524`) and the standalone MST's per-edge loop (`3082–3132`) are
   near-verbatim twins:

   | Step | hybrid `realize_edges` | standalone `add_mst_candidates` |
   |---|---|---|
   | closest points across rect pairs | `closest_points` (`3479`) | `closest_block_points` → `closest_points` (`3091`) |
   | coincident faces → shared edge | `shared_edge_segment` (`3484`) | `shared_edge_segment` (`3096`) |
   | single-point projection → corner L | `corner_diagonal_L` (`3497`) | `corner_diagonal_L` (`3109`) |
   | straight V / straight H | `make_seg`, **min-stub-gated** (`3501–3506`) | `make_seg`, **NOT gated** (`3111–3114`) |
   | diagonal L + min-stub gate | `choose_edge_h_first` + `m_h`/`m_v` (`3509–3519`) | `choose_edge_h_first` + `m_h`/`m_v` (`3118–3130`) |
   | tag legs with `edge_id` | yes (`3486`,`3498`,`3521`) | `tag_edge` (`3085`,`3132`) |

   Same shape decisions and helpers — but two copies that can drift, and they are
   **not** already identical: for aligned rects closer than the min-stub floor the
   **hybrid rejects the whole candidate** at the straight leg (`3501–3506`), while
   **standalone MST emits the short straight segment** with no gate (`3111–3114`)
   — only its *diagonal* legs are min-stub-gated. So a shared realizer must
   **parameterize the straight-edge gate** (a `gate_straight` flag) to keep each
   caller's candidate pool byte-for-byte; unify the gate and the pools change.

### Genuinely shared helpers (both hybrid and standalone MST)

- **`complete_relay_junctions`** (`3555` / `3139`) — the hard part: wires the
  relay stubs so no silent feedthrough relay survives.
- **`annotate_endpoints`** on the raw stubs before completion (`3554` / `3138`).
- **Clean-topology gates** — the hybrid uses `topology_is_clean_tree`
  (`topology.cpp:3168`; must be connected **and acyclic**, because it adds a
  second path between blocks the trunk already reaches, so a leftover redundant
  path would close a real loop). Standalone MST uses the weaker
  `topology_is_connected`: its abstract edges form a tree, but the *realized*
  wire is not guaranteed acyclic — an incidental collinear overlap between two
  edges' legs adds a SEG connection `ConnTopology` infers, creating a cycle even
  though the chosen MST edges don't. That connected-but-cyclic case is still
  routable, so the standalone path **deliberately keeps it** (`topology.cpp:3150`)
  and only drops a genuinely *disconnected* result (a collinear butt-joint
  `ConnTopology` can't wire-join). Coverage checks (`connected_block_names` +
  span) are shared inside `topology_is_clean_tree` (`3193–3208`).
- Primitives: `closest_points`, `shared_edge_segment`, `corner_diagonal_L`,
  `make_seg`, `choose_edge_h_first`, `manhattan_nearest`, `get_min_stub_length`.

### Hybrid-only machinery (legitimate divergence)

Everything about the inherited spine has no standalone-MST analogue:

- trunk-rooted MST with selective stub replacement (`3409–3458`);
- the `<4-block` coverage fallback pool (`3319`, `3647–3666`): standalone MST
  bails below 4 blocks (`3017`), so for a 3-pin bundle the hybrid is the *only*
  MST-type coverage and a non-clean hybrid is stashed and emitted only if nothing
  clean was produced;
- the **redundant seed-trunk drop** (`seed_trunk_is_redundant`, `3567–3575`,
  `3616–3623`): if the MST edges already connect everything and the trunk is
  vestigial (or an OOB detour trunk dangles off the tree at one point), the hybrid
  is dropped with a counted `[TopoGen] dropped N redundant trunk+MST hybrid(s)`
  note (`3642–3645`) — see [`bus005_dangling_scan_2026-07.md`](bus005_dangling_scan_2026-07.md).

---

## 3. `add_multi_trunk_candidates` (`topology.cpp:3676–3893`) — the BITRUNK family

Two-level datapath trees for high-fan-out nets over regular placements. Needs
≥4 blocks (`3682`). Everything here is built on the **`Axis` abstraction**
(`topology.h:115–136`) — a small orientation helper exposing
`along/perp/along_center/along_face/mkseg/…` so one body of arithmetic emits both
orientations. This is the reuse pattern the BITRUNK code leans on hardest.

### 3a. Legacy `BITRUNK_H` / `BITRUNK_V` — `emit_legacy_bitrunk` (`3693–3744`)

Two parallel rung trunks at the 1st and 3rd perp-quartiles plus a central
perpendicular backbone joining them, with each leaf stubbing to the nearer rung.
Written **once against `Axis`** and emitted in both orientations:

- `emit_legacy_bitrunk(true)` → two H rungs + V backbone = **BITRUNK_H** (a *row*
  of receivers), byte-identical to the historical hard-coded shape, and
  **always-on** (`3745`).
- `emit_legacy_bitrunk(false)` → two V rungs + H backbone = **BITRUNK_V** (a
  *column* of receivers), the previously-missing mirror. **Opt-in** (`3755`,
  gated on `allow_multi_trunk_`) because it is a measured QoR net-negative
  on-by-default (comment `3747–3753`: unplaced +594, runtime +35%; the planner
  over-selects a realization-fragile shape).

Guards worth noting: `p_t1 == p_t2` (quartiles collapse, `3706`) and
`a_min == a_max` (no along-extent → zero-length rung wires NUTS can't place,
`3712–3716`) both bail. Leaf stubs go through **`emit_tap_segment`** (`3726`),
which appends the stub and records its busterm seed in one call — the shared
"add a segment that taps this block" primitive (`topology.h:240`). A stub shorter
than the min-stub floor aborts the whole candidate (`3729–3731`).

### 3b. Two-level `BITRUNK_HVH` / `BITRUNK_VHV` — `emit` (`3785–3887`)

A root spine (one orientation) feeds K perpendicular **branch** trunks, each
tapping a cluster of root-axis-aligned leaves; a leaf either stubs to its branch
or is a multi-tap **pass-through** when the branch runs down through its extent.
Also fully `Axis`-parameterized (`3789–3798`, root-axis vs perp-axis coordinate
lambdas all delegate to `Axis`).

Flow:

1. **`cluster(key, K)`** (`3759–3777`) — sort leaves by root-axis key, cut at the
   `K-1` largest gaps (the natural columns/rows of a datapath). `<2` clusters ⇒
   it's just a trunk, bail (`3803`).
2. For each cluster, a **branch trunk** spans the cluster's full perp extent and
   reaches the root perp line (`3820–3834`). A leaf whose root-axis range
   straddles the branch is a **pass-through** (no stub, inclusive straddle per
   audit C4-05, `3838–3844`); the rest get a leaf stub via `emit_tap_segment`
   (`3845–3848`), with the too-short-stub abort (`3846`).
3. **Root spine** spans the extreme branch positions, prepended at index 0 via
   **`prepend_segment`** (`3858`; `topology.h:254`) — a front-insert that re-keys
   `seg_busterms`/`seg_conns` so it can't silently mis-wire junctions.
4. Trunk/branch/backbone `seg_busterms` left explicitly null (`3872`);
   **`annotate_endpoints` deliberately NOT called** (`3861–3871`) for the same
   spurious-feedthru reason as §1.
5. Kept only if **`topology_is_clean_tree`** passes (`3874`) — the same gate the
   hybrid uses — then deduped against existing candidates by exact geometry
   (`same_geo`, `3876–3885`), since different K can yield the same tree.

`emit(true/false, K)` for `K ∈ {2,3}` (`3889–3892`) → both orientations at two
cluster counts.

### Reuse summary for BITRUNK

- **Shares:** `Axis` (write-once-both-orientations), `emit_tap_segment`,
  `prepend_segment`, `topology_is_clean_tree`, `get_min_stub_length`,
  `finalize_candidates` tail, and the *convention* of null-tap spines +
  no-`annotate_endpoints`.
- **Does not share:** its own clustering + branch-span logic (no analogue
  elsewhere); it does not use the MST helpers at all.

---

## 4. Reuse scorecard

> **Update (landed).** The two MST duplications this scorecard flagged are now
> fully deduplicated — see [§4.1](#41-status-both-mst-duplications-deduplicated-landed).
> The scorecard below is kept as the *pre-refactor* picture the audit started
> from; the current shared helpers are `realize_mst_edge` (edge geometry) and the
> multi-rect `compute_mst` (tree computation).

| Path | Reuses pure-trunk | Reuses pure-MST | Shared helpers |
|---|---|---|---|
| `add_trunk_mst_candidates` | **Yes** — copies each trunk `Topology` and mutates it (`3532`); no re-derivation | ~~No routine reuse~~ → **now shares `realize_mst_edge` + `compute_mst`** | `complete_relay_junctions`, `annotate_endpoints`, `topology_is_clean_tree`, edge primitives |
| `add_mst_candidates` | n/a | (is the pure path) | same edge primitives, `complete_relay_junctions`, `topology_is_connected` |
| `add_multi_trunk_candidates` | independent (own spine/cluster logic) | none | `Axis`, `emit_tap_segment`, `prepend_segment`, `topology_is_clean_tree` |
| all paths | — | — | `annotate_endpoints`/`restore_face_graze_junctions` (stub paths), `finalize_candidates` tail |

### 4.1 Status: both MST duplications deduplicated (landed)

Both duplications the audit identified have since been removed. Each is
**byte-identical by construction** (argued per step below) and was **measured
QoR-neutral** on the full corpus (`tools/qor_corpus.py --compare`: 0 better, 0
worse, 35/35 unchanged), with the geometry-checking fast test tier passing
unchanged.

> **What the corpus does and does not prove.** `qor_corpus.py --compare` diffs
> only three aggregate values per flow — `overlaps`, `unplaced`, `viol_bundles`.
> A `35/35 unchanged` result establishes **QoR-neutrality**, not byte identity:
> in principle a different MST edge or candidate pool could net out to the same
> three numbers. The byte-identity claim here rests on the **construction
> arguments** below (identical algorithm + identical inputs → identical output),
> for which the QoR-neutral corpus and the passing geometry tests are
> corroborating evidence, not the proof. A true byte-for-byte guard would diff
> serialized candidate/topology output across the corpus (not yet wired up).

**Step 1 — edge realization → `TopologyGenerator::realize_mst_edge`.** The
abutment / corner-L / straight / diagonal-L cascade (with `edge_id` tagging) is
now one private member called from both loops. Two flags reconcile the callers:

- `prefer_h_first` unifies the orientation argument — `strategy==0` (standalone)
  and `!is_h` (hybrid) both map to `corner_diagonal_L(…, prefer_h_first?0:1, …)`
  and `choose_edge_h_first(…, prefer_h_first)`.
- `gate_straight` preserves the one real divergence: the hybrid rejects a
  too-short **straight** leg, the standalone path does not (only its diagonals).

Rect *selection* stays in each caller (standalone picks the closest rect pair
across multi-rect blocks; the hybrid pre-selects the trunk-nearest rect), so the
helper takes two already-chosen rects.

**Step 2 — MST computation → multi-rect `compute_mst`.** The audit warned that
routing the standalone path through `compute_mst` **as-is** would change routes:
`add_mst_candidates` weighted each edge by the **minimum manhattan distance over
every physical rect pair** of two blocks, whereas `compute_mst` accepted only
**one `Rect` per node** — for a multi-rect bundle the two metrics can pick a
different MST. The safe fix — and the one that landed — was to **extend**
`compute_mst` with a multi-rect overload (`vector<pair<string, vector<Rect>>>`,
weight = min over rect pairs) and have the single-rect overload wrap-and-delegate
(a 1-rect node reduces to `manhattan_nearest`, so the `hybrid` and `trunk_mst`
callers are untouched). Because the Kruskal is otherwise identical (same edge
enumeration, sort, union-find), feeding it the same rects reproduces the
standalone tree exactly. `add_mst_candidates`' inline Kruskal is gone; both MST
computations now share this one implementation.

> The lesson worth keeping: the "behavior change" only existed for the *naive*
> unification (single-rect `compute_mst`). Extending the shared helper to carry
> the standalone's multi-rect weighting made it a true, measured 0-change dedup.

Both paths already shared the *hard* part (`complete_relay_junctions`); with the
edge geometry and the tree computation now shared too, the only per-caller code
left is the genuinely divergent machinery (§2's hybrid-only spine handling,
§3's BITRUNK clustering).

---

## See also

- [`convergent_bundling.md`](convergent_bundling.md) — fan-in tree generation
  (per-bit taper) built on the same MST/trunk machinery.
- [`trunk_mst_and_feedthru_plan.md`](trunk_mst_and_feedthru_plan.md) — the design
  history of the hybrid + relay completion.
- [`topology_tree_gen_design.md`](topology_tree_gen_design.md) — the BITRUNK
  two-level tree design and the `Axis` write-once-both-orientations refactor.
- [`big2_b25_abutment_tap_dnuts_2026-07.md`](big2_b25_abutment_tap_dnuts_2026-07.md)
  — why `annotate_endpoints` is two-pass (abutment before bare face-coincidence).
- [`single_source_topo_truth.md`](single_source_topo_truth.md) — why spines carry
  null taps and skip `annotate_endpoints`.
