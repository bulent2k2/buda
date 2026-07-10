# Bottom-Up Template Planning — Design Plan

Status: **PROPOSAL** (draft for review — open questions at the end).
Scope: a new hierarchical-flow mode where the user marks selected cell
templates as **bottom-up**: their cell-local interconnect is planned, NUTSed,
and (track-phase permitting) detailed-NUTSed **once per template**, and the
result is copied to every instance. The copied routing becomes **keepouts**
that higher-level bundles see and detour.

Companion review: this plan was preceded by a cross-check of
[HIER_TOPOLOGY.md](../HIER_TOPOLOGY.md) against the code; the stale-doc items
and functional opens found there are listed in §1 because several of them are
prerequisites for this work.

---

## 1. Current state and the gaps this plan must close

The hier flow today (see [HIER_PLANNER.md](../HIER_PLANNER.md)) is
**top-down**: `run_planner hier` expands each cell-level HBundle template into
per-instance wrappers (`HierMixin._expand_hier_bundles`,
`src/buda_session/hier.py`) and plans depth-0 first, with unplanned cell-local
bundles parking a soft *demand reservation* on TOP bands inside their instance
bbox. Verified gaps relative to what bottom-up needs:

| # | Gap | Where |
|---|-----|-------|
| G1 | **No shared decision across instances.** `optimize_topologies` scores each expanded instance wrapper independently; only explicit pinning (`topology_pinned` + `pinned_seg_layers`, propagated at expansion, `hier.py:745-748`) forces a shared candidate index, and layer assignment is always per-wrapper. | `src/buda_cmds/planner_cmds.py:112-124`, `src/congestion_planner.cpp` |
| G2 | **Instance transform is translation-only.** `offset_topology(t, dx, dy, inst_name)` shifts and name-qualifies; `component.orient` (which the BDB *does* carry, incl. `rotate_comp`/`flip_comp` composition) is never consulted. A rotated/mirrored instance would receive geometrically wrong copies. | `src/topology.cpp:30-75`, `hier.py:709-737` |
| G3 | **Abstract NUTS enforces keepouts on non-TOP layers only** (`NUTSEngine::low_keepouts` filters `!is_top`). A keepout on a TOP layer — exactly where higher-level bundles route — is invisible to the abstract solve (planner band capacity and DNUTS do honor it). | `src/nuts.cpp:1019-1026` |
| G4 | **No cross-instance track-phase check.** Track centres are a pure function of the layer's absolute `(origin, unit_pitch, slots)` plus absolute-rect overrides/keepouts; nothing compares what two translated instance windows actually see. | `src/routing_grid.cpp:50-143` |
| G5 | **No template-level routing persistence.** Routing is persisted per expanded instance (`_add_expanded_bundle` stores only the selected topology per instance); the `cell` table has no attributes for flags like *bottom-up*, and `BDB::_set_meta` is not bound to Python. | `src/buda_session/persist.py`, `src/bdb.cpp`, `src/bind_db.cpp:385` |
| G6 | **No API to inject fixed occupancy into NUTS** other than Floorplan `KeepoutZone`s (which inherit G3). The planner has `inject_band_demand` but that is soft demand, not a hard blockage. | `src/nuts.cpp:1237-1255`, `src/congestion_planner.h:212-214` |

Stale-doc items in HIER_TOPOLOGY.md (to fix in a doc-only commit alongside
this work): file map points at monolithic `buda_cli.py` (now
`buda_cmds/topologies_cmds.py` + `buda_session/hier.py`); the cross-level case
uses an endpoint-only floorplan, not the depth-D floorplan; the floorplan
cache keys are `('cell', parent)` / `('depth', d)`, not
`(depth, cell_context, instance)`; the §6 priority rule is implemented
(`-(level·10000 + n_candidates)` + width tie-break in C++), not pending; and
`multi_trunk`, BDB candidate persistence, the coverage gate, sidecar
re-application, and the `BIDIR:` reason form are undocumented.

---

## 2. User-visible flow

