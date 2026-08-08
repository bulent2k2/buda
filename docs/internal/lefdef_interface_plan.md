# LEF/DEF interface — plan for closing Lens B

Status: **proposed**, 2026-08-08.  Companion to the Lens B assessment in
[`../Analysis.md`](../Analysis.md), which found that BUDA is "a
self-contained planning environment with its own world model, not a
flow-participating point tool".  This is the plan to change that,
starting with the interface that gates everything else.

Every claim below was checked against the source; file:line references
are given so a reviewer can re-derive the conclusion rather than trust
it.

---

## 0. Decide the target first, because it changes the cost by ~4×

Lens B named a fork, and the plan branches on it:

| | **Advisory planner** (recommended) | **Flow-participating router** |
|---|---|---|
| Writer emits | DEF `BLOCKAGES` + bus corridors + optional guide wires | DEF `NETS + ROUTED` with real vias |
| Needs a via library | no | **yes** |
| Needs DRC legality (width/spacing/area/enclosure) | no | **yes** |
| Needs DBU-exact geometry | desirable | **mandatory** |
| Rough size | 1–2 quarters | multi-quarter, and competes with mature routers |
| What the customer does with it | honours corridors during detailed routing | consumes the routes directly |

The advisory path is recommended because it matches what the tool is
demonstrably good at (early bus/corridor feasibility, layer budgeting,
congestion-aware planning before detailed routes exist) and because
BUDA's own corpus shows it does not yet converge clean at chip scale
(Analysis A10: 7/7 chip vehicles carry residual unplaced bits) — a
*corridor* plan with residual bits is still useful; a *final route* with
252 unplaced bits is not.

**Phases 1–4 below are common to both paths.**  Only Phase 5 differs.
Nothing in the early phases forecloses the router path later.

---

## 1. Phase 0 — the failure signal (≈ 3 days, do it first)

Not LEF/DEF, but the prerequisite for testing any of it: today
`check_design` reporting violations still **exits 0** (verified —
a design with violations exits 0 while a *malformed script* exits 1).
An importer regression suite is impossible to gate without a failure
signal, and every phase below needs one.

- Wire the audit outcome to the exit code.  The plumbing exists
  (`exit <code>` and setup errors already exit 1); `_check_design`
  returns `None` on every path (`src/buda_session/reports.py:588`).
  Proposal: `check_design` sets a session failure flag; `main()` exits
  non-zero if set.  Gate behind a flag (`--strict-check`, or
  `check_design strict`) for one release so existing scripts don't
  break, then flip the default.
- Emit a machine-readable run report (`--report-json <path>`):
  per-command status, violation counts by type, unplaced/overlap
  totals, wirelength, runtime.  The data is already computed for the
  terminal summary (`src/buda_cli.py:384`) and `qor_table.py --json`
  proves the shape.
- Stop counting errors by substring-matching "error"/"warning" in
  captured output (`src/buda_cli.py:337`) — a net named `error_flag`
  inflates the count.

**Gate:** existing flows byte-identical; a new test asserts a violating
flow exits non-zero under the flag and 0 without it.

---

## 2. Phase 1 — the coordinate model (≈ 1 week; the highest-value structural fix)

**The finding that makes this cheap.**  The routing engine is
**unit-agnostic**: `grep` for micron semantics across `topology.cpp`,
`nuts.cpp`, `congestion_planner.cpp`, `routing_grid.cpp` returns
**zero** hits.  `Point`/`Rect` are plain `int` (`src/topology.h:50`),
and nothing in the engine claims those integers are microns.  The µm
interpretation lives only at the boundaries — `bdb.cpp`'s DEF import
divides by `UNITS` (`:1387`), `gds_io.cpp`, and comments in
`detailed_nuts.cpp`.

So today's 1 µm quantization is **not** an engine limitation; it is an
import convention.  DEF gives DBU integers, import divides them to
double µm, the BDB stores `REAL`, and the engine then truncates to
integer µm — losing ~2000 DBU per unit at a typical 2000 DBU/µm, i.e.
roughly 20–25 track pitches on an advanced node.  That single conversion
is what makes real-PDK data unusable, not the algorithms.

**Proposal: make the import scale explicit.**

- Introduce a session-level *engine units per DEF database unit* factor.
  Default `= 1/UNITS` reproduces today's µm behaviour exactly
  (byte-identical; the corpus is the gate).
- A real-PDK import selects **1 engine unit = 1 DBU**, making the
  engine's integers exact and eliminating quantization entirely.
- Range check: a 10 mm die at 2000 DBU/µm is 2×10⁷ engine units — well
  inside int32.  Audit the handful of places that multiply coordinates
  (`2L * coord` cut matching is already `long`; `nuts_geom` area/product
  computations need a review pass).
