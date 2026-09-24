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
reach — whose FIRST bundler, as the engine runs the flow, is not in its
own text — or that sets its own cap anywhere in its source tree, in any
spelling the engine dispatches (upper case, an alias, an alias defined in
a sourced file).  It fails a
run whose report records a command error, since the CLI prints `Error:` and
carries on.

The arms run one after the other, so each gets its own copy of every file
the flow opens or writes.  Without that, the second arm inherited the first
arm's database: a flow that builds its design into a named `open_bdb` file
died in the second arm on rows the first had written, and a flow's own
`save_bdb` left its checked-in fixture holding the one-net arm's route
(both measured).  A named database is copied into each arm as it stood
before the A/B, and every file the flow writes is renamed into the arm.  A
`:memory:` or `.sql` open writes nothing of the user's and is left as it
is.  What cannot be kept apart is refused before either arm runs: a `.sql`
opened with `writeback`, or a named open or file write inside a sourced
file.  Of the 293 checked-in flows, none gains a refusal and 10 have a path
rewritten.

When the flow opens a database, each arm also ends with a `save_bdb`
snapshot of it, and the judge, `tools/independent_audit.py`, scores that
checkpoint.  The judge reads the stored geometry and imports no engine; its
verdict is a row of its own, beside `check_design`'s.  The snapshot is the
tool's, so its seconds are left out of the arm's times.  It is a snapshot
rather than a redirect of the open because moving `soc_small`'s `:memory:`
database onto disk made every command that writes it commit to disk: its
bundled arm went from 3.7 s to 12.7 s.  The six timed stages moved from
2.77 s to 2.84 s between them; the rest was in the commands that build the
design, such as `add_bus` at about 0.07 s each.

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
* **The wire column's sign depends on the design.** On
  `demo/comprehensive_demo.buda` the one-net arm uses **1.5 % MORE** wire
  (70,104 against 71,152), the opposite direction to `soc_small`'s −4.1 %.
  The speed result holds on both.
* **The judge does not model non-default rules.** A wire governed by an NDR
  spans several signal slots, so its centre is not a slot centre, and the
  judge reports it `OFF_GRID`.  On `flow/ndr_shield_hier.buda` built into a
  named file, it reported 20 of them, all on NDR-governed nets, where
  `check_design` reported none.  Read a judge row on an NDR design with
  that in mind.
* **A file the flow READS after writing it** is followed into the arm only
  by `open_bdb`.  No checked-in flow reads one of its own outputs any other
  way.
* **`set_max_bundle_bits 1` splits AFTER bundling.** The bundler's grouping
  runs and is then undone, which is why the bundler row is not faster in
  the one-net arm.  It measures planning one net at a time, not a bundler
  that never grouped.
