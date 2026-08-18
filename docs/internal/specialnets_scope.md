# Scoping: power rails from a DEF, for NDR shielding

**Status: scoped, not built. The conclusion is that the piece worth building
is NOT the one this was called.** Probed against `main` on 2026-08-15.

The question came out of `opens_interchange.md` item 14. Giving
`flow/ariane133` its technology LEF made every NDR *width* question
meaningful there for the first time — but a LEF says nothing about which
tracks the power grid takes, so every imported layer is all-signal and NDR's
**shield / credit / bond** machinery still has nothing to bite on. The
obvious next step was "import the DEF's `SPECIALNETS`". Probing first says
otherwise, in three steps.

§1 was itself corrected after review caught it overstating the reader's
coverage — the correction is kept in place rather than smoothed away, since
it is the same fault this document exists to describe.

---

## 1. The import reads SPECIALNETS geometry — but only its simplest form

`read_specialnet` (`def_io.cpp`) parses the wires, and `bdb.cpp` turns each
segment into a keepout tagged `"SPECIALNET <net>"`:

```cpp
// Power straps are real metal a signal cannot use.
for (const auto& w : def.special_wires) { … stats.keepouts.push_back(…); }
```

So a strap **that the reader retains** blocks correctly, on both consumers
(the Floorplan for the planner, the RoutingGrid for DNUTS). There is no
missing *consumer*.

**The reader itself is another matter, and this is a correction to the first
draft of this document, which claimed there was no missing importer at all.**
`read_specialnet` collects points only while the next token is `(`, so it
handles a contiguous width-plus-polyline and nothing else. Measured by
parsing one-net DEFs through `parse_def`:

| special-wire form | wires kept | points | census |
|---|---:|---:|---|
| `+ ROUTED M6 2000 ( x y ) ( * y )` | 1 | 2 | — |
| `+ ROUTED M6 2000 + SHAPE STRIPE ( x y ) ( * y )` | **0** | — | `SPECIALNETS.no_geometry` |
| `… ( x y ) ( * y ) M6_M7 ( x y ) ( x2 * )` (via mid-path) | 1 | **2** | — |
| `+ ROUTED M6 2000 RECT ( … ) ( … )` | **0** | — | `SPECIALNETS.no_geometry` |
| `+ ROUTED M6 2000 POLYGON ( … ) …` | **0** | — | `SPECIALNETS.no_geometry` |

Three things to take from that table:

- **`+ SHAPE STRIPE` drops the stripe entirely.** DEF's grammar is
  `ROUTED layer width [+ SHAPE type] [+ STYLE n] points`, so the `+` sits
  between the width and the first `(` — the loop never starts. This is not an
  exotic form; it is what a PDN generator emits for every stripe it draws.
- **A via truncates the path.** The run before the via is kept and everything
  after it is silently discarded, so a strap that changes layer is imported as
  its first leg alone.
- ~~**The census actively misreports it.**~~ **RESOLVED 2026-08-15.** A net
  whose geometry was present but unparsed was recorded as
  `SPECIALNETS.no_geometry` — not silence, but a positive claim that the DEF
  contained no wires — and a truncated path was recorded as nothing at all,
  since a kept wire looks like a complete read. Each path is now censused by
  what defeated the reader (`SPECIALNETS.unread_wire`,
  `SPECIALNETS.partial_wire`), and `no_geometry` is emitted only for a net
  with no `+ ROUTED` at all. This was the one part treatable as a defect in
  its own right rather than a missing feature, because making the gap **loud**
  needs no design to measure on — whereas reading the forms does. The reader
  itself is [opens_interchange item 15](opens_interchange.md).

**And the reason the first draft got this wrong is the subject of §4.** The
two DEFs in this repo with any PDN are ours, and both are written in exactly
the one form the reader handles — so "the reader is complete" was validated
against vehicles typed by hand in the simplest legal syntax. That is item 12's
mistake, one level further in than the place this document warns about it.

## 2. What is actually dropped is the strap's IDENTITY

`KeepoutZone` is `{bbox, layer_ids, inside_block}` — no net, no label. The
importer's `why` string carries `"SPECIALNET VDD"`, and
`_apply_def_keepouts` uses it only for the summary line:

