# The LOW-layers experiment (bigsoc `-low N`)

*2026-10-13.  Driver: `tools/low_layers_experiment.py`; flow: `flow/tcl/bigsoc.tcl -low N`;
page: the bigsoc testbed artifact (see [artifacts.md](../artifacts.md)).*

## Question

What does the planner lose, and gain, when the mock stack's lowest LOW layers
are not declared?  `-low N` keeps the N highest LOW layers of
`flow/mockpdk/stack.buda` (M2 H, M3 V, M4 H; TOP M5..M7 always stay) and drops
the `def_layer` / `def_track_pattern` lines of the rest.  The netlist and the
PDK area model are unchanged and `auto_floorplan` sizes cells by the M5 bit
pitch, so the die is identical in every arm (the driver checks the die line).

Leaf footprints block every LOW layer, so LOW metal exists only in channels;
the planner prices it cheap for short stubs and dead-span escalation moves
doomed LOW segments to TOP.

## Method

One thread (`btcl -j 1`), `BUDA_REPLAN_PROF=1`, arms run in sequence.  Dials:
`nq2` (full flow, healers on), `nq8full` (NQ=8 through DNUTS, `-noheal`),
`nq8abs`, `nq16abs` (abstract NUTS only, `-noheal`).  Reproduce:

    tools/low_layers_experiment.py --out flow/tcl/log/low_layers
    tools/low_layers_experiment.py --reparse --out flow/tcl/log/low_layers

## Results (LOW layers kept: 3 = the stack as is)

| arm | kept | bands | planner s | run_nuts s | abstract ovl | abs WL | end verdict |
|---|---|---|---|---|---|---|---|
| nq2 | 3 / 2 / 1 / 0 | 116k / 97k / 78k / 58k | 0.17 / 0.24 / 0.18 / 0.14 | 0.20 / 0.24 / 0.24 / 0.12 | – | 52k / 55k / 66k / 67k | 0 ovl 6 unpl / 0 ovl 6 unpl / 9 ovl 159 unpl / 61 ovl 720 unpl |
| nq8abs | 3 / 2 / 1 / 0 | 2.87M / 2.40M / 1.92M / 1.44M | 4.4 / 4.4 / 3.9 / 4.2 | 1.5 / 1.7 / 8.2 / 5.3 | 112 / 128 / 320 / 1,398 | 384k / 402k / 511k / 763k | – |
| nq16abs | 3 / 2 / 1 / 0 | 5.32M / 4.44M / 3.55M / 2.66M | 12.7 / 10.7 / 11.3 / 7.2 | 7.3 / 5.9 / 21.3 / 17.8 | 194 / 232 / 643 / 2,937 | 614k / 631k / 797k / 1.24M | – |

(`nq8full` agrees with `nq8abs` through the abstract stage; its detailed WL
is 5.1M / 5.8M / 7.8M / 10.3M.)  Full tables: `results.md` in the output dir.

## Findings

* **Effectiveness degrades monotonically.**  Dropping M2 costs about +5 %
  (NQ=8) / +3 % (NQ=16) abstract WL and a few more overlaps; dropping M3 as
  well roughly triples the overlaps; with no LOW layer at all the abstract
  overlaps are ~12x (NQ=8) / ~15x (NQ=16) the baseline.
* **Planner time does not follow band count.**  Bands fall by half at
  `-low 0`, but the planner's wall time falls far less (NQ=16: 12.7 s ->
  7.2 s).  The STRICT phase does shrink, but STRICT failures climb
  (NQ=16: 107 -> 578 -> 1,041) and the rip-up ladder re-plans take over.
* **run_nuts repair time rises 2.4-5x** once M3 is gone (NQ=16: 7.3 s ->
  21.3 s at `-low 1`), because the abstract placer has more overlaps to repair.
* Fewer supply-doomed seats at `-low 0` (0 against 82 / 143) is not a
  win: with no LOW layer nothing is escalated from LOW; the seats are
  simply gone and the overlaps are what is left.