```buda
open_bdb design.bdb
...
set_bottom_up proc_cell            # mark template(s); 'off' to unmark
run_hier_bundler
generate_hier_topologies           # unchanged — templates already generate once

run_planner hier                   # (a) bottom-up cells: local plan once, copy to instances,
                                   #     commit before higher levels plan (they detour)
run_nuts                           # (b) bottom-up cells: local NUTS once, copy tracks
                                   #     (+dx,+dy) per instance; copies become keepouts
check_template_tracks              # (c) verify every instance window sees the same
                                   #     signal tracks (per layer used by the cell)
run_detailed_nuts                  # aligned: local DNUTS once + copy per instance
                                   # misaligned: stop (default) or per-instance solve
```

- `set_bottom_up <cell> [on|off]` — persisted in the BDB so `load_pipeline`
  and the Floorplanner see it.
- `check_template_tracks [on_mismatch stop|independent]` — report-style
  command (like `check_connectivity`); its verdict is cached on the session
  and consumed by `run_detailed_nuts`. If the user never runs it,
  `run_detailed_nuts` runs it implicitly for bottom-up cells (fail-safe).
- All existing commands keep their semantics for unmarked cells; a design
  with no `set_bottom_up` behaves exactly as today.

---

## 3. Stage (a) — Planner: solve the template locally, reuse everywhere

**Where:** `run_planner hier` path (`planner_cmds.py` + `HierMixin` +
`CongestionPlanner`).

1. **Local template solve.** Before the global planner loop, for each
   bottom-up cell (processed deepest-first so nested bottom-up cells resolve
   before their parents): build the **cell-local floorplan**
   (`_build_cell_local_floorplan`, already exists) and run a *dedicated*
   `CongestionPlanner` over only that cell's cell-local template HBundles, in
   cell-local coordinates. This is a genuine local solve — the template's
   candidate choice and per-segment layer assignment are decided by
   intra-cell congestion only, deterministically, independent of any
   instance's surroundings.
2. **Broadcast.** Write the decision onto the template wrapper as a pin:
   `topology_pinned = true`, `selected_topology_index`, `pinned_seg_layers`.
   The existing expansion already propagates all three to every instance
   wrapper (`hier.py:745-748`) and `offset_topology` preserves candidate
   ordering — so instance copies are exact translates with identical layers.
   **This closes G1 with machinery that already exists**; the new code is the
   local solve + pin, not a new sharing mechanism.
3. **Commit-first ordering.** In the global pass, bottom-up instance wrappers
   are planned first (their "planning" is now just charging the pinned
   assignment into the band model — they are slide-checked but not
   re-decided), replacing their soft demand reservation with real committed
   usage. Higher-level bundles then plan against that usage and detour.
   Implementation: a `hier.locked` flag on `BundleHierMeta`; priority sort
   puts locked wrappers first; the ladder's rip-up, `negotiate_congestion`'s
   `replan_bundle`, and `ripup_reroute` all skip `locked` wrappers as victims
   *and* as movers.
4. **Overflow at a locked wrapper** (its pinned route lands on a band a
   sibling instance's context can't afford) is reported as a `WARNING` with
   the instance path — it is the price of uniformity, and the
   `check`/fallback story (§5) is where the user chooses to break uniformity.

## 4. Stage (b) — NUTS: solve locally, copy, keep out

**Where:** `run_nuts` path (`nuts_cmds.py` + `NutsFlowMixin` + `NUTSEngine`).

1. **Local solve.** For each bottom-up cell: run a `NUTSEngine` over the
   cell-local floorplan with only that cell's template bundles (cell-local
   keepouts included). Result: `track_position` + adjusted spans per template
   segment, in cell coordinates.
2. **Copy.** For each instance, translate every solved segment by
   `(dx, dy) = (parent.x1, parent.y1)` and insert it into the global
   `NUTSResult` as a **pre-placed, fixed** `TrackSegment` for that instance's
   wrapper (positions are continuous at this stage, so translation is always
   legal — track-phase legality is deferred to stage (c)).
