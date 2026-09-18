# E5 — Feedthrough reservation: guessed against derived

*Experiment E5 of the [Convergence Ladder](convergence_ladder.md), run and
written up 2026-09-17; **corrected the same day** — the hit rate this
write-up first reported (6–11 %) was a mis-measurement, and the top-side
half of the primitive it asked for (build item 6b) was built against that
number and measured; both are in "[The top-side half, built and measured](#the-top-side-half-built-and-measured-6b)"
below, and every hit-rate figure in the tables is the corrected one.  Driver: [`flow/tcl/converge.tcl`](../../flow/tcl/converge.tcl)
(`-primitive reserve -arms uniform,td,bu`) over the vehicle hooks in
[`converge_lib.tcl`](../../flow/tcl/converge_lib.tcl); construction guarded by
`test/tests/test_converge_driver.py` (mid tier, NQ = 2) and
`test/tests/test_cell_layer_reserve.py`.  No picture: the finding is about
rounds, reservations and where the top's metal lands, not about a route.*

## The claim being tested

A block that reserves feedthrough tracks **by guess** either under-reserves
(re-spin) or over-reserves (pays); a block handed the top's **exact crossing
demand** reserves exactly.  The metric is rounds to a clean endpoint, the
reservation's size against the tracks the top actually used, and — new for
this experiment — the reservation's **hit rate**: of the top's tracks over a
governed instance, how many landed on the tracks the block reserved.

## The result, in one paragraph

