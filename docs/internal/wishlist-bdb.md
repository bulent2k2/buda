# Wishlist — BDB, test data & interchange

Deferred follow-ups for the BDB layer (`src/bdb.cpp`), its test-data management,
and the planned OA/GDS interchange. Index: [`wishlist.md`](wishlist.md).

## `open_bdb <file>.sql` write-back mode — ✅ IMPLEMENTED

**Shipped.** `open_bdb <file>.sql writeback` arms the materialized temp binary to
be dumped back to the source `.sql` (via `tools/bdb_serialize.dump`) on
[`save_bdb`], on the next `open_bdb`, and at `exit` / end of run
(`BudaSession._materialize_bdb_sql` / `_write_bdb_sql` / `_flush_bdb_writeback`,
`src/buda_cli.py`). Opt-in — without the keyword the default read-only,
discard-changes behaviour is unchanged, so a read-only flow can never silently
rewrite a committed fixture. `writeback` on a plain binary `.bdb` is ignored (it
already persists directly). Tests: `test/tests/test_bdb_writeback.py` (save,
exit-flush, reopen-flush, no-writeback control, binary-ignored). Docs:
`docs/BDB_REFERENCE.md` (`open_bdb` / `save_bdb`).

**Deferred within this item:** stamping a `modified` provenance marker on
write-back — a wall-clock timestamp would make repeated write-backs a noisy,
non-deterministic diff. Add it with the same normalization that gates
provenance timestamps generally.

**Minor follow-up:** the Floorplanner GUI (`tools/bdb_floorplanner.py`) opens a
`*.bdb.sql` read-only (materializes to a temp binary); wiring its **Save** to
re-serialize back to the `.sql` (reusing `tools/bdb_serialize.dump`) would give
the GUI the same opt-in write-back the CLI now has.

## BDB schema versioning (replace the ad-hoc ALTER TABLE) — ✅ IMPLEMENTED

**Shipped.** `BDB::SCHEMA_VERSION` (currently 1) is stamped into
`PRAGMA user_version`; `BDB::_migrate()` (`src/bdb.cpp`) runs an ordered ladder
from the stored version up to current on every open, then stamps it. The v0→v1
step absorbs the old ad-hoc `ALTER TABLE busterm ADD COLUMN rects` (kept
idempotent). `tools/bdb_serialize.py::dump` now emits `PRAGMA user_version=N;`
(iterdump omits pragmas) so the version survives the `*.bdb.sql` round-trip and is
visible in the diff; a loaded fixture with version 0 self-heals by re-migrating on
open. Exposed to Python as `BDB.schema_version()` / `BDB.SCHEMA_VERSION`. Tests:
`test/tests/test_bdb_schema.py` (fresh stamp, round-trip preservation, v0-missing-
rects migration).

**Next schema change:** bump `SCHEMA_VERSION`, add an idempotent `if (v < N)` step
in `_migrate()`, and regenerate fixtures (`build_fixtures.py`).

## BDB provenance metadata — ✅ IMPLEMENTED (timestamps deferred)

**Shipped.** `BDB::_seed_provenance()` writes `schema_version` (mirror of the
pragma, so it shows in the diffable dump) and `bdb_tool` into the `meta` table;
read via `BDB.meta_get(key, def="")`. Wall-clock created/modified timestamps are
**intentionally deferred** — they would make every fixture regeneration a noisy
diff and defeat `build_fixtures.py --check`; add them later behind dump
normalization (and stamp `modified` from the write-back mode above).

**Where to extend:** `_seed_provenance` in `src/bdb.cpp`; the dump in
`tools/bdb_serialize.py` if a volatile field ever needs normalizing out.

## Persist the routing pipeline into the BDB (feeds OA/GDS export)

Persisting the pipeline's output into the BDB, one stage at a time, so it is
diffable in the `.bdb.sql` and is the eventual source for **BDB → OA (`oaNet` /
`oaTerm`) / GDS** export. Sub-series:

**1. Bundles (Stage 1) — ✅ IMPLEMENTED.** `run_bundler` (flat) and
`run_hier_bundler` (hier) write their bundles into the `bundle` / `bundle_net` /
`bundle_busterm` tables when a BDB is open (schema v2 added the tables; v3 re-keyed
`bundle_net` by `net_id`). Membership is keyed by `net_id`, resolved from the net
name — `add_bundle_net` auto-creates a name-only `net` row if absent, so the flat
flow persists even though its nets may not pre-exist in `net`; hier busterms carry
an `entry`/`exit` role. C++ API: `add_bundle` / `add_bundle_net` /
`add_bundle_busterm` / `clear_bundles` / `all_bundles` / `bundle_nets` /
`bundle_busterms` (`src/bdb.cpp`); Python orchestration
`BudaSession._persist_bundles` (`src/buda_cli.py`). Tests:
`test/tests/test_bdb_bundle_persist.py`. Deferred: `child_ids` (derivable from
`parent_id`); auto-enabling `bdb_net_mode` on `open_bdb` to eagerly persist the
*whole* netlist (+ pins where they resolve), not just bundled nets.

**2. Topologies (Stage 2) — ✅ IMPLEMENTED.** `generate_topologies` (flat) and
`generate_hier_topologies` (hier) persist **all** candidate topologies into
`topology` / `topology_segment` (schema v4) when a BDB is open — before
`run_planner`, so candidates are inspectable/tweakable up front. Keyed by
`(bundle_id, cand_index)` (composite, no autoincrement → deterministic dumps).
C++ API `add_topology` / `add_topology_segment` / `clear_topologies` /
`topologies` / `topology_segments`; Python `BudaSession._persist_topologies`.
Tests: `test/tests/test_bdb_topology_persist.py`. **Deferred:** `seg_busterms` /
`bridge_segments` (re-derivable from geometry via ConnTopology) and slide ranges.

**2b. Planner output (Stage 3) — ✅ IMPLEMENTED.** `run_planner` records its
decision (schema v6): the selected candidate (`topology.is_selected`, via
`set_topology_selected`) and per-segment assigned layers
(`topology_segment.assigned_layer`, via `set_segment_layer`), for both flows. The
**hier** flow's `run_planner hier` expands cell-level bundles into per-instance
wrappers, now persisted as `is_replicated=1` `bundle` rows (`parent_id` = template)
carrying their selected topology — so `bus_segment` rows join back to a bundle and
each instance records its own selection/layers. `clear_expanded_bundles()` keeps
re-plan idempotent. Python `BudaSession._persist_planner_output`
(+ `_add_expanded_bundle`); tests: `test/tests/test_bdb_planner_persist.py`.

**3. Abstract NUTS (Stage 4) — ✅ IMPLEMENTED.** `run_nuts` persists each placed
bus segment into `bus_segment` (placed rectangle + layer) and one **symbolic
bus-via** per bus-level layer transition into `bus_via` (schema v5). C++ API
`add_bus_segment` / `add_bus_via` / `clear_bus_routing` / `bus_segments` /
`bus_vias`; Python `BudaSession._persist_nuts` (+ `_persist_bundle_vias`, which
records a via wherever two segments of a bundle connected per `ConnTopology`
— including trunk/stub T-junctions — sit on different layers).
Tests: `test/tests/test_bdb_nuts_persist.py`.

**4. Route fingerprint + hard FK (schema v7) — ✅ IMPLEMENTED.**
- **`route_snapshot` + content hash** — `run_nuts` writes a singleton
  `route_snapshot` row (id=1): a SHA-256 over a canonical, order-independent
  serialization of all `bus_segment` + `bus_via` rows, plus row counts and stage,
  so a routing change is a reviewable single-line diff in the `.bdb.sql`. C++ API
  `set_route_snapshot` / `route_snapshot`; Python `_persist_route_snapshot`.
- **Hard FK for `bus_segment`/`bus_via`** — now that hier expanded bundles are
  persisted (item 2b), `bundle_id` is a real `REFERENCES bundle(id)` foreign key
  for both flows. `_persist_nuts` ensures the parent bundles are persisted before
  inserting bus rows (persisting planner output first if needed), and
  `clear_bundles` / `clear_expanded_bundles` drop bus rows before their parents.
  The v6→v7 migration rebuilds the bus tables with the FK, dropping pre-FK orphans.
Tests: `test/tests/test_bdb_route_snapshot.py`.

**Deferred (follow-ups):**
- **Detailed NUTS (Stage 9) `net_segment` rows** — per-bit wires on concrete
  tracks (the finest OA/GDS geometry), written after `run_detailed_nuts`. Blocked
  on working out via definition/insertion in detailed NUTS before persisting the
  actual per-bit wires.

These persisted tables are the direct source for the planned **BDB → OA
(`oaNet`/`oaTerm`/vias) / GDS** export; see
[`../BDB_REFERENCE.md`](../BDB_REFERENCE.md) "Planned interchange formats".

