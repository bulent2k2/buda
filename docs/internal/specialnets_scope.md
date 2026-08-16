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

**(a) Carry the strap's identity into the session** — a net label on the
imported keepout, or a parallel strap list beside the keepouts. Small,
self-contained, no semantic risk: nothing reads the field yet, so it is
additive. Worth doing even if (b) waits, because it is the part that is
provably missing rather than merely absent.

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

## 6. Recommendation

**Defer.** (a) is cheap and correct but has no consumer until (b) lands; (b)
has no design to prove it on until a DEF with a real PDN exists. The honest
ordering is:

1. ~~Finish the special-wire reader~~ **DONE 2026-08-16** — and it turned out
   not to need step 2 at all, which is the correction recorded in §5(0).
2. Get a placed, power-routed DEF (an upstream tool run — the real cost here).
   The recipe: [openroad_pdn_recipe.md](openroad_pdn_recipe.md).
3. Then (a) + (b) together, measured on it.

Doing (b) against our three hand-drawn stripes would repeat the mistake item
12 records: building to a vehicle whose shape is an artifact of having been
typed by hand, and calling the result validated.
