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

---

## 1. The import already reads SPECIALNETS geometry

`read_specialnet` (`def_io.cpp`) parses the wires, and `bdb.cpp` turns each
segment into a keepout tagged `"SPECIALNET <net>"`:

```cpp
// Power straps are real metal a signal cannot use.
for (const auto& w : def.special_wires) { … stats.keepouts.push_back(…); }
```

So a strap already **blocks** correctly, on both consumers (the Floorplan for
the planner, the RoutingGrid for DNUTS). It even censuses a special net that
carries no geometry (`SPECIALNETS.no_geometry`). There is no missing importer.

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
| `demo/ariane/ariane.def` | 2 nets (VDD, VSS) | **none** — `( * VDD ) + USE POWER` and nothing else |
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

## 5. What to build, if this is picked up

Two pieces, in order, and the first is worth doing on its own:

**(a) Carry the strap's identity into the session** — a net label on the
imported keepout, or a parallel strap list beside the keepouts. Small,
self-contained, no semantic risk: nothing reads the field yet, so it is
additive. Do this first even if (b) waits, because it is the part that is
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

1. Get a placed, power-routed DEF (an upstream tool run — the real cost here).
2. Then (a) + (b) together, measured on it.

Doing (b) against our three hand-drawn stripes would repeat the mistake item
12 records: building to a vehicle whose shape is an artifact of having been
typed by hand, and calling the result validated.
