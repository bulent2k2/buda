# Wishlist — BDB, test data & interchange

Deferred follow-ups for the BDB layer (`src/bdb.cpp`), its test-data management,
and the planned OA/GDS interchange. Index: [`wishlist.md`](wishlist.md).

## Persist-time write batching — ✅ IMPLEMENTED

**Symptom.** `generate_hier_topologies` on `flow/rnr/slowdown.buda` (100
bundles, 1236 candidates) took ~8s on a MacBook / ~24s in cloud, up from
~0.13s before candidate-topology persistence was added — a ~60× regression at
zero algorithmic change (`generate_candidates` itself profiled at 0.14s; all
the rest was the BDB write).

**Root cause.** Every `add_topology` / `add_topology_segment` /
`persist_seg_busterms` / `persist_seg_conns` / `add_bundle_net` runs as its own
autocommit statement, so each of the ~10 000 inserts commits the WAL — one
fsync per row (~1–6ms each). Nothing wrapped the burst in a transaction, even
though the importers already batch their bulk inserts with raw
`_exec("BEGIN")`/`_exec("COMMIT")`.

**Fix.** Depth-counted transaction batching: `BDB::begin_batch` /
`commit_batch` / `rollback_batch` (`src/bdb.cpp`, bound in `bind_db.cpp`) issue
one real `BEGIN`/`COMMIT` at the outermost nesting level so composing persist
helpers each guard their own body safely; a `_batched` decorator + `_bdb_batch`
context manager (`src/buda_cli.py`) wrap every persist entry point
(`_persist_topologies`, `_persist_bundles`, `_persist_nuts`,
`_persist_detailed_nuts`, `_persist_planner_output`). Measured: slowdown.buda
generate 23.8s → 1.6s in cloud. Tests: `test/tests/test_bdb_batch.py`
(commit/rollback atomicity + nesting no-op) plus the unchanged persist
round-trip suite (batching is transparent to output).

**Busterm-row dedup — ✅ IMPLEMENTED (follow-up).** `persist_seg_busterms` was
the residual after batching (1.1s / 1236 calls): it re-inserted a heavy
JSON-rects `tb:<block>` busterm row via `add_busterm` for *every candidate*
that taps a block, though `tb:<block>` is derived purely from block geometry and
is byte-identical across candidates. Fixed by threading a `seen` set (the
already-written `tb:` ids, scoped to one `_persist_topologies` pass) through
`_persist_topology_annotations` into `persist_seg_busterms` (`bind_routing.cpp`,
optional `seen` arg; `seen=None` keeps the old always-write path for the planner
persist, which writes few candidates): each block's busterm row is written once,
then only the cheap per-candidate `topology_seg_busterm` link. FK-safe — the
first candidate to tap a block writes the row before any link references it, and
`clear_topologies` (which wipes `tb:` rows) precedes the fresh `seen` set.
Measured on slowdown.buda: `persist_seg_busterms` 1.085s → 0.041s (26×),
generate 1.6s → 0.58s — now bounded by `generate_candidates` (0.15s) plus the
unavoidable per-row inserts. Test:
`test/tests/test_seg_busterm_persist.py::test_busterm_rows_deduped_across_candidates`
(one `tb:` row per block, many links) plus the existing reload round-trip suite
(dedup preserves correctness).

**Remaining smaller target (not yet done).** `add_topology_segment`'s per-call
statement re-prepare (~0.07s / 5990 calls) — cache the prepared insert like the
hot read paths do; minor next to the two wins above.

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