## Resume / rehydrate the pipeline from the BDB (checkpoint & continue) — 📋 SPEC

**Status: designed, not implemented.** Today the pipeline→BDB persistence is
strictly **write-only**: every stage writes its rows, and re-running a stage
*clears and regenerates* them. Nothing reads persisted `bundle` / `topology` /
`topology_segment` rows **back** into the live `BudaSession`, so you cannot stop a
run after `generate_[hier_]topologies`, reopen the BDB in a fresh session, and
continue into `run_planner` — `open_bdb` only attaches the DB handle
(`buda_cli.py` `open_bdb` → `self.bdb = buda.BDB(path)`); `self.bundles` is only
ever built by the bundler engine regenerating from scratch.

**Why it's worth having.** Candidate enumeration (`run_bundler` +
`generate_topologies`, especially the hier variants with per-cell templates and
multi-trunk trees) is the runtime-heavy part of the flow; layer assignment
(`run_planner`) is a separately-tuned, re-runnable step. A resume path lets a
designer: (1) **checkpoint** an expensive topology set once and replan it many
times with different `set_planner_param` knobs without re-generating; (2) inspect
/ hand-tweak persisted candidates (a future interactive step) before paying for
the planner; (3) hand a routed-to-topology BDB between tools/machines. It is also
the read-back half that validates the persistence is *lossless enough to route
from*, not just to diff.

### Feasibility — the persisted data is sufficient

The load-bearing finding: **`run_planner` recomputes everything derived**. Inside
`CongestionPlanner::optimize_topologies` each candidate is passed through
`ConnTopology::build(topo, floorplan)` (`congestion_planner.cpp`), which
regenerates busterm faces, per-segment slide ranges, `net_pull`, and trunk
identity from the raw segment geometry + the `Floorplan`. So the rehydrate path
only has to rebuild the **raw** `Topology` (its segments + a few scalar fields)
and the `HBundle`; it does **not** need to persist or reconstruct slide ranges,
`seg_perp`, `seg_busterms`, `bridge_segments`, or trunk info.

| Needed by `run_planner` | Persisted? | Source on resume |
|---|---|---|
| `HBundle` id / level / cell_context / instances / parent_id / spec depths+paths | ✅ `bundle` row | `all_bundles()` |
| `HBundle` net names (→ bit count) | ✅ `bundle_net` (by `net_id`) | `bundle_nets(id)` |
| `Topology.type` / `estimated_wirelength` / `trunk_location` / `pass_through_count` / `connected_block_names` / `feedthru_blocks` | ✅ `topology` row | `topologies(bid)` |
| `Topology.segments` (`start`/`end`/`layer_hint`/`is_jog`) | ✅ `topology_segment` | `topology_segments(bid, ci)` |
| `BundleInput.width` | ❌ | recompute `len(net_names) * 1.5` (as `run_bundler` does) |
| selected index / assigned layers | ✅ `is_selected` / `assigned_layer` | restore for inspection; a fresh `run_planner` overwrites anyway |
| slide ranges / `net_pull` / `seg_perp` / `seg_busterms` / `bridge_segments` / trunk | ❌ (in-memory only) | **recomputed** by `ConnTopology::build` inside the planner — no action |

**The one real prerequisite: the `Floorplan` (and `LayerStack`).** Topology
segments are absolute coordinates, so `ConnTopology::build` needs the *same*
blocks/keepouts, and the planner needs the layer stack + any track patterns.
These are **not** in the topology tables. So resume skips the expensive
bundling+topology-gen, **not** the cheap setup:
- **Flat flow:** the resume script re-declares the setup it originally sourced
  (`def_layer` / `def_track_pattern` / `add_block` / `add_keepout` — e.g.
  `source flow/rnr/mix_tracks.buda` + the `add_block`s), then calls `load_pipeline`
  to pull back the bundles+candidates, then `run_planner`.
- **Hier flow:** blocks already live in the BDB `component` table, so setup is
  `add_blocks_from_bdb` (+ `def_layer`/patterns) before `load_pipeline`. (`src`/`dst`
  endpoint strings live only in the Python `self._net_endpoints` map and are
  **not** needed by `run_planner`; they'd only be required to *re-generate*
  candidates, which resume deliberately skips.)

### Proposed surface

- **`.buda` command `load_pipeline [expanded]`** — rehydrate `self.bundles` from
  the open BDB's `bundle` (+ `bundle_net`) and `topology` (+ `topology_segment`)
  rows. Requires an open BDB and an already-established Floorplan/LayerStack; errors
  clearly if the BDB has no persisted topologies. `expanded` selects the hier
  post-expansion view (`is_replicated=1` per-instance rows) instead of the
  templates. Idempotent (rebuilds `self.bundles` from scratch each call).
