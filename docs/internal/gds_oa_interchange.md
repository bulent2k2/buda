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

## Phase G2 — net/pin recovery via TEXT labels — ⬜ NEXT

Auto-detect: `TEXT` on configured label layer(s) → `net` + `pin` rows (name
from the string, position from `XY`, owning component by containment); absent
→ geometry-only + `import_verilog` pairing. Caveat to resolve: Verilog pairing
needs instance-name correspondence, which GDS only provides via ref
properties — flows that strip them fall back to labels or synthesized names.

## Phase G3 — layer mapping — ⬜

`def_gds_layer <buda_layer_id> <gds_layer> <gds_datatype>` (+ map-file form)
extending `LayerStack`, both directions: import (outline vs label vs routing
layers) and export (metal → layer/datatype). Defaults when unmapped.

## Phase G4 — GDS export — ⬜

`export_gds <file.gds>` streaming from the persisted BDB tables the
v1–v11 series built: `component` bboxes as structure placements,
`net_segment` bit-wires as `PATH`/`BOUNDARY` per mapped layer, `net_via` as
via squares, optional net-name `TEXT` labels — making BUDA's own output
re-importable in labeled mode (the round-trip test). Falls back to
`bus_segment` rectangles when only abstract routing exists. Reuses/extends
the G0 record emitters (ported to C++ or kept in tools/, decided then).

## Phase OA — OpenAccess import/export — ⬜ SPEC-ONLY (gated on Si2 OA SDK)

Behind `BUDA_WITH_OA` (default OFF), a separate `oa_bridge.cpp`:
- `import_oa <lib> <cell> <view>`: walk `oaDesign`/`oaBlock` — `oaInst` →
  `component` rows (real instance names — no GDS naming caveat),
  `oaNet`/`oaInstTerm` → `net`/`pin` rows, `oaBlockage` → keepouts.
- `export_oa`: create `oaNet` wires from `net_segment`/`net_via` rows.
The BDB-side API both directions consume is exactly what G1/G4 exercise, so
enabling OA later is translation-unit work only.

## Verification strategy (all phases)

Round-trips: G0-writer→G1-reader→BDB→`.bdb.sql` diff-stable;
G4-export→G1-import→identical BDB; full-pipeline fingerprint (import a
generated GDS, route to detailed NUTS, export, re-import, compare). Plus
visual smoke via the topology explorer / `bin/viz`.
