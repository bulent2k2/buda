# Dangling-metal ("antenna") shapes — the repro index

Metal that is part of a routed net but attaches to nothing: a wire whose end
runs past its own last junction or tap. Electrically inert, and in real silicon
an antenna-rule problem rather than a wirelength one.

The largest family here is one *kind* of mistake in different places:
**per-SEGMENT data governing a per-BIT quantity** (entries 1, 2, 4). A segment
is attached; a bit on it need not be, and every audit that reads the bus-level
`ConnSeg` graph is blind to the difference — which is why those ran for a long
time with `check_design` reporting Success.

It is not the only cause, and assuming it was cost real time here. Entry 5 is
generation-stage **geometry** (a duplicated leg), and entries 6–7 are a
DetailedNUTS **pass-ordering** problem. The three questions worth asking on a
new instance are at the bottom of this page.

Each entry says how to reproduce it, what is measured, and its status. Nothing
here is asserted from reading the code alone — every measurement is from a run.

| # | shape | vehicle | status |
|---|---|---|---|
| 1 | tapered stub keeps other bits' metal | `flow/rv/soc_conv_div.buda` | FIXED #678 |
| 2 | busterm tap vouches for every bit | `flow/antenna_taper_passthru.buda` | FIXED #690 |
| 3 | bus wider than the block it crosses | `flow/antenna_wide_bus_passthru.buda` | UNREACHABLE |
| 4 | crossing credited at the nominal seg | `flow/rnr/mix2_topdown_refine.buda` | FIXED #695 |
| 5 | a duplicated leg off one block | `flow/mst_shared_leg_prefix.buda` + `flow/mst_shared_leg_suffix.buda` | **5a FIXED opt-in** (#708) · 5b OPEN · **5c reclassified + fixed opt-in** |
| 6 | a culled partner strands a segment | `flow/antenna_culled_partner.buda` + `flow/antenna_starved_partner.buda` | **OPEN — cause found** |
| 7 | `rv/soc` 1.25M units mid-flow | `flow/rv/soc.buda` | **OPEN — same cause as 6** |

Entries 5, 6 and 7 are the live ones. 6 and 7 are **one defect** — a
DetailedNUTS pass-ordering problem, isolated to a 4-bit vehicle in entry 6. 5 is
a *class* of three members sharing one geometry: 5a is fixed behind an opt-in,
5b is open, and 5c turns out to be neither an antenna nor (mostly) a redundancy —
measured, not argued, by `tools/scan_collinear_stubs.py` and the two
`tools/experiment/` scripts, which refuted the cost explanation and localized
the real one: a suppression pass `add_trunk_v` runs and `add_trunk_h` does not. Entry 3 is a negative
result kept as a guard. Entries 3, 6 and 7's vehicles all end DIRTY on purpose;
none belongs in the QoR corpus.

---

## 1. Tapered stub: the trunk keeps metal for bits that branched off

**Mechanism.** A per-bit tapered tree (fan-in / fan-OUT) gives each stub only
*its own* bits, so for every other bit the trunk's endpoint conn resolves to no
wire at all. `adjust_bit_spans`' "ends with no endpoint conn keep the abstract
span" rule then leaves that bit spanning the whole trunk.

**Reproduce.** `flow/rv/soc_conv_div.buda` bundle 1, a 32-bit `FANOUT`.

**Measured before the fix.** All 32 trunk bits spanned the full 108,400 while
the neediest wanted 800 — **73.5% of the vertical metal dangling**, with
`check_design` reporting Success.

**Status.** FIXED (#678). Guarded by `test/tests/test_tapered_bit_spans.py`.

---

## 2. Busterm tap: per SEGMENT while bits are per BIT

**Mechanism.** `Topology::seg_busterms` records taps per segment. A trunk taps
blocks that only *some* of its bits terminate at, and the tap vouched for every
bit on the segment — holding the others out to a block they have no net at.
#678's retraction did not fire, because a busterm face is not a seg conn.

**Reproduce.** `flow/antenna_taper_passthru.buda` — `DIVERGENT` folds three
buses off one driver into one fan-out tree; the trunk taps `r_far` at x=700 and
twelve bits that branch off at x≤520 were stretched there anyway.

**Measured.** Trunk bit metal 10000 → 7840 (−21.6%); ~370 units per bit reaching
nothing. Also `flow/tcl/array.tcl 3 2` bundle 10: detailed WL 1336 → 944 on that
bundle, 24977 → 21615 on the design.

**Status.** FIXED (#690), read through one predicate (`seg_busterm_serves_bit`)
by the placer *and* both audits. Guarded by
`test/tests/test_busterm_tap_membership.py` and `test_tcl_array_flow.py`.

---

## 3. Bus wider than the block it crosses — NOT reachable

**Mechanism (hypothesised).** `detect_bit_antennas` credits a bit with reach
over any connected block its segment passes through, testing the crossing at the
nominal `ConnSeg::perp_pos` — one line — while the placed bundle is bits × pitch
tall. So: could outer bits be credited by a block they never touch?

**Reproduce.** `flow/antenna_wide_bus_passthru.buda`.

**Measured.** No. The crossed block's own edges *are* Hanan lines, so they bound
the trunk's interval and the bus is charged its full width against it: seg 0's
interval collapses to the point [130.0, 130.0] against a bus width of 36.0, and
the design fails as 16 unplaced bits long before any bit could be placed clear
of the block.

**Status.** UNREACHABLE — kept as the negative result. Ends dirty on purpose;
not in the QoR corpus.

---

## 4. Crossing credited at the nominal segment, not at the bit

**Mechanism.** The pass-through reach test ran at the nominal `ConnSeg`, while
what is built is a bit at its own placed track with its own final span. A bit can
be slid off a block the nominal segment crossed; if another segment supplies that
block's coverage, the coverage audit stays clean and the phantom crossing
silently excuses the overhang.

**Reproduce.** `flow/rnr/mix2_topdown_refine.buda` bundle 35 seg 5.

**Measured.** The nominal segment sits at perp 1020 and grazes
`chip/i_dnuts1_4/v0` (y 920–1020) on its boundary; the bits sit at y 1044.5 and
up and never cross it. The block also spans x 2130–2330 while the bits end at
2069.75 — so the credited reach was a coordinate **beyond the end of the wire**,
making `trail = span_hi − reach_hi` negative and cancelling the finding.

**Status.** FIXED (#695) — the audit now measures reach per bit. The metal it
revealed is entry 5a.

---

## 5. A duplicated leg off one block — one geometry, three producers

**Mechanism.** Two wires leave the *same* block from the same point along the
same axis, and the shorter one lies entirely inside the longer. The longer leg's
extra stretch is a duplicate with a **free end**, and it pushes that leg's end
*past* the divergence junction — which makes the junction a mid-span conn, and
DetailedNUTS only snaps a bit to its own via at an ENDPOINT conn, so every bit
keeps the shared abstract end.

The geometry has **three** producers, and only the first is fixed:

| # | producer | shared point | status |
|---|---|---|---|
| 5a | `realize_mst_edge`, shared node holds the LOWER index | legs **start** there | fixed behind `set_trim_mst_legs` |
| 5b | `realize_mst_edge`, shared node holds the HIGHER index | legs **end** there | OPEN |
| 5c | TRUNK stub generation | stub + leg leave one trunk point | **NOT AN ANTENNA — reclassified**; root-caused and fixed behind `set_trim_trunk_stubs` |

`compute_mst` emits every edge with `u < v` and `realize_mst_edge` routes
`u → v`, which is what splits 5a from 5b. The lever is **which block is the
driver**, not declaration order: `nodes` comes from the busterm list and the
driver heads it, so re-ordering `add_block` cannot move the hub's index while
demoting the hub to a receiver can.

**Reproduce.**

* 5a — `flow/mst_shared_leg_prefix.buda`, and `flow/rnr/mix2_topdown_refine.buda`
  bundle 35 in a real design.
* 5b — `flow/mst_shared_leg_suffix.buda`: the same four rectangles with the hub
  demoted to a receiver. It turns the trim **on**, so what it shows survives a
  build that has the fix and was asked to apply it.
* 5c — `tools/scan_collinear_stubs.py` (no argument scans a default spread of 11
  flows). It is also present in 5a's own fixture, nine pairs of it — all of the
  REDUNDANT kind there, which is what made the shape look worse than it is.

**Measured.**

* 5a: 8.75 + 5.75 + 2.75 units past the last via on bundle 35. On the standalone
  vehicle at the shared corner (2010,1010): `MST_HV` seg1 len 120 inside seg3 len
  1320; `MST_VH` seg3 len 70 inside seg1 len 620.
* 5b: `MST_VH` seg2 len 120 inside seg4 len 1320, `MST_HV` seg4 len 70 inside
  seg2 len 620, both at the END (2010,1010). **The harm differs in kind** — both
  legs tap the hub's face, so there is no free end and `check_design` reports
  Success. The cost is duplicate metal (480 of 11114 detailed WL) plus a
  perpendicular partner claiming an ENDPOINT conn to *both* legs, which NUTS
  reports as a junction infeasibility and which feeds ripup's contenders. Do not
  sell the mirror as the same defect.
* 5c: see the section below — it is **not an antenna** and mostly not even
  redundant. `tools/scan_collinear_stubs.py` measures it.

**Status.** 5a FIXED behind an **opt-in** (#708): `set_trim_mst_legs [on|off]`,
default off, byte-identical unused, `BUDA_MST_LEG_TRIM=1` for corpus A/B. Opted
in on `mix2_topdown_refine`: `check_design` 3 violations → Success, detailed WL
793215 → 793198. 5b and 5c are strict `xfail`s in
`test/tests/test_mst_shared_leg_prefix.py`, and they **still xfail with the trim
on** — so they record "not covered" rather than "not exercised".  5c's xfail
stays (it is the DEFAULT behaviour) but is no longer unfixed: its twin passes
under `set_trim_trunk_stubs on`.

### 5c is not an antenna, and mostly not redundant either

Filing 5c here was an error of provenance: it was found by generalizing the
antenna scanner, and it kept the label of the instrument rather than of the
defect. Both legs are attached at **two** points each — a block tap at the far
end, the trunk junction at the shared end — so nothing dangles and
`detect_bit_antennas` is right to stay silent.

The natural next thought is that the shape is at least *wasteful*: N collinear
stubs to N aligned blocks, where the longest passes over the rest, so all but
one could go. `tools/scan_collinear_stubs.py` tests exactly that, classifying
every containment pair by whether the long leg actually serves the short leg's
block (BUDA's rule is INTERIOR overlap — abutment does not cover, and
`Topology::pass_through_count` agrees). Over 11 flows, flat and hier, small and
large:

```
REDUNDANT:long_leg_crosses_it        174     (22.3%)
LOAD_BEARING:long_leg_only_grazes    547
LOAD_BEARING:short_leg_taps_nothing   60
SELECTED+LOAD_BEARING                  4
SELECTED+REDUNDANT                     0
```

So the "longest saves the rest" rule is **real but a minority case** — about one
pair in four. The other three in four are collinear by *coincidence of
placement*: two blocks share an x1 (or y1), so both stubs leave the trunk on the
same line, and the long one runs along the short one's block **edge** without
covering it. `mix2_topdown_refine` bundle 14 is the canonical instance —
seg3 at x=640 grazes `u2` (x[640,690], y[1170,1220]) and the topology reports
`pass_through_count: 0`. Delete that stub and `u2` opens.

**And the last line is the one that decides priority: `SELECTED+REDUNDANT` is
zero.** In every flow measured, the pairs that reach a *selected* candidate are
all load-bearing; no removable stub has been observed in a routed design. So
removing them buys no wire anywhere we can currently measure — the benefit is
pool hygiene, in exchange for the pool-resorting blast radius that made 5a
opt-in. That is a bad trade on present evidence.

Two things would change it, and both are measurements rather than opinions: a
design where a REDUNDANT pair is selected, or a demonstration that the shape
costs ranking (a candidate losing to a rival only because it carries duplicate
length it did not need). Until one of those exists, 5c is **recorded, not
open** — and it belongs to topology generation, not to this page.

#### Both bars were tested. Neither was met — and 5c got fixed anyway.

The ranking bar above was the interesting one, because there is a plausible
mechanism: `wirelength()` (topology.cpp) sums segment lengths with **no overlap
dedup**, so a duplicate stub of length L adds exactly L; and `apply_segment`
(congestion_planner.cpp) charges **each segment independently** at
`eff_width + track_pitch`, so two collinear same-bundle segments on one layer
charge the shared bands twice — for metal NUTS will place once. Both mechanisms
are real. Neither is the explanation.

`tools/experiment/base_rate_collinear.py` measured the null first, because
"zero selected" means nothing without a base rate. Over 621 planned bundles:

```
OBSERVED selected+redundant : 0
NULL A (uniform in the bundle's pool)     : expected 4.55 +/- 1.83   z = -2.49
NULL B (uniform within the WINNER's class): expected 0.00 +/- 0.00
```

Null B is **exactly** zero: in no bundle does any candidate sharing the winner's
class carry redundancy. The zero is a placement fact, not a scoring one.

`tools/experiment/twin_cost_collinear.py` then tested the ranking claim
directly. Since congestion cost is non-negative, `kWL*(wl−L) + max over
remaining of (total − cong)` is a floor the trimmed twin cannot go under —
including the double-charge relief, which is therefore bounded rather than
guessed. Result: **128 of 150 candidates provably cannot flip**, and the median
saving (0.67) closes about a third of the median gap it would need to (2.24).
`seg_cost` is a **max** over segments, so a short stub is almost never the
argmax — the double charge is largely muted in the candidate's own score, and
what it really inflates is the committed usage *other* bundles see.

So the ranking bar failed, and the fix that landed is justified on a different
footing: not QoR, but a **one-sided gap in the generator**. The by-class table
is what pointed at it — redundancy occurs in `TRUNK_H` (109 of 3388) and
`TRUNK_H_OOB` (41 of 595) and **nowhere else**, and every redundant stub is a
vertical stub off a horizontal spine. `add_trunk` has always carried the
suppression pass; it is gated on a `suppress_stubs` parameter that only
`add_trunk_v` passes `true`, because the H/V unification adopted the V structure
and passed `false` from `add_trunk_h` to keep the H output byte-for-byte
identical. `TRUNK_V` has no redundant pairs because its suppressor already
removes them.

**Status: root-caused and fixed behind `set_trim_trunk_stubs [on|off]`**,
default off, byte-identical unused, `BUDA_TRUNK_STUB_TRIM=1` for corpus A/B —
opt-in for 5a's reason, since dropping a stub re-sorts the WL-ordered pool. The
newly-enabled H path uses the **strict-interior** coverage rule matching
`verify.cpp`; enabling V's historical **inclusive** rule on H over-suppressed at
the graze boundary and the coverage gate paid for it by dropping candidates
(`big2` pool 2084 → 1880 inclusive, → 2011 strict; load-bearing pairs preserved
0 vs 508). Tightening V is a separate, non-byte-identical change and is not made
here. On present evidence this buys **no QoR** — it removes a defect, not a
regression.

**Why opt-in, and the part worth carrying to the next fix of this kind.** The
first cut applied the trim unconditionally and cost `chip3_topdown` 260 → 636
unplaced bits. That was originally written down here as pure occupancy
displacement — "the bundles that newly fail select identical candidates on both
sides". **That was wrong**, and measuring it properly is what produced the
opt-in. Comparing every bundle's selected `(index, type)` and pool size, `main`
vs an always-on build:

```
selected TYPE  changed:  9 of 640
selected INDEX changed: 10
pool SIZE      changed:  3      b14 72→80   b31 74→78   b42 59→62
control (main run twice):  0 / 0 / 0
```

The chain is longer than "shorter geometry → occupancy": candidates are
**WL-sorted**, so trimming one **renumbers indices**, which changes which
candidates clear generation gates — hence three pools that change *size*, which
occupancy cannot do — which flips selections, which moves occupancy. The
geometry is sound in every case constructible; what moves is the **search
space**. That also makes `chip3_topdown`'s number a poor signal about the trim's
quality, and it is why narrowing the trim would not have shrunk the blast radius
in proportion to how often it fires.

---

## 6. A partner deleted AFTER its neighbour was stretched to meet it

This is the live one, and entry 7 is the same defect at scale.

**Mechanism.** DetailedNUTS runs three passes in this order:

```
place_by_layer  ->  adjust_bit_spans  ->  cull_keepout_crossers
```

`adjust_bit_spans` extends each bit to reach its partner's track.
`cull_keepout_crossers` then deletes bits whose FINAL span still crosses a
keepout. **A partner removed by that cull is removed after the neighbour was
already stretched to meet it**, so the neighbour keeps metal aimed at a wire
that no longer exists. #678's stale-end rule cannot help: it lives inside
`adjust_bit_spans`, one pass too early.

**Reproduce.** A matched pair, because the contrast is the argument:

* `flow/antenna_culled_partner.buda` — the defect.
* `flow/antenna_starved_partner.buda` — the control. Identical design, identical
  pin, **one number different** in the keepout, and the dangling metal is gone.

Two details make these work. A keepout declared *up front* is one the planner
routes around, so it can never produce this — both declare it **after
`run_planner`**, the deterministic stand-in for the DEF-imported blockage
`flow/rv/soc` has. And the keepout must **miss the segment's span midpoint**:
`signal_tracks_in` tests keepouts at that single sample point, so covering the
midpoint fails placement *admission* instead and the bits never reach the cull.

**Measured.**

| | keepout | `num_keepout_bits` | seg 1 |
|---|---|---|---|
| culled | y 380..**450** (misses midpoint) | **4** | spans `[183.5,710]`, reaches `[183.5,183.5]` |
| starved | y 380..**720** (covers midpoint) | **0** | `[183.5,183.5]` — retracted |

4 findings of ~529 units = **2115 units of dangling metal on M4**, out of a 2922
detailed WL. The starved twin comes to 807 with no findings. `num_unplaced` is 4
in *both* — which is why a guard must assert `num_keepout_bits`, or it cannot
tell which path it tested.

**The audit is not the problem.** `check_design dnuts` reports every one of
those bits. It is the **placer** that leaves the metal.

**Status.** OPEN, cause identified, not fixed here — the fix is an engine change
(retract after the cull, or cull before adjusting) and the choice between those
is the owner's. Both vehicles are pinned by
`test_bit_antenna_audit.py::test_a_partner_lost_to_the_CULL_strands_its_neighbour`
and `…::test_a_partner_STARVED_before_span_adjustment_retracts_it_instead`, so
whichever way it is fixed, the pair is what must not regress.

---

## 7. `flow/rv/soc` — entry 6 at scale

**Reproduce.** `bin/buda flow/rv/soc.buda --no-viz --verbose-conn`, then read
`flow/rv/log/soc_flow.log`.

**Measured on current `main`.** **64 findings — 2 bundles × 32 bits × 19,600
units each**, i.e. 1,254,400 units:

```
Bundle 142: Seg 1 bit 0 on layer M5 spans [139200,158800]
            but this bit only reaches [139200,139200] — 0 + 19600 of dangling metal
```

**It is entry 6.** Mid-flow, immediately after the first `run_detailed_nuts`,
`num_keepout_bits` is **66** — every lost bit here goes through
`cull_keepout_crossers`, the post-adjustment path, which is exactly the twin
that dangles. (The earlier reading of this entry — that rv/soc must have some
*other* cause because the isolated vehicle came out clean — was wrong: that
vehicle was on the *starved* path and never reached the cull at all.)

**Status.** OPEN, but MID-FLOW ONLY — the healers re-pin and it does not survive
to the endpoint, which `test/tests/test_bit_antenna_audit.py` pins from both
sides (it must fire mid-flow, and must be gone at the end). So it costs nothing
in the shipped route today; it is a latent shape that a design without those
healers would keep. Fix entry 6 and this goes with it — the 4-bit vehicle is the
one to develop against.

---

## What checks exist, and what each is blind to

| check | reads | blind to |
|---|---|---|
| `detect_antennas` (`ANTENNA`, structural) | attachment *positions* on the bus-level `ConnSeg` graph | a segment attached at ≥2 points whose individual BITS are not |
| tap-overhang (#514) | a terminal piece over a block the segment **taps** | a piece past the last junction that taps nothing (entry 5a) — and 5b, where BOTH legs tap the block and neither piece is terminal |
| `detect_bit_antennas` (per bit) | per-bit vias + served taps + crossings the bit really makes | whether a *crossing* should vouch for a bit at all — the open question entry 3's vehicle exists to frame |
| `check_dnuts` block coverage | per-bit coverage of connected blocks | nothing here; it is the check that keeps the retractions honest |

Note that entry 6 is **not** a checker gap — the per-bit audit reports it in
full. It is the placer emitting metal the audit then correctly complains about,
which is the healthier of the two failure modes and the reason it is measurable
at all.

Three things to check first on any new instance:

1. **Is the datum governing the decision per SEGMENT or per BIT?** Entries 1, 2
   and 4 were all that. A segment is attached; a bit on it need not be.
2. **Which PASS removed the thing that is missing, relative to the pass that
   read it?** Entry 6 (and so 7) was that: the same bits lost one pass earlier
   are handled correctly, and one pass later are not.
3. **Does the fix move the SEARCH SPACE?** Entry 5 was that, and it is the one
   that does not look like a correctness question at all. Candidate pools are
   WL-SORTED, so any generation-stage change to geometry renumbers indices, and
   renumbering changes which candidates clear the generation gates — so the pool
   itself changes and selections flip design-wide. Before judging such a fix by
   a corpus number, establish whether the number is measuring the fix or the
   shift. `set_prune_dominated`, `set_dedup_loci`, `set_drop_dangling` and now
   `set_trim_mst_legs` are all opt-in for exactly this reason.
