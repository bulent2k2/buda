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

## BDB schema versioning (replace the ad-hoc ALTER TABLE)

**What:** There is no schema version today — a single ad-hoc
`ALTER TABLE busterm ADD COLUMN rects` (`src/bdb.cpp`, ~line 170) is the only
migration, applied blindly if the column is missing. Add `PRAGMA user_version`
(or a `meta.schema_version` row) plus a small ordered migration hook run at open
time, so committed `*.bdb.sql` fixtures survive schema evolution with an explicit
upgrade path instead of silent add-if-missing.

**Why deferred:** Not needed until the schema starts changing under checked-in
fixtures; recorded now so the first schema-affecting change adds the hook rather
than another blind ALTER.

**Where to start:** BDB open path (`src/bdb.cpp` constructor / schema setup); model
migrations as an ordered list keyed by version. Verify committed fixtures load and
`build_fixtures.py --check` stays a no-op.

## BDB provenance metadata

**What:** Add self-describing provenance to the `meta` table: tool/schema version,
source-recipe hash, created/modified markers. Keep volatile fields (timestamps)
out of the *diffable* dump (or normalize them) so provenance noise doesn't defeat
clean `*.bdb.sql` diffs.

**Why deferred:** Cosmetic until multiple producers/consumers exist; pairs
naturally with schema versioning above.

**Where to start:** `meta` read/write in `src/bdb.cpp`; the dump filter in
`tools/bdb_serialize.py` if any field must be normalized out of the text form.

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
