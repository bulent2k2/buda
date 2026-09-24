# Signal pre-routes — handing DNUTS wiring to the detailed router

Status: **PLAN** (2026-09-24).  Nothing below is built.  It extends
mechanism B of the [LibreLane plan](librelane_hier_flow.md) (§5, §8 step 6,
phase 3) from "the router's own routed bus, relabelled FIXED" to "BUDA's
bit-wires, written by BUDA", and adds the case the phase-0 measurement did
not cover: a pre-route that stops short of a pin, with the router guided
from its end to the pin.

**Naming.**  In BUDA "pre-route" already means a POWER / GROUND / CLOCK /
SHIELD track (`PreRoutedSegment`, `RoutingGridStack.preroutes`, the
`[Preroutes]` viewer button).  This plan is about SIGNAL wiring, so it says
**signal pre-route** throughout.  Any command or file it adds must not reuse
the bare word.

## 1. The idea, and what it is worth

Today BUDA hands a router **tunnels**: `emit_guides` writes each bus net's
corridor as an OpenROAD guide.  The router still picks every track itself.
Measured on phase 0 (§8 step 5), it keeps 98.1 % of the wire inside the
corridor.  DNUTS has already chosen those tracks, bit by bit, with the bit
order, NDR widths and shields applied.  A signal pre-route hands over the
wires themselves: DEF `NETS … + FIXED` wiring the router must route around
and may not move.

What that buys over a guide:

| | Guide (today) | Signal pre-route |
|---|---|---|
| Which track each bit takes | the router's choice inside the corridor | BUDA's, exactly |
| Bit order across a bus | not preserved | preserved |
| NDR width / spacing / shields | the router's own NDR handling, if the rule is passed on | BUDA's placement (DEF `+ SHIELD` for shields, to be verified) |
| Wire known before routing | no — only after `detailed_route` | yes — RC is exact at plan time |
| Router workload | every bus net routed | bus nets removed from the problem |

The third and fourth rows are the original motivation, from the 1990s
system BUDA is modelled on (`docs/origin/paper.md`): once the buses are
wired, "most of the full-chip bus wiring is done, which enables accurate
RC extraction".  That system's output was wiring, not guides.  Per-pin
timing budgets (LibreLane plan §2.4, phase 3) want the same thing: a budget
is only as good as the wire it is computed from.

What it costs is **responsibility**.  A guide the router disagrees with is
re-routed.  A FIXED wire the router disagrees with is a DRC violation that
nothing downstream will fix.  So every fault BUDA's own audit misses becomes
a hard failure (§5), which is why the plan is gated.

## 2. What is already established

* **Measurement B passed** (2026-09-05, LibreLane plan §8 step 6).  A 32-bit
  bus carrying `+ FIXED` wiring came out of `global_route` and
  `detailed_route` byte-identical (`compare_bus_wires.py`: 32 unchanged).
  The other 101 nets were routed around it (`Routed nets: 101`), with
  `Number of violations = 0`.
* **Two limits on that result.**  The FIXED wiring was the ROUTER's own
  route, relabelled by `mark_fixed.py`, so it was DRC-clean by
  construction.  And every net was COMPLETE, pin to pin.  Neither holds for
  BUDA's wiring in general.
* **Existing wiring removes a net from global routing.**  With the other
  nets still carrying `+ ROUTED` wiring, `global_route` routed 0 nets and
  `detailed_route` re-derived guides from the existing wires.  That is the
  fact the partial case (§4) turns on: a net whose wiring stops short of a
  pin is skipped by `grt`, and `drt` then has guides for the wired part
  only.
* **The pieces BUDA already writes:**
  * `emit_pin_def`: block pins placed where each bit-wire meets the block
    face, on the bit-wire's layer.
  * `emit_guides`: gcell-aligned guides.  This already includes a
    PIN-ACCESS STRIP from each free end of a net's wiring to its nearest
    BDB pin, on the wire's layer, the `terminal` layers and every layer in
    between.
  * `emit_block_size`.
  * `export_def_blockages`.
  * DBU conversion (`advisory.dbu_scale`).
  * DEF name escaping, in two places: `advisory.escape_def_name` and
    `pin_def.def_escape`.  The writer below must pick ONE.
