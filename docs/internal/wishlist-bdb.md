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
