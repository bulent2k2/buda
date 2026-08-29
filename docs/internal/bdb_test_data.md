# BDB Test-Data Management & Diff-able BDB Artifacts

## Problem

BUDA testing is mostly CLI + `.buda` scripts with BDBs created **ephemerally**
inside each test (`:memory:` or `tmp_path`) and discarded. `.gitignore` ignores
every `**/*.bdb`, so no BDB is ever committed.

Going forward we need **hierarchical designs as durable, checked-in test inputs**
(the hier flow — `derive_busterms` → `run_hier_bundler` →
`generate_hier_topologies` → `run_planner hier` — is hard to exercise from flat
`.buda` alone). The historical workaround was to rename the extension to `.b-db`
so a binary BDB slips past the ignore globs. That has three problems, all rooted
in **BDB being a binary SQLite file**:

1. **Not diffable / mergeable.** A committed `.b-db` is an opaque blob; review and
   conflict resolution are impossible.
2. **Tests mutate their input.** Running the hier pipeline writes to the BDB
   (`derive_busterms`/`refine_busterms` clear+rewrite the `busterm` table; WAL
   mode touches `-shm`/`-wal` sidecars), so the checked-in file shows up as a
   spurious unstaged change that pollutes commits.
3. **Future write-back needs review.** When BUDA interconnect is written back to
   the BDB we will want those diffs checkable, and the same canonical, versioned
   representation is the natural feed for the planned **BDB → OA / GDS** export.

## Approach

Treat a checked-in BDB as a **deterministic text artifact**, never a binary blob,
and never let a test mutate its input.

### 1. Diffable text fixtures (`*.bdb.sql`)

A BDB is a plain SQLite file, so `sqlite3.iterdump()` gives deterministic SQL text
(schema first, then one INSERT per row in rowid order per table). We commit that
text; `git diff` shows exactly which components/nets/busterms/bundles changed.

- **`tools/bdb_serialize.py`** — `dump(bdb, sql)`, `load(sql, bdb)`,
  `verify(bdb)`. `dump` opens the source read-only (`mode=ro`) so serializing
  never mutates it. `load` rebuilds a fresh binary (removing any stale
  file + sidecars first). No C++ recompile — pure stdlib `sqlite3`.
- Committed fixtures live in **`test/tests/data/*.bdb.sql`**.

### 2. Copy-to-temp, never-dirty fixture

- **`bdb_input`** fixture in `test/tests/conftest.py`: a factory
  `bdb_input("hier_mixed")` materializes the committed `.bdb.sql` into a throwaway
  binary in `tmp_path` and returns that path. Any pipeline mutation is discarded
  with `tmp_path`; the committed fixture is never touched.
- **`readonly_conn(path)`** in conftest: a raw `mode=ro` `sqlite3` connection for
  pure SQL inspection (physically cannot write, no WAL sidecars). Mutation
  isolation for `buda.BDB`-based tests comes from copy-to-temp, not from here
  (`buda.BDB` opens its own read-write connection with no `query_only` hook).

### 2b. Naming the materialization: `BUDA_BDB_MATERIALIZE_TO`

`open_bdb <x>.bdb.sql` (no `writeback`) materializes into a throwaway temp
binary that receives every pipeline persist and dies with the session.  The
environment variable **`BUDA_BDB_MATERIALIZE_TO=<path>`** names that binary
instead: the semantics of the open are unchanged in the one way that
matters — the `.sql` input is still never written (no writeback source is
armed) — the pipeline persists into the named file exactly as it persisted
into the temp, and the file survives the session as a durable checkpoint.
The request is **popped** by the first no-`writeback` `.sql` open (it names
ONE file), is declined loudly when the open carries `writeback` (that open
already has a durable home — the `.sql` itself), and an **existing** target
is opened as-is rather than re-materialized (a previous session's copy,
pins and routing included; delete it first for a fresh copy of the input).
This is the engine half of `btcl -b`'s read-only-input flow
([TCL_FRONT_END.md](../TCL_FRONT_END.md)); the launcher pre-flights the
flow and only arms it for a flow whose single `open_bdb` is that shape.

### 3. Reproducible fixture generation

- **`test/tests/data/build_fixtures.py`** builds each fixture *deterministically*
  from code (no randomness, no external flow files) and dumps it. Re-running must
  produce a no-op `git diff` (`--check` asserts this). `test_bdb_fixture.py`'s
  `test_committed_fixture_is_up_to_date` guards against staleness in CI.