**Half the claim holds and half does not, and the half that fails is the
half the ladder wrote as obvious.**  The guessed reservation never reaches a
clean endpoint at any size in its whole physical range (F = 4 and 8 per TOP
layer per cell; F = 16 exceeds the smallest cell's 14 tracks and is
refused), and it is worse than reserving nothing at every size — 368 to
2,907 stranded bits at F = 4 against the blind round's 8 to 336 — because
four evenly spaced tracks on every TOP layer of every cell fall inside the
cores' 32-bit seats and push those buses onto LOW layers that cannot host
them.  The derived positional reservation (`derive_cell_layer_reserves` →
`set_cell_layer_reserve`, the primitive E1 asked for) reaches a **clean
endpoint with healers off at three of four sizes** — NQ = 2 in one informed
round, NQ = 8 in one, NQ = 16 in two — where E1's share reached none, and
where the blind band policy needed two rounds and never converged at
NQ = 8.  It reserves **3.2–8.1×** the tracks the top used, the same order
as the blind policy's 4.5–8×, because a template is solved once and the
reservation is the *union* over its instances.  The top lands on the
reserved tracks **0.63–1.00 of the time** (corrected; the first reading,
6–11 %, divided the hits by the instance's supply) — a derived reservation
covers about half the supply over a governed instance, so an unsteered top
lands on it most of the time.  What the reservation buys is that the
block's own buses have *left* the region the top wants, and it is exactly
that displacement which strands the cores' 32-bit bus wherever the derived
tracks cover its seat (NQ = 2 top-down, 360 bits; every second informed
round, 757 / 1,444 / 2,864).  The informed loop has **no fixpoint**, and
the reason is not the hit rate: the informed round re-plans the top from
scratch after the templates moved, and on the recorded NQ = 2 rounds 4 of
the 13 top-level bundles keep their topology and none keeps its seat, so
each round re-derives from a top that moved.  The top-side half of the
primitive — a reservation the top's NUTS and DetailedNUTS *prefer* — was
built (`set_reserve_steer`) and measured below: exact on the mesh (0.00 →
1.00 at no cost), a small lift on the SoC (0.70–1.00) at the price of
dirtier informed rounds, so it ships off by default; the top's plan handed down with the
reservation (6c, below) then gives the loop its fixpoint at every size
and, from a healed blind round, a clean informed round without healers at
NQ ≤ 8.  With the vehicle's own healing
the derived arm is clean at every size in one informed round (in one of
its two arms) and its NQ = 16 route is 11–14 % less wire than the blind
band's, while the guess is healed clean at NQ = 2–8 at 10–30× the blind
round's healing time and never at NQ = 16.

## Construction

**Vehicle.**  `soc.tcl` at NQ = 2, 4, 8, 16 (4 / 8 / 16 / 32 clusters; 63 /
127 / 255 / 511 leaf instances; 95 / 183 / 359 / 711 buses), the ladder's
lead vehicle; `tpu.tcl` at N = 8, 16 as the control.  Every session is one
run of the vehicle through the E1 hooks plus one new one (`-uniform F`);
the report each session leaves now carries, per governed (instance, layer),
the tracks reserved, how many of them the top's placed metal uses, and how
many carry the cell's own metal.

**The three arms**, each a loop until clean or out of moves:

| arm | round 1 | round k | what it is |
|---|---|---|---|
| **uniform** | every `set_bottom_up` cell reserves **F evenly spaced tracks on every TOP layer** (`set_cell_layer_reserve * TOP uniform F`, F = 4), solves once, copies; the top routes against it | F doubles (8, 16, …) until clean, past `-fmax`, or past the smallest cell's supply — the engine refuses an F a cell cannot host, and the driver records that as the sweep's end | the conventional feedthrough reservation: a guess with no plan behind it, said through the SAME primitive so one enforcement and one audit read both arms |
| **td** | plan the whole design **top-down** once; read the tracks the top placed over every instance; write every cell the UNION over its instances, in the cell's frame | solve every template once under the lines, copy, route the top; re-derive from the result | rung 4 with the positional primitive |
| **bu** | the blind round 1 **is** the measurement (E1's `blind` round: templates solved once, copied, frozen; the top routes against them) | the informed round: templates under the lines derived from round 1, then re-derive | the diagnosed loop, positional |

**What a positional reservation says** (E1's "what a share can and cannot
say", for the primitive that replaced it).  `set_cell_layer_reserve C L
p1,p2,…` names cell-local track centres on layer `L` that `C`'s own
interconnect leaves free: each becomes a keepout on the cell-local floorplan
(the local planner's capacity and the local NUTS seats avoid it) and on the
grid clone the reference DNUTS solve runs on; a nested template inherits its
ancestors' corridors projected into its own frame.  The parent keeps the
full grid — a reservation is room *for* the top, never a keepout against
it.  The derivation is the union of the top's placed tracks over every
occurrence of the cell (a template is solved once), which the derivation
prices as `used/inst`, and it reports `seat_hit` — reserved tracks inside
the cell's own worst seat — *without* acting on it: E1's floor kept a share
off the block's own seat; the positional derivation deliberately does not,
because the primitive exists so that the block moves its bus and whether
the local solve finds the room is the measurement.

**The `uniform` form.**  `uniform F` reserves the real signal tracks at the
(k + ½)/F points of the cell's extent (centred, so `uniform 1` is the
middle track), read over the reference occurrence — F tracks a bit can sit
on, not a spacing.  `*` names the marked cells, `TOP` the stack's TOP
layers (M5, M6, M7 on the SoC), and every (cell, layer) pair is computed
before any is stored, so `io_cell`'s 14 M5 tracks bound the whole sweep at
F = 8.

**Reservation efficiency** as in E1: *reserved* = tracks the policy takes
from the block over every governed instance, *used* = tracks the top placed
over that instance, inside or outside the reservation; a governed instance
is one the cell's reservation covers (a 90°-rotated occurrence is not
governed and has no row).  **Hit rate**, new here: the top's tracks that
fell *inside* the reservation over *used*, read off the extended report
(`governed INST CELL LAYER N USED OWN`; the healerless rows below were
re-run under the extended report for this number alone, verdicts
identical).

**What running it fixed.**  The audit reported the cluster's own metal on a
reserved track at the SoC's *misaligned* clusters (three of the eight under a
mirrored quad at NQ = 2): an instance solved in the global DNUTS run under
`check_template_tracks on_mismatch independent` never saw the reservation's
keepouts, which live on the reference solve's grid clone.  Such an instance
now carries the reserved tracks, folded into its frame, as its bundles'
blocked tracks (`BundleHierMeta.blocked_tracks` → `BusSegment.blocked_tracks`,
so the C++ trial sweep reads the same list), and every table here is from
the fixed engine (the healerless numbers moved by nothing but a few units
of wirelength in two rounds — the bits took the neighbouring track).  The
healed rounds then found the second door of the same gap: `ripup_reroute`'s
RELEASE pass withdraws an instance from the uniform copy and solves it
individually — the released cores' own metal sat on their reserved tracks
(2 of 4 at every core, NQ = 2 and 4) — so releasing stamps the same list.  And the
NQ = 16 top-down round found the third: the class pass re-pinned the
cluster template, the release pass withdrew its reference instance, and
seven sibling wrappers rebuilt on the way had lost the list that had been
kept as wrapper state — 13 own tracks on the reservation at each.  The
stamps are now *derived* from the DNUTS plan on every call (every door —
the session run, the healer trials, the C++ sweep — asks for the plan
before it builds segments): a reference or a copy carries none, everything
else of a marked cell carries its folded reservation, whatever the
wrappers' history.  The healed `uniform` and `td` rows below are from the
re-run under that fix (its `bu` rows had no release and are unchanged);
the recorded NQ = 16 round replays with no violated row.  The
abstract-stage audit also read a bus seated flush against a reserved track
as touching it (6 of 48 on `io_blk_cell`, 0 once the bits were placed); it
now reports that reading as the estimate it is and reserves VIOLATED for
placed bits.

**Judge.**  `check_design`, the same audit on every arm — the ladder's
independent geometric audit (build item 2) still does not exist, as the
E1 and E2 write-ups also say.  A parenthesised wirelength excludes stranded
bits and is not comparable to a complete route.

## Results — healers off (the plain pipeline's verdict)

`ovl/unpl/viol` = NUTS overlaps / DetailedNUTS unplaced bits / audit
violations.  `classes` = template classes solved that round (every session
marks the same 18 cells).  `reserved ÷ used` is read off the round's own
governed rows.  The blind round 1 is the `bu` arm's measurement and is
shown once.  `hit rate` = of the top's tracks over the governed instances,
the share that is a reserved one (corrected: `top_used ÷ used` off the
governed and demand rows, re-read from a byte-identical re-run of every
`td`/`bu` round on the 6b build with steering off; the `uniform` rounds'
healerless reports predate the field — their healed rounds read
0.02–0.03).

| size | arm | round | policy | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | hit rate | s |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | blind | 1 | reserve 0 | 1/8/8 | (584,581) | — | — | — | — | 1.4 |
| 2 | uniform | 1 | uniform 4 | 2/368/368 | (557,357) | 1,044 | 2,245 | 0.47 | — | 1.8 |
| 2 | uniform | 2 | uniform 8 | 6/136/136 | (560,705) | 2,088 | 2,221 | 0.94 | — | 1.8 |
| 2 | td | 0 | top-down | 0/0/0 | 525,144 | — | — | — | — | 1.4 |
| 2 | td | 1 | lines r0 | 0/360/360 | (538,483) | 1,324 | 805 | 1.64 | 0.68 | 1.9 |
| 2 | td | 2 | lines r1 | 1/376/376 | (566,769) | 2,144 | 1,019 | 2.10 | 0.84 | 2.0 |
| 2 | bu | 1 | lines r0 | **0/0/0** | 536,005 | 7,234 | 2,269 | 3.19 | 0.65 | 2.3 |
| 4 | blind | 1 | reserve 0 | 5/117/117 | (980,908) | — | — | — | — | 2.5 |
| 4 | uniform | 1 | uniform 4 | 2/741/741 | (1,064,732) | 1,932 | 5,012 | 0.39 | — | 3.4 |
| 4 | uniform | 2 | uniform 8 | 14/269/269 | (1,039,315) | 3,864 | 5,247 | 0.74 | — | 3.4 |
| 4 | td | 0 | top-down | 1/16/16 | (993,477) | — | — | — | — | 2.8 |
| 4 | td | 1 | lines r0 | 1/24/24 | (982,122) | 7,352 | 1,959 | 3.75 | 0.67 | 4.0 |
| 4 | td | 2 | lines r1 | 0/37/37 | (957,112) | 7,888 | 1,809 | 4.36 | 0.69 | 4.0 |
| 4 | bu | 1 | lines r0 | 0/45/45 | (942,054) | 22,475 | 4,738 | 4.74 | 0.82 | 4.5 |
| 4 | bu | 2 | lines r1 | 0/757/757 | (962,888) | 21,668 | 4,793 | 4.52 | 0.97 | 4.5 |
| 8 | blind | 1 | reserve 0 | 6/93/93 | (2,012,407) | — | — | — | — | 5.4 |
| 8 | uniform | 1 | uniform 4 | 1/1454/1454 | (2,118,394) | 3,708 | 8,967 | 0.41 | — | 7.3 |
| 8 | uniform | 2 | uniform 8 | 30/560/560 | (2,025,227) | 7,416 | 9,061 | 0.82 | — | 7.1 |
| 8 | td | 0 | top-down | 2/40/40 | (1,871,476) | — | — | — | — | 5.3 |
| 8 | td | 1 | lines r0 | **0/0/0** | 1,982,521 | 14,528 | 3,411 | 4.26 | 0.78 | 8.2 |
| 8 | bu | 1 | lines r0 | 0/18/18 | (2,147,336) | 53,501 | 9,430 | 5.67 | 0.81 | 9.7 |
| 8 | bu | 2 | lines r1 | 0/1444/1444 | (1,937,231) | 66,230 | 9,580 | 6.91 | 0.86 | 10.4 |
| 16 | blind | 1 | reserve 0 | 11/336/336 | (4,274,800) | — | — | — | — | 12.2 |
| 16 | uniform | 1 | uniform 4 | 1/2907/2907 | (4,373,880) | 7,260 | 16,676 | 0.44 | — | 16.9 |
| 16 | uniform | 2 | uniform 8 | 60/1102/1102 | (4,160,682) | 14,520 | 17,587 | 0.83 | — | 16.5 |
| 16 | td | 0 | top-down | 3/32/32 | (3,677,304) | — | — | — | — | 14.2 |
| 16 | td | 1 | lines r0 | 7/170/170 | (4,323,498) | 45,448 | 8,259 | 5.50 | 0.63 | 20.9 |
| 16 | td | 2 | lines r1 | 0/2864/2864 | (3,857,628) | 63,400 | 7,032 | 9.02 | 0.83 | 20.4 |
| 16 | bu | 1 | lines r0 | 1/8/8 | (3,839,107) | 145,191 | 17,826 | 8.14 | 0.67 | 23.0 |
| 16 | bu | 2 | lines r1 | **0/0/0** | 3,905,996 | 111,767 | 18,136 | 6.16 | 1.00 | 21.7 |

The uniform sweep ended at F = 16 at every size: `io_cell M5: uniform 16
asks more tracks than the cell has (14 signal tracks over its extent)`.

| size | arm | rounds | classes solved | endpoint | E1 for comparison (share / blind band at its best) |
|---|---|---|---|---|---|
| 2 | uniform | 2 | 36 | dirty, 6/136/136 | — |
| 2 | td | 1 + 2 | 36 | dirty, 1/376/376 | share dirty 2/8/8 |
| 2 | bu | 1 + 1 | 36 | **clean** | share dirty 1/8/8; blind clean in 2 rounds at 8.15× |
| 4 | uniform | 2 | 36 | dirty, 14/269/269 | — |
| 4 | td | 1 + 2 | 36 | dirty, 0/37/37 | share dirty 4/45/45 |
| 4 | bu | 1 + 2 | 54 | dirty, 0/757/757 (best round 0/45/45) | share dirty 5/117/117; blind clean in 2 rounds at 5.13× |
| 8 | uniform | 2 | 36 | dirty, 30/560/560 | — |
| 8 | td | 1 + 1 | 18 | **clean** | share dirty 13/96/96; blind never clean |
| 8 | bu | 1 + 2 | 54 | dirty, 0/1444/1444 (best round 0/18/18) | share dirty 11/109/109 |
| 16 | uniform | 2 | 36 | dirty, 60/1102/1102 | — |
| 16 | td | 1 + 2 | 36 | dirty, 0/2864/2864 (best round 7/170/170) | share dirty 21/203/203 |
| 16 | bu | 1 + 2 | 54 | **clean** | share dirty 12/368/368; blind clean in 2 rounds at 4.49× |

## Results — healers on (`soc_lib`'s heal-if-dirty in every round)

The first audit is the healerless verdict of the same session; the final
verdict is after `heal_if_dirty` (negotiate + ripup, and a second round with
`refine_selection` when the first leaves a residue).  The `uniform` and
`td` rows are from the re-run under the derived-stamp fix, the `blind` and
`bu` rows from the run before it (no release commit in any of them, and the
re-run reproduced every shared row to the wirelength).  Hit rates are read
off each round's own report (healed state).

| size | arm | round | policy | first ovl/unpl/viol | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | hit rate | s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | blind | 1 | reserve 0 | 1/8/8 | **0/0/0** | 591,230 | — | — | — | — | 1.9 |
| 2 | uniform | 1 | uniform 4 | 2/368/368 | **0/0/0** | 558,227 | 1,044 | 2,236 | 0.47 | 0.03 | 28.9 |
| 2 | td | 0 | top-down | 0/0/0 | 0/0/0 | 525,144 | — | — | — | — | 1.4 |
| 2 | td | 1 | lines r0 | 0/360/360 | 0/128/128 | (528,399) | 1,324 | 664 | 1.99 | 0.92 | 19.1 |
| 2 | td | 2 | lines r1 | 0/16/16 | **0/0/0** | 612,548 | 1,188 | 687 | 1.73 | 0.39 | 4.3 |
| 2 | bu | 1 | lines r0 | 0/0/0 | **0/0/0** | 536,025 | 7,434 | 2,269 | 3.28 | 0.65 | 2.2 |
| 4 | blind | 1 | reserve 0 | 5/117/117 | **0/0/0** | 1,088,063 | — | — | — | — | 9.8 |
| 4 | uniform | 1 | uniform 4 | 2/741/741 | **0/0/0** | 1,023,648 | 1,932 | 4,946 | 0.39 | 0.03 | 139.1 |
| 4 | td | 0 | top-down | 1/16/16 | 0/0/0 | 1,007,765 | — | — | — | — | 3.1 |
| 4 | td | 1 | lines r0 | 1/24/24 | **0/0/0** | 995,239 | 7,776 | 2,039 | 3.81 | 0.67 | 7.1 |
| 4 | bu | 1 | lines r0 | 0/45/45 | 6/0/0 | 1,020,715 | 24,098 | 5,504 | 4.38 | 0.67 | 76.8 |
| 4 | bu | 2 | lines r1 | 0/37/37 | 3/0/0 | 1,013,284 | 27,537 | 5,480 | 5.03 | 0.91 | 68.0 |
| 8 | blind | 1 | reserve 0 | 6/93/93 | **0/0/0** | 2,175,864 | — | — | — | — | 24.8 |
| 8 | uniform | 1 | uniform 4 | 1/1454/1454 | **0/0/0** | 2,095,343 | 3,708 | 9,155 | 0.41 | 0.02 | 650.5 |
| 8 | td | 0 | top-down | 2/40/40 | 0/0/0 | 1,968,672 | — | — | — | — | 5.9 |
| 8 | td | 1 | lines r0 | 1/16/16 | **0/0/0** | 1,978,425 | 14,496 | 3,411 | 4.25 | 0.78 | 14.4 |
| 8 | bu | 1 | lines r0 | 0/8/8 | **0/0/0** | 1,948,503 | 60,129 | 9,502 | 6.33 | 0.71 | 17.2 |
| 16 | blind | 1 | reserve 0 | 11/336/336 | 3/8/8 | (4,319,399) | — | — | — | — | 308.9 |
| 16 | uniform | 1 | uniform 4 | 1/2907/2907 | 0/736/736 | (4,376,283) | 7,260 | 17,603 | 0.41 | 0.02 | 1395.1 |
| 16 | uniform | 2 | uniform 8 | 60/1102/1102 | 48/1032/1032 | (4,196,967) | 14,520 | 21,203 | 0.68 | 0.03 | 144.3 |
| 16 | td | 0 | top-down | 3/32/32 | 0/0/0 | 3,935,746 | — | — | — | — | 15.9 |
| 16 | td | 1 | lines r0 | 32/464/464 | **0/0/0** | 3,997,844 | 49,864 | 6,697 | 7.45 | 0.86 | 134.3 |
| 16 | bu | 1 | lines r0 | 0/0/0 | **0/0/0** | 3,867,848 | 164,415 | 17,949 | 9.16 | 0.70 | 45.1 |

| size | arm | sessions | endpoint | complete-route WL | E1 healed for comparison |
|---|---|---|---|---|---|
| 2 | uniform | 1 | clean | 558,227 | blind 1 session, 591,230 |
| 2 | td | 1 + 2 | clean | 612,548 | share 1 + 1, 601,617 |
| 2 | bu | 1 + 1 | clean | 536,025 | share 1 + 1, 592,276 |
| 4 | uniform | 1 | clean | 1,023,648 | blind 1, 1,088,063 |
| 4 | td | 1 + 1 | clean | 995,239 | share 1 + 2, 1,061,538 |
| 4 | bu | 1 + 2 | **dirty**, 3/0/0 (overlaps only) | — | share 1 + 1, 1,088,745 |
| 8 | uniform | 1 | clean | 2,095,343 | blind 1, 2,175,864 |
| 8 | td | 1 + 1 | clean | 1,978,425 | share 1 + 1, 2,180,195 |
| 8 | bu | 1 + 1 | clean | 1,948,503 | share 1 + 1, 2,174,752 |
| 16 | uniform | 2 | **dirty**, 48/1032/1032 | — | blind 2, 4,482,219 |
| 16 | td | 1 + 1 | clean | 3,997,844 | share 1 + 1, 4,541,780 |
| 16 | bu | 1 + 1 | clean (no healing needed) | 3,867,848 | share dirty 3/8/8 |

The healers change the reading in two ways.  The guessed reservation is
rescued by them at NQ = 2, 4 and 8 — one session, but 29 / 139 / 650 s of
healing against the blind round's 2 / 10 / 25 s, since every core's
displaced bus has to be re-seated by rip-up — and at NQ = 16 it is beyond
them: 23 minutes take 2,907 stranded bits to 736, F = 8 stops at 1,032, and
the sweep has no F left.  The derived positional reservation, which
healers off was clean at three sizes in one to two rounds, is clean with
healers at **every size in one informed round** in one of its two arms
(td at 4, 8, 16; bu at 2, 8, 16 — at NQ = 16 the blind-derived round is
clean **before** any healer runs, 0/0/0 at the first audit), and its
clean NQ = 16 routes are **10.8–13.7 % less wire** than the blind band's
(3,997,844 td / 3,867,848 bu against 4,482,219) and 12–15 % less than the
share's 4,541,780 — the first size at which the informed arm beats the
blind one on the route itself rather than only on rounds, because a
positional reservation costs the block no layer.  The hit rate is the same
order with healing (uniform 0.02–0.03, derived 0.39–0.92).

## The control — `tpu.tcl` (healers off)

The mesh, where the top's demand is uniform by construction:

| size | arm | round | policy | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | hit rate |
|---|---|---|---|---|---|---|---|---|---|
| 8 | blind | 1 | reserve 0 | 0/0/0 | 550,528 | — | — | — | — |
| 8 | uniform | 1 | uniform 4 | 0/0/0 | 550,528 | 96 | 2,112 | 0.05 | 0.00 |
| 8 | td | 0 | top-down | 0/0/0 | 197,376 | — | — | — | — |
| 8 | td | 1 | lines r0 | 0/0/0 | 550,528 | 2,344 | 2,048 | 1.14 | 0.00 |
| 8 | bu | 1 | lines r0 | 0/0/0 | 550,528 | 2,112 | 2,112 | 1.00 | 0.00 |
| 16 | blind | 1 | reserve 0 | 0/0/0 | 2,174,208 | — | — | — | — |
| 16 | uniform | 1 | uniform 4 | 0/0/0 | 2,174,208 | 192 | 8,320 | 0.02 | 0.00 |
| 16 | td | 0 | top-down | 0/0/0 | 738,816 | — | — | — | — |
| 16 | td | 1 | lines r0 | 0/0/0 | 2,174,208 | 8,784 | 8,192 | 1.07 | 0.00 |
| 16 | bu | 1 | lines r0 | 0/0/0 | 2,174,208 | 8,320 | 8,320 | 1.00 | 0.00 |

Every arm is clean in its first round, as in E1, so the control has no
rounds to count; what it measures is the reservation, and here the
positional derivation is **exact**: the blind-round-derived lines reserve
1.00× what the top uses at both sizes (the union over a mesh's congruent
rows IS each row's demand), against the share's 1.12–1.19×, and the route
under them is byte-identical in wirelength.  The uniform guess reserves
2–5 % of the demand and changes nothing either — on a design that needed
no reservation, a guess that misses costs nothing, which is the one case
where a guess is free.  And the hit rate is **0.00** for every arm: the
reservation is exactly the top's previous tracks, and the unsteered top
lands on none of them — its seats shift by one track phase when the PEs'
buses move off the reserved tracks, the first place the corridor's other
half shows (steered, below, it is 1.00).

## What the tables say

1. **The guessed reservation is worse than none, at every size and every
   F it can take.**  F = 4 strands 368 / 741 / 1,454 / 2,907 bits against
   the blind round's 8 / 117 / 93 / 336; F = 8 strands 136 / 269 / 560 /
   1,102; F = 16 is refused by the smallest cell.  What strands is the
   cores' 32-bit `regf → alu` bus at every core (48 units wide, a
   `U_HVH` pinned onto M2/M3 or M4/M3 — LOW layers whose windows cannot
   host 32 bits, "closed by partner stretch"): four evenly spaced tracks on
   every TOP layer of a 35-track seat land inside it, the local solve
   moves the bus off, and the only place left is a layer that cannot take
   it.  A guess does not merely over- or under-reserve — on a block whose
   seats are full it *damages the block* before it helps the top, which
   the hypothesis had no row for.

2. **The derived positional reservation converges where the share never
   did, and in fewer rounds than the blind band.**  Clean with healers off
   at NQ = 2 (bu, one informed round), NQ = 8 (td, one) and NQ = 16 (bu,
   two), at 3.2× / 4.3× / 6.2× the top's used tracks; E1's blind band was
   clean in two rounds at 8.2× / never / 4.5×, and the share never.  At
   NQ = 4 no arm reaches clean (best 0/24/24, td round 1) where the blind
   band did.  The reservation's size is the same order as the band's
   because both are unions: a band takes a whole layer, a positional line
   takes every track any instance's top ever used.

3. **The top uses the reservation without being told to — and that was
   mis-read the first time.**  This finding first said "hit rate 0.09 /
   0.10 / 0.06 / 0.11: nine of ten tracks the top places over a governed
   instance are NOT the tracks the block reserved".  The scratch script
   behind it divided the hits by the DEMAND row's sixth field, the
   instance's *supply*, instead of its fifth, the top's own tracks — so it
   measured what fraction of the instance's tracks the top hit through the
   reservation, not what fraction of the top's tracks were reserved ones.
   Read right, the unsteered rate is **0.63–1.00** healerless (the tables
   above) and 0.39–0.92 healed: a derived reservation is the union over a
   template's instances of every top track, it covers about half the
   supply over a governed instance (`cluster_cell M5: 88 of 161 tracks`),
   and a top that seats near where it seated before lands on it most of
   the time.  The audit prints the rate itself now (`the top uses a..b of
   them per instance (P% of its N track(s) over them)`) so it is never
   computed by hand again.  What the reservation buys is still what the
   sentence after the wrong number said: the block's own buses have LEFT
   the region the top wants, which is why a clean round is clean.

4. **The informed loop has no fixpoint, and the cause is the plan, not
   the hit rate.**  Each derivation reads the top of the round before, the
   top moved, and the union moves with it: the second informed round
   strands 757 (NQ = 4), 1,444 (NQ = 8) and 2,864 (NQ = 16, td) bits —
   every one a core's 32-bit bus under lines that now cover its seat — and
   at NQ = 16 the same second round goes clean (bu), which is the same
   mechanism with the other sign.  WHY the top moves is measured on the
   recorded NQ = 2 bottom-up rounds (blind round 1 against informed round
   2): of the 13 top-level bundles, **4 keep their topology, 3 their
   layers, 4 their seat windows and 0 their seats** — the templates are
   solved under the reservation, their charges change, and the top's
   planner chooses differently against them.  A hit rate of 0.65–1.00 on a
   union that covers half the supply is compatible with that: the top
   lands on *reserved* tracks, not on *its own previous* tracks.  E1's
   share loop was self-consistent after one round and wrong; the
   positional loop is right at three sizes and not self-consistent at any.
   The derivation reports `seat_hit` for exactly these lines and does not
   act on it; E1's floor was the share's answer to the same collision.

5. **The union is the price of solve-once-copy, and it is most of the
   reservation.**  At NQ = 16 the blind-derived lines reserve 8.1× what
   the top uses because 32 clusters' tops are unioned into one cluster
   template; the per-instance figure the derivation prints (`used/inst`)
   is the honest per-block cost and the union is what a template solved
   once must pay.  Where instances are congruent AND the top is uniform
   (the mesh control) the union collapses to the demand and the
   reservation is exact at 1.00×.

6. **With healers the positional arm wins on the route, not only on
   rounds.**  E1's healed table read "the healers do the work, and the
   budget buys nothing": every clean healed route under a derived share
   was within 1.3 % of the blind one's wire.  Here the blind-derived lines
   at NQ = 16 are clean **before** the healers run (0/0/0 at the first
   audit, 45 s) at 13.7 % less wire than the blind band's healed route,
   and the top-down-derived lines heal clean in one informed round at
   10.8 % less — a band takes a whole layer from every block, a
   positional line takes tracks, and the wire shows it.  The guess is the
   mirror: healers rescue it at NQ = 2–8 at 10–30× the blind round's
   healing time, since every core's displaced bus is re-seated by rip-up
   one at a time, and at NQ = 16 (2,907 stranded) they do not finish.

## What the tables do not say

- **That a positional reservation is the wrong primitive.**  It is the
  right shape — it converges where the uniform share could not, and it
  is exact on the control.  This bullet first said it was half a
  primitive whose other half, a reservation the top *prefers*, "turns a
  6–11 % hit rate into a corridor and gives the informed loop a
  fixpoint".  That half was built and measured (below): it turns the
  mesh's 0.00 into 1.00 and the SoC's 0.65–0.97 into 0.70–1.00, and gives
  the loop no fixpoint, because the loop's instability is in the top's
  PLAN, not in which tracks its bits take.
- **That the derivation should floor by the block's own seat.**  E1's
  floor did that for the share and left the top nothing on the layers it
  wanted; here the collision is reported (`seat_hit`) and the local solve
  is given the chance to move the bus, which it takes when a layer can
  host it and cannot when the only free layers are LOW.  A derivation
  that ALSO steered the top would make the collision rarer rather than
  forbidding it.
- **Anything judged by an independent audit** (build item 2 is still not
  built; every row is `check_design`'s, the same audit on every arm).
- **That the uniform arm was run at its best.**  Its parameter was swept
  over its whole legal range (F = 4, 8; 16 refused) and it was worse than
  nothing everywhere, but a guess with a different SHAPE — F tracks on the
  top pair only, or a band of adjacent tracks rather than evenly spaced
  ones — was not tried, and "evenly spaced on every TOP layer" is the
  ladder's own description of the conventional arm, not a survey of
  practice.
- **Why NQ = 4 is the size no arm cleans.**  Its best derived round leaves
  24 bits (td round 1: one core's bus on a `Z_HVH` over M4/M5/M6 and one
  `U_VHV`, plus 8 of `pc_`) and the blind band cleared it at `reserve 2`;
  the write-up records the observation and does not guess at the
  mechanism.

## What it contributes to the ladder

E5 was the experiment that would price a guessed feedthrough reservation
against a derived one.  It does that — the guess is not just wasteful but
harmful on a block whose seats are full, and the derived reservation is
exact on a mesh and converges on the SoC where the share could not — and
it measures the thing the ladder's rung 4 had assumed: that a reserved
track is a track the top uses.  It is, at 0.63–1.00 on the SoC (and not at
all on the mesh, 0.00, where the seats shift a phase); the write-up first
read 6–11 % off a wrong denominator and sent the build order after a
top-side preference (item 6b, built and measured below) that the loop did
not need.  What the loop needed was the top's plan kept between rounds —
item 6c, built and measured below: the loop has a fixpoint and, handed a
healed blind round, is clean healerless in one round at NQ ≤ 8; E1's
re-run against the positional primitive is this table's `td`/`bu` rows.

## The top-side half, built and measured (6b)

**What was built** (`set_reserve_steer on|off`, off by default; the
engine's `ReserveCorridor`).  Every governed instance's reserved tracks are
installed on the routing grid as a *reserve corridor* (absolute tracks
over the instance's along-extent, owned by the instance's path), and a bus
crossing the instance from outside it — the top over a block, or an
enclosing cell's own bus over a nested reserved child, whose corridor the
cell-local solve reads translated into its frame — is **seated on them by
abstract NUTS** (the centre of the densest footprint-wide run of corridor
tracks replaces the pull as the segment's preference, before
`set_pull_targets`, so the placer, the repack and the tightening pass read
one objective; an alignment sibling or a junction landing still wins) and
**lands its bits on them first in DetailedNUTS** (after span-clear tracks,
before merely nearer ones).  Both halves are gated on the corridor being
able to *host* the bus: the ungated form was measured first and dragged
32-bit buses onto two-track corridors (NQ = 2 bottom-up round 1: clean →
16 unplaced, +5 % wire).  A bundle framed inside the reserving instance is
never steered onto its own reservation.  Building it also found and fixed
a limit in the first half: the reference DNUTS solve carried every
reference instance's reservation as keepouts on ONE grid clone, so a
cluster's reference solve saw its cores' reserved tracks as keepouts and
could never take the corridor the cores left for it; the reference now
carries its reservation as its bundles' blocked tracks, which bind one
instance's own bits where a keepout bound everybody's — byte-identical on
every E5 round (the unsteered re-run below reproduces the tables above to
the wirelength).

**The mesh control, steered** (`tpu.tcl 8 16 -primitive reserve`, healers
off): every arm clean at byte-identical wire (550,528 / 2,174,208), and the
hit rate **1.00** on every derived round (td 2,048 / 2,048 and 8,192 /
8,192; bu 2,112 / 2,112 and 8,320 / 8,320) against 0.00 unsteered; the
uniform guess stays at 0.00–0.01, since its four tracks per layer per cell
cannot host a PE's bus and the gate leaves the bus alone.  This is the
mechanism working as designed: where the top's plan reproduces, its bits go
exactly where the block left room.

**The SoC, steered** (healers off; same rows as the corrected table above,
so the two are read side by side):

| size | arm | round | policy | ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | hit rate | unsteered: ovl/unpl/viol, WL, hit (— = the unsteered loop stopped at a clean round before this one, or a `uniform` round, whose field is on healed reports only) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | uniform | 1 | uniform 4 | 2/368/368 | 557,357 | 1,044 | 2,245 | 0.47 | 0.03 | — |
| 2 | uniform | 2 | uniform 8 | 6/140/140 | 570,273 | 2,088 | 2,259 | 0.92 | 0.09 | — |
| 2 | td | 1 | lines r0 | 0/360/360 | 552,425 | 1,324 | 852 | 1.55 | 0.70 | 0/360/360, 538,483, 0.68 |
| 2 | td | 2 | lines r1 | 0/362/362 | 552,428 | 2,172 | 884 | 2.46 | 1.00 | 1/376/376, 566,769, 0.84 |
| 2 | bu | 1 | lines r0 | 0/16/16 | 562,963 | 7,234 | 2,409 | 3.00 | 0.80 | **0/0/0**, 536,005, 0.65 |
| 2 | bu | 2 | lines r1 | 0/362/362 | 575,746 | 8,137 | 2,501 | 3.25 | 0.99 | — |
| 4 | uniform | 1 | uniform 4 | 2/761/761 | 1,039,971 | 1,932 | 4,959 | 0.39 | 0.04 | — |
| 4 | uniform | 2 | uniform 8 | 14/344/344 | 1,043,506 | 3,864 | 5,905 | 0.65 | 0.08 | — |
| 4 | td | 1 | lines r0 | 1/110/110 | 893,843 | 7,352 | 1,662 | 4.42 | 0.90 | 1/24/24, 982,122, 0.67 |
| 4 | td | 2 | lines r1 | 0/170/170 | 1,145,032 | 5,536 | 2,225 | 2.49 | 0.76 | 0/37/37, 957,112, 0.69 |
| 4 | bu | 1 | lines r0 | 1/96/96 | 1,025,869 | 22,475 | 5,451 | 4.12 | 0.85 | 0/45/45, 942,054, 0.82 |
| 4 | bu | 2 | lines r1 | 2/904/904 | 1,040,215 | 19,765 | 5,506 | 3.59 | 0.94 | 0/757/757, 962,888, 0.97 |
| 8 | uniform | 1 | uniform 4 | 1/1481/1481 | 2,163,104 | 3,708 | 9,479 | 0.39 | 0.03 | — |
| 8 | uniform | 2 | uniform 8 | 30/652/652 | 2,115,064 | 7,416 | 10,093 | 0.73 | 0.07 | — |
| 8 | td | 1 | lines r0 | 1/80/80 | 1,996,654 | 14,528 | 3,306 | 4.39 | 0.86 | **0/0/0**, 1,982,521, 0.78 |
| 8 | td | 2 | lines r1 | 2/40/40 | 1,993,544 | 14,376 | 3,460 | 4.15 | 0.98 | — |
| 8 | bu | 1 | lines r0 | 2/184/184 | 2,458,805 | 53,501 | 11,175 | 4.79 | 0.83 | 0/18/18, 2,147,336, 0.81 |
| 8 | bu | 2 | lines r1 | 0/1444/1444 | 2,031,625 | 65,206 | 9,171 | 7.11 | 0.96 | 0/1444/1444, 1,937,231, 0.86 |
| 16 | uniform | 1 | uniform 4 | 1/3020/3020 | 4,466,401 | 7,260 | 17,910 | 0.41 | 0.03 | — |
| 16 | uniform | 2 | uniform 8 | 60/1334/1334 | 4,412,624 | 14,520 | 21,320 | 0.68 | 0.06 | — |
| 16 | td | 1 | lines r0 | 33/390/390 | 4,800,233 | 45,448 | 9,558 | 4.75 | 0.77 | 7/170/170, 4,323,498, 0.63 |
| 16 | td | 2 | lines r1 | 33/1086/1086 | 4,008,970 | 56,776 | 7,463 | 7.61 | 0.92 | 0/2864/2864, 3,857,628, 0.83 |
| 16 | bu | 1 | lines r0 | 1/8/8 | 4,006,837 | 145,191 | 18,190 | 7.98 | 0.86 | 1/8/8, 3,839,107, 0.67 |
| 16 | bu | 2 | lines r1 | **0/0/0** | 4,090,467 | 106,809 | 18,644 | 5.73 | 0.98 | **0/0/0**, 3,905,996, 1.00 |

The hit rate moves from 0.63–1.00 to 0.70–1.00 — the same order, since
the union corridor was already where most of the top's tracks fell — and
the rounds are **not better and mostly worse**: NQ = 2 bottom-up round 1
(clean unsteered) 16 unplaced; NQ = 4 bottom-up 45 → 96 and top-down 24 →
110; NQ = 8 top-down round 1, E5's clean healerless round, 80 unplaced at
+0.7 % wire, bottom-up 18 → 184 at +14 %; NQ = 16 top-down 170 → 390 at
+11 %, bottom-up round 2 clean either way at +4.7 % wire.  What costs is
concentration: every crossing bus is pulled into the corridor's tracks —
half the supply over a cluster, and a corridor derived from *another*
instance's crossing as often as from this bus's own — and the packing pays
for a preference that changes which reserved tracks the bits take more
than whether they take reserved tracks.  The *bits-only* variant
(`BUDA_RESERVE_STEER_NUTS=0`: seats left at their pull, bits steered inside
the window) was measured at NQ = 2, 4, 8 and is no better (NQ = 2 bottom-up
clean at +2.7 % wire; NQ = 4 bottom-up 121 against 45, top-down 32 against
24; NQ = 8 top-down clean, bottom-up 234 against 18).

**What this settles.**  A reserved track *is* a track the top uses — at
0.63–1.00 without being told and 0.70–1.00 when told — so the rung-4
premise stands; the primitive's other half exists and is exact where the
plan reproduces (the mesh); and the informed loop's missing fixpoint is a
property of the *plan*, which the templates' changed charges move between
rounds (finding 4), not of the tracks.  So the lever ships off by default
(a design reserving nothing is byte-identical either way; the corpus is
unchanged on all 55 comparable flows), and the next build item was 6c:
hand the derivation round's top selections, layers and seats down with
the reservation, so the informed round routes the blocks under the SAME
top the reservation came from, and measure whether a round is then a
fixpoint — built and measured in the next section.

## The plan handed down, built and measured (6c)

**What was built** (`derive_top_plan` → `pin_plan`; `converge.tcl
-handdown`).  The 6b measurement above ended on the loop's missing
fixpoint being a property of the *plan*: the informed round re-planned the
top from scratch after the templates moved, so on the recorded NQ = 2
rounds 4 of the 13 top-level bundles kept their topology and none kept its
seat, and every derivation named a top the next round did not route.
`derive_top_plan` writes, for every *globally planned* bundle — every
routed bundle that is not a bottom-up copy and whose frame is not inside a
scoped cell's placed instance, i.e. exactly the bundles the reserve
derivation read as demand on those cells — one `pin_plan` line: the
selected candidate by content uid and by type spec (the uid first, the
exact candidate; the spec second, shape and nearest locus, for a pool
whose loci moved), the planner's layer per segment, forced, and each
segment's abstract seat as the width-wide slide window
`[pos − w/2, pos + w/2]` NUTS must place inside (a *point* would be
refused by the fit, which needs `hi − lo ≥ width`; the width-wide window
reproduces the position exactly, and to NUTS it is a *seat pin*, flagged
as one on the plan rather than read off its width, so a user's own
`edit_set_slide` window of that width keeps bounding the bits: every
pass respects the interval, so the seat cannot move — while the bit stage
gets the segment's *natural* window, the candidate's own slide cut like
the source's, trunk margin and boundary relax included, because the first
measurement handed the bits the width-wide window too and a
rail-straddling M7 seat holds one signal track fewer than its 32 bits:
32 of 85 stranded bits at NQ = 8 and 128 of 176 at NQ = 16 were the top's
own seats the blind round had filled from its natural window).  A session
sourcing the lines before
bundling holds them until its `run_planner hier`, applies them there, and
after every `run_nuts` audits the seats (`[PlanPin] seated S of N`, each
unhonoured seat named — never a silent re-seat); a bottom-up template's
bundle is skipped and said, since the template is what the budget
re-solves, and an unmarked cell's cell-local bundle — planned globally per
instance — has its entries pinned onto each instance's own wrapper right
after expansion (before it, only the template and its replicas exist).
The natural window is cut exactly as the source's: the candidate's own
slide, the trunk margin, the boundary relax and the partner-reach prune's
zone bounds (the last two Codex P1s on #939).  The driver's `-handdown` makes the measurement round write
its plan and every informed round source the previous round's and write
its own, and adds two columns: `plan` (pins applied, seats honoured) and
`fixpoint` — whether the budget a round *derived* equals the one it *ran
under*, line for line, AND the plan it derived equals the one it ran
under, since both are the loop's state (a healer can move a topology, a
layer or a seat while the budget re-derives the same, and a pin that fell
back to its type spec can leave a different plan — Codex P1 on #939; the
cell then reads `no (plan k of n)`), which is the loop's own convergence
test; an informed round that reaches it stops the arm (the next round
would be the same session again).  The tables below were produced by the
budget-only test and were re-read against the recorded plan files after
the plan half landed: at every round the budget test called a fixpoint,
the derived plan is byte-identical to the one it ran under (0 differing
lines on every healerless and mesh round), and the healed rounds whose
plans differ between rounds (NQ = 4/8/16 `td` round 1, NQ = 16 `bu`
round 1) already read `no` on the budget — so no recorded verdict moves.  A plan is geometry, so under `-handdown` the `td`
arm's top-down measurement round runs on the *aligned* floorplan the
informed rounds route (`-align`: mark, `align_bottom_up`, unmark, nothing
left marked) — on the SoC the alignment reverts every move it tries and
the two floorplans already agree, on the mesh the rows move by a phase and
the unaligned round's seats pinned against moved block faces were 112
BUSTERM violations.

**Healers off.**  Every column as in the tables above, plus the two new
ones; `plan` reads `pins applied / lines sourced, seats honoured / seats
handed down`.  E5's free re-plan rows (the healers-off table above) are
quoted beside each round's verdict for the comparison.

| size | arm | round | plan | fixpoint | ovl/unpl/viol | E5 free re-plan | detailed WL | reserved ÷ used | s |
|---|---|---|---|---|---|---|---|---|---|
| 2 | td | 0 | — | — | 0/0/0 | 0/0/0 | 525,144 | — | 2.1 |
| 2 | td | 1 | 13/13, 24/24 | **yes** (9) | 0/360/360 | 0/360/360 | (534,340) | 1.86 | 2.5 |
| 2 | bu | 1 | 13/13, 36/36 | no (10 of 35) | **0/0/0** | **0/0/0** | 593,478 | 3.15 | 2.9 |
| 4 | td | 0 | — | — | 1/16/16 | 1/16/16 | (993,477) | — | 4.1 |
| 4 | td | 1 | 21/21, 44/44 | no (8 of 19) | 3/32/32 | 1/24/24 | (972,335) | 4.14 | 5.7 |
| 4 | td | 2 | 21/21, 44/44 | **yes** (15) | 3/32/32 | 0/37/37 | (972,655) | 3.58 | 5.3 |
| 4 | bu | 1 | 21/21, 57/57 | no (35 of 60) | 2/45/45 | 0/45/45 | (974,636) | 4.65 | 5.9 |
| 4 | bu | 2 | 21/21, 57/57 | no (5 of 43) | 2/45/45 | 0/757/757 | (974,636) | 4.55 | 5.7 |
| 4 | bu | 3 | 21/21, 57/57 | **yes** (38) | 2/45/45 | — | (974,636) | 4.55 | 5.8 |
| 8 | td | 0 | — | — | 2/40/40 | 2/40/40 | (1,871,476) | — | 7.5 |
| 8 | td | 1 | 37/37, 68/68 | no (8 of 22) | 7/32/32 | **0/0/0** | (1,888,906) | 4.67 | 10.7 |
| 8 | td | 2 | 37/37, 68/68 | no (1 of 18) | 2/32/32 | — | (1,908,950) | 4.32 | 11.4 |
| 8 | td | 3 | 37/37, 68/68 | **yes** (17) | 2/32/32 | — | (1,908,950) | 4.32 | 11.4 |
| 8 | bu | 1 | 37/37, 105/105 | no (33 of 62) | 3/53/53 | 0/18/18 | (2,005,699) | 5.85 | 12.6 |
| 8 | bu | 2 | 37/37, 105/105 | no (4 of 46) | 3/53/53 | 0/1444/1444 | (2,005,699) | 6.01 | 12.6 |
| 8 | bu | 3 | 37/37, 105/105 | **yes** (42) | 3/53/53 | — | (2,005,699) | 6.01 | 12.8 |
| 16 | td | 0 | — | — | 3/32/32 | 3/32/32 | (3,677,304) | — | 20.3 |
| 16 | td | 1 | 69/69, 119/119 | no (14 of 22) | 6/40/40 | 7/170/170 | (3,745,514) | 7.22 | 32.4 |
| 16 | td | 2 | 69/69, 119/119 | **yes** (15) | 6/40/40 | 0/2864/2864 | (3,745,514) | 5.96 | 31.0 |
| 16 | bu | 1 | 69/69, 201/201 | no (31 of 66) | 0/40/40 | 1/8/8 | (4,351,216) | 6.91 | 33.1 |
| 16 | bu | 2 | 69/69, 201/201 | no (1 of 53) | 0/40/40 | **0/0/0** | (4,351,216) | 6.68 | 33.2 |
| 16 | bu | 3 | 69/69, 201/201 | **yes** (52) | 0/40/40 | — | (4,351,216) | 6.68 | 33.4 |

The mesh control (`tpu.tcl`, healers off, the `td` round 0 on the aligned
floorplan — which is why its wire is the bottom-up rounds' 550,528 rather
than the compact top-down 197,376 of the control table above):

| N | arm | round | plan | fixpoint | ovl/unpl/viol | detailed WL | reserved ÷ used |
|---|---|---|---|---|---|---|---|
| 8 | td | 0 (aligned) | — | — | 0/0/0 | 550,528 | — |
| 8 | td | 1 | 96/96, 96/96 | **yes** (2) | **0/0/0** | 550,528 | **1.00** |
| 8 | bu | 1 | 96/96, 96/96 | **yes** (2) | **0/0/0** | 550,528 | **1.00** |
| 16 | td | 1 | 320/320, 320/320 | **yes** (2) | **0/0/0** | 2,174,208 | **1.00** |
| 16 | bu | 1 | 320/320, 320/320 | **yes** (2) | **0/0/0** | 2,174,208 | **1.00** |

**Healers on** (`soc_lib`'s heal-if-dirty in every round, as in the healed
table above; the `first` column is the informed round's *healerless*
verdict under the healed previous round's plan — the number the ladder's
"one informed round" is about; E5's healed rows quoted for comparison as
`first → final`):

| size | arm | round | plan | fixpoint | first | final | E5 healed (first → final) | detailed WL | reserved ÷ used | s |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | td | 1 | 13/13, 24/24 | yes (9) | 0/360/360 | 0/128/128 | 0/360 → 0/128 | (530,308) | 1.86 | 18.0 |
| 2 | bu | 1 | 13/13, 36/36 | no (4 of 36) | **0/0/0** | **0/0/0** | 0/0 → 0/0 | 594,632 | 3.21 | 2.9 |
| 4 | td | 1 | 21/21, 43/44 | no (20 of 26) | 2/16/16 | **0/0/0** | 1/24 → 0/0 | 983,364 | 4.32 | 8.9 |
| 4 | bu | 1 | 21/21, 59/59 | no (4 of 45) | **0/0/0** | **0/0/0** | 0/45 → 6/0/0, then 0/37 → 3/0/0 (never clean) | 1,096,053 | 4.44 | 6.2 |
| 8 | td | 1 | 37/37, 64/67 | no (16 of 25) | 7/16/16 | **0/0/0** | 1/16 → 0/0 | 1,986,136 | 4.78 | 18.3 |
| 8 | bu | 1 | 37/37, 107/107 | no (2 of 48) | **0/0/0** | **0/0/0** | 0/8 → 0/0 | 2,193,266 | 5.99 | 13.4 |
| 16 | td | 1 | 69/69, 83/119 | no (25 of 28) | 35/464/464 | 5/0/0 | 32/464 → 0/0 | 4,355,492 | 5.59 | 141.7 |
| 16 | td | 2 | 69/69, 135/135 | no (6 of 19) | **0/0/0** | **0/0/0** | — | 4,301,684 | 6.54 | 34.3 |
| 16 | bu | 1 | 69/69, 198/201 | no (45 of 74) | 0/8/8 | **0/0/0** | 0/0 → 0/0 | 4,318,745 | 7.41 | 71.3 |

**What the tables say.**

1. **The handed-down top reproduces exactly, and the loop has a
   fixpoint.**  Every pin applies and, healers off, every seat is honoured
   at every size on both vehicles (the `plan` column never falls short),
   the plan file a round derives is the plan it was handed, byte for byte,
   and the budget a round derives reproduces the budget it ran under
   within one to three informed rounds on the SoC (`td` at NQ = 2 in one,
   at NQ = 4/16 in two, at NQ = 8 in three; `bu` at NQ = 4/8/16 in three)
   and in the first on the mesh — where E5's free re-plan never
   reproduced anything (its second informed rounds went to 376, 757,
   1,444 and 2,864 unplaced).  On the mesh the fixpoint is clean and the
   reservation is exactly the top's use (1.00×) for both arms.  The
   rounds it takes on the SoC are the nested templates' own buses
   settling, not the top's: a cluster's bus is demand on the core inside
   it (the inherited corridor), it is re-solved under that corridor each
   round, and the derivation lines that still move between round 1 and
   round 2 (5 of 43, 4 of 46, 1 of 53) are those.

2. **Healers off, the fixpoint is exactly as clean as the top handed down
   — no cleaner.**  The stranded bits at every dirty SoC fixpoint are the
   pinned top's *own*, at seats the measurement round itself could not
   fill (replayed from `BUDA_RECORD` traces at NQ = 8 and compared segment
   by segment): NQ = 8 `td`'s 32 are `ml_0`, whose round-0 M4 seat
   overlaps `nl_11`'s (one of the two overlaps round 0 itself reported,
   carried into every round with the plan); NQ = 8 `bu`'s 53 are the
   blind round's own three overlaps between top buses (`pn_0`×`nl_0`,
   `pc_0`×`nl_0`, `pc_2`×`pc_3` on M6 — 32 + 8 + 8 bits) and its M3
   keepout culls of `nl_4` (5 bits); NQ = 16 `bu`'s 40 are the same M3
   culls of the `nl_*` buses.  No template bit strands in any of them.
   The five extra "overlaps" NQ = 8 `td` reports beside the carried one
   are abstract-footprint touches of one unit between a pinned top seat
   and the cluster template's bus packed against the reserved tracks'
   keepout (the top's abstract footprint is a slot wider than the union of
   its bit tracks), and lose no bit.

3. **Pinning the top forbids the re-plan that sometimes rescues it.**
   Where the free re-plan came out clean the hand-down does not (NQ = 8
   `td` round 1: 0/0/0 free against 7/32 pinned; NQ = 16 `bu` round 2:
   0/0/0 free against 0/40 pinned), and at NQ = 2 the free `bu` round
   routes 10 % less wire (536,005 against 593,478: the pinned round keeps
   the blind round's route, the free one finds a shorter top).  The two
   are one trade: the free re-plan can repair the measurement round's
   defects and cannot reproduce its top; the hand-down reproduces the top
   and cannot repair it.  So the loop's *convergence* and its
   *cleanliness* are two different things, measured apart.

4. **Hand a healed top down and the informed round is clean without
   healers.**  The `bu` arm's blind round healed clean and its plan handed
   down: the informed round's *healerless* verdict is **0/0/0 at NQ = 2,
   4 and 8** in ONE round — where E5's free re-plan under the same
   reservation read 0/45 and then 0/757 at NQ = 4 (never clean, healed or
   not) and 0/8 at NQ = 8 — and 0/8/8 at NQ = 16, healed to 0/0/0.  This
   is the ladder's claim measured: a hierarchical design whose top is
   *kept* between rounds routes its blocks under the reservation derived
   from that top in one informed round, clean, at three of four sizes and
   8 bits short at the fourth.  The `td` arm is the weaker source: a
   top-down round's healed plan is routed at round 1 with 2–35 overlaps
   and 16–464 unplaced (the templates, now solved under the reservation,
   and the pinned top contend where the top-down round had planned them
   together), healed clean at NQ = 4/8 in that round and at NQ = 16 in the
   next, whose healerless verdict is then 0/0/0 too; at NQ = 2 it is E5's
   known dirty fixpoint (the union reservation covers the core's own
   32-bit seat, 360 stranded, 128 after healing) and stays it.  The
   healers move seats when they run (NQ = 16 `td` round 1: 83 of 119
   honoured at the end; `bu`: 198 of 201), which is why the healed arms
   read `fixpoint: no` — they stop at clean, before the budget settles.
   Building this measured one more healer fault: a ripup move that
   re-pins a `pin_plan`-ed bundle to a different shape carried the forced
   per-segment layers onto it, an unbuildable LAYER_DIR route behind a
   clean (opens, overlaps) metric — 34/44/66 audit violations on the first
   healed rounds; a trial that moves a bundle to another shape now drops
   its forced layers and seat windows (the sequential trial, the C++ sweep
   and the screen alike).  Neither that nor the seat pin's natural window
   moves a flow that hands nothing down: the QoR corpus is byte-identical
   on all 56 comparable flows (`--vs` main's merge commit, abstract and
   detailed WL +0; `ariane133_heal` needs fetched inputs this container
   lacks).

**What this settles.**  The loop converges once the top is handed down —
a fixpoint at every size, exact on the mesh — and it converges to
whatever the top handed down was: clean from a healed blind round at
NQ ≤ 8 without a healer in the informed round, dirty from a dirty one.
The rung-4 primitive and its loop are therefore both in hand; what the
SoC's `td` arm at NQ = 2 still shows is the reservation's own limit (a
union that covers the block's seat), which no top plan fixes, and the
next question is whether the derivation should yield there — leave the
block its seat and hand the top the loss — which is a derivation policy,
not a new primitive.  That policy is built and measured below (6d).

## The derivation yields the block its seat, built and measured (6d)

**What was built** (`derive_cell_layer_reserves yield`; `converge.tcl
-yield`, 2026-09-18).  The derivation's policy for the case the `td` arm
at NQ = 2 left: where the reservation covers a block's own seat, the
block keeps its seat and the top takes the loss.  Two things had to be
measured before the rule was right, and both are the rule now.

1. **The test is the contiguous run, not the count.**  The first cut gave
   back the count shortfall — the reserved tracks inside the block's
   worst seat window minus what the DNUTS admission pool leaves, the
   doomed-seat census's own arithmetic — and changed nothing at the
   fixpoint.  On the core (a 32-bit bus in a 36-track M5 window with 8
   corridor tracks inside it) the count model reads 28 free and gives 4
   back; the informed round was replayed by hand with 4, 2 and then ONE
   corridor track left in the window, and every time the core's local
   planner fled to a U-shaped detour on M4/M3 with no signal tracks under
   it (`insufficient signal tracks (0)`, the same 360 bits); with none
   left it kept `I_V` on its seat and the round routed clean.  An
   abstract seat is *one rectangle*, so a single reserved track inside
   the window fragments the run the planner's capacity model needs.  The
   rule (`_pick_yield`) is therefore: no run of `need` consecutive
   reserved-free tracks in the window → give back the block's CURRENT
   seat first (every reserved track under its own metal's span, so the
   local solve need not move at all), then the nearest remaining tracks
   one at a time until such a run opens; a run long enough → nothing
   (the block shifts within its window, which is E5's clean case).  The
   count model stays only as the no-grid fallback.
2. **The test is run once per seat, against the union that fragments
   it.**  What a cell-local solve keeps free is `_effective_reserves` —
   the cell's own line UNION every ancestor corridor projected into its
   frame — so that union is the blocked set, and `_yield_seats` walks the
   cells deepest first, builds it per (cell, layer), picks once, and
   splits the give-back between the cell's own line and its ancestors'.
   Both halves are load-bearing.  The inherited half is what E5's
   fixpoint turned on: the core has no line of its own — the top takes no
   track over it directly — and its 360 bits were the *cluster's*
   corridor crossing its seat, inherited as every ancestor's reservation
   is (`_inherited_reserves`); judging each line against its own cell's
   seat alone yielded 4 tracks to the cluster and 8 to the io block and
   left the cores stranded exactly as before.  The union is what the
   first cut of this pass still got wrong, in two ways a reviewer caught
   and the nested test now pins (`#940`): it tested the own tracks and
   the inherited images as two separate blocked sets, so each half could
   find a long enough free run while the union left none, and it kept one
   image per ancestor track, although an ancestor track has a *different*
   image at every occurrence of the nested cell — which is exactly why
   the inheritance unions over occurrences.  Measured on the mirrored
   nested vehicle, an 8-bit bus with a 19-track window at each of two
   occurrences: the two-set form left it a longest free run of 6 and
   called the seat protected; the union form leaves 13.  Giving an image
   back removes every ancestor track behind it, since an image clears
   only when all of them go — which also settles who pays: not the
   nearest ancestor, all of them.  A corridor held by a cell outside the
   derivation's scope is not this derivation's to move (a scoped
   derivation does not replace it, and it is still in force next round):
   it narrows the run test and what it costs the seat is said rather
   than yielded.  Each give-back is charged to the line it came from and
   said with the seat it served (`cluster_cell M5: 19 of 83 reserved
   track(s) yielded to nested l1_cell's own seat`).

Each give-back is a `yield` column on the line and a note naming the
seat; a seat that cannot host its own bus even with nothing reserved is
said as the block's own shortfall; a line yielded whole reserves nothing
and is counted as the removal it is.  The plain derivation is
byte-identical (`test_yield_changes_nothing_where_the_seat_keeps_a_long_enough_run`;
the two-instance vehicle banded to M6 strands 24 bits under the full
union and routes clean, top and block, under the yielded one).  The
floor reads the block's seat off the *current* plan and not its
alternatives, so a block that could have re-planned onto another layer is
yielded to all the same — the mesh's `row_cell` (below) and E5's own
two-instance vehicle, whose unbanded bus moves to M4/M2 under the full
union, are both such cases.

**Healers off** (`converge.tcl soc 2 4 8 16 -primitive reserve -arms td,bu
-informed 4 -handdown -yield`; the 6c rows quoted for comparison).
**Measured under the corrected derivation** (the admission-pool fix,
`5562120`), as are the healed table and the mesh control below:

| size | arm | round | plan | fixpoint | yielded | ovl/unpl/viol | 6c (no yield) | detailed WL | reserved ÷ used | s |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | td | 0 | — | — | 38 | 0/0/0 | 0/0/0 | 525,144 | — | 2.0 |
| 2 | td | 1 | 13/13, 24/24 | no (6 of 12) | 32 | **1/0/0** | 0/360/360 | 532,452 | 1.67 | 2.4 |
| 2 | td | 2 | 13/13, 24/24 | **yes** (9) | 32 | 1/0/0 | — | 532,406 | 1.72 | 2.5 |
| 2 | bu | 1 | 13/13, 36/36 | no (14 of 37) | 130 | 0/8/8 | **0/0/0** | (588,637) | 2.93 | 2.8 |
| 2 | bu | 2 | 13/13, 36/36 | no (8 of 33) | 101 | 0/8/8 | — | (593,244) | 3.02 | 2.8 |
| 2 | bu | 3 | 13/13, 36/36 | no (2 of 29) | 98 | 0/8/8 | — | (593,237) | 3.05 | 2.9 |
| 2 | bu | 4 | 13/13, 36/36 | no (2 of 29) | 101 | 0/8/8 | — | (593,244) | 3.05 | 2.9 |
| 4 | td | 0 | — | — | 149 | 1/16/16 | 1/16/16 | (993,477) | — | 3.9 |
| 4 | td | 1 | 21/21, 44/44 | no (14 of 22) | 36 | 6/32/32 | 3/32/32 | (982,385) | 3.33 | 5.3 |
| 4 | td | 2 | 21/21, 44/44 | no (6 of 18) | 8 | 3/32/32 | 3/32/32 | (972,655) | 3.38 | 5.1 |
| 4 | td | 3 | 21/21, 44/44 | **yes** (15) | 8 | 3/32/32 | — | (972,655) | 3.57 | 5.0 |
| 4 | bu | 1 | 21/21, 57/57 | no (46 of 66) | 174 | 4/45/45 | 2/45/45 | (1,018,744) | 4.17 | 5.7 |
| 4 | bu | 2 | 21/21, 57/57 | no (44 of 65) | 77 | 3/45/45 | 2/45/45 | (992,398) | 4.40 | 6.0 |
| 4 | bu | 3 | 21/21, 57/57 | no (1 of 42) | 77 | 3/45/45 | 2/45/45 | (992,398) | 4.50 | 6.2 |
| 4 | bu | 4 | 21/21, 57/57 | **yes** (41) | 77 | 3/45/45 | — | (992,398) | 4.50 | 6.3 |
| 8 | td | 0 | — | — | 107 | 2/40/40 | 2/40/40 | (1,871,476) | — | 7.7 |
| 8 | td | 1 | 37/37, 68/68 | no (16 of 26) | 52 | 21/40/40 | 7/32/32 | (1,861,746) | 4.00 | 11.3 |
| 8 | td | 2 | 37/37, 68/68 | no (7 of 20) | 8 | 3/40/40 | 2/32/32 | (1,834,862) | 4.14 | 10.9 |
| 8 | td | 3 | 37/37, 68/68 | **yes** (15) | 8 | 3/40/40 | 2/32/32 | (1,882,554) | 4.36 | 11.7 |
| 8 | bu | 1 | 37/37, 105/105 | no (52 of 71) | 133 | 6/53/53 | 3/53/53 | (2,061,941) | 5.37 | 14.3 |
| 8 | bu | 2 | 37/37, 105/105 | no (47 of 66) | 47 | 5/53/53 | 3/53/53 | (2,007,057) | 5.96 | 14.8 |
| 8 | bu | 3 | 37/37, 105/105 | **yes** (40) | 47 | 5/53/53 | 3/53/53 | (2,007,057) | 5.96 | 15.2 |
| 16 | td | 0 | — | — | 87 | 3/32/32 | 3/32/32 | (3,677,304) | — | 20.6 |
| 16 | td | 1 | 69/69, 119/119 | no (18 of 24) | 0 | 6/40/40 | 6/40/40 | (3,745,142) | 6.78 | 31.5 |
| 16 | td | 2 | 69/69, 119/119 | no (2 of 16) | 4 | 6/40/40 | 6/40/40 | (3,745,514) | 5.96 | 30.2 |
| 16 | td | 3 | 69/69, 119/119 | **yes** (15) | 4 | **6/32/32** | 6/40/40 | (3,746,216) | 5.96 | 30.0 |
| 16 | bu | 1 | 69/69, 201/201 | no (41 of 71) | 142 | 10/40/40 | 0/40/40 | (4,314,900) | 6.37 | 39.7 |
| 16 | bu | 2 | 69/69, 201/201 | no (19 of 62) | 31 | **0/40/40** | 0/40/40 | (4,351,508) | 6.48 | 34.5 |
| 16 | bu | 3 | 69/69, 201/201 | **yes** (52) | 31 | 0/40/40 | 0/40/40 | (4,351,508) | 6.68 | 35.0 |

(`yielded` is the tracks the round's own derivation gave back, i.e. what
the NEXT round runs under; a `td` round-0 entry is the top-down
measurement's.  The `s` column is not comparable to the 6c tables — two
loops shared the machine.)

**Healers on** (`… -heal -informed 3 -handdown -yield`; `first` is the
informed round's healerless verdict under the healed previous round's
plan, `final` its healed one, 6c's pair quoted).  **Measured under the
corrected derivation**, like the two tables around it; eight of its nine
rows reproduce the previous pass exactly and the ninth is NQ = 16 `td`,
below:

| size | arm | round | plan | fixpoint | first | final | 6c (first → final) | detailed WL | reserved ÷ used |
|---|---|---|---|---|---|---|---|---|---|
| 2 | td | 1 | 13/13, 24/24 | no (6 of 12) | 1/0/0 | 1/0/0 | 0/360 → 0/128 | 532,452 | 1.67 |
| 2 | td | 2 | 13/13, 24/24 | **yes** (9) | 1/0/0 | 1/0/0 | — | 532,406 | 1.72 |
| 2 | bu | 1 | 13/13, 31/36 | no (34 of 51, plan 4 of 15) | 1/8/8 | **0/0/0** | 0/0 → 0/0 | 590,991 | 2.98 |
| 4 | td | 1 | 21/21, 37/44 | no (24 of 28, plan 8 of 25) | 5/16/16 | **0/0/0** | 2/16 → 0/0 | 1,082,463 | 3.37 |
| 4 | bu | 1 | 21/21, 59/59 | no (22 of 54) | 0/8/8 | **0/0/0** | 0/0 → 0/0 | 1,087,535 | 4.14 |
| 8 | td | 1 | 37/37, 54/67 | no (22 of 28, plan 14 of 44) | 20/24/24 | **0/0/0** | 7/16 → 0/0 | 2,010,662 | 3.56 |
| 8 | bu | 1 | 37/37, 107/107 | no (20 of 57) | **0/0/0** | **0/0/0** | 0/0 → 0/0 | 2,174,952 | 5.65 |
| 16 | td | 1 | 69/69, 114/119 | no (26 of 29, plan 4 of 71) | 4/8/8 | **2/0/0** | 35/464 → 5/0/0, then 0/0 | 4,642,741 | 5.54 |
| 16 | td | 2 | 69/69, 116/120 | no (14 of 24, plan 4 of 71) | 46/1032/1032 | **0/0/0** | — | 4,111,278 | 7.53 |
| 16 | bu | 1 | 69/69, 196/201 | no (54 of 80, plan 6 of 72) | 4/32/32 | **0/0/0** | 0/8 → 0/0 | 4,417,825 | 6.85 |

The mesh control (`converge.tcl tpu 8 16 -primitive reserve -arms td,bu
-informed 3 -handdown -yield`, healers off), **re-run under the corrected
derivation and reproduced exactly** — every round, every wirelength, both
fixpoint verdicts: clean at every round with
wire byte-identical to the 6c control (550,528 at N = 8, 2,174,208 at
N = 16) and the reservation exactly the top's use (1.00×) — but the `td`
arm's fixpoint now reads `no (1 of 2)` where 6c's read `yes (2)`: the
top-down round's derivation yields the `row_cell` M4 line whole (the
top's 8 tracks sit in the row's own 8-bit seat with no run of 8 beside
them), the informed round then routes the row's bus elsewhere within its
band and its derivation reads the M4 demand back, so the one line
oscillates while the design stays clean and the arm stops on clean.  A
block that had an alternative is yielded to anyway, at no cost here.

**What the tables say.**

1. **The reservation's limit at NQ = 2 is resolved as a STRANDING, and
   costs one overlap.**  The `td` arm's informed round strands nothing —
   `1/0/0` healerless in one round — where 6c held E5's dirty fixpoint at
   0/360/360 healerless and 0/128 healed.  The 32 tracks given back are
   the cluster's corridor over the core's seat, the union over the core's
   four occurrences of tracks the top used over *other* occurrences, so
   the top loses nothing it needed over this one and every pin still
   applies with every seat honoured (13/13, 24/24).  What does not go
   away is one abstract overlap, and it is the one result the vehicle's
   healers do not clear either: the healed `td` arm at NQ = 2 also ends
   `1/0/0`, at a fixpoint.  So the 360 stranded bits go; "clean" does
   not.
2. **The cost elsewhere is small, and it runs in both directions.**
   Healers off, against 6c: NQ = 16 `td` reaches its fixpoint eight bits
   BETTER (6/32/32 against 6/40/40), NQ = 16 `bu` and NQ = 4 `td` land on
   the same verdict, and the yield costs 8 bits at NQ = 2 `bu` (a pinned
   8-bit `pc` bus, where 6c was clean), one overlap at NQ = 4 `bu`, and
   one overlap plus 8 bits at NQ = 8.  The transient first rounds are
   noisier than 6c's (NQ = 8 `td` round 1 reads 21 overlaps) but settle
   by round 2 in every arm.
   This paragraph replaced a much stronger claim, and the correction is
   the point: the first measurement of this section reported NQ = 16 `bu`
   going from 40 stranded bits to **264**, and read that as the policy
   handing the pinned top a loss it cannot route around.  That was an
   artefact of a defective pick — the yield was choosing against half of
   what fragments a seat (see the build note above) — and not a property
   of the policy.  Measured against the corrected pass the arm is
   `0/40/40`: zero overlaps, and the same 40 bits as the no-yield
   baseline.
3. **With the vehicle's own healing, seven of the eight arms are clean**,
   NQ = 2 `td` being the exception at `1/0/0`.  The informed rounds'
   first (healerless) verdicts are close to 6c's at NQ = 2/16 and worse
   at NQ = 4/8 (5/16/16 and 20/24/24 against 2/16 and 7/16), and the
   healers clear all of them but that one.

   What the re-measurement changed on NQ = 16 `td` is not WHETHER it
   cleans but what cleaning costs, and the distinction took a second look
   to see.  Under the previous pass that arm cleaned in ONE informed
   round (`0/0/0` at round 1, 3,971,612).  Under the corrected derivation
   round 1 ends `2/0/0` at 4,642,741 — two fewer seats honoured, the
   reservation down from 6.92x used to 5.54x — and the arm carries on to
   a SECOND informed round, which enters at `46/1032/1032` healerless and
   is healed to `0/0/0` at 4,111,278.  That round took **9526 s against
   round 1's 220 s**, a factor of 43, and is the only round in either SoC
   table that needed more than a few minutes.  So the yield's price on
   this arm is a round and a great deal of healing, not cleanliness.

   This page said `six of the eight` for about an hour, which was read
   off round 1 while round 2 was still solving — a verdict taken before
   the arm had finished.  It is recorded here rather than quietly
   corrected because it is the same mistake the section warns about
   twice elsewhere: a number that has not converged is not a result.

   Eight of the table's nine round-1 rows reproduce the previous pass
   exactly, NQ = 16 `bu` included in every column, so what moved is one
   arm.  The fixpoint column reads `no` on every healed row except the
   NQ = 2 `td` one, as in 6c: the healers move seats (NQ = 16: 114 of 119
   and 196 of 201 honoured) and the arms stop at clean.

4. **The loop still converges** (healers off): every arm reaches a
   budget-and-plan fixpoint in two to four informed rounds except `bu` at
   NQ = 2, where 2 of 29 lines keep moving through four rounds — the
   give-backs re-derive slightly differently each round as the blocks'
   seats settle — and the dirty verdict there is the same 8 bits each
   round.

**What this settles.**  The yield is a lever with a measured trade, not a
default — the same conclusion 6b reached from the other side, but on a
much smaller trade than this section first recorded.  It does what it was
built for: the block keeps its seat, and the 360 bits the reservation's
own limit stranded at NQ = 2 are gone, healed or not.  What it costs is
one overlap at NQ = 2 that no healer clears, 8 bits at NQ = 2 bottom-up,
an overlap or two at NQ = 4/8, and — at NQ = 16 top-down, healed — a
SECOND informed round whose healing runs 43x longer than the first,
against eight bits gained at NQ = 16 top-down healerless and parity at
NQ = 16 bottom-up.  The cost falls on the top-down arm at both ends of
the size range, which is the arm whose plan is pinned: at NQ = 2 as an
overlap nothing clears, at NQ = 16 as a round and the healer time to
finish it.  Healers off, the
strand it converts is the pinned top's, and the top's pinned plan cannot
then move; that mechanism is real and is why the NQ = 2 overlap survives
healing, but it is worth a few bits and an overlap here rather than the
hundreds the first measurement claimed.  A derivation that yields AND
re-plans the top's affected buses (unpinning exactly the bundles whose
seats were yielded, the way the healers do) is the shape that would take
both, and is a driver policy on top of `pin_plan`, not a new primitive.

## Provenance

- `flow/tcl/converge.tcl soc 2 4 8 16 -primitive reserve -arms
  uniform,td,bu -out run` (healers off); `… -heal -out runh` (healed; its
  `uniform` and `td` rows re-run as `… -heal -arms uniform,td -out runh2`
  after the derived-stamp fix, the `bu` rows kept — no release commit ran
  in them);
  `flow/tcl/converge.tcl tpu 8 16 -primitive reserve -arms uniform,td,bu
  -out runt` (the control).  Each writes
  `e5_<vehicle>_<heal>_step1_reserve.md` and one `.log`/`.rep` pair per
  session.  The CORRECTED healerless hit rates are from
  `BUDA_RESERVE_STEER=0 converge.tcl soc 2 4 8 16 -primitive reserve -arms
  td,bu` on the 6b build (byte-identical rounds; the original healerless
  reports predate the governed rows' hit field), the healed ones re-read
  off the original healed reports with the right denominator.
- The 6b measurement: `converge.tcl soc 2 4 8 16 -primitive reserve -arms
  uniform,td,bu` with `BUDA_RESERVE_STEER=1` (the SoC, healers off; NQ = 16
  `td`/`bu` rerun alone after the sweep was stopped), the same for `tpu 8
  16`, and `BUDA_RESERVE_STEER_NUTS=0 … soc 2 4 8 -arms td,bu` for the
  bits-only variant; the plan-stability probe replays `BUDA_RECORD`
  traces of `soc.tcl 2 -bottomup -noheal` and the same `-shares` the
  driver derived, comparing each top-level bundle's selected topology,
  layers and seat between the two.
- The 6c measurement: `converge.tcl soc 2 4 8 -primitive reserve -arms
  td,bu -informed 4 -handdown` and `… soc 16 … -informed 4 -handdown`
  (healers off), `… soc 2 4 8 -heal … -informed 3 -handdown` and `… soc 16
  -heal … -informed 3 -handdown` (healed), `converge.tcl tpu 8 16
  -primitive reserve -arms td,bu -informed 3 -handdown` (the control; its
  `td` round 0 aligned by the driver).  Each run leaves
  `<p>_<arm>_plan_r<k>.buda` beside the budget files, and the `plan` /
  `fixpoint` columns are the driver's (`buda::query plan_pins`,
  `converge::policy_diff`).  The stranded-bit attributions are from
  `BUDA_RECORD` traces of the NQ = 8 rounds replayed in Python and
  compared segment by segment against the blind round's own.
- The 6d tables: `flow/tcl/converge.tcl soc 2 4 8 16 -primitive reserve
  -arms td,bu -informed 4 -handdown -yield` (healers off), `… -heal
  -informed 3 -handdown -yield` (healed), `converge.tcl tpu 8 16
  -primitive reserve -arms td,bu -informed 3 -handdown -yield` (the
  control), all at `-j 2` with the two SoC loops sharing one four-core
  machine (so their `s` column is not the 6c tables').  The hand replays
  behind the contiguous-run rule: `soc.tcl 2 -noheal -bottomup -shares
  <edited r0 file> -plan <td plan r0>` with 4, 2, 1 and 0 of the eight
  corridor tracks left inside the core's M5 window (`varA/B2/B3/B`);
  the core's window and the inherited tracks read off the `BUDA_RECORD`
  recording of the NQ = 2 top-down round in Python.
  **The three tables are not all from one pass right now**, which the
  headers say individually and this bullet says once.  There have been two
  corrections, and a row belongs to whichever it has been re-run under:

  1. The FIRST measurement went through a yield that tested each seat
     against half of what fragments it and kept one image per ancestor
     track (#940 review).  All three tables were re-run against that
     correction on the same commands; the mesh control came back
     byte-identical, the SoC moved, most of all at NQ = 16 bottom-up, whose
     264 stranded bits — the first measurement's headline cost — were the
     defect and not the policy.
  2. The SECOND correction is the admission pool (`5562120`): the
     contiguous-run test read a single-x probe while the `need`/`pool` it
     was judged against came from the seat's span-clear admission
     arithmetic.  **All three tables are re-run under it**, so the
     comparison the conclusions ask for is between rows from one engine.
     The mesh control reproduced exactly again; the healerless table moved
     nine cells, seven of them in `yielded`, which is the column the yield
     itself writes; the healed table moved ONE ROW, NQ = 16 `td`, from
     `0/0/0` to `2/0/0` — which cost the section its "seven of eight"
     claim and is written into conclusion 3 rather than absorbed.

  The healed table's NQ = 16 rows were first taken from two `-informed 1`
  runs, because the full `-informed 3` sweep's NQ = 16 `td` arm had carried
  past round 1 — where the recorded run stopped at clean — and was still
  solving round 2.  That sweep then FINISHED (9526 s on that one round),
  and its rows reproduce both capped runs exactly, column for column, so
  the capped measurement stands and the table is the full sweep's.  What
  the finish added is the round 2 the capped runs could not reach, which
  is the row that settles what the arm costs; a verdict published from the
  capped runs alone said `six of eight` and was wrong.

  Saying all this beats leaving it to be inferred: a reader at *What this
  settles* is being asked to compare the two SoC tables.
- Engine at the merge of #936 plus this change (the `uniform` form, the
  driver's `uniform` arm, the blocked-track enforcement on globally solved
  instances, the extended report); the correction and the 6b rows at the
  merge of #937 plus the 6b change (`set_reserve_steer`, the reference's
  reservation as blocked tracks, the audit's printed hit rate).  `BUDA_THREADS_REQUEST` at the launcher
  default (half the machine's logical CPUs).
