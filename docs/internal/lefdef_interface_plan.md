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
- **`+ PARTIAL <maxDensity>`** density limits, the standard construct
  for steering congestion away from a region without forbidding it — the
  closest portable expression of "leave room here for the bus plan".

Corridors themselves are conveyed by the manifest, never as blockages.

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
> - **(d)** `set_unit_check on|warn|off`, bounds `[4, 1e7]` tracks across,
>   calibrated over 12 flows (24.4 … 797.2) against a ~1.2e6 physical
>   ceiling.  Fires on **0 of 41** corpus flows.
>
> The scaling hole the review raised is real and is documented rather than
> closed: script-declared distances stay in layout units and are NOT scaled
> by the import factor.  (d) is what makes that a stop instead of a silent
> optimistic plan.


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
and `+ PARTIAL <maxDensity>` density limits — **not** the corridors
themselves (see §0).  Deterministic ordering (sorted, like `gds_io`) and
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