- Fixtures may also be produced from the existing generators
  (`tools/build_hier_demo.py`, `tools/buda2bdb.py`) and then dumped, but a
  committed fixture should always have a deterministic regeneration path.

### 4. `.gitignore`

Binary `**/*.bdb*` stay ignored; `!**/*.bdb.sql` keeps text fixtures tracked. If a
binary `.b-db` is ever committed for byte-exactness, its `-shm`/`-wal` sidecars
are ignored so they don't leak.

## Usage

```bash
# Regenerate all committed fixtures (should be a no-op diff):
PYTHONPATH=build python3 test/tests/data/build_fixtures.py
PYTHONPATH=build python3 test/tests/data/build_fixtures.py --check   # CI drift check

# Ad-hoc round-trip of any BDB:
python3 tools/bdb_serialize.py dump   my.bdb my.bdb.sql
python3 tools/bdb_serialize.py load   my.bdb.sql rebuilt.bdb
python3 tools/bdb_serialize.py verify my.bdb            # dump→load→dump stable
```

In a test:

```python
def test_something(bdb_input):
    path = bdb_input("hier_mixed")      # fresh temp binary; safe to mutate
    sess = buda_cli.BudaSession(); sess.no_viz = True
    sess.do_command(f"open_bdb {path}")
    sess.do_command("run_hier_bundler depth 1 bidirectional")
    ...                                 # committed .bdb.sql is untouched
```

## Forward-looking design (write-back, diffing, OA)

Not yet implemented — recorded here so write-back lands on the right foundation.
Interconnect is currently in-memory only (`self.bundles`, `self.nuts_result`,
`self.detailed_result`); there is no routing table today.

1. **Schema versioning.** ✅ Implemented — `BDB::SCHEMA_VERSION` in
   `PRAGMA user_version` + an ordered `BDB::_migrate()` ladder run at open (the
   v0→v1 step absorbs the old ad-hoc `ALTER TABLE … rects`). `bdb_serialize.dump`
   emits `PRAGMA user_version=N;` so the version survives the round-trip. See
   [`wishlist/wishlist-bdb.md`](wishlist/wishlist-bdb.md).
2. **Provenance metadata.** ✅ Implemented — `schema_version` + `bdb_tool` in the
   `meta` table (via `BDB::_seed_provenance`), read with `BDB.meta_get`. Wall-clock
   timestamps deferred so they don't add diff noise to regenerated fixtures.
3. **Persist the pipeline (bundles → topologies → NUTS).** ✅ Implemented through
   abstract NUTS — `run_[hier_]bundler` writes the `bundle` / `bundle_net` /
   `bundle_busterm` tables (schema v2; v3 re-keyed `bundle_net` by `net_id`),
   candidate topologies into `topology` / `topology_segment` (v4), planner output
   into `topology.is_selected` + `topology_segment.assigned_layer` and hier expanded
   bundles (v6), abstract-NUTS bus routing into `bus_segment` / `bus_via` (v5,
   FK-hardened in v7), and detailed-NUTS per-bit wires + vias into
   `net_segment` / `net_via` (v8, net identity via `net_id`). A singleton
   `route_snapshot` row fingerprints the routed output with a content hash
   (v7; v8 adds the detailed-row counts), so a routing change is a reviewable
   single-line `.bdb.sql` diff. See [`wishlist/wishlist-bdb.md`](wishlist/wishlist-bdb.md).
   These tables are the direct
   source for the planned **BDB → OA (`oaNet`/`oaTerm`) / GDS** export
   (see [BDB Reference](../BDB_REFERENCE.md) "Planned interchange formats").

## Files

- `tools/bdb_serialize.py` — dump/load/verify.
- `test/tests/conftest.py` — `bdb_input` fixture, `readonly_conn`, `DATA_DIR`.
- `test/tests/data/build_fixtures.py` — deterministic fixture builder.
- `test/tests/data/*.bdb.sql` — committed diffable fixtures: `hier_mixed`
  (small, review-friendly; NOT detailed-routable) and `hier_routed` (its
  scaled + misaligned twin, routes cleanly through `run_detailed_nuts` — use
  it for flow-level detailed-persistence / resume coverage).
- `test/tests/test_bdb_fixture.py` — round-trip, no-dirty-input, staleness guard.
- `.gitignore` — tracks `*.bdb.sql`, ignores binary BDBs + `.b-db` sidecars.