```python
by_why[k.why.split()[0]] = by_why.get(k.why.split()[0], 0) + 1   # "SPECIALNET"
```

So by the time the routing stages see a strap it is an anonymous rectangle.
That matters because every NDR rail predicate is an **identity** question:
`ndr_shield_net_matches` asks whether a rail is electrically the rule's
shield net (GND/VSS/GROUND one family, VDD/VCC/POWER the other), and
`ndr_rail_credits` asks whether such a rail is immediately adjacent to the
run. A rectangle with no name cannot answer either.

## 3. And the two rail models are not interchangeable

This is the part that redirects the work.

| | pattern rail | DEF strap |
|---|---|---|
| shape | a **periodic slot** in a repeating track pattern | an **absolute rectangle** at fixed coordinates |
| density | one rail every N slots (`VDD 2 1 (_ 1 1)x12 GND 2 1`) | 2–3 stripes across a whole die |
| declared by | `def_track_pattern` | `SPECIALNETS … + ROUTED` |

Measured on the two DEFs in this repo that have any PDN geometry at all:

```
flow/def/chip.def   VDD: M6 width 2000 at x=30000 and x=230000;  VSS: x=130000
flow/rv/soc.def     VDD: M6 width 2000 at x=182160 and x=829840; VSS: x=506000
```

Three stripes on one layer, spanning the die. Converting that into a
periodic pattern rail is not a translation — it is a fabrication, correct
only if the PDN happens to be periodic, which a real one drawn as sparse wide
stripes is not. **"SPECIALNETS → pattern rails" is the wrong shape.**

Crediting does not need periodicity anyway. R5a asks a *local* question — is
there an identity-matching rail immediately beside this run whose metal spans
it — and an absolute rectangle answers that directly.

---

## 4. The blocker for measuring any of it

**No design here has a real power grid.**

| DEF | SPECIALNETS | geometry |
|---|---|---|
| `demo/ariane/ariane.def` | 2 nets (VDD, VSS) | **none** — `( * VDD ) + USE POWER` and no `+ ROUTED` at all, so here `SPECIALNETS.no_geometry` is the truth and not §1's misreport |
| `flow/def/chip.def` | 2 nets | 3 stripes, hand-authored |
| `flow/rv/soc.def` | 2 nets | 3 stripes, hand-authored |
| the rest | none | — |

The real one is a **floorplan** DEF: the PDN has not been built yet, so its
`SPECIALNETS` carries connectivity and no wires. So the design that motivated
this work would gain **nothing** from it today, and the two that have straps
are ours — the same "a human typed them" limitation that kept macro `OBS`
invisible until somebody else's file arrived (item 12).

Getting a real PDN means a placed-and-power-routed DEF, which upstream
generates rather than ships: an OpenROAD or Innovus run, not a download.
The errand is written out in
[openroad_pdn_recipe.md](openroad_pdn_recipe.md) — install, the exact inputs
(all of them already digest-pinned in `flow/ariane133/`), the pdngen script,
and how to check the result.

## 5. What to build, if this is picked up

Three pieces, in order, and the first two are each worth doing on their own:

**(0) Finish the special-wire reader** — `+ SHAPE`/`+ STYLE` before the
points, a path that continues past a via, and the `RECT`/`POLYGON` forms; and
stop reporting an unparsed net as `SPECIALNETS.no_geometry`, which is a false
statement rather than a missing one. Provably needed the moment a real PDN
arrives, since a generator emits `+ SHAPE STRIPE` on every stripe — so §4's
"get a power-routed DEF" and this are the same errand.

**Amended 2026-08-16: they are NOT the same errand, this paragraph's "cannot
be measured on anything but a synthetic case" was wrong, and (0) is now
DONE.** OpenROAD's own pdn regression goldens (`src/pdn/test/*.defok`) are
pdngen OUTPUT and are fetchable through the channel `flow/ariane133/fetch.py`
already uses. BUDA read **0 of their 685 metal paths** — every one carries
`+ SHAPE` — and now reads all 685, with the 6781 single-point via placements
censused as what they are rather than as unread wire. So the reader's
correctness had real generator bytes to be tested against with no OpenROAD
install at all; only the KEEPOUT-impact question still needs §4's run. The
synthetic-case objection held for as long as nobody looked for somebody
else's output, which is item 12's lesson wearing one more costume.