* The `check_design nuts` audit-violation count excludes overlaps, so it
  falls at `-low 0` while the design is much worse; read overlaps.

## Caveats

Single thread, single run per cell (wall times on a shared box are noisy;
the profile counters -- bands, STRICT failures, ladder re-plans -- are not).
`-low 3` is byte-identical to the unmodified flow.  The experiment
varies resources only; it does not re-tune planner parameters for the
thinner stack.  `-low 0` is refused together with caps (`-caps`), which need
a LOW band.

## Round 2: swap each dropped LOW layer for a TOP layer (2026-10-13)

`bigsoc.tcl -low K -top (3-K)` keeps K LOW layers and adds 3-K TOP layers above
M7 (M8 H, M9 V, M10 H, each with the track pattern of the TOP layer below it
in the same direction: M6 for H, M5 for V).  The layer count stays six, so
the band count stays at the full-stack size.  Same netlist, die and
floorplan.  `tools/low_layers_experiment.py --match` runs it; `LOW_RECORD=1`
records each session as a flat `.buda` beside its log, which
`tools/render_design.py` renders (floorplan, abstract NUTS, and DNUTS where
the dial runs that far).  Round 1's drop-only runs were repeated in the same
session and reproduce its counts.

| dial | stack (LOW + added TOP) | abstract ovl | first-pass fails | planner s | run_nuts s | abstract WL | detailed WL | end (ovl / unplaced / viol) |
|---|---|---|---|---|---|---|---|---|
| nq2 | 3 + 0 | 16 | 7 | 0.2 | 0.15 | 52,164 | 1,183,230 | 0 / 6 / 6 |
| nq2 | 2 + 1 | 10 | 3 | 0.2 | 0.19 | 48,368 | 1,092,955 | 0 / 0 / 0 |
| nq2 | 1 + 2 | 15 | 12 | 0.2 | 0.20 | 49,606 | 1,153,328 | 0 / 0 / 23 |
| nq2 | 0 + 3 | 18 | 18 | 0.2 | 0.07 | 50,963 | 1,200,904 | 1 / 32 / 51 |
| nq8abs | 3 + 0 | 112 | 49 | 4.3 | 1.3 | 383,639 | – | – |
| nq8abs | 2 + 1 | 93 | 32 | 4.7 | 1.2 | 354,949 | – | – |
| nq8abs | 1 + 2 | 59 | 83 | 4.2 | 2.5 | 388,534 | – | – |
| nq8abs | 0 + 3 | 153 | 102 | 4.2 | 1.0 | 410,108 | – | – |
| nq16abs | 3 + 0 | 194 | 107 | 10.7 | 5.0 | 614,459 | – | – |
| nq16abs | 2 + 1 | 129 | 85 | 10.2 | 4.3 | 589,234 | – | – |
| nq16abs | 1 + 2 | 205 | 141 | 10.7 | 7.8 | 708,758 | – | – |
| nq16abs | 0 + 3 | 306 | 191 | 10.7 | 4.7 | 728,148 | – | – |

(`nq8full`: detailed WL 5.14M / 5.10M / 7.18M / 7.75M and 3,383 / 3,178 /
2,159 / 3,008 unplaced bits for the four stacks, healerless.)

Findings: swapping buys back most of what removal cost (NQ 8 abstract
overlaps with no LOW layer: 153 swapped against 1,398 removed; NQ 16: 306
against 2,937) at no change in planner time, since the band count is
unchanged; the cost moves to wire (NQ 8 detailed WL +40 to +51% for two or
three swaps) and the benefit is not monotone (NQ 8 is best at two swaps,
NQ 16 at one).  The healerless NQ 8 run strands 2,200 to 3,200 bits at every
stack, so swapping does not fix those; their cause was not investigated.
The added layers copy M5 and M6's patterns, so a real stack could differ.
