# E1 — Blind bottom-up vs derived budget

*Experiment E1 of the [Convergence Ladder](convergence_ladder.md), run and
written up 2026-09-16.  Driver: [`flow/tcl/converge.tcl`](../../flow/tcl/converge.tcl)
(ladder item 5) over the vehicle hooks in
[`converge_lib.tcl`](../../flow/tcl/converge_lib.tcl); construction guarded by
`test/tests/test_converge_driver.py` (mid tier, NQ = 2).  No picture: the
finding is about rounds and reservations, not about a route.*

## The claim being tested

Handing a block the **complement of the top's measured demand** converges in
one informed round; handing it nothing converges in R blind rounds, with R
growing with instance count.  The metric is rounds to a clean endpoint, the
effort spent (template classes solved), the endpoint reached, and the
**reservation efficiency** — tracks reserved over the blocks against tracks
the top actually used there.

## The result, in one paragraph

**The claim is refuted for the primitive as built, and the reason is
measured.**  The blind policy (`reserve_top_layers`, its step swept to its
best) reaches a clean endpoint in **two rounds** at NQ = 2, 4 and 16 with
healers off — reserving 4.5 to 8 tracks for every track the top used — and
never at NQ = 8; with the vehicle's own healing it is clean in **one round
with no reservation at all** at NQ = 2, 4 and 8 and in two at NQ = 16.  The
derived budget (`derive_cell_layer_shares` → `set_cell_layer_share`) reaches
a clean endpoint in **no round** with healers off, at any size, and does
not improve on the round it was derived from; with healers on, the
top-down-derived arm heals clean in one informed round at NQ = 2, 8 and 16
(two at 4) — the **same session count as the blind arm**, at 3–8.5× the
top's used tracks in reservation where the blind arm needed none or 9.8× —
and the blind-round-derived arm stays dirty at NQ = 16 on the seat the
blind round left.  A
`set_cell_layer_share` is a **uniform** budget — the first `floor(s ×
n_signal)` slots of every period, over the whole instance — and the top's
demand is **positional**; the block's own buses fill their seats to 89–100
% on the layers the top wants, so the floor the derivation must respect
leaves those layers at full use, and what it can hand the top elsewhere the
top does not need.  Rung 4 wants a positional reservation, which is E5's
primitive, not a share.

## Construction

**Vehicle.**  `soc.tcl` at NQ = 2, 4, 8, 16 (4 / 8 / 16 / 32 clusters; 63 /
127 / 255 / 511 leaf instances; 95 / 183 / 359 / 711 buses), the ladder's
lead vehicle (Q1); `tpu.tcl` at N = 8, 16 as the control.  Every session is
one run of the vehicle through five hooks the driver passes
(`-reserve N`, `-shares FILE`, `-derive FILE`, `-noheal`, `-report FILE`),
so an arm is a *sequence of ordinary vehicle runs* and the report each one
leaves (both verdicts, marks, the reserve, the derived lines, one demand
row per instance and layer) is what the tables are built from.

**The three arms**, each a loop until clean or out of moves:

| arm | round 1 | round k | what it is |
|---|---|---|---|
| **blind** | the block routes freely (`set_bottom_up *`, every eligible cell solved once and copied), is frozen, the top routes against it | the policy takes the block's highest layers away — `reserve_top_layers step·(k−1)` — and every instance is re-spun | rung 0, then rung 3: the industry's information flow |
| **td** | plan the whole design **top-down** once; read the per-instance per-layer demand; write every cell the complement as a share | solve every template once under the shares, copy, route the top; re-derive from the result for the next round | rung 4 as the ladder wrote it |
| **bu** | the blind round 1 **is** the measurement: it routed the top against the frozen blocks, so its demand rows are the top's demand on this geometry | the informed round: templates under the shares derived from round 1, then re-derive | the diagnosed loop (E4): rung 0, then rung 4 |

