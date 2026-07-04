# GDSII + OpenAccess Interchange — Plan & Status

BUDA's third design entry/exit path (besides DEF/LEF+Verilog and hand-built
BDBs): **import first**, then export. Decisions locked with the project owner:

- **GDS first, OA spec-only** — GDSII gets the working implementation (no
  external EDA library, testable in CI); OpenAccess stays a designed interface
  behind an optional CMake flag until the Si2 OA SDK is available.
- **Net recovery = TEXT labels + Verilog pairing** — parse `TEXT` records on
  designated label layers when present; otherwise import geometry only and
  pair with `import_verilog` (mirrors the DEF+Verilog merge flow).

All phases follow the repo's importer pattern: a hand-written, self-contained
parser/writer in its own translation unit, populating the same BDB tables,
coordinates normalized to **µm** (`docs/BDB_REFERENCE.md` "Design interchange
formats").

## Phase G0 — deterministic GDS writer as test scaffolding — ✅ IMPLEMENTED

`tools/gds_build.py`: a minimal, dependency-free GDSII **writer**
(`GdsBuilder`) emitting `HEADER/BGNLIB/LIBNAME/UNITS/BGNSTR/STRNAME/BOUNDARY/
SREF/AREF/TEXT/PROPATTR/PROPVALUE/ENDEL/ENDSTR/ENDLIB` with **zeroed
timestamps**, so tests generate their binary GDS inputs deterministically —
the same discipline as `build_fixtures.py` (GDS is binary; checked-in blobs
would violate the diffable-test-data principle). This scaffolding grows into
the real exporter in G4.

## Phase G1 — GDS reader → BDB placement/hierarchy — ✅ IMPLEMENTED

`src/gds_io.cpp` (`buda_core`), CLI command **`import_gds <file.gds>`**
(requires an open BDB; fresh load like `import_def_lef`):

- **Record layer**: 4-byte record headers + big-endian payloads; excess-64
  base-16 8-byte reals; `UNITS` gives the dbu→µm factor.
- **Structures → `cell` rows**: footprint = *recursive* bbox (own
  `BOUNDARY`/`BOX` geometry ∪ transformed child refs), memoized with a cycle
  guard — GDS structures are macros, so the LEF `SIZE` analogue is the full
  extent.
- **`SREF`/`AREF` → `component` hierarchy**: unreferenced structures are
  roots; placements elaborate recursively into absolute-µm component rows
  with dotted paths and growing depth (the `import_verilog` convention).
  `AREF` expands its cols×rows array. `STRANS` mirror / `ANGLE` (snapped to
  0/90/180/270 with a warning) / `MAG` are applied at bbox level (corner
  transform), matching BDB's bbox placement model.
- **Instance naming**: GDS refs are anonymous; a `PROPVALUE` property on the
  ref is used as the instance name when present, else `<struct>_<ordinal>`
  is synthesized (deterministic).
- `TEXT` records are counted (stats) but not yet consumed — that's G2.

Tests: `test/tests/test_gds_import.py` — writer→reader round-trips (units
scaling, hierarchy/depths/absolute bboxes, AREF expansion, recursive
footprints, property-named instances, top detection), a full
import→`add_blocks_from_bdb`→route pipeline run, `.bdb.sql` round-trip,
re-import over a routed checkpoint (FK-safe `clear_design`), and error paths
(bad magic, truncation).

**Demo:** `python3 tools/gds_demo.py` (after `bin/bb` + `source bin/activate`)
generates a small SoC as GDSII (2×2 core AREF + L2 + IO), imports it, routes
it through detailed NUTS, and opens the interactive visualizer; `--png <file>`
renders headlessly, `--no-viz` just prints the summary.

## Phase G2 — net/pin recovery via TEXT labels — ✅ IMPLEMENTED

`import_gds <file.gds> [labels <layer_csv>]`: each `TEXT` label is a net (name
= the string); its pin lands on the **deepest** component containing the
label's elaborated position, with dir `UNKNOWN` (the hier bundler's positional
fallback handles direction). Labels flow through the hierarchy transforms like
geometry — a label inside a referenced structure repeats per instance (the
standard GDS flattening semantic, shorting those instances onto the label's
net). `labels 63,64` restricts the label layers; default = every `TEXT` is a
label. Labels outside every component are skipped with a warning. Auto-detect
holds: no labels → geometry-only + `import_verilog` pairing, as before.

**Payoff (tested):** a labeled GDS runs the hierarchy-aware flow with ZERO
Verilog — labels → `net`/`pin` rows → `derive_busterms` → `run_hier_bundler`
→ routed through detailed NUTS (`test_hier_flow_from_labeled_gds_no_verilog`).
BDB API: `add_label_pin(net_name, comp_id, pin_name, px, py)`
(`_ensure_net` + `INSERT OR IGNORE`).