- The detailed layer stays `double` for track positions; its
  identity quantizer uses an absolute `1e-6` quantum
  (`src/detailed_nuts.cpp:40`).  At DBU magnitudes that quantum is
  ~10³ ulps wide, so the ±1 tolerance reasoning still holds — but this
  must be re-derived, not assumed, and pinned with a test.

**Gate:** whole corpus byte-identical at the default factor; a new
DBU-mode fixture round-trips DEF → BUDA → DEF with zero coordinate
drift.

---

## 3. Phase 2 — LEF reader (≈ 3–4 weeks)

Today's LEF support is two ad-hoc scanners recognizing `MACRO`, `SIZE`,
`PIN`, `DIRECTION`, `USE`, `RECT` (`src/bdb.cpp:1174`, `:1195`).
Everything else is ignored, pin geometry is collapsed to a centroid
(`:1246`), and `USE POWER|GROUND|CLOCK` pins are dropped outright
(`:1206`).

**Architecture.**  New `src/lef_io.{h,cpp}`, following the `gds_io.cpp`
precedent (self-contained reader/writer in its own translation unit,
BDB-agnostic where possible).  `bdb.cpp` is already 3 732 lines; this
does not belong in it.  LEF and DEF share a lexical family, so both
readers sit on one **statement-level tokenizer** (`lefdef_lex.h`) that
honours `;` terminators rather than newlines — which by itself fixes the
multi-line `COMPONENTS` fragility noted in Analysis Lens B item 6.

| Step | Content | Notes |
|---|---|---|
| 2a | Tokenizer + macro completeness: `SIZE`, `ORIGIN`, `FOREIGN`, `SYMMETRY`, `SITE`, multi-port `PIN` with per-port `LAYER`+`RECT`, `OBS` | Fixes wrong pin coordinates for macros with non-zero `ORIGIN`; keeps power/ground pins instead of dropping them |
| 2b | Tech `LAYER`: `TYPE`, `DIRECTION`, `PITCH`, `WIDTH`, `SPACING`, `OFFSET`, `AREA` | → `Layer.dir` + a **synthesized `TrackPattern`**, replacing hand-typed `def_layer`/`def_track_pattern` |
| 2c | `VIA` / `VIARULE` → a via library (data only, no DRC yet) | Required by the router path's writer; harmless to carry on the advisory path |
| 2d | `MANUFACTURINGGRID`, `UNITS` | Feeds Phase 1's scale factor |

**The model gap, stated honestly.**  `Layer` holds direction, span
preferences and GDS mapping but **no** width/pitch/spacing/area
(`src/layering.h`), and `TrackSlot` is `{type,label,width,space_after}`
(`src/routing_grid.h:31`).  Pitch/width/spacing/offset map cleanly onto
what exists.  Min-area, EOL spacing, parallel-run-length tables, cut
rules and enclosure **do not** — they need new structures, and they are
only required on the router path.  Phase 2 therefore reads and *stores*
what it can honour, and records the rest in a `lef_unmodelled` table so
nothing is silently dropped.

**Precedence rule** (must be explicit, mirroring `set_cell_layer_cap`
vs `set_layer_caps_by_depth`): an explicit `def_layer` /
`def_track_pattern` in a script **always** outranks imported tech data,
in either declaration order; imported values fill only what the script
left unspecified.  This keeps every existing flow byte-identical while
letting a real design drop the hand-typed stack entirely.

---

## 4. Phase 3 — DEF reader (≈ 3–4 weeks)

Today's DEF reader is a three-state line-at-a-time `std::regex` machine
(`src/bdb.cpp:1309`) handling `UNITS`, `DIEAREA`, `COMPONENTS`, `NETS`.
The checked-in `demo/ariane/ariane.def` *contains* 20 `TRACKS`
statements, 6 `GCELLGRID`s, 495 `PINS` and `SPECIALNETS` — every one
discarded today.

| Step | Content | Why it matters |
|---|---|---|
| 3a | Tokenizer + `COMPONENTS n`/`NETS n` **count reconciliation**, loud "imported X of Y" summary, hard error on a cell missing from LEF | Kills the silent **0.5 × 0.5 µm** fallback (`:1408`) that turns a wrong-LEF run into a plausible, entirely wrong floorplan |
| 3b | `TRACKS` → exact `TrackPattern` (origin/step/count), `GCELLGRID` | The real track grid, with the real offset — strictly better than deriving from LEF `PITCH` |
| 3c | `BLOCKAGES` + macro `OBS` + component `HALO` → `add_keepout` per layer | **The single most surprising omission for a routing tool.** The keepout machinery already exists (`src/routing_grid.h:197`, `src/topology.h:465`) — this is wiring, not new capability |
| 3d | `PINS` (top-level ports, currently skipped at `:1459`), `SPECIALNETS` (power straps → keepouts, and later pre-route geometry) | Ports are real endpoints; straps are real obstacles |
| 3e | `NONDEFAULTRULES` → the **landed NDR** feature (`def_ndr`/`set_ndr`) | Rare alignment: the internal feature already exists and matches the DEF concept |
| 3f | `VIAS` (custom via definitions) | Feeds the writer on the router path |

