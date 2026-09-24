# The judge — an audit that is not the router

Convergence-ladder build item 2 ([convergence_ladder.md](convergence_ladder.md),
"The judge must not be BUDA"), built 2026-09-20.

> If DetailedNUTS routes both arms and `check_design` scores them, a skeptic
> says the referee wears our jersey.

`tools/independent_audit.py` is the answer: it reads the persisted BDB tables
with `sqlite3` from the standard library and checks the route from geometry
alone.  It was specified to come BEFORE any A/B table was written, and did
not — E4, E2, E1 and E5 (6b, 6c and 6d with it) were all scored by
`check_design`.  This page is what it does, what it refuses to claim, and
what it found on its first application to those rounds.

## Running it

```bash
tools/independent_audit.py <design.bdb|design.bdb.sql>    # 0 clean, 1 dirty, 2 unjudgeable
tools/independent_audit.py ckpt.bdb --json res.json --quiet
```

A flow that opens `:memory:` leaves nothing to judge; give it a durable home
with the redirect `btcl -b` arms, which changes no flow text:

```bash
BUDA_BDB_MEMORY_TO=/tmp/run.bdb bin/buda flow/soc_small.buda --no-viz
tools/independent_audit.py /tmp/run.bdb
```

In the convergence driver, **`converge.tcl -judge`** does that per round and
puts the verdict in the table as its own column, beside the engine's:

```
| vehicle | size | heal | arm | round | ... | final ovl/unpl/viol | ... | judge |
| soc     | 2    | off  | td  | 0     | ... | 0/0/0               | ... | clean |
| soc     | 2    | off  | blind | 1   | ... | 1/8/8               | ... | 104   |
```

Two columns, never one.  A row where they disagree is the finding the judge
exists to make possible, so the driver reports what the judge said and never
substitutes its own reading: `clean`, a violation count, or `—` when the
judge declined to judge — which is not clean and must not read as it.  (A
fourth status, **3**, says the design WAS judged and the `--json` file could
not be written — see the fourth pass below.)

## What it checks

| kind | what it says |
|---|---|
| `OFF_GRID` | a bit-wire whose METAL is not centred on a SIGNAL slot of its layer's effective pattern |
| | — the stored RECTANGLE, not the `track_position` recorded beside it: the rectangle is the wire and the scalar is the router's claim about it |
| `ROW_MISMATCH` | a bit-wire whose recorded `track_position` contradicts that rectangle — they are redundant by construction, so one of them is stale |
| `SHORT` | two DIFFERENT nets whose metal overlaps on one layer |
| `KEEPOUT` | a bit-wire lying over a keepout that blocks its layer |
| | — the DECLARED zones **and** the ones nobody declares: every solid leaf cell's footprint, on every non-TOP layer, which is the list `Floorplan::low_layer_keepouts` hands the engine's own audit |
| `OPEN` | a net whose metal is not one connected piece, or that does not reach an endpoint block |
| | — two wires count as joined by a via only where the via LANDS ON BOTH: a `net_via` row *says* two segments are joined, and whether they are is geometry |
| `NO_METAL` | a net a bundle carries with no placed metal at all |
| `LAYER_DIR` | a bit-wire running across its layer's declared direction |

It imports **no engine** — not `buda`, not `buda_db`, not `src/`, not the
`tools/` modules that do.  Where a track lies, what a keepout blocks, what
"connected" means: all re-derived here from the stored numbers, so a defect
in the engine's arithmetic surfaces as a DISAGREEMENT instead of being
reproduced faithfully by both sides.  That claim is tested twice
(`test_independent_audit.py`), because each check alone has a hole: the
source is scanned for a forbidden import, and the tool is RUN in a
subprocess with `PYTHONPATH` emptied from a directory that is not the repo.
The second is the one that matters, and it has a trap this repository has
been caught by before — `conftest.py` pins the repo's own `build/` at
`sys.path[0]`, so an in-process check finds `buda` importable whatever the
tool does.