`bu`'s round 1 and `blind`'s round 1 are the same session (one run, reported
in the blind rows).  Two informed rounds per derived arm; a second round
re-derives from the first with the scope pinned to the first file's cells.

**The blind policy's parameter** is the step — layers given up per round —
swept at 1 and 2, and its ceiling is 4 (a band must keep an H and a V layer;
the stack is M2..M7).

**What a share can and cannot say.**  `set_cell_layer_share C L p` thins
`C`'s pattern on `L` to its first `floor(p/100 × n_signal)` SIGNAL slots per
period, over the whole instance, for `C`'s own solve and every nested cell's
(the thinned view is installed over the instance's bbox).  The derivation
takes `100 − worst top demand` over the cell's instances, then **floors it
by the cell's own need**: the worst own seat over its instances (subtree
included) by the DNUTS admission arithmetic — a bus needing N of the P
tracks in its seat cannot live under a share keeping fewer than N/P of
them.  Where the block needs every slot, the layer gets no line (full use)
and the note says what the top wanted there.  This floor was **added by
this experiment**: the first derived round, without it, stranded **410 bits
at NQ = 2** where the blind round strands 8 — every one a core's 32-bit bus
in a 35-track seat under its cluster's 71 % M5 share.  `nofloor` keeps the
pure complement as the study control.

**Reservation efficiency** is read off the last round's demand rows: for
every (instance, layer) pair carrying a reservation, *reserved* is the
tracks the policy takes from the block (every track of a reserved layer
over an instance of a **capped** cell — `reserve_top_layers` caps every cell
below the top level and leaves the top level, the SoC's `quad_cell`,
unrestricted, so its rows carry no reservation and do not count; the report
records which cells were capped, `buda::query caps`; `1 − kept/n_signal` of
the supply under a share) and *used* is every track the top placed over
that instance on that layer, inside or outside the reserved slots.
reserved ÷ used > 1 is padding.  (The first version of these tables summed
the top N layers over EVERY instance, the uncapped `quad_cell` rows
included — Codex P2 on #935; every blind ratio here moved by under 0.6 when
recomputed from the same reports, and no finding changed.)

**Judge.**  `check_design`, the same audit on every arm — the ladder's
independent geometric audit (build item 2) does not exist yet, as E2's
write-up also says.  A parenthesised wirelength excludes stranded bits and
is not comparable to a complete route.

## Results — healers off (the plain pipeline's verdict)

`ovl/unpl/viol` = NUTS overlaps / DetailedNUTS unplaced bits / audit
violations.  `classes` = template classes solved that round (every session
marks the same 18 cells).  The first audit and the final verdict coincide
here, since nothing heals.

