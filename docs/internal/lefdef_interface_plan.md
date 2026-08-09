# LEF/DEF interface — plan for closing Lens B

Status: **accepted (advisory path), 2026-08-08.**  The fork in §0 is
decided: BUDA targets the **advisory planner** role.  Sections marked
*router path* are explicitly out of scope and retained only so a future
revisit starts from a written baseline.  Companion to the Lens B
assessment in
[`../Analysis.md`](../Analysis.md), which found that BUDA is "a
self-contained planning environment with its own world model, not a
flow-participating point tool".  This is the plan to change that,
starting with the interface that gates everything else.

Every claim below was checked against the source; file:line references
are given so a reviewer can re-derive the conclusion rather than trust
it.

---

## 0. The target: advisory planner (decided)

Lens B named a fork that changes the cost by ~4×.  **It is decided in
favour of the advisory planner.**  The comparison is kept for the
record:

| | **Advisory planner** (recommended) | **Flow-participating router** |
|---|---|---|
| Writer emits | corridor manifest → native route guides, + DEF `BLOCKAGES` for keep-clear/density | DEF `NETS + ROUTED` with real vias |
| Needs a via library | no | **yes** |
| Needs DRC legality (width/spacing/area/enclosure) | no | **yes** |
| Needs DBU-exact geometry | desirable | **mandatory** |
| Rough size | 1–2 quarters | multi-quarter, and competes with mature routers |
| What the customer does with it | honours corridors during detailed routing | consumes the routes directly |

The advisory path was chosen because it matches what the tool is
demonstrably good at (early bus/corridor feasibility, layer budgeting,
congestion-aware planning before detailed routes exist) and because
BUDA's own corpus shows it does not yet converge clean at chip scale
(Analysis A10: 7/7 chip vehicles carry residual unplaced bits) — a
*corridor* plan with residual bits is still useful; a *final route* with
252 unplaced bits is not.

**What "advisory" commits us to, precisely.**  This deserves care,
because standard DEF can express *"do not route here"* but has no
standard construct for *"route this net here"* — only actual pre-routed
geometry, which is the router path.  So the advisory artifact is a
**pair**:

1. **A corridor manifest — the primary artifact.**  Machine-readable
   (JSON/CSV): per bundle, its nets, layer, and the rectangles reserved.
   A few lines of tool-specific Tcl turn it into native route guides
   (`create_route_guide` and equivalents).  BUDA ships the manifest plus
   one worked example; sites adapt it.  This is where the *positive*
   intent — "these nets belong in this corridor" — lives.
2. **DEF `BLOCKAGES` — the negative half only, used carefully.**