3. **Blockage for higher levels.** Each copied segment is registered as
   fixed occupancy for the remaining (higher-level) NUTS solve. Two pieces:
   - **New `NUTSEngine::add_fixed_segments(...)`** (closes G6): fixed
     segments enter each `LayerSolver`'s `occupied` list exactly like keepout
     intervals do today, on **any** layer, and are immovable in repack.
   - **Floorplan/grid keepout mirror:** each copied segment also becomes a
     per-layer `KeepoutZone` (span × occupied width) via the existing
     `add_keepout_zone` + `RoutingGrid.add_keepout` path, so the **planner**
     (band capacity — already all-layer) and **DNUTS** (signal-track
     filtering + `cull_keepout_crossers`) see the same blockage with zero new
     code in those stages. These zones are tagged as derived (auto-cleared
     and regenerated on re-run, never persisted as user keepouts).
   - **Fix G3** so the mirror also protects TOP layers in any *subsequent*
     abstract NUTS pass (`run_nuts_on_layer`, ripup trials): extend
     `low_keepouts()` to include TOP-layer zones that are explicitly
     layer-tagged (leaf-cell implicit zones stay LOW-only, preserving today's
     behavior for unmarked designs).
4. **Ordering inside one `run_nuts`:** local solves first, copies injected,
   then the normal per-layer global solve runs for everything else. Bottom-up
   segments never appear as free segments in the global solve.

## 5. Stage (c) — `check_template_tracks` + DNUTS copy

**Where:** new command in `verify_viz_cmds.py`, helper in `ReportsMixin`
(mirroring `check_connectivity`); a small C++ helper beside
`RoutingGrid`/`verify` for the track enumeration.

1. **What "same signal tracks" means.** For each bottom-up cell, for each
   layer its local routing uses, for each solved segment's perpendicular
   interval: enumerate `signal_tracks_in` over each instance's translated
   window, subtract the instance origin, and compare the resulting relative
   track-centre lists (and slot widths) across instances, within epsilon.
   This is the ground truth — it automatically accounts for pattern phase
   (offset not a multiple of `unit_pitch`), absolute-rect
   `add_grid_override` regions that cut through some instances but not
   others, and grid keepouts that differ per instance.
2. **Report.** Per (cell, layer): `ALIGNED` or `MISALIGNED`, with the
   offending instances, the phase delta (`offset mod unit_pitch`), and
   missing/extra tracks. Summary per cell: aligned everywhere → eligible for
   DNUTS-copy. Verdict cached on the session (`self._template_track_verdict`).
3. **DNUTS for aligned cells.** Run detailed NUTS once on a **reference
   instance** (the first, in its absolute window — the grid API is
   absolute-coordinate, so "local" means "reference instance's window");
   copy each `NetSegment`/`NetVia` to the other instances by translation.
   Alignment guarantees the copied bit positions land on real signal tracks.
4. **Mismatch policy.** `on_mismatch stop` (default): `run_detailed_nuts`
   refuses with the report and a pointer at the fix (move the instance to a
   pitch-aligned site, or align the override). `on_mismatch independent`:
   misaligned cells' instance bundles are handed to the normal global DNUTS
   individually (uniformity broken knowingly, warning printed). Aligned cells
   still copy. (Open question Q2: whether a *partially* aligned cell may copy
   for its aligned instances and solve only the outliers.)
5. **Bit-level keepouts.** After copy, each instance's bit wires refine the
   stage-(b) bus-level keepout: register per-track grid keepouts so
   higher-level DNUTS bits cannot land on occupied tracks where spans
   overlap. (If the bus-level zone from §4.3 already covers the full occupied
   width, this is mostly a no-op; the refinement matters when the bus-level
   footprint was conservative.)

## 6. BDB enhancements

| Change | Detail |
|---|---|
| `cell.bottom_up INTEGER DEFAULT 0` | Schema migration (next version bump); `BDB::set_cell_bottom_up` / accessor on `CellRow`; pybind in `bind_db.cpp`. Chosen over `meta` rows because it is a per-cell attribute the Floorplanner should display/edit. |
| Bind `set_meta` to Python | Cheap, generally useful (session flags like the mismatch policy could persist); independent of the column decision. |
| Template routing persistence | Persist the template's local decision on the **template bundle rows themselves** — the tables already support it: `topology.is_selected` + `topology_segment.assigned_layer` on the template's candidates (today only instance rows get these). Local NUTS result: persist per-instance `bus_segment`/`net_segment` copies exactly as today (so `export_gds`, `load_pipeline expanded`, and viz work unchanged), plus a provenance marker. |
| Provenance | `bus_segment.source` / `net_segment.source` TEXT (`'solved'` default, `'bottomup_copy'` for copies) — lets `load_pipeline`, reports, and debugging distinguish copied rows; also the hook for a future incremental re-copy. |
| `load_pipeline` | Rehydrate `bottom_up` flags + template-level selections so a resumed session re-enters the flow with locked wrappers instead of re-deciding. |