* **What does NOT exist:**
  * No DEF `NETS` wiring writer.
  * No LEF `VIA` / `VIARULE` reader: `lef_io.cpp` skips both, "explicitly
    deferred".
  * `NetVia` names no via.  It carries a layer pair and a point, nothing
    else.
  * BUDA's own DEF reader RECORDS `NETS` wiring without reading it
    (`def_io.cpp`, `NETS.ROUTING`).  So a round trip can only be checked by
    a separate reader, as `compare_bus_wires.py` already does.

## 3. Case 1 — the complete pre-route

In the H+B arm BUDA places the block pins too (`emit_pin_def`), so a top
bus between two hardened macros can be wired end to end: each bit-wire
reaches the face where the pin rectangle sits on the same layer.  This is
measurement B's case with BUDA's wiring in place of the router's.

### 3.1 The writer

`emit_signal_preroutes <file.def> [nets <prefix,...>] [fixed|routed]
[lef <tech.lef>] [only_clean <judge.json>]`.  The name is provisional.

It writes a DEF `NETS` fragment: one entry per net carrying signal
pre-route wiring.  Deliberately it does NOT write a whole DEF.  A small
merge script (`flow/librelane/.../merge_preroutes.py`, the sibling of
`mark_fixed.py`) splices the fragment into the placed DEF the flow already
has.  The script REFUSES a net the placed DEF does not declare, and refuses
a net that already carries wiring.  The reason for the split: the placed
DEF owns components, pins, rows, tracks and the PDN, and BUDA should not
re-emit what it did not compute.

For each bit:

1. **One path per `NetSegment`.**  Emit `( x1 y ) ( x2 y )` for an H track
   (and the V equivalent) on the segment's layer, which comes from the
   imported LEF names (`def_layer` / `import_lef_tech`).  Coordinates go
   through `dbu_scale`.  Joined segments on one layer become one path when
   they are collinear, otherwise `NEW` clauses.
2. **Wire extension.**  DEF regular wiring extends each path end by half
   the wire width unless an extension is given.  BUDA's `span_lo/hi` are
   centreline endpoints that meet a perpendicular track's centre, so the
   default extension is what makes an L-corner overlap.  That must be
   CHECKED against OpenROAD's reader, not assumed: write one L-corner,
   read it into ODB, and compare the shapes.
3. **Width.**  Regular DEF wiring takes the layer's default width (LEF
   `WIDTH`).  A bit whose width differs, meaning an NDR-governed bit or a
   track pattern whose signal slot is not the LEF width, needs
   `+ NONDEFAULTRULE` on the net and a matching rule in the DEF's
   `NONDEFAULTRULES`.  Phase 1 REFUSES such a net, loudly, rather than
   writing it at the wrong width.  Widths come in phase 3.
4. **Vias.**  Each `NetVia` becomes a via at `(x, y)`.  This needs a LEF
   `VIA` reader.  Read the fixed vias (`VIA <name> [DEFAULT] … END`) and
   their three layers, then pick the `DEFAULT` via per adjacent cut.  A
   `VIARULE GENERATE` is not needed in phase 1: every open PDK in reach
   (sky130, NanGate45) defines fixed defaults.
   * **Via stacks are a real gap.**  On `flow/soc_small.buda`, **168 of
     1,056** per-bit vias (16 %) jump three layers (M2→M5 / M3→M6).
   * Each needs a stack of cuts plus a landing pad on EVERY intermediate
     layer, and nothing in BUDA reserved those pads.  DNUTS never asked
     whether the intermediate layers are free at that point, so a pad can
     land on another net's track.
   * Phase 1 emits a stack only where the judge (below) confirms every
     intermediate landing is free.  Otherwise it leaves that bit to the
     router (§4).
5. **Names.**  Use the spelling the placed DEF uses.  The merge script
   matches against it rather than re-deriving an escape.  `emit_pin_def`
   learned that odb reads an escaped pin name back differently, so this is
   the thing to test first, not last.

`fixed` is the default.  `routed` exists for measurement C (§4), not as a
recommended mode: routed wiring is wiring the router may rip up, and it
stops `grt` all the same.

### 3.2 Measurement B′ — BUDA's wiring, complete

