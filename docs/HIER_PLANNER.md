# Hierarchical Planner — Phase E Design

## 1. Goal

Run `CongestionPlanner` on the HBundle set produced by Phases C and D with
two new properties:

1. **Top-down ordering**: depth-0 bundles claim their routes before depth-1
   bundles see the congestion map, so the upper-level routing choice
   implicitly constrains lower levels.

2. **Constraint-first ordering within each depth level**: bundles with fewer
   topology candidates (less routing flexibility, "no waffle room") are
   solved first so they get first pick before the congestion map fills up.

---

## 2. Why Ordering Matters

The existing flat `CongestionPlanner` sorts bundles widest-first.  For
hierarchical designs this is insufficient:

| Situation | Risk without ordering |
|-----------|----------------------|
| depth-1 cross-block bundle routed before depth-0 | depth-1 may claim a path that conflicts with the parent-level topology, requiring expensive rework |
| 1-candidate cell-level bundle routed last | its only valid path may be blocked by earlier bundles |
| depth-1 intra-cell bundle has tight face constraint | only 1–2 L-shapes valid; must run first within its depth level |

---

## 3. Priority Encoding

A single `priority` field on `BundleWrapper` encodes both depth and constraint
tightness:

```
priority = -(level × 10 000 + n_candidates)
```

| Bundle | level | n_cands | priority |
|--------|-------|---------|---------|
| depth-0 cross-block, 7 options | 0 | 7 | −7 |
| depth-0 cross-block, 1 option  | 0 | 1 | −1 |
| depth-1 intra-cell, 5 options  | 1 | 5 | −10 005 |
| depth-1 cross-block, 7 options | 1 | 7 | −10 007 |

The planner sort key becomes `(priority DESC, width DESC)` — higher priority
(less-negative) is processed first.

---

## 4. Instance Expansion

Cell-level HBundles from Phase C carry candidates in **cell-local coordinates**
(origin at the parent instance's lower-left corner).  The planner operates on
the global floorplan with absolute coordinates.

Before calling the planner, each cell-level wrapper is expanded into one
wrapper per `HBundle.instances` entry.  Each replica has candidates
**transformed to absolute coordinates** by adding the instance's (x1, y1):

```
For each instance path in b.instances:
  parent = BDB.component(path)
  dx, dy = parent.x1, parent.y1
  new_candidates = [offset_topology(t, dx, dy) for t in template.candidates]
  new_wrapper.candidates = new_candidates
  new_wrapper.priority   = -(b.level * 10_000 + len(new_candidates))
```

`offset_topology(t, dx, dy)` creates a new `Topology` with every segment's
start/end Point shifted by (dx, dy).  All other fields (type, wirelength,
pass_through_count) are copied; `trunk_location` is left as-is (metadata only).

---

## 5. Algorithm — `run_planner hier [N]`

```
1. Guard: require open BDB and non-empty self.bundles.

2. Apply sidecar selections (_apply_selections): load the .json sidecar and
   pin any architect-overridden topology indices on the *original* bundles in
   self.bundles (pre-expansion). This ensures sidecar pins refer to the
   canonical pre-expansion bundle IDs that the user sees in dump_hbundles and
   visualize_topologies.

3. Expand: walk self.bundles and produce expanded_wrappers:
   - Cross-block bundle → keep as-is
   - Cell-level bundle with instances [i0, i1, …]:
     For each instance ik:
       new_w = clone of w with candidates offset by (ik.x1, ik.y1)
       new_w.priority = -(b.level * 10_000 + len(new_candidates))
     Append new_w to expanded_wrappers.
   Record the mapping in _hier_expansion_map:
     { original_bundle_id → [expanded_wrapper, …] }
   This map is used by select_topology to propagate manual pins after expansion.

4. For all other wrappers (cross-block):
   w.priority = -(b.level * 10_000 + len(w.candidates))

5. Build CongestionPlanner on self.fp (the global/depth-D floorplan that
   was populated with absolute-coord blocks via add_blocks_from_bdb).
   Apply _planner_params.

6. Call optimize_topologies(expanded_wrappers, N).
   The planner sorts by (priority DESC, width DESC) internally.

7. Replace self.bundles with expanded_wrappers.
   Subsequent run_nuts / visualize / check_connectivity use expanded_wrappers.
```

**`_hier_expansion_map`:** The map `{ original_bundle_id → [expanded_wrappers] }` is stored on the session after expansion. `select_topology` uses it to apply a pin to all per-instance wrappers that originated from the same template bundle. It also signals to `check_connectivity` and `dump_hbundles` that `run_planner hier` has been called.

---

## 6. Interaction with NUTS

`run_nuts` calls `NUTSEngine.solve(self.bundles, ...)`.  After `run_planner
hier`, `self.bundles` contains the expanded wrappers with absolute-coord
candidates and the planner-selected topology index.  NUTS processes them
identically to the flat-flow wrappers — no NUTS changes needed.

---

## 6b. `check_connectivity` — Post-Expansion Block Verification

After `run_planner hier`, `check_connectivity` performs an additional check
specific to hierarchical flows: it verifies that every `connected_block_name`
referenced in the selected topologies exists in the current floorplan
(`self.fp`). If any are missing, it prints:

```
  Warning: N block(s) referenced in topologies but not in floorplan: name1, name2, ...
  Hint: call 'add_blocks_from_bdb N skip' for all required depths.
```

This check is only active when `_hier_expansion_map` is non-empty (i.e. when
`run_planner hier` has been used). It catches the common error of importing
only depth-0 blocks with `add_blocks_from_bdb 0` when depth-1 cell-level
bundles also need `add_blocks_from_bdb 1 skip` — because their selected
topologies reference absolute paths such as `proc_i/pa_i` that do not exist
at depth 0.

---

## 7. Constraint Propagation (Phase E.2 — Future)

Phase E.1 (this implementation) relies solely on the shared congestion map to
propagate constraints top-down: depth-0 routes are applied first, increasing
band usage, and depth-1 candidates that overlap see higher cost.  This is
approximate — it doesn't enforce that depth-1 faces match depth-0 entry/exit
faces exactly.

Phase E.2 will add explicit face-matching: after depth-0 topology selection,
extract the entry/exit face of each depth-0 endpoint block and filter depth-1
cross-block candidates to only those that terminate within the chosen face.
This turns the approximate cost signal into a hard constraint.

---

## 8. Files Modified

| File | Change |
|------|--------|
| `src/congestion_planner.h` | Add `priority = 0.0` field to `BundleWrapper` |
| `src/congestion_planner.cpp` | Sort by `(priority DESC, width DESC)` |
| `src/bindings.cpp` | Expose `priority` |
| `src/buda_cli.py` | Add `run_planner hier`; helpers `_expand_hier_bundles`, `_offset_topology` |
| `test/tests/features/hier_planner.feature` | BDD scenarios |
| `test/tests/test_hier_planner.py` | Step defs + standalone tests |