**A judge is worth what it catches**, so the tests are a mutation matrix:
each fault is planted in the tables in SQL, one at a time, and the judge must
name it.  Reverting any one of its **thirty-three** rules fails at least one
named test: short, keepout, off-grid, layer-direction, metal in two pieces,
a net that does not reach its block, a net with no metal, a via that does not
land on the wires it claims to join, the abutment control (a T-junction
touches and does not overlap, and a judge that called that a short would
fail every correct route), the unjudgeable exit status, both halves of the
hierarchy rule below, both halves of the membership fallback (falling back
at all, and saying so), both halves of the rectangle rule below, the
partial-coverage refusal, the unreadable-input status, the
override-crossing note, the implicit leaf-cell keepout, its TOP-layer
control (the same wire over the same cell on a TOP layer is ordinary
over-the-cell routing and must stay clean), and the note a checkpoint gets
when it does not record which layers are TOP, a keepout with no layer set
blocking every layer, the override order read from the stored ordinal, and
a failed `--json` write keeping its own status, a structurally malformed
slot list and a structurally malformed layer-stack row each being
unjudgeable, judging the metal rather than the recorded position, reporting
the two when they disagree, and taking the perpendicular axis from the
LAYER — with the two controls that keep the last three apart (a row moved
consistently is `OFF_GRID` alone, a scalar moved alone is `ROW_MISMATCH`
alone), an unresolvable net id and an unresolvable bundle membership row
each being unjudgeable, a malformed `keepout.layers` CSV being unjudgeable,
and its control (an EMPTY list is the every-layer convention, not a
malformed one).  (The count read *eighteen*
while the list held nineteen — the override-crossing note was added to the
list and not to the number.)

## What it deliberately does not claim

A clean verdict here is about GEOMETRY and nothing else: not timing, not
DRC at a real detailed router's rule deck, nothing the ladder page's
"What is deliberately not claimed" disclaims.  Six limitations are stated
rather than left to be discovered:

* **Region overrides** make the effective pattern a function of position,
  and a wire is judged at its MIDPOINT.  One whose span crosses an override
  boundary is counted and reported as a note, not a violation: whether the
  engine is entitled to place such a wire is a modelling question this file
  has not settled, and a judge must not manufacture violations out of one.
* **Reach is judged against the endpoint COMPONENT's bbox**, not the pin
  rectangle — that is the contract BUDA routes to, a bundle landing on a
  block face with the block's own routing taking it from there.
* **An unplaced component** (the `-1,-1,-1,-1` convention) is skipped for
  reach, counted, and named.
* **A MULTI-RECT leaf cell contributes no implicit keepout.**  Its rects
  tile its bbox exactly (`set_cell_rects` requires the union to EQUAL the
  declared extent), so judging the bbox would claim the notches too and
  accuse a wire routed through a gap the design left open.  Skipped,
  counted, and named — and a checkpoint that does not record which layers
  are TOP gets the whole rule skipped, with a note saying `KEEPOUT` covered
  the declared zones alone.
* **A design with no `bundle_net` rows** cannot say which nets were supposed
  to carry metal, so the scope falls back to the nets that HAVE metal —
  shorts and broken metal are still judged, `NO_METAL` is not — and the
  fallback is NAMED in the output.  A silent narrowing is how an audit comes
  to mean less than its reader thinks.