**Minor follow-up — ✅ IMPLEMENTED.** The Floorplanner GUI
(`tools/bdb_floorplanner.py`) now writes back to the `.bdb.sql` it was opened
from: opening a `*.bdb.sql` (via `fp foo.bdb.sql` or File→Open) materializes a
temp binary AND remembers the source (`FloorplannerAppState.sql_source`), so
**Write** re-serializes the working binary back to the `.sql`
(`fpc.save_sql` → `bdb_serialize.dump`). A new **Save As…** button targets a
chosen `*.bdb.sql` (remembered as the new Save target) or a fresh `*.bdb`
binary the session switches to (`fpc.save_bdb_as_binary`, via a clean
dump→load round-trip). Read-only sessions are guarded exactly as `write_bdb`.
Tests: `test/tests/test_floorplanner_save.py` (Save As to .sql, write-back
round-trip, Save As to binary, no-target + read-only guards).

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
Tests: `test/tests/test_bdb_topology_persist.py`. `seg_busterms` is **now
persisted too** (topo-truth Phase 3, schema v9): each real tap becomes a
routing-time `tb:<block>` busterm row + a `topology_seg_busterm` link, written by
the `persist_seg_busterms` bridge and reloaded by `load_seg_busterms` — it is
**not** re-derivable from geometry anymore (Phase 2 retired ConnTopology's
geometric fallback; see `single_source_topo_truth.md`). **Still deferred:**
slide ranges (recomputed). `bridge_segments` is persisted too since v11
(`topology_bridge_segment`; see the resume item below).

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

**5. Detailed NUTS (Stage 9, schema v8) — ✅ IMPLEMENTED.** The prerequisite —
via definition/insertion in detailed NUTS — landed first: the DNUTS engine now
emits per-bit `NetVia`s (each symbolic bus-via fanned out to `bit_width`
individual vias at each bit's own track crossing; drawn in the visualizer under
`[Vias/Conns]` in detailed mode). `run_detailed_nuts` then persists:
- **`net_segment`** — one row per bit-wire (placed rectangle + the bit's net
  identity: `net_names[bit_index]` resolved to `net_id` via `_ensure_net`).
- **`net_via`** — one row per per-bit via, sharing the parent `bus_via`'s
  `(bundle_id, from_seg, to_seg)` key with `bit_index` appended.
Both carry the hard `bundle(id)` FK (NOT NULL, parent-ensure, delete-order
rules as the bus tables); `run_nuts`/`clear_bundles`/`clear_expanded_bundles`
invalidate them; `route_snapshot` is rewritten with stage `detailed_nuts`
(net rows hashed by net **name**; bus counts preserved; `n_net_*` columns).
C++ API `add_net_segment` / `add_net_via` / `clear_detailed_routing` /
`net_segments` / `net_vias`; Python `_persist_detailed_nuts`.
Tests: `test/tests/test_bdb_dnuts_persist.py`,
`test/tests/test_detailed_nuts_vias.py` (via model).

These persisted tables are the direct source for the planned **BDB → OA
(`oaNet`/`oaTerm`/vias) / GDS** export; see
[`../BDB_REFERENCE.md`](../../BDB_REFERENCE.md) "Planned interchange formats".

## Resume / rehydrate the pipeline from the BDB (checkpoint & continue) — ✅ IMPLEMENTED

**Shipped** as the `load_pipeline [expanded]` command /
`BudaSession._load_pipeline_from_bdb` (schema v10 added the `bus_segment`
solver-state columns — perpendicular interval + corner-split track bounds —
plus `bundle_net.ord` for bit order and `topology.is_pinned` for pre-plan
pins). It restores, as deep as was persisted: bundles + all candidate
topologies (each reloaded candidate's `seg_busterms` restored **logically**
via `load_seg_busterms` from the v9 `topology_seg_busterm` links — the
single-source-of-topo-truth Phase 3 primitive, never re-derived from
geometry — so continuations reproduce single-session results
**bit-identically**, same rows and same `route_snapshot` hash), the planner's
selection + assigned layers + pins, and a rehydrated `NUTSResult` from
`bus_segment`. `ripup_reroute` now
**re-persists** its final routing (planner output + NUTS + detailed rows at
stage b) so a post-ripup checkpoint resumes from the improved routing.
Two-phase tests (stop → fresh session → re-declare setup → `open_bdb` →
`load_pipeline` → continue): topo-gen→planner→NUTS, planner→NUTS,
NUTS→DNUTS, and NUTS+ripup→DNUTS (congested fixture where ripup genuinely
re-routes), plus fail-fast guards. Tests: `test/tests/test_bdb_resume.py`;
docs: `docs/BDB_REFERENCE.md` (`load_pipeline`).