- **`BudaSession._load_pipeline_from_bdb(expanded=False)`** — the implementation.

### Rehydrate recipe (per persisted bundle)

```
for br in bdb.all_bundles():            # filter by is_replicated per `expanded`
    hb = buda.HBundle()
    hb.id = br.id; hb.level = br.level; hb.cell_context = br.cell_context
    hb.instances = br.instances; hb.parent_id = br.parent_id
    hb.net_names = bdb.bundle_nets(br.id)          # names, resolved from net_id
    # (+ spec depths/paths, num_terminals from br)
    w = buda.BundleWrapper()
    w.input.original_bundle = hb
    w.input.width = len(hb.get_net_names()) * 1.5
    cands = []
    for tr in bdb.topologies(br.id):               # ordered by cand_index
        t = buda.Topology()
        t.type = tr.type; t.estimated_wirelength = tr.wirelength
        t.trunk_location = tr.trunk_location
        t.pass_through_count = tr.pass_through_count
        t.connected_block_names = json.loads(tr.connected_blocks)
        t.feedthru_blocks = json.loads(tr.feedthru_blocks)
        segs = []
        for sr in bdb.topology_segments(br.id, tr.cand_index):
            s = buda.Segment()
            s.start = buda.Point(int(sr.x1), int(sr.y1))
            s.end   = buda.Point(int(sr.x2), int(sr.y2))
            s.layer_hint = sr.layer_hint; s.is_jog = sr.is_jog
            segs.append(s)
        t.segments = segs                          # reassign whole vector
        cands.append(t)
    w.input.candidates = cands
    self.bundles.append(w)
# also restore self._bundler_strategy / _hier_expansion_map bookkeeping as needed
```

### Edge cases & open questions (resolve at implementation)

- **Selected-index restore.** `topology.is_selected` + `topology_segment.assigned_layer`
  let `load_pipeline` pre-populate `plan.selected_topology_index` / `plan.seg_layers`
  so `load_pipeline` alone (no re-plan) yields an inspectable planned state; a
  subsequent `run_planner` overwrites it. Decide whether `load_pipeline` restores
  the plan or leaves it unset (pinning via `select_topology` is the middle ground).
- **Hier expansion map.** The in-memory `_hier_expansion_map` (template→instances)
  isn't persisted; on `load_pipeline expanded` the per-instance rows carry
  `parent_id`, so the map is reconstructable, but downstream hier re-plan/ripup
  paths that key off it need auditing.
- **Coordinate typing.** Persisted seg coords are `REAL`; `Point` is integer
  (`py::init<int,int>`). Topology geometry is integer-valued, so cast is safe, but
  assert no fractional loss.
- **Consistency guard.** `load_pipeline` should verify the referenced blocks exist
  in the current Floorplan (fail fast if setup wasn't re-declared) and optionally
  check the `route_snapshot`/schema version for provenance.
- **Scope (non-goals for v1):** resuming *after* NUTS (rebuilding `nuts_result`
  from `bus_segment`/`bus_via`) and after detailed-NUTS is a later increment; v1
  targets the post-`generate_topologies` → `run_planner` handoff only.

### Verification — the two-phase test (to add with the implementation)

`test/tests/test_bdb_resume.py`:
1. **Flat resume.** Phase 1: a `BudaSession` sources `mix_tracks.buda`, opens a
   writeback `.bdb`, adds blocks + a bus, `run_bundler`, `generate_topologies`,
   then stops (no planner). Phase 2: a **fresh** `BudaSession` re-declares the
   setup (layers/patterns/blocks), `open_bdb` the same file, `load_pipeline`,
   `run_planner`, `run_nuts`. Assert phase-2 `self.bundles`/candidates match the
   persisted rows and that a full single-session run of the same inputs yields the
   **same** planner selection + assigned layers + `route_snapshot` hash.
2. **Hier resume.** Same shape from a `bdb_input('hier_mixed')` fixture:
   `derive_busterms` + `run_hier_bundler` + `generate_hier_topologies` in phase 1;
   `add_blocks_from_bdb` + `load_pipeline expanded` + `run_planner hier` in phase 2.
3. **Guard tests:** `load_pipeline` with no open BDB / no persisted topologies
   errors clearly; `load_pipeline` before re-declaring blocks fails fast.
