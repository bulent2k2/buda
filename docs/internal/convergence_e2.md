# E2 — Abstract precision: blob vs bbox vs exact

*Experiment E2 of the [Convergence Ladder](convergence_ladder.md), run and
written up 2026-09-13.  Driver: `tools/experiment/e2_abstract_precision.py`
(`--nq N` repeatable, `--out DIR`, `--healers`, `--pin-ring`, `--loci`); its
construction is guarded by `test/tests/test_e2_abstract_precision.py` (mid
tier, NQ = 2).  The pictures are `tools/render_design.py`'s, one triptych per
arm and size.*

## The claim being tested

The top level loses routability in proportion to how coarsely it sees a
block's committed metal.  Same frozen block routing, three views of it: the
whole footprint (what a LEF `OBS` abstract says), the bounding box of the
routing, and the routing itself.

## Construction

**The source.**  `soc.tcl <NQ> -bottomup`, recorded at the `do_command` choke
point (`BUDA_RECORD`), so the arms are the flat script that flow issued.  In
that run every congruent cell is solved once and copied, and the copied
bundles are `hier.locked`: their placed bus segments are the block's committed
metal.  The driver runs the recording in-process and reads them off the
session — the instance each segment lies in, its layer, its placed rectangle.

**The four arms** are that recording with three changes and one switch, and
the guard asserts they differ in nothing else:

- the block-internal buses — the nets of the locked bundles — are removed,
  because every arm represents that metal as *obstruction* rather than routing
  it again;
- `set_bottom_up * off` right after `align_bottom_up`, so the instances sit
  where the source run put them and nothing is a template any more;
  `check_template_tracks` goes with it;
- a fixed tail: bundler, generation, `run_planner hier`, NUTS + audit, DNUTS
  + audit, `report_wirelength`.  Healers OFF in the primary table;
  `negotiate_congestion 10` + `ripup_reroute 20` in the secondary one;
- the switch — which keepouts stand for the block's metal:

| arm | one keepout per | what it stands for |
|---|---|---|
| **none** | — | the control: the top-level buses with the block's metal not there at all.  Not a design anyone can build; the floor. |
| **blob** | (instance, used layer): the instance footprint inset by an 8-unit pin-access ring | a LEF `OBS` abstract |
| **bbox** | (instance, used layer): the bounding box of the routing on that layer | the tightest rectangle a block could publish |
| **exact** | placed bus segment of the block's routing, at its track and width | the routing itself — what BUDA's copies already are |

The abstractions nest and the guard checks it keepout by keepout: every exact
rectangle lies inside a bbox one, every bbox inside a blob one; blob and bbox
carry the same count, exact more; the layer-areas are strictly ordered.

## Results — healerless (the primary table)

Every arm routes the same top-level bundles (13 / 21 / 37 / 69 at NQ = 2 / 4 /
8 / 16);
a bit-wire is one bit on one segment, so the count moves with the topologies
chosen.  A parenthesised wirelength excludes the stranded bits and is not
comparable to a complete route (`report_wirelength` says so on the line above
it).

| NQ | arm | keepouts | keepout layer-area | ovl | unplaced | audit | detailed WL |
|---|---|---|---|---|---|---|---|
| 2 | none | 0 | 0 | 0 | 0 | clean | 444,689 |
| 2 | blob | 58 | 20,208,384 | 3 | **160** | 160 in 5 bundles | (418,094) |
| 2 | bbox | 58 | 962,553 | 0 | 8 | 8 in 1 | (466,865) |
| 2 | exact | 88 | 357,500 | 0 | 5 | 5 in 1 | (469,853) |
| 4 | none | 0 | 0 | 0 | 0 | clean | 799,087 |
| 4 | blob | 110 | 37,340,928 | 9 | **432** | 432 in 12 | (491,244) |
| 4 | bbox | 110 | 1,414,137 | 1 | 50 | 50 in 3 | (855,905) |
| 4 | exact | 156 | 624,764 | 1 | 42 | 42 in 3 | (873,868) |
| 8 | none | 0 | 0 | 0 | 0 | clean | 1,582,348 |
| 8 | blob | 214 | 71,606,016 | 8 | **449** | 449 in 17 | (1,433,519) |
| 8 | bbox | 214 | 2,317,305 | 1 | 50 | 50 in 3 | (1,872,602) |
| 8 | exact | 292 | 1,159,292 | 1 | 41 | 41 in 3 | (1,856,619) |
| 16 | none | 0 | 0 | 0 | 0 | clean | 3,212,195 |
| 16 | blob | 422 | 140,136,192 | **79** | **2024** | 2024 in 50 | (1,708,148) |
| 16 | bbox | 422 | 4,123,641 | 1 | **87** | 88 in 10 | (3,695,152) |
| 16 | exact | 564 | 2,228,348 | 1 | **195** | 195 in 10 | (3,933,982) |