**A correction worth stating loudly, because the first draft got it
backwards** (review on PR #641): a DEF routing blockage makes geometry
unavailable to **all** routed nets, including the very buses the
corridor is reserved for.  Emitting a reserved corridor as a plain
blockage would tell the downstream router to route *around* the bus
plan — the exact opposite of the intent, and it would look plausible
while quietly destroying the plan.  `REGIONS`/`GROUPS` do not rescue it:
they constrain placement, not routing.

So blockages are used only where the semantics genuinely are negative:

- areas the plan requires to stay **clear** (e.g. lower-layer keep-outs
  under a reserved corridor), and
- **`+ PARTIAL <maxDensity>`** density limits over the corridors.

**A second correction, from the review of the built 4b** (Codex P1 on PR
#648): `PARTIAL maxDensity` is a **PLACEMENT**-blockage option in the DEF
5.8 grammar, not a LAYER routing-blockage one.  So the construct above is
not "steering congestion away from a region" — it caps how densely *cells*
may be placed under a planned bus.  That helps pin access and leaves the
area less contended, but DEF has **no** routing-density concept at all, so
it is not the reservation.  (Our own reader is permissive enough to accept
`LAYER … + PARTIAL`, which is why a round-trip test alone did not catch the
first, invalid, emission.)

The consequence is worth stating plainly rather than softening: the routing
intent lives in the manifest and **nowhere else**.  Corridors themselves are
conveyed by the manifest, never as blockages.

That split is honest about the format's limits and keeps the portable
part portable.  It also raises the Tcl work in Phase 5 from "nice to
have" to **the delivery vehicle for the primary artifact** — which
argues for pulling a minimal guide-emitter forward rather than leaving
all of Tcl to Phase 5.

**Deferred by this decision** (recorded, not deleted): the via library
(2c), DEF `VIAS` (3f), routed-net emission (4b), and all DRC/enclosure
modelling.  The trigger to revisit is a customer who wants BUDA's routes
*consumed* rather than *honoured* — at which point the reader work below
is already done and only the writer and the physical models remain.

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

## 2. Phase 1 — the coordinate + unit model (≈ 2 weeks; the highest-value structural fix)

> **LANDED** (2026-08).  All four sub-items (a)-(d) are built; the contract
> is written up as-built in [engine_units.md](engine_units.md), which is now
> the reference — this section is the reasoning that produced it.
>
> - **(a)** `set_import_scale micron|dbu|<n>` (BDB `set_import_scale` /
>   `set_import_scale_from_def_units`), applied at import only, persisted as
>   meta `lu_per_um` as a NUMBER and restored on open.  GDS import/export
>   convert through it too, so a scaled BDB cannot emit geometry
>   `lu_per_um`× too large in a valid-looking file.
> - **(b)** `set_track_pitch auto` derives the inter-bus gap from the grid.
>   **Opt-in, not a new default**: a derived gap is a larger reservation on
>   every patterned design — a QoR change, not a unit fix.
> - **(c)** [engine_units.md](engine_units.md).
> - **(d)** `set_unit_check on|warn|off`.  TWO signals, because one was not
>   enough: the *ratio* (tracks across, bounds `[0.5, 1e7]`, calibrated over
>   124 flows / 580 layer-rows spanning 3.66 … 797.2 against a ~1.2e6
>   physical ceiling) plus, for a design that DECLARED an import scale, the
>   *absolute* pitch check (`unit_pitch / lu_per_um` ∈ 0.005 … 500 µm).
>   Fires on **0 of 41** corpus flows and 0 of the 124 swept.
>
> The scaling hole the review raised is real and is documented rather than
> closed: script-declared distances stay in layout units and are NOT scaled
> by the import factor.  (d) is what makes that a stop instead of a silent
> optimistic plan — and building the regression for it is what exposed that
> the ratio signal alone could not: a 2000× mismatch on a 720 000-unit
> design reads 720 000 tracks across, nonsense that sits comfortably inside
> any physically-justifiable ratio bound.  The absolute check exists because
> of that measurement, not in anticipation of it.
>
> Two calibration lessons worth keeping:
> - a bound is only as good as the *population* it was measured over.  The
>   first minimum (4) came from the QoR corpus alone and broke six unit-test
>   fixtures, which are far smaller than any corpus vehicle.
> - a ratio is invariant under a consistent unit — which is what makes it
>   detect an inconsistent one, and also what caps how much it can detect.


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

**Scaling coordinates alone is NOT sufficient** (review on PR #641,
verified).  Engine units are used for *every* physical quantity, not
just coordinates, and several are hardcoded constants or script-declared
values that would stay in the old scale:

- bus width is `len(bits) * 1.5` — "1.5 layout-units per bit", a literal
  (`src/buda_cmds/bundling_cmds.py:703`);
- the NUTS inter-bus track pitch defaults to `1.0`
  (`src/buda_session/nutsflow.py:725`);
- the planner's default capacity model is `CapacityMode::WIDTH`
  (`src/congestion_planner.h:671`), which consumes those widths;
- plus every script-declared distance: `corner_margin`,
  `set_min_stub_length*`, `detour_channel`, `def_layer` span_min/max.

At 2000 DBU/µm a bus would reserve ~1/2000 of the space it needs, and
the run would look wildly feasible while meaning nothing.

**What makes this tractable** is that the width model already prefers
grid-derived geometry: `LayerStack::eff_bus_width` returns
`bits * layer.bit_pitch` whenever the layer has a track pattern, and
falls back to `base_width * dilution` only when it does not
(`src/layering.cpp:67`).  So in exactly the flow Phase 1 targets — one
with imported tech tracks — **bus width becomes grid-derived and
scale-correct automatically**, and the `1.5` literal is only the
no-pattern fallback.  It does *not* cover track pitch or the
script-declared distances above.

So Phase 1 is: (a) the import scale factor; (b) make the remaining
physical defaults grid-derived rather than literal — track pitch should
come from the layer pattern, as width already does; (c) treat every
script-declared distance as being in engine units and document that
contract in one place; and (d) add a cheap **unit-plausibility guard**
at plan entry — if total reserved bus width is a negligible fraction of
the die extent (or a track pitch is sub-unit against the grid), fail
loud.  That guard is what turns this whole class of error from "silently
optimistic plan" into a stop.

*Revised size: ~2 weeks, not 1.*  The alternative Codex raised — a typed
unit boundary (`struct Dbu`/`struct EngineUnit`) — is the rigorous fix
and worth costing separately if this recurs; it is large because every
geometry signature changes.

**Gate:** whole corpus byte-identical at the default factor; a DBU-mode
fixture round-trips DEF → BUDA → DEF with zero coordinate drift **and**
reserves bus widths within a few percent of the µm-mode run
(the check that would have caught the scaling hole).

---

## 3. Phase 2 — LEF reader (≈ 3–4 weeks)

> **2a / 2d LANDED** (2026-08).  `src/lefdef_lex.h` (the shared token layer)
> and `src/lef_io.{h,cpp}` (a recursive-descent LEF reader) replace the two
> line-oriented scanners in `bdb.cpp`, which now holds only the PROJECTION
> onto what the BDB stores.  2b (tech `LAYER` → synthesized `TrackPattern`)
> is deliberately separate: it changes what the planner sees, which the macro
> reader does not.
>
> What changed beyond "reads more":
> - **`ORIGIN` is honoured.**  Pin coordinates are relative to the geometry
>   origin, so ignoring it put every pin of such a macro somewhere it is not —
>   with no symptom until routing landed on nothing.
> - **Layout stopped being syntax.**  The scanners read line by line, so a
>   statement wrapped across lines — legal, and what tool-written files
>   eventually contain — was invisible.  The tokenizer honours `;`.
> - **A malformed technology file now stops the run**, with `file:line`,
>   instead of importing as a partial library that reads as complete.
> - **Unmodelled constructs are recorded**, not dropped: `LefLibrary::
>   unmodelled` with the line, plus a census in BDB meta (`lef_unmodelled`)
>   and a printed summary.  "We ignored it" and "it was not in the file" no
>   longer look the same.
>
> Fidelity evidence: on the two real LEF files in the tree
> (`demo/ariane/ariane.lef`, 45 pins; `tools/data/four_blocks.lef`) the new
> reader's projection is **identical** to a re-implementation of the old
> scanner — same pins, same coordinates, same directions.  Full tier 2590
> passed, 0 failed.
>
> Deliberately NOT changed: power/ground/clock pins are still not projected
> into BDB pin rows (they are pre-routes, not signal terminals) — but they
> are now read and reachable, so that is a decision at the projection
> boundary rather than a silent drop inside a parser.
>
> **2b LANDED** too: `import_lef_tech <file.lef> [top <N>]`.  ROUTING layers
> with a DIRECTION become layers; PITCH+WIDTH synthesizes an ALL-SIGNAL track
> pattern — the honest reading of LEF alone, since the file says nothing
> about which tracks a power grid takes (that is the DEF's SPECIALNETS).
> Layer ids come from the trailing integer in the name, so an imported stack
> and a script saying `def_layer 4` mean the same layer.  TOP is a BUDA
> notion LEF does not carry: topmost per direction by default.
>
> The precedence rule the plan asked to be explicit about is implemented in
> BOTH directions and tested that way — declared first, the import skips it
> (by name OR by id, two genuinely different collisions); declared later, it
> REPLACES what the import installed.  The second direction needed
> `LayerStack::remove_layer`: `add_layer` appends, so a duplicate id would
> have left both rows in the vector with lookups silently taking the first,
> i.e. the imported one.  An override that flips H/V also re-registers the
> imported pattern, which the routing grid stores direction-side.
>
> Known gap left open: `tools/def_cluster.py` and `tools/def_viz_o2.py` carry
> their own independent Python LEF scanners.  Consolidating them onto this
> reader is worth doing and is not part of Phase 2.


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
| ~~2c~~ | ~~`VIA`/`VIARULE` → via library~~ | **Deferred** (router path only) — see §0 |
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

> **LANDED** (2026-08).  `src/def_io.{h,cpp}` on the shared token layer
> replaces the three-state line-at-a-time `std::regex` machine; `bdb.cpp`
> keeps only the projection, and `import_def_lef` now RETURNS what it read.
>
> - **3a** Counts reconciled and reported (`imported X of Y`, marked on
>   mismatch).  A cell with no LEF footprint is now an **error** — the silent
>   0.5x0.5 µm fallback turned a wrong-LEF run into a plausible, entirely
>   wrong floorplan.  `allow_missing_footprints` overrides it out loud.
> - **3b** `TRACKS` → **bounded** `TrackPattern` + `GCELLGRID`.  The model
>   change the review asked for: a DEF `TRACKS … DO n STEP s` is an
>   ENUMERATION, and tiling past it invents tracks the technology does not
>   have.  Hand-declared patterns keep unbounded semantics by default.
> - **3c** `BLOCKAGES` + macro `OBS` + component `HALO` + power straps →
>   keepouts, on BOTH consumers (Floorplan for the planner, RoutingGrid for
>   DetailedNUTS — installing one leaves a blockage half the pipeline cannot
>   see).
> - **3d** `PINS` → boundary components behind an explicit `is_port` flag
>   (schema v23).  This is the plan's option (i); (ii) was not needed because
>   every downstream stage already understands components, and the flag is
>   what keeps the fiction visible to the database and the audits.
> - **3e** `NONDEFAULTRULES` are read and recorded; wiring them to the landed
>   `def_ndr`/`set_ndr` feature is left for when a design needs it, since the
>   mapping is per-rule content rather than a name.
>
> **Benchmark (the plan's acceptance criterion):** 1 020 007 lines /
> 340 000 components in **0.30 s** — ~1.15 M components/s, pinned by
> `test_a_million_line_def_parses_in_seconds`.  Target was "well under a
> minute".
>
> **Second review round** (Codex P1s on #649, all four verified against the
> source before touching anything; three were real defects, the fourth a
> real trade-off):
>
> - **A `PLACEMENT` blockage is not a routing keepout.**  It says where
>   *cells* may go, carries no layer, and a layerless keepout is installed on
>   EVERY routing layer — so importing one forbade signal routing through an
>   area the file left completely routable.  The round trip made it acute:
>   Phase 4b emits `PLACEMENT + PARTIAL` blockages over its own corridors, so
>   BUDA → DEF → BUDA turned each planned corridor into a hard keepout
>   against itself, the exact inversion Phase 4 exists to prevent.  Now
>   recorded as unmodelled — BUDA has no placement-legalisation stage to
>   apply it to.  `+ PARTIAL <density>` likewise: a density cap is not a
>   prohibition, and a hard keepout over-blocks it in the same direction.
> - **`UNPLACED` components no longer land on the origin.**  The reader's
>   default coordinates are (0,0), so a normal bbox stacked every unplaced
>   instance on top of every other one at the die corner and the pipeline
>   routed that pile as a floorplan; the note printed afterwards does not
>   undo geometry the next stage already believed.  They now get
>   `-1,-1,-1,-1` — the repo's EXISTING unplaced convention, written by
>   `import_verilog` — so there is one meaning of unplaced, not two.  Their
>   halos and macro OBS are skipped with them, and their pins keep the
>   cell's DIRECTION while leaving position unknown.
> - **Net-connection resolution was O(N²).**  Each connection re-scanned
>   `def.components` for its instance (and `def.pins` for its port), so the
>   reader built to survive a 10⁶-line DEF spent its time walking a vector.
>   Indexed by name once.  Measured, since the shape is the point: 20 000
>   components **1.86 s → 0.33 s** (5.6×), 40 000 **7.97 s → 0.58 s**
>   (13.8×) — the base grows 4.3× per doubling, the fix 1.7×.
> - **Whole-file buffering: partly fixed, and the rest stated rather than
>   waved away.**  `read_def` held the file three times over
>   (`ostringstream`, `.str()`, then the lexer's copy); it now reads into one
>   buffer and moves it, measured 42.5 → 38.2 MB on a 3.2 MB DEF.  It is
>   still NOT a streaming reader, and the honest reason is that the text is
>   not the dominant term: on a 19.7 MB / 1.02 M-line DEF the parse peaks at
>   ~115 MB, most of it the parsed model (340 k components at ~220 B each).
>   True streaming means inserting rows as they parse, which
>   `import_def_lef` cannot do today — it walks `def.components` a second
>   time for macro OBS, and net resolution needs the name index above.  The
>   working ceiling is therefore **~6× the file size**, recorded here so a
>   user can budget for it, with streaming as the named follow-up.
>
> **A finding, not a fix:** the checked-in `demo/ariane` pair is MISMATCHED —
> its DEF instantiates 133 × `fakeram45_256x16` while its LEF defines only
> `sram_asap7_16x256_1rw`, so under the old importer every macro on that
> 2.7 mm die was a 0.5 µm speck and nothing said so.  The demo data is left
> as it is (which of the two files is authoritative is the owner's call);
> the reader now refuses it unless told otherwise.


Today's DEF reader is a three-state line-at-a-time `std::regex` machine
(`src/bdb.cpp:1309`) handling `UNITS`, `DIEAREA`, `COMPONENTS`, `NETS`.
The checked-in `demo/ariane/ariane.def` *contains* 20 `TRACKS`
statements, 6 `GCELLGRID`s, 495 `PINS` and `SPECIALNETS` — every one
discarded today.

| Step | Content | Why it matters |
|---|---|---|
| 3a | Tokenizer + `COMPONENTS n`/`NETS n` **count reconciliation**, loud "imported X of Y" summary, hard error on a cell missing from LEF | Kills the silent **0.5 × 0.5 µm** fallback (`:1408`) that turns a wrong-LEF run into a plausible, entirely wrong floorplan |
| 3b | `TRACKS` → `TrackPattern` + **new finite bounds**, `GCELLGRID` | The real grid with the real offset. **Model change required** (see below) — `TrackPattern` is origin + repeating slots and tiles outward without limit (`src/routing_grid.h:39`), while a DEF `TRACKS … DO n STEP s` is a *finite* set |
| 3c | `BLOCKAGES` + macro `OBS` + component `HALO` → `add_keepout` per layer | **The single most surprising omission for a routing tool.** The keepout machinery already exists (`src/routing_grid.h:197`, `src/topology.h:465`) — this is wiring, not new capability |
| 3d | `PINS` (top-level ports, skipped at `:1459`) + **an endpoint model for them**, `SPECIALNETS` (power straps → keepouts) | **Parsing alone is insufficient** (see below): the bundler drops any pin whose `comp_id` is not a component at the bundling depth (`src/bundler.cpp:168`), so imported ports would be silently ignored |
| 3e | `NONDEFAULTRULES` → the **landed NDR** feature (`def_ndr`/`set_ndr`) | Rare alignment: the internal feature already exists and matches the DEF concept |
| ~~3f~~ | ~~`VIAS` (custom via definitions)~~ | **Deferred** (router path only) — see §0 |

**3b — the `TrackPattern` model must grow (review, PR #641).**  Mapping
`origin/step/count` onto today's type cannot be exact: `tracks_in_range`
tiles the pattern from the origin outward to cover whatever interval it
is asked about (`src/routing_grid.h:39-56`), so a query outside the
DEF-declared range would **invent** tracks that the technology does not
have — silently, and in the direction of optimism.  Phase 3b therefore
adds a finite extent (first/last, or origin + count) to `TrackPattern`,
with queries clamped to it.  Existing hand-declared patterns keep
unbounded semantics by default, so the corpus is unaffected.

**3d — die ports need a routable identity (review, PR #641).**  Reading
`PINS` does not make them endpoints.  `PinRow` is keyed by component,
and endpoint derivation skips any pin whose `comp_id` is absent from
`comp_by_id` or sits at another depth (`src/bundler.cpp:168`); a null
BDB `comp_id` would additionally read back as integer 0
(`src/bdb.cpp:1584`).  So a net reaching the die edge would be
**silently incomplete** — exactly the failure class `check_design`'s
`BUSTERM_OPEN` exists to prevent, arriving before the audit can see it.
Two candidate designs, to be chosen in 3d rather than assumed:
*(i)* synthesize a zero-area boundary **component** per port (cheapest —
every downstream stage already understands components, at the cost of a
fictional instance in the hierarchy), or *(ii)* add a first-class port
endpoint kind threaded through busterm derivation and topology
generation (cleaner, wider blast radius).  Recommendation: (i) behind an
explicit `is_port` flag on the component row, so the fiction is visible
in the database and to the audits.

**Scale.**  The reader has only ever run on a 3 878-line floorplan DEF.
A real post-place DEF is 10⁶–10⁸ lines; per-line `std::regex` will not
do. The tokenizer replaces it, and Phase 3 must include a large-file
benchmark as an explicit acceptance criterion (target: ≥10⁶ lines in
well under a minute, memory measured and documented).

---

## 5. Phase 4 — the advisory writer (≈ 2–3 weeks)

> **LANDED** (2026-08).  `src/buda_session/advisory.py` + `emit_guides` /
> `export_def_blockages`.
>
> **4a leads, and the ordering is the design.**  The manifest carries the
> POSITIVE intent — "route these nets here" — as JSON/CSV plus a worked
> `create_route_guide` Tcl script.  That is the thing BUDA actually computed
> and the thing DEF has no way to say; a GDS rectangle carries no net
> identity a router can adopt, which is what made the output a picture
> rather than a constraint.
>
> **4b carries only what DEF can honestly say.**  The obvious move — one
> `BLOCKAGES` rect per corridor — is exactly backwards: a blockage tells the
> router to STAY OUT, so it would forbid the routing the plan is asking for.
> What goes in is the design's real keepouts as hard blockages (that IS what
> a blockage means) and, opt-in, `+ PARTIAL <maxDensity>` PLACEMENT blockages
> over the corridors — a cap on cell density under a planned bus, which is
> the nearest thing DEF has and is **not** the reservation (§0's second
> correction: `PARTIAL` is a placement option, and DEF has no routing-density
> concept at all).
>
> Each corridor names **its own** nets, not the bundle's: a tapered fan-in
> branch (`Topology::seg_bits`) carries a subset, and guiding the whole
> bundle down it would be a wrong instruction rather than a loose one.
>
> Both halves of the plan's acceptance criterion are tested, and the second
> is the load-bearing one: `test_corridors_are_not_emitted_as_blockages`
> asserts that **no hard blockage overlaps any corridor**, so the §0
> correction is a test rather than untested prose.  Manifest and DEF are both
> byte-deterministic (the `gds_io` discipline), and the DEF round-trips
> through the Phase 3 reader.
>
> **Not done, deliberately:** router-path emission (`NETS … + ROUTED` with
> real vias) stays deferred per §0 — the data exists in
> `net_segment`/`net_via`, so the deferral costs only the writer.
>
> **Acceptance vehicle:** the plan named `demo/ariane`, but Phase 3 showed
> that pair is mismatched (its DEF's cells are absent from its LEF), so the
> worked example runs on a synthetic design instead.  Fixing the demo data is
> the owner's call.


There is **no DEF writer today** — the only export is `export_gds`.
This is the gap that makes the tool's output "a picture, not a
constraint": GDS rectangles carry no net identity a P&R tool can adopt.

**4a — corridor manifest + guide emitter (the primary artifact).**  Per
bundle: net names, layer, and the reserved rectangles from the placed
`bus_segment` extents plus margin, in JSON/CSV (reusing Phase 0's report
plumbing), together with one worked `create_route_guide`-style Tcl
script.  This carries the positive intent that DEF cannot express, so it
leads rather than follows.

**4b — DEF `BLOCKAGES`, negative semantics only.**  Keep-clear regions
and `+ PARTIAL <maxDensity>` PLACEMENT-density limits — **not** the
corridors themselves (see §0).  Deterministic ordering (sorted, like `gds_io`) and
a DEF → BUDA → DEF round-trip test.

**Acceptance for both:** a worked end-to-end example on the checked-in
`demo/ariane` DEF — import, plan, emit, and re-read the emitted DEF —
showing that the guides name the right nets and that no blockage
overlaps a corridor it is meant to protect.  Without that example the
artifact's semantics are untested prose.

*Router-path emission (`NETS … + ROUTED` with real vias) is deferred —
see §0.  The data for it already exists in `net_segment`/`net_via`, so
the deferral costs nothing but the writer and the physical models.*

---

## 6. Phase 5 — the rest of Lens B (sizing only)

> **BUILT: the Tcl front end and the logging conventions.**  Written up in
> [TCL_FRONT_END.md](../TCL_FRONT_END.md) and
> [message_ids.md](message_ids.md); the packaging bullet is **not** built and
> is re-scoped below.
>
> **Tcl.**  The sizing note above ("mostly mechanical; the registry is
> already a dict") was right about the commands and wrong about the shape.
> The mechanical build — `tkinter.Tcl()` inside Python — is wrong twice: it
> puts BUDA's Python in charge and asks the site's flow to run *under* it,
> which is the opposite of integrating into a flow that already exists; and
> it makes a Tcl front end depend on **tkinter**, a GUI toolkit, so a
> headless compute farm may not have it.  So the processes are inverted: the
> site's own `tclsh` is the parent, sources `tools/buda.tcl`, and BUDA runs
> behind a pipe (`tools/buda_server.py`).  Commands are discovered FROM the
> running engine, so the two sides cannot drift.  Errors follow Tcl's
> convention rather than BUDA's — a command that fails by *printing*
> `Error: …` still raises, because a flow that continues past a failed step
> ships a wrong result.
>
> Two things only a real interpreter exposed, both fixed: a fail-fast
> command needed its own status (`FATAL`) so a crash could not read as a
> clean finish, and the echo channel needed an explicit UTF-8 encoding —
> under a shell with no locale, `tclsh` defaults stdout to iso8859-1 and
> every `→` in the planner's output becomes `?`.  The second is also a
> lesson about the test: pytest's children inherit `LC_CTYPE=C.UTF-8` (PEP
> 538), so the first version of the regression passed with the fix removed
> and now strips the locale on purpose.
>
> **Logging.**  Message ids (`BUDA-<NNNN>: <SEVERITY>: <text>`), a registry
> that refuses an unregistered id, `dump_messages`, and non-overwriting flow
> logs (the previous run rotates to `<name>.1`).  Severity is read off the
> LINE, not the registry, which is what makes `set_unit_check warn` a
> *downgrade of BUDA-1901* rather than a different message — a methodology's
> gate on the id keeps working either way.  The counters now read the
> declared severity and fall back to the old prose regexes for unidentified
> output, so existing flow logs count identically; what changes is that a
> FATAL no longer counts as zero errors and a DEF count mismatch no longer
> counts as none.
>
> **Not built: the packaged wheel.**  It needs a build of the C++ extension
> per platform and per Python, which is its own CI problem with its own
> gates, and shipping an untested wheel is worse than shipping none.  The
> other half of that bullet — `BUDA_NO_APP=1` as the batch default on macOS
> — turned out to be **already satisfied**: `bin/buda`'s Darwin relaunch
> requires all three of stdin/stdout/stderr to be TTYs and skips `--no-viz`
> runs, so a redirected or batch invocation already falls through to the
> direct launch.  Adding a second mechanism for it would be redundant.

- **Tcl front end** over the existing command registry (~weeks; the
  registry is already a dict, so this is mostly mechanical).  **BUILT** —
  and the shape, not the command list, was the work.
- **Packaged wheel**; `BUDA_NO_APP=1` as the batch default on macOS.
  **Wheel: not built.**  `BUDA_NO_APP`: already satisfied by the existing
  tty/`--no-viz` guards in `bin/buda`.
- **Logging conventions**: severity levels, message IDs, non-overwriting
  logs (today a re-run silently overwrites unless `--log`/`--tag`).
  **BUILT.**
- **Router path only**: real via geometry, DRC-legality checking against
  the subset of rules BUDA emits, DBU-exact everywhere.  *Deferred by the
  §0 decision.*

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
| 1 | Import scale + unit consistency (defaults, guard) | ~2 weeks |
| 2 | LEF reader (tokenizer, macros, tech layers, vias) | 3–4 weeks |
| 3 | DEF reader (tokenizer, TRACKS, blockages, pins, NDR) | 3–4 weeks |
| 4a | Corridor manifest + guide emitter + worked Tcl | 2 weeks |
| 4b | DEF `BLOCKAGES` (keep-clear / `PARTIAL` density) | 1 week |
| 5 | Tcl front end, packaging, logging | 3–4 weeks |
| — | ~~router path: vias, routed nets, DRC~~ | **deferred, §0** |

**≈ one quarter to a credible advisory pilot** (0 → 4b).  Phase 5's Tcl
work is no longer optional polish: it is how half the advisory artifact
reaches the customer's tool, so it should start as soon as Phase 4b's
manifest schema is fixed.

## 9. Decisions

1. **Advisory vs flow-participating** — **DECIDED: advisory** (§0).
   Via library, DEF `VIAS`, routed-net emission and DRC modelling are
   deferred with a written revisit trigger.
2. **DBU-exact import: opt-in or default?**  Proceeding **opt-in** in
   Phase 1 unless directed otherwise — it keeps every existing flow
   byte-identical, and the corpus gate stays meaningful throughout.
   Revisit once real designs are running.
3. **Tech data in the BDB or session state?**  Proceeding with
   **persisted (schema v23)** — otherwise a resumed checkpoint silently
   plans against a different stack, the same bug class as the currently
   un-persisted `def_gds_layer` mapping.
4. **Strict-exit default timing** — one release opt-in, then flip the
   default.  Open to a faster flip if no external scripts depend on the
   current exit-0 behaviour.