**Scale.**  The reader has only ever run on a 3 878-line floorplan DEF.
A real post-place DEF is 10⁶–10⁸ lines; per-line `std::regex` will not
do. The tokenizer replaces it, and Phase 3 must include a large-file
benchmark as an explicit acceptance criterion (target: ≥10⁶ lines in
well under a minute, memory measured and documented).

---

## 5. Phase 4 — DEF writer (≈ 2–3 weeks advisory / +4–6 router)

There is **no DEF writer today** — the only export is `export_gds`.
This is the gap that makes the tool's output "a picture, not a
constraint": GDS rectangles carry no net identity a P&R tool can adopt.

- **4a — advisory artifact (recommended first).**  Emit `BLOCKAGES +
  LAYER` for the bus corridors the planner reserved, plus `REGIONS`/
  `GROUPS` where useful.  Needs **no via model and no DRC** — the
  consumer is a router that must route *around/within* what BUDA
  planned.  This is the smallest artifact with real methodology value.
- **4b — routed nets (router path).**  `NETS … + ROUTED <layer> ( x y )
  ( x y )` from `net_segment` (which already carries
  `net_id, layer, x1..y2, width`) with vias named from the Phase 2c
  library, plus `SPECIALNETS` passthrough.  Deterministic ordering and a
  DEF→BUDA→DEF round-trip test, mirroring the discipline `gds_io`
  already demonstrates.

---

## 6. Phase 5 — the rest of Lens B (sizing only)

- **Tcl front end** over the existing command registry (~weeks; the
  registry is already a dict, so this is mostly mechanical).
- **Packaged wheel**; `BUDA_NO_APP=1` as the batch default on macOS.
- **Logging conventions**: severity levels, message IDs, non-overwriting
  logs (today a re-run silently overwrites unless `--log`/`--tag`).
- **Router path only**: real via geometry, DRC-legality checking against
  the subset of rules BUDA emits, DBU-exact everywhere.

---

## 7. Cross-cutting gates (non-negotiable)

1. **Byte-identity for existing flows.**  Nothing here may change a
   route for a script that declares its own stack.  The 41-flow corpus
   with WL ±0.00% is the gate, run base-vs-branch per phase.
2. **Every phase ships its own fixtures.**  Small, checked-in, diffable:
   a tech LEF, a macro LEF, a DEF with `TRACKS`/`BLOCKAGES`/`PINS`/
   `SPECIALNETS`, plus one large generated DEF for the scale benchmark.
3. **Loud on the unmodelled.**  Anything read but not honoured is
   recorded and reported, never silently dropped — the existing
   fail-loud philosophy applied to interchange.
4. **Schema versioning.**  Tech data belongs in the BDB (it is the
   central store, and `load_pipeline` must resume with the same stack) —
   a v23 migration, following the existing forward-migration discipline
   that refuses to open a newer database (`src/bdb.cpp:516`).

---

## 8. Sequencing and rough size

| Phase | Deliverable | Size |
|---|---|---|
| 0 | Exit codes + JSON report | ~3 days |
| 1 | Explicit import scale (DBU-exact option) | ~1 week |
| 2 | LEF reader (tokenizer, macros, tech layers, vias) | 3–4 weeks |
| 3 | DEF reader (tokenizer, TRACKS, blockages, pins, NDR) | 3–4 weeks |
| 4a | DEF writer — advisory (blockages/corridors) | 2–3 weeks |
| 4b | DEF writer — routed nets + vias (router path) | +4–6 weeks |
| 5 | Tcl, packaging, logging | 3–4 weeks |

**≈ one quarter to a credible advisory pilot** (0 → 4a), with 5 running
in parallel as capacity allows.  The router path adds a quarter or more
*plus* the DRC/via modelling that Analysis Lens B item 3 sizes as large
and open-ended.

## 9. Open decisions (owner input needed)

1. **Advisory vs flow-participating** — determines whether 4b and the
   via/DRC model are in scope at all.  Recommendation: advisory first,
   explicitly, and revisit after a pilot.
2. **DBU-exact import: opt-in or default?**  Opt-in keeps every flow
   byte-identical forever; default-on is the honest choice for new
   designs but needs a corpus migration.  Recommendation: opt-in in
   Phase 1, revisit once real designs are running.
3. **Does tech data persist in the BDB (v23) or stay session state?**
   Recommendation: persist — otherwise a resumed checkpoint silently
   plans against a different stack, the same class of bug the
   already-noted un-persisted `def_gds_layer` mapping has.
4. **Strict-exit default timing** — one release opt-in, then flip?