**Gap closed (v11):** `Topology.bridge_segments` (TEG over-the-block bridges)
— flagged by #138 as the one remaining un-persisted `Topology` field — is now
persisted in `topology_bridge_segment` (written next to the seg-busterm links
at every topology-persist site) and restored by `load_pipeline`, so TEG-over
multi-rect designs resume losslessly. `run_nuts_on_layer` also re-persists its
re-solved routing now (bus + detailed rows — the same stale-checkpoint class
of bug `ripup_reroute` had), and the detailed-routable `hier_routed` fixture
gives hier detailed-persistence + hier resume flow-level coverage.
Tests: `test/tests/test_bdb_resume_gaps.py`.

The original design notes below are kept for reference.

**Previously:** the pipeline→BDB persistence was
strictly **write-only**: every stage writes its rows, and re-running a stage
*clears and regenerates* them. Nothing read persisted `bundle` / `topology` /
`topology_segment` rows **back** into the live `BudaSession`, so you could not stop a
run after `generate_[hier_]topologies`, reopen the BDB in a fresh session, and
continue into `run_planner` — `open_bdb` only attaches the DB handle
(`buda_cli.py` `open_bdb` → `self.bdb = buda.BDB(path)`); `self.bundles` was only
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

The load-bearing finding: **`run_planner` recomputes everything derived — except
connectivity**. Inside `CongestionPlanner::optimize_topologies` each candidate is
passed through `ConnTopology::build(topo, floorplan)` (`congestion_planner.cpp`),
which regenerates per-segment slide ranges, `net_pull`, and trunk identity from
the raw segment geometry + the `Floorplan`. Crucially, since topo-truth Phase 2,
`ConnTopology` **reads** busterm taps from `Topology::seg_busterms` and never
re-derives them from geometry — an unannotated topology taps *nothing*. So the
rehydrate path must rebuild the **raw** `Topology` (segments + scalar fields)
**and restore its `seg_busterms` via `load_seg_busterms`** (persisted since
Phase 3, schema v9); it does not need slide ranges, `seg_perp`, or trunk info.
~~`bridge_segments` remains the one un-persisted `Topology` field (TEG-over
gap)~~ (superseded 2026-08-22 annotation: persisted since v11 — see the
**Gap closed (v11)** note above and the ✅ row in the table below; this
sentence predates it and sat un-struck in the kept-for-reference section,
where a skimming reader took it at face value).

| Needed by `run_planner` | Persisted? | Source on resume |
|---|---|---|
| `HBundle` id / level / cell_context / instances / parent_id / spec depths+paths | ✅ `bundle` row | `all_bundles()` |
| `HBundle` net names (→ bit count) | ✅ `bundle_net` (by `net_id`) | `bundle_nets(id)` |
| `Topology.type` / `estimated_wirelength` / `trunk_location` / `pass_through_count` / `connected_block_names` / `feedthru_blocks` | ✅ `topology` row | `topologies(bid)` |
| `Topology.segments` (`start`/`end`/`layer_hint`/`is_jog`) | ✅ `topology_segment` | `topology_segments(bid, ci)` |
| `BundleInput.width` | ❌ | recompute `len(net_names) * 1.5` (as `run_bundler` does) |
| selected index / assigned layers | ✅ `is_selected` / `assigned_layer` | restore for inspection; a fresh `run_planner` overwrites anyway |
| `Topology.seg_busterms` (endpoint→busterm taps) | ✅ `topology_seg_busterm` + `tb:` busterm rows (v9) | `buda.load_seg_busterms(bdb, bid, ci, topo)` — **required**: ConnTopology no longer re-derives taps (Phase 2) |
| slide ranges / `net_pull` / `seg_perp` / trunk | ❌ (in-memory only) | **recomputed** by `ConnTopology::build` inside the planner — no action |
| `Topology.bridge_segments` (TEG-over bridges) | ✅ `topology_bridge_segment` (v11) | `topology_bridges(bid, ci)` — restored by `load_pipeline` |

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
        # Restore the authoritative endpoint→busterm annotation LOGICALLY
        # (persisted at generate time; ConnTopology reads it and never
        # re-derives taps from geometry — an unannotated topology taps nothing).
        buda.load_seg_busterms(bdb, br.id, tr.cand_index, t)
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
   persisted rows — **including `seg_busterms`** (identical ConnTopology BUSTERM
   taps; a `load_pipeline` that forgets `load_seg_busterms` yields tap-less
   candidates that silently route disconnected) — and that a full single-session
   run of the same inputs yields the **same** planner selection + assigned
   layers + `route_snapshot` hash.