The source run itself, for reference — BUDA's own bottom-up with every net
routed, healers as the flow runs them: first DNUTS audit **8 / 117 / 93 / 336**
violations at NQ = 2 / 4 / 8 / 16, healed to **0 / 0 / 0** with 0 overlaps and
0 unplaced at the first three sizes and to **8 in 1 bundle** (3 overlaps, 8
unplaced) at NQ = 16 — the E4 fault, the `pc_0` seat, exactly as
[`soc.md`](../../flow/tcl/soc.md) records it.

## Results — healed (`negotiate_congestion 10` + `ripup_reroute 20` on every arm)

| NQ | arm | ovl | unplaced | first audit → last | detailed WL |
|---|---|---|---|---|---|
| 2 | none | 0 | 0 | clean | 444,689 |
| 2 | blob | 0 | **112** | 160 → 112 | (367,110) |
| 2 | bbox | 0 | **0** | 8 → 0 | 474,590 |
| 2 | exact | 0 | **0** | 5 → 0 | 474,590 |
| 4 | none | 0 | 0 | clean | 799,087 |
| 4 | blob | 2 | **223** | 432 → 223 | (561,074) |
| 4 | bbox | 1 | 18 | 50 → 18 | (883,113) |
| 4 | exact | 0 | **4** | 42 → 4 | (924,256) |
| 8 | none | 0 | 0 | clean | 1,582,348 |
| 8 | blob | 2 | **314** | 449 → 314 | (1,301,638) |
| 8 | bbox | 1 | 18 | 50 → 18 | (1,890,546) |
| 8 | exact | 0 | **4** | 41 → 4 | (1,901,257) |
| 16 | none | 0 | 0 | clean | 3,212,195 |
| 16 | blob | **37** | **1623** | 2024 → 1623 | (1,777,491) |
| 16 | bbox | 0 | 18 | 87 → 18 | (3,966,316) |
| 16 | exact | 0 | **12** | 195 → 12 | (4,051,863) |

## What the tables say

1. **Precision orders the outcome at NQ = 2, 4 and 8 in both tables, and
   at NQ = 16 only once the healers have run.**  At the three smaller sizes
   none ≤ exact ≤ bbox ≤ blob on unplaced bits, monotone with no crossing,
   and the guard asserts that ordering at NQ = 2 so an engine change that
   broke it would be noticed.  At NQ = 16 the two precise arms **swap**
   healerless — exact strands 195 bits against the bbox's 87, both in 10
   bundles, both with one overlap — and swap **back** healed: 12 against
   18, both at 0 overlaps.  The outer ordering (none ≤ {bbox, exact} ≪
   blob) holds at every size in both tables; the claim that finer is always
   better for the PLAIN pipeline does not, and this write-up was drafted
   saying it did before the NQ = 16 rows came in.  See "the NQ = 16 swap"
   below.

2. **The blob is a different regime, not a worse point on the same curve.**
   Healerless it strands 160 / 432 / 449 / 2024 bits against the bbox's
   8 / 50 / 50 / 87, and it is the only arm with more than one overlap (79
   at NQ = 16 against 1).  The healers, which
   take bbox and exact to clean at NQ = 2, to 18 / 4 at NQ = 4 and 8 and to
   18 / 12 at NQ = 16, take the blob from 449 to 314 and from 2024 to 1623
   (with 37 overlaps left).  Its keepout layer-area is **56×, 60×, 62×, 63×** the
   exact arm's at NQ = 2, 4, 8, 16 — the abstract reserves sixty times the metal
   the block's routing occupies — and the picture shows why: with a ring of 8
   the footprint of a cluster is a wall on every layer its routing touched,
   and a top-level bus has to go round it.

3. **bbox to exact is a 2× area difference and a small routing one that
   changes sign with size.**  The bbox is 2.69× / 2.26× / 2.00× / 1.85× the
   exact layer-area — it tightens with size as a cluster's routing fills its
   own box — and costs 20 % more stranding healerless at NQ = 4 / 8 (50
   against 41 / 42) and 4.5× more healed (18 against 4); at NQ = 16 it
   strands 87 where exact strands 195 healerless, and 18 where exact
   strands 12 healed.  At NQ = 2 both heal clean at an **identical**
   474,590, i.e. at that size the extra precision changed nothing the top
   used.

4. **Representing the block's metal at all costs wire.**  The only complete
   routes with obstruction are the two healed NQ = 2 arms: +6.7 % over the
   floor (474,590 against 444,689).  Every other obstructed row is
   parenthesised, so no other wire comparison is made here.

## The NQ = 16 swap

At NQ = 16 the exact arm strands more than twice what the bbox arm does
(195 against 87, healerless), reversing the order the three smaller sizes
show — and the healers reverse it again (12 against 18, both at 0
overlaps): the swap is a property of the plain pipeline's plan, not of the
obstruction, and the second candidate reading below turned out to be the
measured one.  What is established: the counts, from the same driver and the same
audit; that both arms strand in 10 bundles with one overlap each; that the
exact arm takes twice the time (4.1 s against 2.0 s) with 564 keepouts
against 422.  The tool names WHAT stranded, read off
each arm's DetailedNUTS log (deterministic: identical at `BUDA_THREADS=1`
and unset, in-process and through the CLI):