| size | arm | round | policy | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | s |
|---|---|---|---|---|---|---|---|---|---|
| 2 | blind | 1 | reserve 0 | 1/8/8 | (584,581) | — | — | — | 1.8 |
| 2 | blind | 2 | reserve 1 | 1/8/8 | (584,581) | 3,553 | 112 | 31.72 | 1.6 |
| 2 | blind | 3 | reserve 2 | **0/0/0** | 527,039 | 9,458 | 1,160 | 8.15 | 1.5 |
| 2 | td | 0 | top-down | 0/0/0 | 525,144 | — | — | — | 1.8 |
| 2 | td | 1 | shares r0 | 2/296/296 | (539,487) | 1,140 | 381 | 2.99 | 1.8 |
| 2 | td | 2 | shares r1 | 2/8/8 | (582,159) | 1,121 | 265 | 4.23 | 1.8 |
| 2 | bu | 1 | shares r0 | 1/8/8 | (586,220) | 3,722 | 1,259 | 2.96 | 1.8 |
| 2 | bu | 2 | shares r1 | 1/8/8 | (586,220) | 3,722 | 1,259 | 2.96 | 1.8 |
| 4 | blind | 1 | reserve 0 | 5/117/117 | (980,908) | — | — | — | 3.5 |
| 4 | blind | 2 | reserve 1 | 4/45/45 | (1,002,666) | 6,643 | 614 | 10.82 | 3.0 |
| 4 | blind | 3 | reserve 2 | **0/0/0** | 945,980 | 17,779 | 3,467 | 5.13 | 2.9 |
| 4 | td | 0 | top-down | 1/16/16 | (993,477) | — | — | — | 3.8 |
| 4 | td | 1 | shares r0 | 4/101/101 | (914,410) | 3,142 | 313 | 10.04 | 3.7 |
| 4 | td | 2 | shares r1 | 4/45/45 | (989,472) | 2,121 | 552 | 3.84 | 3.6 |
| 4 | bu | 1 | shares r0 | 5/117/117 | (981,590) | 9,194 | 2,443 | 3.76 | 3.6 |
| 4 | bu | 2 | shares r1 | 5/117/117 | (981,590) | 9,194 | 2,443 | 3.76 | 3.7 |
| 8 | blind | 1 | reserve 0 | 6/93/93 | (2,012,407) | — | — | — | 8.7 |
| 8 | blind | 2 | reserve 1 | 8/85/85 | (1,949,459) | 12,849 | 1,149 | 11.18 | 6.6 |
| 8 | blind | 3 | reserve 2 | 1/8/8 | (1,847,877) | 34,453 | 7,149 | 4.82 | 6.4 |
| 8 | blind | 4 | reserve 3 | 0/**1444**/1444 | (1,851,256) | 56,481 | 8,969 | 6.30 | 6.5 |
| 8 | blind | 5 | reserve 4 | 16/520/520 | (1,842,234) | 70,314 | 8,969 | 7.84 | 6.2 |
| 8 | td | 0 | top-down | 2/40/40 | (1,871,476) | — | — | — | 8.3 |
| 8 | td | 1 | shares r0 | 8/88/88 | (1,892,125) | 6,849 | 1,414 | 4.84 | 9.0 |
| 8 | td | 2 | shares r1 | 13/96/96 | (1,891,981) | 7,393 | 1,459 | 5.07 | 8.7 |
| 8 | bu | 1 | shares r0 | 11/109/109 | (1,955,348) | 18,469 | 5,013 | 3.68 | 8.4 |
| 8 | bu | 2 | shares r1 | 11/109/109 | (1,955,348) | 18,272 | 5,013 | 3.64 | 8.8 |
| 16 | blind | 1 | reserve 0 | 11/336/336 | (4,274,800) | — | — | — | 23.7 |
| 16 | blind | 2 | reserve 1 | 15/203/203 | (4,357,598) | 25,179 | 2,669 | 9.43 | 16.7 |
| 16 | blind | 3 | reserve 2 | **0/0/0** | 3,757,248 | 67,733 | 15,072 | 4.49 | 16.0 |
| 16 | td | 0 | top-down | 3/32/32 | (3,677,304) | — | — | — | 25.3 |
| 16 | td | 1 | shares r0 | 19/187/187 | (4,357,918) | 13,266 | 1,822 | 7.28 | 23.9 |
| 16 | td | 2 | shares r1 | 21/203/203 | (4,290,792) | 14,471 | 2,723 | 5.31 | 24.0 |
| 16 | bu | 1 | shares r0 | 12/368/368 | (4,222,602) | 38,950 | 11,667 | 3.34 | 24.6 |
| 16 | bu | 2 | shares r1 | 12/368/368 | (4,222,602) | 38,902 | 11,667 | 3.33 | 24.3 |