Verilog-pairing caveat (stands): name correspondence needs ref properties;
flows that strip them fall back to labels or synthesized names.

## Phase G3 — layer mapping — ✅ IMPLEMENTED

`def_gds_layer <buda_layer_id> <gds_layer> [<gds_datatype>]` (datatype
defaults 0) binds a `def_layer` metal layer to a GDS (layer, datatype) pair,
stored per-`Layer` on the `LayerStack` (`set_gds_mapping` / `get_gds_layer` /
`get_gds_datatype` / `layer_for_gds` reverse lookup / `gds_mapped_pairs`).
Two more command forms: `def_gds_layer file <path>` reads a map file
(`<buda_layer_id> <gds_layer> [<gds_datatype>]` lines, `#` comments) and
`def_gds_layer labels <csv>` registers the default TEXT label layers for
`import_gds` (an explicit `labels` argument on the import still overrides).

Both directions are served:
- **Import:** the CLI passes `gds_mapped_pairs()` to `import_gds`; shapes on
  mapped pairs are **routing wires, not macro-outline geometry** — counted
  (`GdsImportStats.n_routing_shapes`) but excluded from cell footprints. This
  is what keeps outlines clean when re-importing a routed/exported GDS (the
  G4 round-trip requirement). Datatype-precise: (8,1) still footprints when
  only (8,0) is mapped.
- **Export (G4):** the exporter writes each metal layer's `net_segment`
  wires to its mapped pair; unmapped layers fall back to a default at export
  time.

Unmapped GDS layers keep the G1 behavior (all geometry is outline). The
mapping is session state like `def_layer` itself (scripts re-declare it),
not persisted in the BDB.

## Phase G4 — GDS export — ✅ IMPLEMENTED

`export_gds <file.gds> [outline <gds_layer>] [labels <gds_layer>|off]
[via_size <um>]` — a C++ `GdsWriter` in `gds_io.cpp` (the port of the G0
record emitters: 1 nm dbu, zeroed timestamps, `PROPATTR 61` instance names —
identical DBs give identical bytes), **streaming from the persisted BDB
tables** the v1–v11 series built, so it works on a reopened checkpoint with
no live pipeline:

- **Cells → structures**: outline `BOUNDARY` (0,0,w,h) on `(outline_layer,
  0)` (default 10) + child `SREF`s reconstructed from the cell's first
  component instance at relative offsets, each carrying the child instance
  name as `PROPVALUE`.
- **Orientation (v13)**: each placement re-emits its `component.orient` token
  as `STRANS` (mirror) + `ANGLE`, computing the SREF origin (via `XForm`) so
  the oriented cell reproduces the stored bbox — so a rotated/mirrored
  instance now round-trips (previously it exported unrotated with a warning).
  The `n_dim_mismatch` warning now fires only for a genuine resize (bbox dims
  matching neither the cell nor the oriented cell), not a representable
  rotation. Non-unit `MAG` is still not captured (it stays baked into the
  bbox); deeply-nested oriented instances remain best-effort (the same caveat
  the template-instance reconstruction already carries) — top-level oriented
  placements round-trip exactly.
- **Top structure**: die-extent rectangle (anchored at the roots' min corner
  — the die is an extent, so `bbox_of(top)` round-trips), root `SREF`s, the
  routing, and the labels. Import materializes a `cell` row for the top it
  reads (footprint = die) but no component; export detects that "orphan"
  cell (no instances, size == die) and re-emits it AS the top — not orphan +
  synthetic top, which would re-import as two tops.
- **Routing**: `net_segment` bit-wire rectangles as `BOUNDARY` on each
  layer's `def_gds_layer`-mapped (layer, datatype) pair, `net_via` rows as
  `via_size` squares (default 1 µm) on the upper layer's pair; falls back to
  the abstract `bus_segment`/`bus_via` rows when no detailed rows exist
  (`GdsExportStats.stage` = `detailed_nuts` / `abstract_nuts` / `""`).
  Unmapped layers default to `(buda_layer, 0)` with a warning.
- **Labels**: one net-name `TEXT` per `pin` row at the pin position on
  `(label_layer, 0)` (default = first `def_gds_layer labels` layer, else
  63), so the file re-imports in labeled mode; `labels off` disables. Pin
  direction is not representable — dirs come back `UNKNOWN`.

**Round-trip (tested, `test/tests/test_gds_export.py`)**: import → export →
re-import is **identical** (components incl. `orient`, cell footprints, die,
nets, pins);
the full-pipeline fingerprint routes a labeled GDS through detailed NUTS,
exports, re-imports with the same `def_gds_layer` map, and gets the same
design back with every routing shape excluded from footprints (G3).
`tools/gds_demo.py` finishes with exactly this round-trip.
Python: `BDB.export_gds(path, layer_map=[], outline_layer=10,
label_layer=63, write_labels=True, via_size=1.0)` with `layer_map` entries
`(buda_layer, gds_layer, gds_datatype)`; returns `GdsExportStats`.