* **Non-default rules are not modelled.**  A bit governed by an NDR width
  (`def_ndr … width x1.5`) spans several signal slots, so its metal is
  centred between slot centres and this file reports it `OFF_GRID` — a
  FALSE POSITIVE class, not a routing fault.  Measured on
  `flow/ndr_shield_hier.buda` built into a named file (found by
  `tools/bundling_ab.py`'s judge row, #953): 20 `OFF_GRID` on the bundled
  arm and 30 on the one-net arm, every one on an NDR-governed net (`loc_*`,
  `t2x_*`), where `check_design` reports none; the one-net arm's single
  `OPEN` (`tdef_0` not reaching `u1`) is the fault `check_design` reports
  too, so the two agree on the real one.  Read a judge row on an NDR design
  with that in mind — through `converge.tcl -judge` as much as through the
  A/B.  Modelling it means reading `ndr_rule` / `bundle.ndr_rule` and
  accepting a run of `width_slots` slots as one wire, which is the engine's
  own `ndr_spec_for_layer` arithmetic re-derived here, not imported.

### Five, from review

Codex's two passes found five, and the first four are the same thing — a
judge must not pass, or mis-say, what it did not evaluate:

* **Overlap is a property of two rectangles**, not of what either wire says
  about itself.  The short check bucketed and intersected through each
  wire's own `along`/`perp`, which are a function of its `is_horiz`, so a
  wire with the WRONG orientation — the `LAYER_DIR` case — was binned on a
  different physical axis from its neighbours and then compared one wire's
  x-interval against the other's y-interval.  A checkpoint could report
  `LAYER_DIR` and hide the `SHORT` in the very same metal.  The layer now
  picks ONE axis and intersects the stored rectangles.
* **Partial coverage is not a clean verdict.**  The guard refused a design
  with no track pattern at all, but a checkpoint patterned on SOME of its
  routed layers passed it, left `OFF_GRID` unevaluated for the rest, and —
  with nothing else firing — exited 0, so a converge table would read
  `clean` for metal nothing judged.  A routed layer with no pattern is now
  exit 2, with the layer named.
* **Exit 1 is for violations.**  A path that does not exist raised
  `SystemExit(str)`, which exits **1** — so automation gating on the
  documented contract booked a typo'd path as a dirty route.  A design that
  cannot be READ is unjudgeable: exit 2.
* **A wire inside an override crosses no boundary.**  The note counted any
  wire whose rectangle INTERSECTED an override, so an ordinary wire routed
  inside one was reported as spanning a boundary it never reaches.  The
  honest test is whether the effective pattern CHANGES along the wire, which
  is what the note claims, so it now samples the two ends and the midpoint
  and compares the patterns.

And one outside the judge, in the engine it reads:

* **Overlapping region overrides lost their declaration order on restore.**
  `effective_pattern_at` returns the FIRST override containing a point, and
  `grid_overrides()` handed them back `ORDER BY layer_id,x1,y1,x2,y2` — so a
  checkpoint could resolve an overlap to a different pattern than the
  session that wrote it, silently, both patterns being legal.

### The third pass

Codex was asked to look again and found four more, all real.  Three are the
first pass's lesson at a finer grain — a judge must not report what it did
not evaluate, and must not report a number it did not produce — and one is
the P1 above.

* **An unreadable database is unjudgeable, not dirty.**  The missing-path
  guard covered a path with no file; a file that EXISTS and cannot be parsed
  — a truncated `.bdb`, a half-written `.bdb.sql`, a stored slot list that is
  not JSON — raised out of `audit()` and exited **1** through Python's own
  traceback, which is the status this page documents as violations.  Caught
  around the whole audit rather than at the open, because the reads are lazy
  and a corrupt page surfaces at whichever query first touches it.
* **The stale judge sidecar.**  `converge.tcl` reads `<round>.judge.json`
  for the violation total, and the judge writes it only on a verdict — so a
  rerun of a named round whose judge died before writing left the previous
  run's file in place, and the run published a sidecar naming this round
  while holding the last one's numbers.  Cleared before the judge runs, as
  the checkpoint and the report already were.
* **The override order needed an explicit ordinal after all.**  `ORDER BY
  rowid` fixed the coordinate sort and left two holes the second pass named:
  an upsert keeps a re-declared region's ORIGINAL rowid, so a rebuild
  declaring the same regions in a new order wrote a checkpoint that restored
  the OLD winner, and `DO UPDATE` stored the LATER pattern for a repeated
  region where the live grid keeps the EARLIER one.  Both are gone: the set
  is written WHOLE, in the order the live grid holds it, with the order
  stored (`grid_override.ord`, schema **v31**) and a repeat collapsing to its
  first declaration.  The journal is what makes that exact — it already held
  every declaration in order, and the restore now records what IT installs in
  the place `add_override` puts it, so mirroring the journal is mirroring the
  grid and there is no second rule to keep in step.  Measured while pinning
  it: the rebuild-in-a-new-order case does NOT diverge on its own, because
  `open_bdb` installs the stored overrides into the live grid before the
  flow's lines run, so the rebuilding session inherits the stored order too.
  The divergence is real and reachable through the PRE-OPEN declaration,
  which is the commonest shape in this tree — the same asymmetry the journal
  itself exists for — and that is the case the test plants.
* **The keepouts nobody declares.**  The P1, and its own section below.

### The fourth pass

Three more, all real, and two of them are the same lesson as the third
pass's: a status that is not a verdict must not wear a verdict's number.

* **A keepout with no layer set blocks EVERY layer (P1).**  That is the
  engine's convention wherever a zone is tested — `verify.cpp::zone_on_layer`
  is `layer_ids.empty() || layer_ids.count(layer)`, `nuts_geom.h`'s
  `keepout_occupied` reads the same way — and it is what the restore builds,
  since the stored CSV parses to an empty list and `add_keepout_zone` takes
  it as-is.  This file had it exactly BACKWARDS and said so in a comment
  ("an empty layer list governs no layer — that is what the restore does
  with it"), so such a zone blocked nothing here and could carry a design to
  a clean verdict.  A comment asserting the opposite of the code it
  describes is worse than none: it is what a reader checks instead of
  checking.  No design in this tree carries one — every checkpoint measured
  reads 0 of 0 — so it was a latent hole rather than a live miss, which is
  exactly the kind a mutation test is for.
* **The override order needed reading, not just storing.**  The engine's v31
  fix gave `grid_override` its declaration ordinal and `BDB::grid_overrides`
  reads `ORDER BY ord, rowid`; this file still ordered by `rowid` alone, so
  on a checkpoint whose `ord` disagrees with row order the judge resolved an
  overlap differently from the session that wrote it — an OFF_GRID reported
  where there is none, or one missed.  Fixing the writer and leaving the
  second reader behind is its own failure mode, and the reason it is worth
  naming: the judge reads the same tables the engine does, so every schema
  change has two sides here.
* **A failed `--json` write is not a violation.**  The write happens after
  the verdict, outside the guarded audit, so an unwritable path exited **1**
  — the status reserved for geometry violations — and a clean design would
  be booked as dirty by a caller gating on the contract.  It is not exit 2
  either: the design WAS judged.  It gets its own status, **3**, and the
  message carries the verdict so nothing is lost when `--quiet` silenced the
  report.

None of them moved a verdict: every design in the table below judges exactly as
it did before, the bottom-up finding bundle for bundle.  That is what a
correctness fix to a judge should look like — it changes what the file is
ENTITLED to say, not what it happened to say here.

### The fifth pass

Two, both real, and the second is the one worth keeping in mind.

* **`json.loads` succeeding is not the same as the row being readable.**
  `'[{}]'` is valid JSON; so is a slot whose width is the string `"x"`, and
  so is a bare object where a list belongs.  `JSONDecodeError` therefore
  never fires for any of them, and the fault surfaced later as a
  `KeyError`/`TypeError`/`ValueError` out of the reader, exiting **1**
  through Python's own traceback — the third pass's unreadable-input fix
  reached through the one door it had left open.  Those three are caught at
  the PARSE, where the row can be named, rather than widened into
  `UNREADABLE`: design-wide they are also the shape of an ordinary defect in
  this file, and a judge that reports its own bugs as an unjudgeable design
  is precisely what naming that set was for.  The finding named the slot
  list; `read_layer_stack` does `int(d["id"])` on the `meta` row the same
  way, so both readers go through the one guard — fixing one and leaving the
  other is the failure mode the override ordinal had already taught here.

* **The judge was reading the router's claim, not its metal.**  A
  `net_segment` row carries the track position TWICE: as the scalar the
  engine recorded and as the rectangle `persist.py` derives from it
  (`track_position ± width/2`, and it is the only writer).  `check_on_grid`
  tested the scalar.  So a rectangle moved off the grid under a
  `track_position` still naming a signal track was called **CLEAN** —
  reproduced before the fix on the two-instance vehicle by shifting one
  wire's rectangle half a slot and leaving the scalar alone: exit 0, verdict
  clean, while the same shift applied to BOTH fields is reported.  Every
  other check in this file reads the rectangle, so the one check that did
  not was also the one grading the engine's arithmetic instead of its
  geometry, which is the thing this file exists not to do.

  Now the rectangle is judged and the disagreement is its own kind,
  `ROW_MISMATCH`, rather than a silent preference for either field: a stale
  scalar over sound metal and sound metal under a stale scalar are different
  faults, and neither is `OFF_GRID`.  Two controls hold them apart, because
  a fix that reported both on every off-grid wire would pass the first test
  while saying nothing.

  Building it exposed a flaw in the first cut, and an existing test caught
  it: taking the perpendicular axis from the row's own `is_horiz` made a
  single flipped flag report as THREE faults — `LAYER_DIR`, correctly, plus
  an `OFF_GRID` and a `ROW_MISMATCH` read off the wire's own length.  The
  metal had not moved; only a claim about it had.  The axis comes from the
  LAYER's declared direction now, which is the same principle as the fix
  itself one level down.

  Latent here, like the empty-layer keepout: `ROW_MISMATCH` is **0** on
  every checkpoint measured, the disagreeing pair being a persistence fault
  no run in this tree produces.  So no verdict moves — soc_small, soc_mid,
  the four hier flows and the mesh control still clean, the bottom-up rounds
  still exactly 104 and 201, the finding's 96 off-grid copies bundle for
  bundle.

### The sixth pass

Two, and both are a stored field the judge read without asking whether it
could.

* **An unresolvable net id was collapsed, not refused.**  A wire whose
  `net_id` named no `net` row took the EMPTY name, so two DIFFERENT nets
  read as one and their overlapping metal was not a short.  Measured on the
  two-instance vehicle by overlapping `x_0` onto `loc_0` — 70 x 2 units of
  real metal — and then deleting the two `net` rows: `SHORT` while the names
  resolve, **exit 0 CLEAN** once they are gone.  The id alone would keep the
  two apart, but it would give the connectivity checks no endpoints, and the
  rule here since the partial-coverage refusal is that a partial verdict
  must not read as a clean one — so the design is refused, naming the count
  and the first wire.

  `bundle_net` is refused the same way, which is beyond the finding: that
  table IS the scope `NO_METAL` is judged over, so a row naming a net
  nothing defines would judge that kind over a subset and still report
  clean.  The fifth pass had already taught that fixing one reader of a
  stored field and leaving its twin is its own failure mode.

* **A malformed `keepout.layers` CSV was a traceback.**  `int('M4')`, or the
  `x` of `6,x`, raised out of a reader neither `_decode` nor `UNREADABLE`
  covered, exiting **1**.  Same treatment as the fifth pass's JSON rows and
  for the same reason.  Its control matters as much as the fix: an EMPTY
  list is the convention for a zone that blocks every layer (the fourth
  pass's P1), so a guard that rejected it would quietly turn that fix off
  while looking stricter.

Both latent: a census of all **34** checkpoints on disk reads **0**
unresolved net ids and **0** malformed layer tokens, and `persist.py` binds
every name through `_ensure_net`, so neither can fire on a file the engine
wrote.  No verdict moves — soc_small, soc_mid, the four hier flows and the
mesh control still clean, the bottom-up rounds still exactly 104 and 201.

### The keepouts nobody declares, and what measuring them showed

Every keepout-aware stage in the engine tests against
`Floorplan::low_layer_keepouts` — the declared zones PLUS one zone per solid
non-container leaf block on the non-TOP layers — and `verify.cpp` audits
against that same list.  The judge read only the `keepout` table, so it could
call a LOW-layer wire lying over a cell clean where `check_design` reports
`KEEPOUT_CROSS`: the one direction a referee must never be weaker in.

Adopting the rule was not obvious, and the reason is the hierarchy mistake in
a new direction.  The engine enforces it PER ROUTING FRAME, where a container
is transparent and a deep leaf inside it is not in the frame at all — so a
top-level LOW wire over a distant cell is permitted there, and a judge
applying leaf footprints over the whole design is STRICTER than the engine.
That is the right way round for a judge (the metal is physically over a cell
either way), but "stricter" is exactly how the 2048-OPEN rule started, so it
was **measured before it was adopted**, with a probe that applies the rule
universally and counts:

| design | leaves | wires on a non-TOP layer | crossings |
|---|---:|---:|---:|
| `flow/soc_small.buda` | 219 | 5,680 | **0** |
| `flow/soc_mid.buda` | 843 | 21,456 | **0** |
| `soc.tcl 2 -bottomup` | 63 | 536 | **0** |
| control: the same probe on the TOP layers, soc_small | 219 | 3,648 | 1,461 |

The control is the half that makes the zeros mean something: over-the-cell
routing on a TOP layer is everywhere, so the probe can see crossings when
there are any.  The rule is now applied, and every verdict in the table below
is unchanged with it live — soc_small clean over **657** implicit zones,
soc_mid clean over **2,529**, the mesh control clean over **288**, the
bottom-up round still exactly 96 `OFF_GRID` and 0 `KEEPOUT`.

It needed one thing from the engine, for the same reason the grid did: the
checkpoint holds `track_pattern.is_horiz` and nothing that says which layers
are **TOP**, and TOP is not decoration here — it is what decides whether a
cell's footprint blocks a wire.  The session now writes the layer stack as a
meta row beside the route snapshot (one site, which cannot be reached without
the stack being current, where `def_layer` and `import_lef_tech` and one
replacing the other are four).  A checkpoint written before that judges
without the rule and SAYS so.

### The hierarchy rule, and how it was got wrong first

The first rule written here required every pin-bearing component to be
reached, and `flow/soc_small.buda` came back with **2048 OPENs** against a
route `check_design` calls clean.  The judge was wrong, not BUDA: a
hierarchy propagates a net's pin to every ancestor up to the common one, so
one net names a leaf AND the container holding it, while the bundle routes
to the container FACE.  Measured on `da_0_0_0`: the wire runs y 464..608 at
x 260, landing exactly on `quad_0/cl_0/core`'s top edge and
`quad_0/cl_0/l1d`'s bottom edge, while `core/regf` (296..448) and
`l1d/tag` (624..904) sit inside them, reached by those cells' own routing.

Reach is now required of the **outermost pin-bearing components** only,
which is exactly what the hierarchy licenses and no more: an inner
component is satisfied by an ancestor that CARRIES A PIN OF THIS NET, never
by a container that merely encloses it.  Both halves are pinned in both
directions on `flow/hier_testcase.buda`.

## What it needed from the engine

Nothing in the tree could be judged at first, and the reason was a real gap
rather than a missing convenience.  The routing grid and the keepouts got a
table in v29 so a resume routes against the same physical model the build
did — persisted by WRITE-THROUGH at each declaration, which reaches only the
declarations made while a BDB is open.  `source flow/tracks/tracks.buda`
before `open_bdb` is the order nearly every flow here is written in, so
those checkpoints carried a route and **zero `track_pattern` rows**: the
failure v29 exists to remove, reached from the other side.

The session now journals each grid declaration and `open_bdb` replays the
journal after the restore.  Precedence is unchanged — what the checkpoint
holds fills in what this session has not declared, what this session
declared wins and is what gets stored — and both orders now store the same
patterns, asserted row for row.

The second thing it needed is the same shape: the **layer stack**, written as
a meta row beside the route snapshot, because `track_pattern` says which way
a layer runs and nothing said whether it is TOP — and without that the
implicit leaf keepouts above cannot be judged at all.  Both are facts the
route is meaningless without, and in both cases the checkpoint held the route
and not the fact.

## First verdicts

Every design judged so far, with `check_design`'s verdict beside it.

**Provenance, because this page's own subject is who is entitled to score
what**: every row below was re-measured on a build that MATCHES the source.
The first pass was not — this container's `build/` was 53 minutes' work and
53 HOURS old, predating C++ that had landed on `main`, which
`tools/measure_guard.py` says exactly the right thing about: *"the sweep
would measure a binary that does not match the source — a wrong number, not
an error."*  It caught it, in a mid-tier test, after the rows were taken.
On the rebuild every one of them reproduces — all six flows, both controls
and both bottom-up rounds, detailed wirelengths included (525,144 for the
top-down round, 550,528 for the mesh, 584,581 and 980,908 for the two
bottom-up ones) — and the finding below reproduces bundle for bundle and
track for track.  So nothing here moved — but it is recorded rather than
quietly dropped, because "I re-ran it and it was the same" and "I never
checked" are indistinguishable in a table.

| design | `check_design` | judge |
|---|---|---|
| two-instance reserve vehicle (24 wires) | Success | clean |
| `flow/hier_bundle1`, `hier_four_blocks`, `hier_four_blocks_cell`, `hier_testcase` | Success | clean |
| `flow/soc_small.buda` (9328 wires, 6 layers) | Success | clean |
| `flow/soc_mid.buda` (**35,096** wires, 6 layers — judged in 1.2 s) | Success | clean |
| `converge tpu 8 -arms blind` (mesh, bottom-up) | 0/0/0 | clean |
| `converge soc 2 -arms td` (no copies) | 0/0/0 | clean |
| `converge soc 2 -arms blind` (bottom-up) | 1/8/8 | **104** |
| `converge soc 4 -arms blind` (bottom-up) | 5/117/117 | **201** |
| `converge soc 2 -arms blind`, after the #946 fix | 1/8/8 | 8 |
| `converge soc 4 -arms blind`, after the #946 fix | 5/85/85 | 73 |

The agreements are the point as much as the disagreements: a judge that
never agrees is measuring itself.

## The first finding: a bottom-up copy lands off the grid

On the SoC's bottom-up arm the judge reports **96 bit-wires on no signal
track at all**, at NQ = 2 and NQ = 4 alike, and no in-house audit mentions
them.  The engine's own verdict for that NQ = 2 round is `1/8/8` — one
overlap and the eight `pc_0` bits whose supply-doomed seat is already
documented ([flow/tcl/soc.md](../../flow/tcl/soc.md)); the judge finds those
eight too, as `OPEN`.  The 96 are new.

What is MEASURED, on
`converge.tcl soc 2 -arms blind -maxreserve 0 -informed 0 -judge`:

* All 96 belong to bundles 127, 128 and 129 — `bu_locked` **copies** of
  `core_cell`'s `regf→alu` template at `quad_0/cl_1/core`,
  `quad_1/cl_0/core` and `quad_1/cl_1/core`.
* The **reference** instance's bundle (126, `quad_0/cl_0/core`) is 32/32 on
  grid.  Each copy is **0/32**.
* The copy is a rigid translation by the instance offset: the reference's
  first bit sits at x = 336.5 and `quad_0/cl_1/core`'s at 1360.5, exactly
  1024 apart — the instances' Δx.
* M3 is vertical with an 18-unit period whose signal centres fall at
  3.5, 5, 6.5, 8, 12.5, 14, 15.5, 17.  1024 mod 18 = 16, so every copied
  bit lands off every slot; 1360.5 sits INSIDE the GROUND slot spanning
  1359..1361.
* The instances' x phases mod 18 are 4, 2, 14 and 12 — four different
  phases, one template.
* `align_bottom_up` had already said so in the same run: *"quad_0/cl_1/core
  (inside a marked parent) sits off cell 'core_cell's chosen phase — the
  parent template places it at an incompatible offset; not fixable by
  translation"*, and reverted its one attempted move.
* `check_template_tracks` nevertheless reports **`cell 'core_cell':
  ALIGNED — 4 instance(s) see identical signal tracks (ref
  quad_0/cl_0/core, 12 window(s) compared)`**, so DNUTS copied.
* The other layers are fine: the same cell's M5 and M6 copies are 32/32 on
  grid (M6 is horizontal and every instance shares y = 112; the M5 deltas
  are multiples of its period).

So the checker's verdict and the placed metal disagreed, on a cell the
aligner had already flagged.

**The mechanism** (found and fixed under #946, 2026-09-24): the core's M3
segment lies over a LOW-layer keepout, so the SPAN-CLEAR pool
(`signal_tracks_in_span`) that `check_template_tracks` compared was EMPTY
at the reference and at every copy — empty against empty reads identical,
hence `ALIGNED`.  DetailedNUTS does not stop at an empty span-clear pool:
when it is short of the bits it falls back to the MIDPOINT pool
(`signal_tracks_in` at the span midpoint, `detailed_nuts.cpp`), and those
pools are a phase apart (reference relative positions 190.0, 191.5, 193.0 …,
`quad_0/cl_1/core` 190.5, 192.0 …, 64 tracks against 63).  The reference
solved on its midpoint pool, and the copy transplanted those positions onto
instances whose own midpoint pools are elsewhere.  `rel_tracks` did see a
phase shift wherever it LOOKED; it looked at the one pool that was empty.

The check now compares every pool DetailedNUTS can seat the bits from —
the span-clear pool and the midpoint fallback — and reports the latter by
name (`L3 seg0 midpoint: 64 track(s) vs 63 at reference`), so `core_cell`
reads MISALIGNED and, under `on_mismatch independent`, the three copies are
solved on their own tracks.  Measured: NQ = 2 goes from 104 judge findings
to the eight `pc_0` OPENs `check_design` already reports (all 96 OFF_GRID
gone, the engine's verdict and wirelength unchanged); NQ = 4 goes from 201
to 73 — OFF_GRID 96 → 0 and NO_METAL 64 → 32, with the remaining 32
NO_METAL, 37 OPEN and the 4 cross-bundle SHORTs of #948 left standing and
the engine's own count falling from 117 to 85 unplaced.  `tpu 8` stays
clean, and the QoR corpus is unchanged (0 better / 0 worse / 56
unchanged, abstract and detailed WL +0 — no corpus flow has a template
whose span-clear pool is short).  Pinned by
`test_tcl_soc_flow.py::test_a_copied_core_lands_on_its_own_signal_tracks`,
which fails on the pre-fix engine (NQ = 1, 32 OFF_GRID).  A flow under the
default `on_mismatch stop` that used to copy such a cell now REFUSES DNUTS
with the report instead — the policy's own meaning, applied to the pool the
bits actually come from.

Two controls bound it, and both point the same way: the mesh
(`tpu 8`), whose `-bottomup` snaps the row pitch onto the stack's track
period, is clean; and the SoC's own TOP-DOWN round, which makes no copies
at all, is clean at the same size.  The fault is in the copy, not in the
design or the judge.

**What it means for the published tables**: every "clean" in E1 and E5 —
6b, 6c and 6d included — is a `check_design` clean, and the bottom-up arms
of those tables carried this off-grid metal (the fix above removes it; the
rows themselves have not been re-run under it yet).  The
tables are not withdrawn on that account: the claim they support is about
the LOOP (does an informed round converge, and in how many rounds), both
arms are scored the same way, and the 96 wires are present in every arm's
bottom-up rounds alike.  But a row that reads clean is clean BY THE
ENGINE'S AUDIT, and until the rows are re-run and re-judged under the fix,
that is what those tables say.  The ladder page's build order put the judge first
for exactly this reason.