| size | arm | rounds | classes solved | endpoint |
|---|---|---|---|---|
| 2 | blind | 3 | 54 | clean |
| 2 | td | 1 + 2 | 36 | dirty, 2/8/8 |
| 2 | bu | 1 + 2 | 54 | dirty, 1/8/8 |
| 4 | blind | 3 | 54 | clean |
| 4 | td | 1 + 2 | 36 | dirty, 4/45/45 |
| 4 | bu | 1 + 2 | 54 | dirty, 5/117/117 |
| 8 | blind | 5 | 90 | **dirty**, 16/520/520 (best round: 1/8/8 at reserve 2) |
| 8 | td | 1 + 2 | 36 | dirty, 13/96/96 |
| 8 | bu | 1 + 2 | 54 | dirty, 11/109/109 |
| 16 | blind | 3 | 54 | clean |
| 16 | td | 1 + 2 | 36 | dirty, 21/203/203 |
| 16 | bu | 1 + 2 | 54 | dirty, 12/368/368 |

## Results — healers on (`soc_lib`'s heal-if-dirty in every round)

The first audit is the healerless verdict of the same session; the final
verdict is after `heal_if_dirty` (negotiate + ripup, and a second round with
`refine_selection` when the first leaves a residue).  Healing is where the
seconds go: the 16-cluster blind round 1 spends 400 s healing 336 stranded
bits down to 8.

| size | arm | round | policy | first ovl/unpl/viol | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | s |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | blind | 1 | reserve 0 | 1/8/8 | **0/0/0** | 591,230 | — | — | — | 2.8 |
| 2 | td | 0 | top-down | 0/0/0 | 0/0/0 | 525,144 | — | — | — | 1.8 |
| 2 | td | 1 | shares r0 | 2/296/296 | **0/0/0** | 601,617 | 1,140 | 364 | 3.13 | 12.3 |
| 2 | bu | 1 | shares r0 | 1/8/8 | **0/0/0** | 592,276 | 3,984 | 1,268 | 3.14 | 2.7 |
| 4 | blind | 1 | reserve 0 | 5/117/117 | **0/0/0** | 1,088,063 | — | — | — | 12.9 |
| 4 | td | 0 | top-down | 1/16/16 | 0/0/0 | 1,007,765 | — | — | — | 4.3 |
| 4 | td | 1 | shares r0 | 4/101/101 | 1/0/0 | 1,091,810 | 3,466 | 963 | 3.60 | 15.5 |
| 4 | td | 2 | shares r1 | 4/45/45 | **0/0/0** | 1,061,538 | 2,592 | 678 | 3.82 | 5.1 |
| 4 | bu | 1 | shares r0 | 5/117/117 | **0/0/0** | 1,088,745 | 7,843 | 2,784 | 2.82 | 12.9 |
| 8 | blind | 1 | reserve 0 | 6/93/93 | **0/0/0** | 2,175,864 | — | — | — | 33.1 |
| 8 | td | 0 | top-down | 2/40/40 | 0/0/0 | 1,968,672 | — | — | — | 9.2 |
| 8 | td | 1 | shares r0 | 8/109/109 | **0/0/0** | 2,180,195 | 6,784 | 1,304 | 5.20 | 69.6 |
| 8 | bu | 1 | shares r0 | 11/109/109 | **0/0/0** | 2,174,752 | 16,947 | 5,590 | 3.03 | 45.0 |
| 16 | blind | 1 | reserve 0 | 11/336/336 | 3/8/8 | (4,319,399) | — | — | — | 403.5 |
| 16 | blind | 2 | reserve 1 | 15/203/203 | **0/0/0** | 4,482,219 | 25,179 | 2,561 | 9.83 | 213.5 |
| 16 | td | 0 | top-down | 3/32/32 | 0/0/0 | 3,935,746 | — | — | — | 28.0 |
| 16 | td | 1 | shares r0 | 19/187/187 | **0/0/0** | 4,541,780 | 11,768 | 1,379 | 8.53 | 275.8 |
| 16 | bu | 1 | shares r0 | 15/384/384 | 4/8/8 | (4,245,861) | 43,308 | 12,500 | 3.46 | 388.3 |
| 16 | bu | 2 | shares r1 | 11/336/336 | 3/8/8 | (4,319,621) | 41,241 | 12,689 | 3.25 | 404.4 |