## Deferred export niceties — PATH wires & AREF arrays — ⬜

Both are **export-side compaction niceties, not correctness gaps**: import
handles both fully, and export already round-trips both *losslessly*, just
verbosely. Captured here so the trade-offs don't have to be re-derived.

### PATH wires

- **What it is.** A GDSII `PATH` is a stroked centerline — a polyline + a
  `WIDTH`, with an optional `PATHTYPE` end cap (0 butt, 1 round, 2 square /
  half-width extension); the reader offsets the centerline by ±`WIDTH`/2 to get
  the filled shape. One path with N vertices replaces N−1 rectangles and
  carries the routing intent (a connected run), not just filled area.
- **What BUDA does today.** Export writes every routing segment (and via) as an
  axis-aligned **`BOUNDARY`** rectangle (`GdsWriter::boundary_rect`), so a
  bus of 8 bits over 3 segments is 24 rectangles, not 8 paths. Import, by
  contrast, *already* reads `PATH` fully (strokes ±`WIDTH`/2, applies
  `PATHTYPE` 1/2 end extension), so the round-trip is asymmetric: we read
  paths, we write rectangles.
- **What adding it means.** A `GdsWriter::path` emitter
  (`PATH`/`WIDTH`/`PATHTYPE`/`XY`) and an `emit_rect` that chooses path vs
  rectangle. Payoff: smaller files, more wire-like output in layout viewers.
- **Why deferred.** (1) Marginal value — our segments are already simple
  axis-aligned rectangles that round-trip perfectly as `BOUNDARY`. (2) Real
  round-trip risk — a wire's stored geometry is a **bbox** (two corners), not a
  centerline+width, so export would have to *infer* the centerline and width
  (which axis is the run, which the width); degenerate cases (a near-square
  via, a zero-length stub, even vs odd width in DB units) can stroke back to a
  slightly different rectangle. `BOUNDARY` has no such ambiguity — what you
  write is exactly what you read. It trades a guaranteed-exact round-trip for a
  file-size win the checkpoint/interchange goal doesn't need.

### AREF arrays

- **What it is.** An `AREF` places one structure on a regular grid in a single
  record: structure name + `COLROW` (cols × rows) + three points (origin,
  column-pitch endpoint, row-pitch endpoint). One `AREF COLROW 8 8` stands in
  for 64 placements — the compact way to express a memory array, pad ring, or
  systolic grid.
- **What BUDA does today.** Import **expands** an AREF into one `component` row
  per element, synthesizing names `<struct>_<0..N-1>` (a GDS array has no
  per-element instance names). Export emits an individual **`SREF`** per
  component, so a 2×2 AREF re-exports as 4 SREFs. The round-trip is *correct*
  (same N components, names, positions) — only the array-ness (compaction) is
  lost; a `COLROW 16 16` leaves as 256 SREFs.
- **What adding it means.** Export would **detect** a regular grid among
  sibling components sharing a cell (constant pitch, complete rectangle,
  consistent orientation) and collapse it back to an `AREF`.
- **Why deferred.** Purely cosmetic / size benefit, and the grid detection is
  easy to get subtly wrong (partial arrays, one displaced element, mixed
  orientations). Of the two, AREF is the *safer* to add later — it needs no
  geometric inference, just grid detection — whereas PATH carries the
  bbox→centerline+width inference risk above.

## Phase OA — OpenAccess import/export — ⬜ SPEC-ONLY (gated on Si2 OA SDK)

Behind `BUDA_WITH_OA` (default OFF), a separate `oa_bridge.cpp`:
- `import_oa <lib> <cell> <view>`: walk `oaDesign`/`oaBlock` — `oaInst` →
  `component` rows (real instance names — no GDS naming caveat),
  `oaNet`/`oaInstTerm` → `net`/`pin` rows, `oaBlockage` → keepouts.
- `export_oa`: create `oaNet` wires from `net_segment`/`net_via` rows.
The BDB-side API both directions consume is exactly what G1/G4 exercise, so
enabling OA later is translation-unit work only.

## Verification strategy (all phases)

Round-trips (all now tested): G0-writer→G1-reader→BDB→`.bdb.sql`
diff-stable; G4-export→G1-import→identical BDB
(`test_export_reimport_is_identical`); full-pipeline fingerprint — import a
generated GDS, route to detailed NUTS, export, re-import, compare
(`test_routed_roundtrip_through_cli`, and `tools/gds_demo.py` end-to-end).
Plus visual smoke via the topology explorer / `bin/viz`.
