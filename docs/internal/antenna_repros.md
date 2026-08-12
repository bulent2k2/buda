# Dangling-metal ("antenna") shapes — the repro index

Metal that is part of a routed net but attaches to nothing: a wire whose end
runs past its own last junction or tap. Electrically inert, and in real silicon
an antenna-rule problem rather than a wirelength one.

The reason there are so many separate entries is that they are all the same
*kind* of mistake in different places: **per-SEGMENT data governing a per-BIT
quantity**. A segment is attached; a bit on it need not be. Every audit that
reads the bus-level `ConnSeg` graph is blind to the difference, which is why
several of these ran for a long time with `check_design` reporting Success.

Each entry says how to reproduce it, what is measured, and its status. Nothing
here is asserted from reading the code alone — every measurement is from a run.

| # | shape | vehicle | status |
|---|---|---|---|
| 1 | tapered stub keeps other bits' metal | `flow/rv/soc_conv_div.buda` | FIXED #678 |
| 2 | busterm tap vouches for every bit | `flow/antenna_taper_passthru.buda` | FIXED #690 |
| 3 | bus wider than the block it crosses | `flow/antenna_wide_bus_passthru.buda` | UNREACHABLE |
| 4 | crossing credited at the nominal seg | `flow/rnr/mix2_topdown_refine.buda` | FIXED #695 |
| 5 | two MST edges duplicate a leg | `flow/mst_shared_leg_prefix.buda` (branch `claude/mst-leg-overshoot`) | **OPEN** |
| 6 | a culled partner strands a segment | `flow/antenna_culled_partner.buda` + `flow/antenna_starved_partner.buda` | **OPEN — cause found** |
| 7 | `rv/soc` 1.25M units mid-flow | `flow/rv/soc.buda` | **OPEN — same cause as 6** |

Entries 5, 6 and 7 are the live ones, and 6 and 7 are now known to be **one
defect**: a DetailedNUTS pass-ordering problem, isolated to a 4-bit vehicle in
entry 6. Entry 3 is a negative result kept as a guard. Entries 3, 6 and 7's
vehicles all end DIRTY on purpose; none belongs in the QoR corpus.

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
revealed is entry 5.

---

## 5. Two MST edges leaving one block duplicate a leg

**Mechanism.** `realize_mst_edge` routes each edge on its own, from the closest
point between its two blocks, so two edges incident on the *same* block start at
the same face point. When both go L-shaped with the same first axis, the shorter
one's leg lies inside the longer one's. The longer leg's prefix is a duplicate
with a **free end**, and it pushes the leg's end *past* the divergence junction —
which makes that junction a mid-span conn, and DetailedNUTS only snaps a bit to
its own via at an ENDPOINT conn.

**Reproduce.** `flow/mst_shared_leg_prefix.buda`, which lives on branch
`claude/mst-leg-overshoot` (PR #708) together with the proposed fix — it is
deliberately NOT duplicated here, so there is one copy to keep in step with
whatever that PR settles on. The vehicle reproduces the shape on `main` as well:
`git show origin/claude/mst-leg-overshoot:flow/mst_shared_leg_prefix.buda > /tmp/m.buda`
then run it. Also `flow/rnr/mix2_topdown_refine.buda` bundle 35 in a real design.

**Measured.** 8.75 + 5.75 + 2.75 units past the last via on bundle 35. On the
standalone vehicle, on `main`, at the shared corner (2010,1010): `MST_HV` seg1
len 120 inside seg3 len 1320; `MST_VH` seg3 len 70 inside seg1 len 620.

**Status.** OPEN — a fix exists in **PR #708** but is *not* recommended as it
stands: it clears the shape (WL 13753 → 13736 on the pinned candidate,
`check_design` dirty → Success) at the cost of `chip3_topdown` 6/260/34 →
6/636/78. That regression is measured **collateral**, not bad geometry: the trim
fires ~5 times there, and the bundles that newly fail select *identical*
candidates on both sides — plain 2-segment `L_HV` shapes with no MST leg in them
— failing DNUTS *admission* because global track occupancy moved.

A second, independent gap in that fix is recorded on the PR: `compute_mst` emits
every edge with `u < v` and `realize_mst_edge` routes `u → v`, so when the shared
node holds the higher index both legs **end** at the shared point rather than
start there, and a start-only match misses it. It is latent and needs a
*non-driver* block to be the shared MST node — the fixture cannot expose it,
because its hub is the bus driver and so heads the busterm list whichever way the
blocks are declared.

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
| tap-overhang (#514) | a terminal piece over a block the segment **taps** | a piece past the last junction that taps nothing (entry 5) |
| `detect_bit_antennas` (per bit) | per-bit vias + served taps + crossings the bit really makes | whether a *crossing* should vouch for a bit at all — the open question entry 3's vehicle exists to frame |
| `check_dnuts` block coverage | per-bit coverage of connected blocks | nothing here; it is the check that keeps the retractions honest |

Note that entry 6 is **not** a checker gap — the per-bit audit reports it in
full. It is the placer emitting metal the audit then correctly complains about,
which is the healthier of the two failure modes and the reason it is measurable
at all.

Two things to check first on any new instance:

1. **Is the datum governing the decision per SEGMENT or per BIT?** Entries 1, 2,
   4 and 5 were all that.
2. **Which PASS removed the thing that is missing, relative to the pass that
   read it?** Entry 6 (and so 7) was that: the same bits lost one pass earlier
   are handled correctly, and one pass later are not.