| size | arm | sessions | endpoint | complete-route WL |
|---|---|---|---|---|
| 2 | blind | 1 | clean | 591,230 |
| 2 | td | 1 + 1 | clean | 601,617 |
| 2 | bu | 1 + 1 | clean | 592,276 |
| 4 | blind | 1 | clean | 1,088,063 |
| 4 | td | 1 + 2 | clean | 1,061,538 |
| 4 | bu | 1 + 1 | clean | 1,088,745 |
| 8 | blind | 1 | clean | 2,175,864 |
| 8 | td | 1 + 1 | clean | 2,180,195 |
| 8 | bu | 1 + 1 | clean | 2,174,752 |
| 16 | blind | 2 | clean | 4,482,219 |
| 16 | td | 1 + 1 | clean | 4,541,780 |
| 16 | bu | 1 + 2 | **dirty**, 3/8/8 | — |

## The blind policy's step (healers off, blind arm only)

The step is the policy's own parameter, swept to its best before the
comparison is read (the ladder's strawman defence).  Step 2 goes straight to
`reserve 2`:

| size | round | policy | final ovl/unpl/viol | detailed WL | reserved ÷ used |
|---|---|---|---|---|---|
| 2 | 1 | reserve 0 | 1/8/8 | (584,581) | — |
| 2 | 2 | reserve 2 | **0/0/0** | 527,039 | 8.15 |
| 4 | 1 | reserve 0 | 5/117/117 | (980,908) | — |
| 4 | 2 | reserve 2 | **0/0/0** | 945,980 | 5.13 |
| 8 | 1 | reserve 0 | 6/93/93 | (2,012,407) | — |
| 8 | 2 | reserve 2 | 1/8/8 | (1,847,877) | 4.82 |
| 8 | 3 | reserve 4 | 16/520/520 | (1,842,234) | 7.84 |
| 16 | 1 | reserve 0 | 11/336/336 | (4,274,800) | — |
| 16 | 2 | reserve 2 | **0/0/0** | 3,757,248 | 4.49 |

Two rounds instead of three at NQ = 2, 4 and 16 — the same endpoint, the
same reservation, one useless round (`reserve 1`) skipped — and the same
failure at NQ = 8.  So the blind policy at its best is **two rounds and a
4.5–8× reservation**, and that is the number the derived budget had to beat.

## The control — `tpu.tcl` (healers off)

The mesh, where the top's demand is uniform by construction (every row's
buses cross every row the same way):

| size | arm | round | policy | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used |
|---|---|---|---|---|---|---|---|---|
| 8 | blind | 1 | reserve 0 | 0/0/0 | 550,528 | — | — | — |
| 8 | td | 0 | top-down | 0/0/0 | 197,376 | — | — | — |
| 8 | td | 1 | shares r0 | 0/0/0 | 550,528 | 2,430 | 2,048 | 1.19 |
| 8 | bu | 1 | shares r0 | 0/0/0 | 550,528 | 2,376 | 2,112 | 1.12 |
| 16 | blind | 1 | reserve 0 | 0/0/0 | 2,174,208 | — | — | — |
| 16 | td | 0 | top-down | 0/0/0 | 738,816 | — | — | — |
| 16 | td | 1 | shares r0 | 0/0/0 | 2,174,208 | 9,660 | 8,192 | 1.18 |
| 16 | bu | 1 | shares r0 | 0/0/0 | 2,174,208 | 9,552 | 8,320 | 1.15 |

Every arm is clean in its first round (`tpu.tcl -bottomup` is clean to
N = 32, as its page says), so there are no rounds to count; what the
control measures is the *reservation*: the derived share reserves
**1.12–1.19×** what the top uses, and the route under it is byte-identical
in wirelength to the unconstrained one.  (The top-down row's wirelength is
a different geometry, not a different route: `-bottomup` snaps the row
pitch onto the track period, as `tpu.tcl` documents.)