What (0) did NOT do, and what §4's run is still for: change any route. No DEF
in the tree carries `+ SHAPE`, so the fix is byte-identical on every flow —
correct, and unmeasured. Details in `opens_interchange.md` item 15.

**(a) Carry the strap's identity into the session** — **LANDED 2026-08-17.**
Of the two shapes offered here — a net label on the imported keepout, or a
parallel strap list beside the keepouts — it is the **label**: a parallel
list would be a second copy of geometry the keepouts already hold, and a
second copy is the drift this document's own R4 rule exists to prevent.

What landed, reader to session:

| | |
|---|---|
| `DefImportStats::Keepout::net` | set on the `SPECIALNETS` path, empty elsewhere |
| `KeepoutZone::net` | the session-side label |
| `Floorplan::add_keepout_zone(..., net = "")` | defaulted, so every existing caller is untouched |
| `_apply_def_keepouts` | passes it through, both bindings expose it |

Carried as its **own field** rather than recovered from the provenance
string `why` (`"SPECIALNET VDD"`): a net name may contain a space, so
splitting that string is a parse that can be wrong, and a field whose whole
purpose is that an identity survives intact must not arrive via a lossy
round trip.

Empty for obstruction with no net behind it — a macro's `OBS`, a `LAYER`
blockage, a hand-declared `add_keepout`. (Not a component halo: item 13
established that a halo carries no layer and produces no keepout at all,
so there is no halo zone to label either way.) That is deliberate and
test-pinned: `net` is a claim that this metal *belongs to* a net, and
labelling anonymous obstruction would degrade it from "the rail this is" to
"some string".

**It has a reader on day one.** `import_def_lef`'s keepout census now names
the nets (`SPECIALNET:3  (nets: VDD:2, VSS:1)`). A field nothing looks at
rots silently, and this one exists so that a *later* consumer can trust it —
exactly the situation in which silent rot is undetectable.

**Not built, and why.** The `RoutingGrid` keeps its keepouts as a bare
`std::vector<Rect>`, so giving them identity is a type change rather than an
additive field, and it would touch every consumer that walks that list.
Which of the two stores (b) needs is a real question: `ndr_rail_credits`
already takes a `(label, type)` pair — no change needed there, the R4
single-sourcing is intact — but its DNUTS caller enumerates *track slots*
from the grid, so (b) may well want the grid side too. Deferred to (b),
where it can be built against a consumer instead of a guess.

Nothing routes differently: additive field, defaulted parameter, no
behaviour keyed on it yet.

**(b) Teach the three NDR rail predicates to see strap geometry** alongside
pattern slots — `ndr_rail_credits` (R5a crediting), the R9 `NDR_SHIELD`
audit, and `emit_shield_bond_vias` (R6). All three currently enumerate track
slots by type; each would additionally consider a labelled strap whose metal
spans the run. The single-sourcing rule (`opens_ndr.md` R4) says credit and
audit must derive the answer from the same predicate, so the strap lookup has
to be one shared function or the two will drift — the fault family this repo
keeps re-finding.

**What NOT to build:** a `SPECIALNETS → def_track_pattern` synthesizer.
See §3.

## 6. Recommendation — DISCHARGED

The ordering held, and every step of it has now run:

1. ~~Finish the special-wire reader~~ **DONE 2026-08-16** — and it turned out
   not to need step 2 at all, which is the correction recorded in §5(0).
2. ~~Get a placed, power-routed DEF~~ **DONE 2026-08-16** —
   [openroad_pdn_recipe.md](openroad_pdn_recipe.md), run through OpenROAD
   26Q3 in Docker.
3. ~~Then (a) + (b) together, measured on it~~ **DONE 2026-08-17.**

The caution above was right and is worth keeping: doing (b) against the three
hand-drawn stripes would have repeated item 12's mistake. What it did not
anticipate is that a REAL PDN can be just as useless a vehicle for a
different reason — see §7.

---

## 7. What (b) turned out to be

**Not three lookups. One insertion point and one predicate.**