**Vehicle:** phase-0 `two_reg32`, the same one measurement B used.  BUDA's
pins (`pins.buda` → `emit_pin_def`), BUDA's route (`buda_route.buda`),
then the writer in place of `mark_fixed.py`.  Everything else is the
phase-0 recipe, `--strip-others` included.

**Pass means all four of:**

* `compare_bus_wires.py` reports every bus net unchanged;
* `Routed nets` equals the number of non-bus nets;
* `detailed_route` reports `Number of violations = 0`;
* signoff DRC (KLayout + Magic, LibreLane's own) reports no new
  violation.

The last one matters most.  The phase-0 FIXED bus was DRC-clean because
the router drew it.  BUDA models tracks and widths, not the rest of the
deck: minimum area, end-of-line spacing, via enclosure, minimum step.

**If it fails,** the result is which rule, and the per-rule count is what
decides whether phase 1 is a writer fix or a modelling project.

### 3.3 Gates before any real design

A FIXED wire is only as right as the plan that drew it, so three faults the
judge (`tools/independent_audit.py`) found and `check_design` did not stop
being reports and become blockers:

* **#946** (FIXED 2026-09-24): bottom-up copies sitting inside a GROUND
  slot.  As FIXED metal that is a short to the power grid.  The check that
  licensed the copy compared an empty span-clear pool while the bits came
  from the midpoint fallback; it now compares both.
* **#947** (FIXED 2026-09-24): `check_design` had no on-grid check.  The
  writer must not rely on an audit that cannot see the fault it would ship;
  it now reports `OFF_GRID`, NDR runs included (judged by the metal's
  edges).
* **#948**: two different bundles' nets overlapping on one layer.  As FIXED
  metal that is a short between signals.

The writer therefore takes `only_clean <judge.json>`: a net is pre-routed
only when the judge's verdict on it is clean, and every other net falls
back to its guide.  That is per net, not per design, and it is the default
for any flow that is not a measurement.  A second requirement: BUDA must
route against the PDN the router will see.  The judge's `KEEPOUT` check
covers the declared zones, which includes PDN straps once the pdngen DEF's
`SPECIALNETS` are imported (CLAUDE.md, "STRAPS ARE RAILS").  A flow that
plans against no PDN cannot pre-route, only guide.

## 4. Case 2 — pre-route to the edge, guide to the pin

Not every pin is BUDA's.  A net may end on a standard cell the router
places later, or on a macro pin BUDA did not write.  Or BUDA may choose to
leave a via stack or a congested pin approach to the router.  In each case
BUDA's wiring stops short: the long haul is wired, the last mile is not.

**Why it will not just work.**  §2's third fact: existing wiring takes the
net out of `global_route`, and `detailed_route` derives its guides from
the wires.  Those guides cover the wired part and nothing else, so the
last mile has no guide and the net stays open.

**The mechanism** is what `emit_guides` already writes.  Its pin-access
strip runs from each free end of a net's wiring to the nearest pin, on the
wire's layer, the `terminal` layers and every layer between.  That strip
IS the last-mile guide.  So the partial case is:

* the FIXED wiring (from §3.1) for the long haul;
* `emit_guides` for the WHOLE net — the corridor over the pre-route plus
  the strip to the pin;
* `read_guides` into the ODB, using the tier-1a cut (`guide_route.tcl`)
  that already hands guides to LibreLane's detailed route.

For that to connect, `drt` must treat the FIXED metal as the net's own
shape: a legal terminal to connect to, not an obstacle.  Nothing in the
router's documentation says whether it does.  That is the measurement.

### 4.1 Measurement C — partial pre-route plus last-mile guide

**Vehicle:** `two_reg32` again, with the pre-route deliberately cut back
from each pin by a few gcells, so the last mile is the router's.  Four
arms:

| Arm | Wiring | Guides |
|---|---|---|
| C0 | none | full `emit_guides` (mechanism A, the control) |
| C1 | `+ FIXED`, cut back | full `emit_guides` |
| C2 | `+ ROUTED`, cut back | full `emit_guides` |
| C3 | `+ FIXED`, cut back | none (confirms the gap above is real) |

**Pass** for C1 means all of:

* every bus net connected (`drt` reports no open, and LVS agrees);
* the pre-routed part unchanged (`compare_bus_wires.py` on that part
  only);
* 0 violations.

**Expected:** C3 leaves opens.  If it does not, the gap in §4 is not real,
and the plan gets simpler.  C2 tells us whether `routed` is ever worth
offering.

### 4.2 The per-net rule that comes out of it

If C1 passes, the writer's output is a per-net decision, reported rather
than silent:

* **complete pre-route**: the net's pins are BUDA's, the judge is clean,
  and every via stack's landings are free;
* **partial pre-route plus guide**: BUDA wires what it can prove and guides
  the rest;
* **guide only**: anything the judge flags, anything under an NDR before
  phase 3, and any design with no PDN.

## 5. Measurement D — does it pay?

B′ and C establish that pre-routes WORK.  Whether they are worth it is a
QoR question, asked on the benchmark the LibreLane plan already runs.  Use
tier 1a, H+B arm, at N = 2 and N = 8 (the two points where the arm exists).
Compare guides (the current H+B) against pre-routes plus guides, using the
§7.3 metrics.

§7.3 already counts wire the honest way (top plus every block, once per
instance), so a pre-route that saves top wire by pushing it into the blocks
shows up on both sides.

**What would make the case:**

* setup slack.  H+B at N = 8 fails §7.4's floor on setup alone, by
  0.0185 ns, and exact bus RC is the natural lever;
* router runtime on the top;
* DRC equal or better.

**What would sink it:**

* signoff DRC rising, since BUDA does not model the full deck;
* a timing loss from BUDA's track choices being worse than the router's
  on slack-critical bits.

The judge column goes beside every row, as in the convergence experiments.

## 6. Phases

| Phase | Builds | Gate to the next |
|---|---|---|
| 0 | this plan; LEF `VIA` reader (fixed vias + `DEFAULT`); wire-extension probe (§3.1 step 2) | the probe says which extension makes BUDA's corners overlap |
| 1 | `emit_signal_preroutes` + `merge_preroutes.py`; refuse NDR nets, and stacks the judge cannot clear; tests: a Python DEF reader round-trips the writer's wiring against `detailed_result` bit for bit, and the merge refuses unknown/already-wired nets | **B′** passes |
| 2 | partial pre-routes: cut-back option on the writer; per-net decision report (§4.2) | **C1** passes, **C3** shows the gap |
| 3 | NDR widths (`NONDEFAULTRULES`) and shields (`+ SHIELD`, verified against `drt` first); via stacks with reserved landings (DNUTS would have to reserve the intermediate pads, the first ENGINE change in this plan) | **D** on tier 1a |
| — | #946 / #947 / #948 fixed, or the `only_clean` fallback proven to cover them | before any design other than a measurement vehicle |

Phase 0's reader is useful whatever happens after.  `export_gds` draws
vias as bare squares today (CLAUDE.md, GDS export) for the same reason:
BUDA has never known what a via looks like.

## 7. Risks, ranked

1. **DRC rules BUDA does not model** (minimum area, end-of-line, enclosure,
   minimum step).  B′ is what measures this.  If it bites, the choice is
   between modelling the rules and pre-routing only the straight middle of
   each wire.
2. **Via stacks.**  16 % of per-bit vias on `soc_small` jump three layers,
   with unreserved landings.  Phase 1 hands those bits to the router.
   Reserving the landings is an engine change and is deferred to phase 3
   on purpose.
3. **The router's treatment of FIXED metal as a terminal**: unknown, and
   measurement C is the whole of it.
4. **Plan faults becoming shorts** (#946 / #947 / #948).  Mitigated by
   `only_clean`, not by hoping.
5. **Name and coordinate agreement** with the placed DEF.  Low risk because
   `emit_guides` already agrees with it (the phase-0 corridors landed
   within a gcell), but test it first.

## 8. What this plan does not claim

* That pre-routes beat guides on QoR.  Measurement D asks that, and a
  wash is a possible answer, as the +0.6 % H+B arm total was (LibreLane
  plan §11 item 3).
* That BUDA's detailed placement is DRC-clean.  It has never been checked
  against a full rule deck.
* Anything about power / ground pre-routes, which already mean something
  else here (see "Naming" above).
