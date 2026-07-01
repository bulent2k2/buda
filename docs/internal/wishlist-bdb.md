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

**2. Topologies (Stage 2) — ⬜ NEXT.** Persist each bundle's candidate/selected
topologies (segments, type, layer hints, slide ranges). New table(s) referencing
`bundle(id)`; next schema version.

**3. Abstract NUTS (Stage 4) — ⬜.** `bus_segment` rows (+ **bus-vias / symbolic
vias** between segments on different layers) with a content hash per snapshot;
later `net_segment` for detailed NUTS. These tables are the direct OA/GDS feed.

**Where to start (next):** topology row schema in `src/bdb.cpp`; write from the CLI
after `generate_topologies` / `run_planner` (`src/buda_cli.py`). Interchange design
intent: [`../BDB_REFERENCE.md`](../BDB_REFERENCE.md) "Planned interchange formats".