All three consumers already asked the grid the same way — `credit_at`'s
`rail_covers_span`, the R9 audit's `_ndr_end_credit`, and
`emit_shield_bond_vias` every one query `preroutes` / `preroutes_in`. So
"teach the three predicates to see straps" resolved to: make a strap BE a
preroute. A `SPECIALNETS` strap is metal belonging to a power net, which is
exactly what a `PreRoutedSegment` represents; emitting it from
`preroutes_in` reaches all three through machinery they already use.

Two things had to be true first:

- **The identity had to reach them.** §5(a) put `net` on the Floorplan's
  `KeepoutZone`, but `DetailedNUTSEngine` holds only a `RoutingGridStack` and
  no `Floorplan` — and `bdb_cmds.py` dropped the net on the way into the
  grid. `GridKeepout{bbox, net}` closes that; it is the type change (a)
  deferred, and the grid was the only possible route.
- **The lookup had to become one function.** R4 was already half-violated:
  identity was shared (`ndr_rail_credits`) but GEOMETRY existed twice — a C++
  coverage sweep in `credit_at` and a line-for-line Python twin in
  `_ndr_end_credit`. `ndr_credit_rail` (routing_grid.cpp) is now the single
  answer to "is there a crediting rail immediately beyond this edge whose
  metal runs the length of this segment", over BOTH rail kinds, and the audit
  calls it instead of re-deriving it.

Conservative by construction: an anonymous keepout (OBS, a LAYER blockage, a
hand-declared `add_keepout`) is never emitted as a rail — a consumer asking
"is there a VDD rail here" must not be answered by a block footprint — and
`credit_at` consults straps only AFTER the pattern path declines, and only
for `is_strap` rails, so a design that declares pattern rails decides exactly
as it did before.

**Measured** on `flow/ariane133/ariane133_ndr_straps.buda` (the m4-m7 vehicle
+ the spliced strap DEF + a `shield bus … credit bond` rule on M6/M7):

| bond | count |
|---|---:|
| M7 shield → M6 strap | 1653 |
| M6 shield → M7 strap | 548 |
| M6 shield → M5 strap | 722 |
| **total** | **2923** |

Every one is necessarily strap-derived: this design's patterns come from DEF
`TRACKS` with no tech LEF, so they are synthesized ALL-SIGNAL and the grid
carries **zero** pattern rails. Before the change there was nothing on any
layer for `emit_shield_bond_vias` to find. The layer pairs are also the right
ones — M6 is V, M5/M7 are H, so each bond crosses to an adjacent
PERPENDICULAR layer, which is R6's rule. 29 shields remain unbonded and are
flagged `NDR_BOND` rather than passing silently.

### Two traps this vehicle sets, both paid for

Neither is about the PDN, and both cost a full debug cycle:

- **A real PDN is not automatically a usable vehicle.** On the FULL ten-layer
  stack the interconnect lives on M8-M10 while the PDN stops at M7, so straps
  and signal metal occupy almost disjoint layers, every lookup returns "no
  rail", and the code runs, does nothing, and looks fine. The m4-m7 stack
  (M6/M7 as TOP) is what puts ~97% of the wire on the two strap-bearing
  layers.
- **A floorplan DEF places the macros and nothing else.** The first rule was
  scoped to `amo_req_o` — the widest bus in the design at 131 bits — which is
  driven by `ex_stage_i/lsu_i/i_store_unit/i_amo_buffer`, a pure-logic
  container with no placed descendant. No geometry, no candidates, no route,
  ever. Only SRAM-connected nets have geometry here.

A third was self-inflicted and is recorded because the shape recurs: the
vehicle initially dropped the m4-m7 flow's `negotiate_congestion` calls, and
99 bit-wires landed where ~980 do with them. That was briefly read as "the
NDR rule collapsed the design" — a control with the rule removed placed the
same 99, which is what a control is for.

### Residual

`bundle.ndr_rule` is stamped on PRE-EXPANSION template rows, while the rows
that route are the expanded per-instance ones, which carry no stamp. So there
is currently no persisted way to ask "was this ROUTED bundle governed?" —
which made the working result read as a failure twice during this work. Worth
closing; it is an observability gap, not a correctness one.
