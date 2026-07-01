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

## Routing write-back + snapshot hash (feeds OA/GDS export)

**What:** Persist interconnect back into the BDB. Add `route_snapshot` +
`bus_segment` / `net_segment` tables (mirroring the stage-4 / stage-9 structs) with
a content hash per snapshot, so routing changes become reviewable in the `.bdb.sql`
diff. These tables are the direct source for the planned **BDB → OA (`oaNet` /
`oaTerm`) / GDS** export.

**Why deferred:** Routing output is in-memory only today (`self.bundles`,
`self.nuts_result`, `self.detailed_result`); there is no routing table. This is the
foundation the `open_bdb … writeback` mode above will exercise.

**Where to start:** schema in `src/bdb.cpp`; write from the CLI after
`run_nuts` / `run_detailed_nuts` (`src/buda_cli.py`). Interchange design intent:
[`../BDB_REFERENCE.md`](../BDB_REFERENCE.md) "Planned interchange formats" and the
forward-looking section of [`bdb_test_data.md`](bdb_test_data.md).