| NQ = 16, healerless | culled — final span crosses a keepout on its layer | admission strands — a seat short of tracks | total |
|---|---|---|---|
| bbox | 55 | 32 | 87 |
| exact | 99 | 96 | 195 |

The exact arm loses on BOTH counts.  Its admission strands are four M5
seats the log names — bundles 15, 62, 66 (4 unreserved tracks against 32
needed) and 57 (12 against 16) — and the built-in cull heal escalated three
span-starved LOW segments (243 → 211 → 195) before the count settled; the
bbox arm's two heal trials cut opens to 51 and 74 but raised overlaps 1 → 3
and 1 → 2, so both were rejected under the componentwise accept and its 87
stands (finding that out is what made the heals SAY when they reject a
trial: the flow's one-line summary had quoted the rejected 74 as the
result).  What is
NOT established is WHY the finer keepouts strand more, and it is
deliberately not guessed at — `soc.md` records five channel readings on
this vehicle that each turned out to be a seat or a face.  Two readings are
*candidates* to be measured, both testable with knobs that exist: a
keepout's four edges are Hanan loci, so 564 exact rectangles offer the
top-level planner trunk positions BETWEEN a block's segments that a bbox
forbids outright, and a bit seated there is exactly what the cull removes
and the M5 seats are exactly what the keepouts between them leave short
(`set_keepout_loci` cannot separate this — the keepouts are not inside a
block — so it wants an arm whose exact keepouts contribute no loci); and
the healers, which had not run in the primary table, may recover one arm
more than the other — MEASURED: they take exact from 195 to 12 and bbox
from 87 to 18, so the healed order is the small-size order again, and what
the healerless swap says is that the finer keepouts hand the plain planner
a worse first plan that the healers can repair further.  Why the first
plan is worse is still the unguessed part.  Either way the
finding stands on its own: **finer obstruction is not monotonically better
for the top level's FIRST plan at every size, and a write-up that said so
from three sizes was wrong at the fourth; healed, it is.**  The blob's regime is unchanged by it —
2024 stranded, 79 overlaps, 63× the layer-area.

## What the tables do not say

- **The exact arm is not BUDA's own copies.**  The source run heals to 0 at
  every size; the exact arm heals to 4 unplaced bits at NQ = 4 and 8.  A
  keepout is inert: the healers can RELEASE a locked instance and re-solve it
  (`ripup_reroute`'s release pass) or re-pin its template for every instance
  (the class pass), and a keepout gives them nothing to move; a keepout also
  adds Hanan loci the planner's obstruction model reads differently from
  occupancy.  The two flows heal differently too (the source runs
  `soc_lib`'s heal-if-dirty with `refine_selection` and a second round).  So
  E2 measures the *precision* axis of the abstraction and nothing about
  keepouts against copies — but the 4-bit gap between the most precise
  obstruction and a coordinated block is a number worth keeping: it is the
  cheapest E1-shaped measurement made so far.
- **The judge is `check_design`.**  The plan's independent geometric audit is
  build item 2 and does not exist yet; every row above is the tool's own
  verdict, the same audit on every arm.  The write-up is to be re-judged when
  the audit lands, as every table on the ladder is.
- **The blob's 8-unit pin ring is an assumption**, chosen as what a LEF `OBS`
  leaves for pin access; a wider ring shrinks the blob, and the ring is a
  driver option (`--pin-ring`) so the sensitivity can be measured rather than
  argued.
- **The control is not a design.**  `none` is what the top-level buses cost
  with the block's metal absent, the floor every other arm is read against.
- **NQ = 16 nearly went unmeasured.**  The source run at that size is the
  E4 fault — `soc.tcl 16 -bottomup` is dirty at the default channel (3
  overlaps, 8 unplaced, one supply-doomed seat) and exits 1 on that verdict —
  and the driver's first cut refused a recording whose flow exits non-zero,
  reading a verdict as a crash.  The recording is accepted on the verdict
  line now and the source row reports the dirty endpoint as it ran.  Had the
  size been dropped, the swap above would not have been seen.

## Pictures

`tools/experiment/e2_abstract_precision.py --nq 8 --out DIR` regenerates
everything at NQ = 8: `soc8_<arm>_{fp,nuts,dnuts}.png` and `_meta.json` per
arm, `e2_summary.json`, and the markdown table on stdout.  The artifact
carries the NQ = 8 blob and exact DetailedNUTS panels side by side — the
hatched keepouts are the arm's view of the block's metal, drawn in the layer's
colour, and the top-level bit-wires route round them.

## Provenance

- Source flows: `soc.tcl 2|4|8|16 -bottomup`, recorded with `BUDA_RECORD`.
- Healerless sweep: `e2_abstract_precision.py --nq 2 --nq 4 --nq 8`, then
  `--nq 16` alone (the source at that size takes 190 s).
- Healed sweep: the same with `--healers` (NQ = 16 alone, 230 s source + up to
  28 s per arm).
- Pin ring 8, keepout loci default (`all`).