2. **Hier resume.** Same shape from a `bdb_input('hier_mixed')` fixture:
   `derive_busterms` + `run_hier_bundler` + `generate_hier_topologies` in phase 1;
   `add_blocks_from_bdb` + `load_pipeline expanded` + `run_planner hier` in phase 2.
3. **Guard tests:** `load_pipeline` with no open BDB / no persisted topologies
   errors clearly; `load_pipeline` before re-declaring blocks fails fast.

## Audit 2026-07: re-plan expanded-parent FK staleness + persist-step checking (C6-09) — RESOLVED

Both coupled findings landed together (the staleness fix unblocked C6-09).

- **Staleness:** `run_planner hier` now restores the pre-expansion TEMPLATE
  wrappers (`_hier_bundles_orig`) before re-expanding, so a second run
  re-derives its per-instance ids from the templates (stable) instead of
  re-expanding the prior run's per-instance wrappers — ids no longer drift
  (5..8, not 9..12), the expansion map keys on the real templates, and the
  checkpoint persists the whole routing subtree (regression:
  `test_replan_persists_expanded_instances_no_fk_drop`).
- **C6-09:** the persist mutators go through `step_checked` and throw on any
  non-`SQLITE_DONE` result (regression:
  `test_audit4.py::test_persist_step_throws_on_fk_violation`); the NUTS_DDL
  header comment is updated.

Original write-up (kept for context):

- **Re-plan expanded-parent staleness (newly discovered).** A second
  `run_planner hier` re-expands the templates into per-instance bundle rows
  whose `parent_id` (via `_hier_expansion_map` / `expanded_to_template`) can
  point at a PRIOR expansion's instance id — a row `clear_expanded_bundles`
  just deleted — instead of the real template. The whole orphaned subtree
  (bundle → topology → routing) then FK-fails and is **silently dropped**, so
  a checkpoint taken after a re-plan is missing those instances and a resume
  cannot restore them. Repro: the dogleg-cell flow's second `run_planner hier`
  (`test_doglegged_template_replan_resets_and_readopts`) produces an expanded
  row `id=9 parent=5` while only `[1,2,3,4]` exist. Fix: resolve
  `expanded_to_template` values to the CURRENT template ids on every re-plan
  (or persist templates before their instances and re-point stale parents).
- **C6-09: check the persist step's result.** The persist mutators ignore
  `sqlite3_step`'s return, so an FK/constraint failure silently drops the row.
  The fix (throw on non-`SQLITE_DONE`) is correct and valuable, but turning it
  on today converts the staleness bug above from a silent drop into a hard
  crash of the hier re-plan flow — so it must land WITH, or after, that fix.
  When both land: add `step_checked` to add_bundle / add_topology /
  add_topology_segment / add_bus_segment / add_bus_via / add_net_segment /
  add_net_via / add_busterm / renumber_topology, and flip the NUTS_DDL header
  comment back to "FK-rejected insert throws".
