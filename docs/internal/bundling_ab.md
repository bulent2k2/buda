# What bundling buys — one design, measured

BUDA's premise is that a chip's interconnect should be planned as BUSES,
not as single wires.  This page measures that premise on its own: one
design, planned twice, with the grouping the only difference.

```bash
BUDA_ARCH=x86-64-v2 bin/bb
python3 tools/bundling_ab.py flow/soc_small.buda --json ab.json
```

`tools/bundling_ab.py` runs the flow as written, then again with
`set_max_bundle_bits 1` injected right before its own bundler.  That splits
every bundle into one-net parts: every connection becomes its own "bus".
Floorplan, layer stack, planner settings and repair steps are the flow's
own text in both runs.  The tool refuses a flow whose bundler it cannot
reach, or that sets its own cap anywhere in its source tree.  It fails a
run whose report records a command error, since the CLI prints `Error:` and
carries on.

## The measurement

**Setup.** Run on 2026-09-24:

* `main` at `2dfc617`, built with `BUDA_ARCH=x86-64-v2` (CI's ISA);
* a 4-core Linux container;
* the vehicle is `flow/soc_small.buda`: the SoC vehicle at NQ = 8, which
  is 16 processor clusters and 8,272 nets, with the healers in its flow.

| | bundled | one net per bundle | ratio |
|---|---|---|---|
| bundles planned | 323 | 8,272 | 25.6x |
| candidate paths | 2,023 | 51,624 | 25.5x |
| bundler (s) | 0.18 | 1.09 | 6.1x |
| path generation (s) | 0.54 | 12.05 | 22.3x |
| planner (s) | 0.79 | 22.26 | 28.2x |
| track fitting (bus) (s) | 0.24 | 148.84 | 620.2x |
| track fitting (bit) (s) | 0.41 | 171.0 | 417.1x |
| repair (s) | 0.61 | 1.17 | 1.9x |
| engine total (s) | 3.71 | 365.6 | 98.5x |
| first bus-level check (violations) | 1 | 415 | |
| first detailed check (violations) | 40 | 0 | |
| final check (violations) | 0 | 0 | |
| detailed wirelength | 1,968,672 | 1,887,680 | -4.1 % |

**Repeatability.** Four runs of the same build agree exactly on every
count and on wirelength.  Engine time is 102x, 97x, 98.5x and 95x across
the four.  The fourth ran after the Codex fixes on #953, which read counts
from the flow log and fail a run that reports a command error; it passed
that gate.  The table above is the third run.

## What it says

**Bundling is a SPEED result on this design, not a wire-quality one.**

* Planned one net at a time, the chip routes clean with **4.1 % less
  wire**.  Its first detailed check is even cleaner: 0 violations, against
  40 in 2 bundles for the bundled run, which the repair steps then clear.
* The bus-level picture runs the other way.  The unbundled abstract result
  has 415 check violations (segments seated on keepouts), which the
  detailed stage then places around.  The bundled one has 1.  So a
  one-at-a-time plan is not a worse ROUTE here, but it is a much worse
  PLAN to read before the detailed stage.
* The cost is time: about **100x** end to end.  Most of it is in track
  fitting: 620x at the bus level and 417x at the bit level, against 25x
  the objects.  Why those two stages grow so much faster than the object
  count is not investigated here.  The planner, with 25x the objects,
  takes 28x the time.

Why 100x matters is BUDA's own pitch: planning a floorplan change "in the
same sitting".  At 4 s that holds.  At six minutes, for one design point on
a 16-cluster chip, it does not.  How the ratio scales with design size is
NOT measured here.  One size is one point, and nothing above says the gap
grows or shrinks.

What bundling is also FOR is not in this table.  A bus planned as a unit
keeps its bits together, in order, under one rule, which is what an NDR
shield, a timing budget per bus, or a
[signal pre-route](signal_preroutes.md) written as whole buses needs.
Those are reasons to plan in groups that a wirelength column cannot score.

## Limits

* **One design.** It is generated, not from a real netlist.  A design whose
  nets do not share endpoints would bundle less, and the ratio would shrink
  with it.
* **One configuration.** Both arms run the flow's own settings.  No
  setting was tried that might suit one-net planning better, in time or
  in wire.
* **`set_max_bundle_bits 1` splits AFTER bundling.** The bundler's grouping
  runs and is then undone, which is why the bundler row is not faster in
  the one-net arm.  It measures planning one net at a time, not a bundler
  that never grouped.
