# E4 — Diagnosed vs blind iteration

*Experiment E4 of the [Convergence Ladder](convergence_ladder.md), written up
2026-09-13 from measurements that already existed in
[`flow/tcl/soc.md`](../../flow/tcl/soc.md).  No new run was needed and none
was made; every number below is that page's, cited to its table.  There is no
picture: `tools/render_design.py` draws what was placed, and the finding is
eight bits that were not.*

## The claim being tested

A loop whose failure is **named** closes in one step.  A loop that only sees a
count has to search, and when the response to its one knob is non-monotone,
the search has to be a sweep — a ramp that stops at the first clean point can
stop on a point that a larger step would have skipped, or step from a clean
point onto a dirty one.

The metric is the number of full flow runs to reach a clean endpoint, and
*what the remedy touched*: the cause, or the symptom.

## The vehicle and the fault

`soc.tcl 16 -bottomup`: the SoC at 32 clusters, 427 leaves, 627 bundles,
19,424 bit-wires, every congruent cell solved once and copied.  At the default
channel it comes back **dirty** — 3 overlaps, 8 unplaced bits, 8 audit
violations.  The 8 bits are one bundle, `hb-2` (`DRV:io/p_0|REC:quad_0/cl_0/rtr/xbar`,
the first io pad into cluster 0's crossbar — `pc_0`, 8 bits of `CW`), and the
tool reports the reason each time as a **supply-doomed seat**: the segment's
assigned window offers 7 signal tracks for 8 member bits, a static
width-infeasibility, not a reservation conflict.

That is the whole difference between the two arms.  A conventional flow reports
"8 unplaced"; BUDA's `check_design` reports *which segment, which layer, how
many tracks against how many bits, and which category*.

## The blind arm: sweep the one knob

Without the diagnosis, the visible lever is the channel (`-GAP`, `-M`): the
routing gap between blocks.  Sweeping it at NQ = 16 (`soc.md`, "every sizing
fix pushed the channel further out"):

| GAP = M | 16 | 24 | 32 | 48 | 96 |
|---|---|---|---|---|---|
| endpoint | ✗ 3 ovl / 8 unpl | **ok** | ✗ 8 unpl | ✗ 1 ovl / 8 unpl | **ok** |
| detailed WL | (4,319,399) | 4,648,190 | (5,058,836) | (5,856,977) | 8,621,243 |

Five runs.  Two of the five gaps are clean and they are not adjacent: 24 is
clean, 32 and 48 are not, 96 is.  A parenthesised wirelength excludes the
stranded bits and is not comparable to a complete route.

What the non-monotone row licenses is exact and narrow.  A ramp from the
default in steps of 8 lands on 24 after **two** runs and is clean.  A ramp in
steps of 16 lands on 32 and 48 — both dirty — and the next point it would try,
64, was never measured, so how many runs *that* ramp needs is unknown.  A ramp
that starts from a clean point and wants margin steps from 24 onto 32 and gets
worse.  The blind loop's round count is therefore **a property of the sweep
design, and no sweep design is known safe in advance**: the only reading the
table supports is "sweep, do not ramp", which is what `soc.md` says.

The same shape at NQ = 32 (three gaps run): 16 ✗ (3 ovl / 8 unpl), 24 ok,
96 ok.

## The diagnosed arm: act on the named seat

The census says the seat is 7 tracks for 8 bits.  The lever that addresses a
seat is the bundle's bit count on it, not the channel: `set_max_bundle_bits 4
for pc_` splits the one bundle so no segment asks a seat for 8 bits.  One run,
at each size (`soc.md`, "the seat lever, measured rather than asserted"):

| remedy | NQ | ovl | unpl | viol | doomed seats reported | detailed WL |
|---|---|---|---|---|---|---|
| none (default GAP 16) | 16 | 3 | 8 | 8 | 1 | (4,319,399) |
| `set_max_bundle_bits 4 for pc_` | 16 | 3 | **0** | **0** | **0** | 4,284,321 |
| none (default GAP 16) | 32 | 3 | 8 | 8 | 4 advisory lines | (8,265,153) |
| `set_max_bundle_bits 4 for pc_` | 32 | 3 | **0** | **0** | **0** | 8,293,531 |

The stranding is gone in one run at both sizes, and the advisory that named it
is gone with it.  The three overlaps are **not** gone, and that is the point of
the row rather than a weakness of it: the overlaps are the fixed-copy residue,
a different fault the census never attributed to the seat.  The diagnosed
remedy removed exactly what the diagnosis named and nothing else.

The channel, by contrast, clears both faults at once — `-GAP 24 -M 24` is
0 / 0 / 0 at NQ = 16 and NQ = 32 — which is precisely why the channel read as
*the cause* for five revisions of `soc.md` before the seat lever separated the
two.

## What the comparison says, and what it does not

| | blind (channel sweep) | diagnosed (seat lever) |
|---|---|---|
| runs to remove the stranding | depends on the sweep: 2 with an 8-step ramp from 16, more with any other design, unknown for some | **1** |
| what the remedy touched | the symptom — the channel is not the cause (the same segment strands at 16, 32, 48 and 64, and reports a doomed seat even at the clean 24) | the cause — the seat's bit demand |
| endpoint reached | clean at 24 (3 of 3 faults) | stranding cleared; 3 overlaps remain |
| wire, complete routes only | 4,648,190 at NQ = 16 | 4,284,321 at NQ = 16 (a complete placement with 3 overlaps, not a clean one) |

Three things this does **not** show:

1. **That the diagnosed loop reaches a clean endpoint on its own.**  It does
   not: the overlaps are a second fault with a second remedy, and for a clean
   endpoint the channel alone is still the cheapest point (`soc.md` says so and
   this write-up keeps it).  What the diagnosed loop reaches in one step is the
   fault it named.  Counting "rounds to clean" naively would credit the blind
   loop for stumbling onto a gap that happens to fix both; the honest count is
   rounds per *named* fault, and there the ratio is one against a sweep.
2. **Why healing succeeds at 24 and not at 32 or 48.**  `soc.md` withdrew one
   asserted mechanism and deliberately supplies no other; this write-up follows
   it.
3. **Anything judged by an independent audit.**  Both arms are scored by
   `check_design`.  The plan accepts that for E4 because the claim is about the
   loop rather than the route, and the two arms are judged identically; every
   later experiment on the ladder uses the independent judge instead.

## What it contributes to the ladder

E4 is the **diagnosability** half of "BUDA in the loop", and it is
rung-independent: it says nothing about what the block was told, only that when
the tool names the fault, the round that fixes it is one round, and when it
does not, the round count is a property of a search nobody can design safely
in advance.  It also records the cost of getting this wrong: a symptom-driven
loop can fix the symptom — the channel *does* clear the stranding — and record
the wrong cause, which is what happened here for five revisions and what the
seat lever finally separated.

## Provenance

- Gap sweeps: `soc.md` §"`-bottomup`: every sizing fix pushed the channel
  further out" (NQ = 16 five gaps; NQ = 32 three gaps).
- Seat table and the recurring segment: §"What the bottom-up failure actually
  is".
- Seat lever tables: §"The seat lever, measured rather than asserted".
- Every run was `soc.tcl <NQ> -bottomup [-GAP g -M g]`, healers included as the
  flow runs them; the lever runs add `set_max_bundle_bits 4 for pc_`.