## What the tables say

1. **The blind band policy converges; the derived share does not.**  With
   healers off, `reserve_top_layers 2` — the top pair for the top level,
   everything below capped at M5 — is clean in the third round at NQ = 2, 4
   and 16.  Neither derived arm reaches a clean endpoint at any size in
   either of its informed rounds, and at NQ = 4, 8 and 16 the informed
   round strands **more** than the round it was derived from (td: 16 → 101,
   40 → 88, 32 → 187; bu: 117 → 117, 93 → 109, 336 → 368).  The
   hypothesis — one informed round against R blind ones — is refuted for
   `set_cell_layer_share` as the rung-4 primitive.

2. **The informed loop reaches a fixpoint at once, and it is the wrong
   one.**  In the `bu` arm the second derivation reproduces the first
   (identical shares, identical route, identical verdict at every size):
   the derived budget is self-consistent after one round.  It is just not
   clean.  In the `td` arm the second round moves (the top-down demand and
   the frozen-block demand differ), and lands where the blind round 2
   lands at NQ = 2 and 4 (2/8/8 and 4/45/45 — the same counts as
   `reserve 1`).

3. **Why: a share is uniform, the demand is positional, and the block
   already fills its seats.**  The derivation's notes name it at every
   size.  On M5 and M6 — the layers the top wants — the cluster's, the
   core's, the l1's and the l2's own 32-bit buses need 32 of the 32–36
   tracks in their seats (89–100 %), so the floor leaves those layers at
   **full use** and the top gets nothing there; what the derivation can
   still hand the top is M2/M4/M7 on the cluster and M5–M7 on the io block,
   which is not where the stranding is.  The `collide` column said the
   same thing the other way round on the two-instance test design (6 of
   the top's 8 tracks inside the kept slots): the top's tracks sit at
   specific positions, a share removes the *last* slots of every period,
   and the two need not meet.  Without the floor (the `nofloor` control)
   the shares that would have handed the top its complement strand the
   block's own buses instead — 410 bits at NQ = 2 against the blind
   round's 8 — which is worse, not better.

4. **The blind policy pays for its convergence in reservation.**  At the
   clean round it reserves **8.2× / 5.1× / 4.5×** the tracks the top used
   (NQ = 2 / 4 / 16), and its intermediate round reserves 9–32×.  The
   derived budgets reserve 3–7×: tighter, and useless.  Reservation
   efficiency is only worth reading on a clean route, and the derived arms
   never have one.

5. **The blind policy is not monotone either, and at NQ = 8 it never
   converges.**  `reserve 2` leaves the E4 fault (1/8/8 — one 8-bit seat),
   `reserve 3` caps the blocks at M4 and strands **1,444** bits of their
   own buses (24 seats with zero M2 tracks for 32-bit buses), `reserve 4`
   strands 520.  The policy's only knob overshoots the fault it cannot
   see, exactly the E4 shape: a loop that sees a count has to sweep, and
   this one has nowhere left to sweep.

6. **The step does not rescue it.**  Step 2 reaches the same clean
   endpoint in two rounds where step 1 took three, at the same 4.5–8×
   reservation, and fails at NQ = 8 the same way (reserve 4 strands 520).
   The policy's best is two rounds; the derived budget needed to beat
   two, and did not reach clean at all.

7. **With healers, the healers do the work, and the budget buys nothing.**
   The blind round 1 heals clean with **no reservation at all** at NQ = 2,
   4 and 8, so at those sizes there is nothing for a budget to buy and the
   derived arms' 3–5× reservations are pure padding on a route the healers
   would have closed anyway.  At NQ = 16 the E4 seat (`bundle 2`, 8 bits,
   a LOW window with zero tracks) survives the healers in the blind round
   1 and in both blind-derived rounds — a share cannot add tracks to a
   window that has none — and two things clear it in one more session:
   the blind `reserve 1` (9.8× reservation, 4,482,219) and the
   top-down-derived shares (8.5×, 4,541,780, +1.3 % wire).  The session
   count is the same either way, and the informed arm's one advantage is
   a reservation 13 % tighter on a design that needed one whole layer.
   Every clean healed route under a derived budget is within 1.3 % of the
   blind one's wire; the two are the same route by another road.

## What the tables do not say

- **That rung 4 is wrong — only that a share is not rung 4.**  The ladder's
  row says "derived `set_cell_layer_share` per cell + `hier.locked` copies
  as track-level keepouts", and the share is the half that exists.  What
  the top's demand asks the block for is *these tracks over this region*,
  and the primitive that says that is a positional reservation — the
  corridor E5 needs.  The derivation now reports, per cell and layer,
  exactly where a share cannot express the complement (the "positional
  reservation" notes), which is the specification for that primitive.
- **That the floor is exact.**  It is read off the abstract seats, and the
  seat a cell-local solve gives the same bus can be narrower (the cluster's
  own 32-bit bus sat in an 86-track seat top-down and a 35-track seat in
  its template; the subtree rule catches the cores' seats, which is what
  took the informed round from 410 to 296 stranded at NQ = 2, not to 8).
  A floor read off a cell-local solve would be exact and would say "full
  use" on still more layers.
- **That the informed loop is the E4 loop.**  The `bu` arm was meant to be
  the diagnosed one — measure, then act — and it acts on the wrong quantity:
  the E4 fault is a seat with zero tracks in its window, which the doomed-seat
  census names and `set_max_bundle_bits 4 for pc_` fixes in one run; a share
  derived from the top's demand cannot see it.  Diagnosability (E4) and the
  budget's information content (E1) are separate axes, and this run kept
  them separate.
- **Anything judged by an independent audit** (build item 2 is still not
  built; every row is `check_design`'s, the same audit on every arm).
- **What the control shows is the mechanism working where its premise
  holds.**  On the mesh the top's demand over a row IS uniform, so a
  uniform share expresses it: the derived budget reserves 1.12–1.19× what
  the top uses against the SoC's 3–7×, and costs the block nothing.  The
  SoC's demand is positional and the mesh's is not; the primitive fits one
  and not the other.  The control has no rounds to compare because every
  arm is clean in its first, so it says nothing about convergence.

## What it contributes to the ladder

E1 was the experiment that would show rung 4 converging in one informed
round.  It shows the opposite for the primitive the rung was written
against, and it shows *why* with the tool's own numbers: the block's own
buses occupy their seats to within a track, so a per-layer fraction has no
room to be both the block's budget and the top's reservation.  The blind
band policy converges because it is coarse — a whole layer is a positional
reservation of a kind — and pays 4.5–8× for it.  The informed loop's one
virtue survives: it is self-consistent after one round (the `bu` fixpoint),
which is what a positional derivation will need too.  The build order
changes accordingly: the corridor primitive (E5's "positioned corridor",
`add_grid_override` standing in) moves ahead of the fixed-pin work, and E1
is re-run against it.

## Provenance

- `flow/tcl/converge.tcl soc 2 4 8 16 -out run -j 2` (healers off, step 1);
  `… -heal -out runh -j 2` (healed); `… -step 2 -arms blind -out run2`
  (the step sweep); `flow/tcl/converge.tcl tpu 8 16 -out runt` (the
  control).  Each writes `e1_<vehicle>_<heal>_step<N>.md` and one
  `.log`/`.rep` pair per session.
- Engine at the merge of #934 plus this change (the own-need floor,
  `nofloor`, the hooks and the driver).  `BUDA_THREADS_REQUEST=2`.