## 7. Prerequisite/related fixes folded in

- **G2 (orientation)** — bottom-up *requires* instances to be congruent. At
  minimum: `set_bottom_up` and `run_planner hier` verify all instances of a
  marked cell (and, generally, of any expanded template) share
  `orient == 'N'`; a violation is an error for bottom-up cells and a warning
  for the existing top-down expansion (which silently mis-transforms today).
  Full orientation-aware `offset_topology` (rotate/mirror candidates + NUTS
  /DNUTS copies) is a separable follow-on (open question Q1).
- **G3 (TOP-layer NUTS keepouts)** — fixed as part of §4.3; also closes a
  latent hole for plain flat-flow users who declare TOP-layer keepouts.
- **HIER_TOPOLOGY.md refresh** — doc-only commit fixing the stale items in §1.

## 8. Test plan

Vehicle: `tools/build_hier_demo.py` already builds a BDB where each cell is
instantiated twice — ideal. New `test_hier_bottom_up.py` +
`hier_bottom_up.feature`:

1. **Shared decision:** two instances of a marked cell end with the same
   candidate index and identical per-segment layers; unmarked control cell
   may diverge.
2. **NUTS copy:** instance track positions equal template positions + origin
   offset, exactly; copied segments flagged fixed.
3. **Detour:** a depth-0 bundle whose cheapest path crosses a marked
   instance's copied trunk chooses a detour (or planner charges the band) —
   assert no overlap in `NUTSResult`.
4. **Track check aligned:** instances placed at pitch-multiple offsets →
   `ALIGNED`; DNUTS bit positions equal modulo offset; per-bit vias copied.
5. **Track check misaligned:** shift one instance by a non-multiple of
   `unit_pitch` (or add an override over one instance) → `MISALIGNED`;
   `on_mismatch stop` aborts DNUTS; `on_mismatch independent` solves that
   cell per-instance and completes.
6. **Orientation guard:** `rotate_comp` one instance → `set_bottom_up` /
   planner errors out.
7. **Persistence round-trip:** run flow, `save_bdb`, `load_pipeline expanded`
   in a fresh session → flags, template selection, copies (with provenance)
   restored; `check_connectivity` clean.
8. **No-regression:** full existing fast tier; a design with no
   `set_bottom_up` produces byte-identical `route_snapshot`.

## 9. Implementation order

1. BDB: `cell.bottom_up` column + migration + bindings + `set_bottom_up`
   command + orientation guard. *(small, unblocks everything)*
2. Planner: local template solve + pin broadcast + `hier.locked` +
   commit-first ordering + ladder/ripup exclusions.
3. NUTS: local solve + copy + `add_fixed_segments` + derived keepout mirror +
   G3 fix.
4. `check_template_tracks` + verdict caching.
5. DNUTS: reference-instance solve + copy + `on_mismatch` policies + bit-level
   keepout refinement.
6. Persistence (provenance, template selection, `load_pipeline`).
7. Tests + HIER_TOPOLOGY.md refresh + new doc section in BDB_REFERENCE /
   script reference.

## 10. Open questions (need user input)

- **Q1 — Orientation:** guard-and-refuse for non-`N` instances now (full
  rotate/mirror support as a follow-on), or implement orientation-aware
  copying as part of this work?
- **Q2 — Partial alignment:** when only some instances of a cell are
  track-misaligned, may the aligned ones still receive the DNUTS copy while
  the outliers solve independently, or does any mismatch force the whole
  cell into the chosen fallback?
- **Q3 — Keepout representation:** user's spec says lower-level wires become
  *Keepouts*. Plan proposes fixed-segment injection inside NUTS **plus** a
  derived keepout mirror for planner/DNUTS (§4.3). Acceptable, or should it
  be keepout-zones-only everywhere (simpler mental model, coarser, and needs
  the G3 fix regardless)?
- **Q4 — Scope of the keepout behavior:** derived keepouts only for
  bottom-up-marked cells (plan as written), or should *every* hier cell's
  planned-and-NUTSed local routing become keepouts for higher levels (a
  broader flow change, independent of bottom-up marking)?
