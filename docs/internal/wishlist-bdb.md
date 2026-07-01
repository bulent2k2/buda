# Wishlist — BDB, test data & interchange

Deferred follow-ups for the BDB layer (`src/bdb.cpp`), its test-data management,
and the planned OA/GDS interchange. Index: [`wishlist.md`](wishlist.md).

## `open_bdb <file>.sql` is always read-only — add a write-back mode

**What:** `open_bdb` on a serialized `*.bdb.sql` / `*.b_db.sql` path materializes
the text into a **throwaway temp binary** (`BudaSession._materialize_bdb_sql`,
`src/buda_cli.py`) and discards any changes. That is exactly right for today's
routing flows, which only *read* the BDB. When BDB **write-back** lands (routing
interconnect persisted into the BDB — see below), a flow will sometimes want to
*update* its serialized fixture deliberately. Add an explicit opt-in mode that
materializes, lets the flow mutate the temp binary, then **dumps it back to the
`.sql`** on close (via `tools/bdb_serialize.dump`), instead of the current
always-temp behaviour.

**Why deferred:** No write-back path exists yet — the pipeline's routing results
live only in the Python session, so read-only materialization is correct and
sufficient. Making it round-trip now would be a write path with nothing to write.

**Where to start:** `BudaSession._materialize_bdb_sql` and the `open_bdb` branch
(`src/buda_cli.py`); a flag such as `open_bdb <file>.sql writeback` (or a
`save_bdb_sql` command) that records the source `.sql` and re-dumps on `exit` /
session teardown. Guard it so a read-only flow can never silently rewrite a
committed fixture. Design context: [`bdb_test_data.md`](bdb_test_data.md).

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
