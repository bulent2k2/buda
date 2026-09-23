# BUDA: Progress and the Road Ahead

Internal briefing for prospective angel investors, as of 2026-09-20.  The
tree twin of the published page listed in [artifacts.md](../artifacts.md);
every number here is read from this repository, its design notes and the
founder's paper as of commit 63d4e67c (2026-09-18), and the sources are
listed at the end.  It is written for readers outside chip design: a
glossary at the end explains the terms.

## In short

- **The problem.**  A chip's major blocks are joined by thousands of wires —
  one Intel CPU core the method was used on had 6,000 connections grouped
  into about 300 buses.  Where those wires will run is usually settled late,
  and when there turns out to be no room for them, the fix costs days to
  weeks of schedule.
- **The idea.**  Plan the wiring early and in groups, the way a city lays out
  its highways before its streets.  The founder's version of this shipped
  inside Intel's in-house layout tool in 2002–2003 and was used or piloted on
  four Intel processor projects.
- **What exists.**  A complete open-source rebuild, public since April 2026:
  3,778 changes in under nine months, 4,352 automated tests, and a working
  connection to the open-source OpenROAD/LibreLane chip-building flow.
- **The headline result.**  On a benchmark chip taken all the way to a
  finished layout, adding BUDA to the block-by-block (hierarchical) flow made
  the run 23 % faster, the chip 38 % smaller and the wiring between blocks
  63 % shorter, and it turned a timing failure into a pass.
- **The main risk.**  Every result so far is on designs BUDA's own tooling
  generated or on public benchmarks.  At this small size the conventional
  all-at-once (flat) flow still beats it on run time and chip size; the
  larger size where BUDA is expected to win outright, and a first customer
  design, are both still to be measured.

## What BUDA is

BUDA is an open-source chip-design tool that plans a chip's major wiring as
**buses** — groups of wires that travel together, like the lanes of one
highway — instead of one wire at a time.  Laying out a chip has two steps
that matter here: deciding where the big blocks go (the **floorplan**), and
later drawing every individual wire exactly (**routing**).  The gap between
them is where the expensive surprises happen: a floorplan that looks fine
turns out to have no room for the wires that must cross it, and the
discovery comes days or weeks later.

BUDA closes that gap early.  Given the list of connections (the
**netlist**) and the floorplan, it groups the wires into buses, sketches
several possible paths for each, picks a path and a wiring layer for every
bus without overfilling any region, fits them onto the chip's real wiring
lanes (**tracks**) around the power-supply wiring, and hands the result to a
conventional router as guidance.  On the designs in the repository the
planning re-runs in seconds to minutes, so a designer can move a block and
see the wiring consequence in the same sitting.  The repair steps
(**healers**), which search for fixes, can take hours at the largest sizes:
one healing round on a 32-cluster design took 9,526 s, about 2 h 40 min.

Three properties distinguish it from a router:

- **Hierarchical.**  A large chip is built as blocks inside blocks.  A block
  used eight times is planned once and copied eight times, and the copies
  become obstacles the level above plans around.
- **Early.**  It works from block outlines alone, before each block's
  connection points (**pins**) are fixed — the stage at which the floorplan
  can still be changed cheaply.
- **Measured.**  Every decision is checked against the actual laid-out
  result, and the tool reports its own failures rather than printing a clean
  score over broken wiring.

It is written in C++ with a Python command layer, reads and writes the
industry's standard design-file formats, keeps its own design database, and
drives the open-source OpenROAD/LibreLane chip-building flow end to end.  It
has been public on GitHub since 30 April 2026 under the Apache 2.0
open-source license, at
[github.com/bulent2k2/buda](https://github.com/bulent2k2/buda).

## Results

The headline measurement comes from an 8×8 **systolic array** — a grid of 64
identical arithmetic units, the structure used in AI accelerators — taken
through the full open-source flow to a finished layout (LibreLane 3.0.11 on
the open SkyWater 130 nm process, sky130A, measured 7 September 2026).  A
hierarchical flow **with BUDA** beats the same hierarchical flow **without
it** on run time, chip size, wire, power and hold timing, and turns a
hold-timing failure into a pass.  Every number is LibreLane's own final
check, not BUDA's estimate.

How to read the table.  Three ways of building the same chip are compared.
**Flat (F)** treats the whole chip as one sea of logic: simplest, but its
run time grows steeply with size.  **Hierarchical (H)** builds each block
separately and then assembles them: the practical way to build very large
chips, but the blocks must then be wired together across the top level,
which is where the wiring gets hard.  **H+B** is the same hierarchical flow
with BUDA planning that top-level wiring.  The comparison that measures BUDA
is H+B against H; flat is shown because, at this small size, it is still the
one to beat.  For every row lower is better, except the two timing margins
(**slack**, in nanoseconds), where anything at or above zero passes and a
negative number fails.

| N = 8 | F: flat | H: hierarchical, no BUDA | H+B: hierarchical with BUDA | H+B vs H |
| --- | --- | --- | --- | --- |
| Run time of the whole flow | 4,541 s | 6,208 s | 4,797 s | −23 % |
| Chip (die) area | 1.032 mm² | 6.347 mm² | 3.935 mm² | 0.62× |
| Wire between blocks (top level) | 934,831 µm | 803,897 µm | 300,704 µm | −63 % |
| Total wire (between and inside blocks) | — | 1,980,337 µm | 1,682,776 µm | −15 % |
| Setup slack (timing margin) | −0.550 ns | +0.389 ns | +0.368 ns | −0.021 ns |
| Hold slack (timing margin) | +0.091 ns | **−1.075 ns (fails)** | **+0.112 ns** | fixed |
| Manufacturing-rule violations (DRC, KLayout) | 0 | 0 | 2, then 0 after a LEF fix | — |

In plain terms: BUDA made the hierarchical flow finish 23 % sooner, shrank
the chip by 38 %, cut the wiring between blocks by 63 %, and fixed a
hold-timing failure — the kind of failure a chip cannot run with at any
clock speed.  The price is 0.021 ns of setup margin, which stays positive.

The gains come from three separate things BUDA supplies, and controlled runs
measured each one, so the table is not one lump: how big each block should
be (**block sizes**: die 6.35 → 3.94 mm²), where each block's connection
points go (**pin placement**), and the wiring lanes (**corridors**) it hands
to the router.  Against a control run that used BUDA's block sizes only, the
pins and corridors cut the between-block wire by 59.9 % while adding 40.8 %
to the blocks' own wire — a net 2.8 % saving on total wire at N = 8.  The
between-block saving grew from N = 2 to N = 8 while the block-side cost
held.  The two manufacturing-rule markers traced to one hole in one block's
description file (its LEF), not to BUDA's routing; closing it made this arm
fully clean for the first time.

What the table does not say, stated plainly: at this small size, the
hierarchical flow with BUDA is still 5.6 % slower than the flat flow and its
die is 3.8× larger, because building from separate blocks pays for padding
and wiring channels around each block that the flat flow never needs.  The
case for hierarchy is at larger sizes, where flat runs slow down steeply: a
flat run at ~55 k cells takes 76 min and the next doubling five to six
hours.  The study's own success test — beat the flat flow by 2× on run time
at a size where flat becomes slow — has not yet been measured.  One small
timing loss remains: setup slack is 0.021 ns lower than without BUDA before
the LEF fix, and 0.0185 ns lower after it.  It is real rather than noise —
an independent repeat of the pre-fix arm reproduced every metric bit for
bit, that slack included — while the post-fix figure is a single run.  The
flow has never been timing-driven.

On BUDA's own test designs, the repair steps take a dual-core RISC-V
processor system (1,230 nets, 44 bottom-level blocks at four levels of
nesting) from 66 check violations to clean; a generated system-on-chip is
clean top-down at every size measured, up to 128 clusters; and 47 of the 57
designs in the benchmark suite end clean, with the 10 remaining ones
documented by cause.

## Origin: a proven idea, rebuilt

BUDA is the second life of a technique that shipped inside Galaxy, Intel's
in-house full-chip layout tool, in 2002–2003.  The founder's paper of that
period, *Assisted and Auto Bus Planning in Full-Chip Layout* (kept in this
repository at [docs/origin/paper.md](../origin/paper.md)), describes the
same two ideas BUDA is built on: sketch a handful of candidate paths
(**topologies**) per bus and let the designer choose; then fit the chosen
paths onto the chip's wiring tracks around the power grid
(**track-sharing**), which makes them buildable without running a full
router.

The paper records where it was used and what it bought, on four Intel
processor projects.  On Manzano, the full-chip designer planned about 95 %
of signal wires with auto bus planning early in the design, so that wire
delays and timing could be checked within days; the accompanying
presentation ([docs/origin/talk_contents.md](../origin/talk_contents.md))
puts the bus-planning effort at two weeks before and one day after.
Tanglewood planned all full-chip buses with assisted planning, to blocks two
or more levels down.  Nehalem piloted a combined bottom-up and top-down flow
on a five-level hierarchy.  Tejas planned buses through repeater stations
(points where long wires are re-amplified).  Those results belong to Galaxy,
not BUDA; they are cited here as the evidence that the method works on
production processors.

What changed in twenty years is everything around the method: free,
open-source chip-building software (OpenROAD, LibreLane) now exists to plug
into, and AI-assisted development lets one engineer build and verify a
system that used to take a team.  BUDA's own origin conversation (linked
from the README) frames the project as "revitalizing a proven concept with
modern algorithms and software architecture".  A first prototype was set
aside on 1 January 2026 ("clean-up to get ready for v2"); the current
codebase dates from that reset.

The name is Turkish: *bu da ne* — "what in the world is this?"  The README
expands it as BUndled Design Assistant.

## Timeline

From a reset on 1 January 2026 to a system that drives a full open-source
flow from logic description to manufacturable layout (RTL-to-GDS) by
September: 3,778 commits (saved changes) and 859 merged pull requests
(reviewed batches of changes) in under nine months, with the rate peaking at
1,370 commits in July.

Commits to `main` by month (`git log`, 1 Jan–18 Sep 2026; February and March
had none, September counts to the 18th):

| 2026 | Jan | Apr | May | Jun | Jul | Aug | Sep |
|---|---|---|---|---|---|---|---|
| commits | 7 | 19 | 318 | 608 | 1,370 | 1,127 | 329 |

| Date | Milestone |
| --- | --- |
| 2002–2003 | Bus planning ships in Galaxy, Intel's in-house layout tool; used on the Manzano, Tanglewood, Nehalem and Tejas processor projects |
| 1 Jan 2026 | Fresh start for version 2; the first prototype archived |
| 28 Apr 2026 | The core track-fitting engine (NUTS) and the full pipeline specification land |
| 30 Apr 2026 | Public on GitHub under Apache 2.0 |
| 16 May 2026 | First real design imported: the Ariane (CVA6) RISC-V processor core, through a new reader for its layout file (DEF) |
| 30 May 2026 | BUDA's own design database (BDB); designs with blocks inside blocks become first-class |
| 1–9 Jun 2026 | Reads netlists written in Verilog; groups wires into buses across the hierarchy |
| 11 Jun 2026 | Interactive floorplanning GUI; overfilled regions become a hard limit, with automatic rip-up and re-plan |
| 2 Jul 2026 | Reads and writes GDSII, the layout format sent for manufacture, producing exactly the same bytes on every run |
| 10 Jul 2026 | Repeated blocks planned once and copied to every instance |
| 20–22 Jul 2026 | Web interface; benchmark-suite harness (34 designs then, 57 now) |
| 1–4 Aug 2026 | Automated tests on every proposed change; nightly benchmark sweep; opt-in benchmark gate on changes |
| 8–9 Aug 2026 | Special wiring rules (wider wires, extra spacing, shielding) work end to end; Tcl scripting front end |
| 5–7 Sep 2026 | Full open-source chip build with BUDA in the loop, measured on the 8×8 systolic-array benchmark |
| 16–18 Sep 2026 | Experiments on keeping block and top-level plans consistent across iterations (the "convergence ladder"): reserved wiring tracks and the top-level plan handed down |

## What it does today

BUDA runs the whole wiring-planning pipeline, from the list of connections
to guidance for the router, on both flat and hierarchical designs and in the
industry's standard file formats.  Each of the five stages below is a
separate engine with its own tests and its own checks.

| Stage | What it does, in plain terms | Where it stands |
| --- | --- | --- |
| Bundling | Groups individual wires into buses by where they start and end | Five grouping strategies; a repeated block's buses are worked out once and reused in every copy, rotated copies included |
| Path generation | Sketches several candidate paths per bus: L-, Z- and U-shapes, trunk-and-branch trees, and shapes for irregular blocks | Runs in parallel; every candidate is checked to actually connect all its endpoints |
| Congestion planning | Picks one path per bus and a metal layer for each piece so that no region is overfilled; where one would be, it rips up earlier choices and re-plans | Gives the same answer at any thread count; measured 1.7× faster on the chip-scale test design with parallelism |
| Track fitting (NUTS) | Places every bus on real wiring tracks, then every individual wire on its own track, avoiding the power grid and blocked areas | Two levels: whole buses, then single wires with the connections between layers (vias) |
| Repair (healers) | Reads the actual result and fixes what went wrong: renegotiates crowded regions, rips up and re-routes, swaps paths for shorter ones | Three repair strategies; they take the RISC-V test system from 66 violations to clean |

Around the pipeline:

- **File formats.**  Reads the standard design formats (DEF and LEF for
  layout, Verilog for the netlist) and reads and writes GDSII, the format
  sent for manufacture, with identical bytes on every run and a tested round
  trip.  The DEF reader handles 1 M lines in 0.3 s.  BUDA's own database
  saves every stage, so a session can stop and resume at any point.
- **Three ways to drive it.**  A script language (293 flows checked into the
  repository), a front end in Tcl, the scripting language chip-design tools
  conventionally use (81 Tcl flows), and a web interface.  An interactive
  Floorplanner GUI edits block placement and launches the flow.
- **Checks.**  A design check (`check_design`) runs at every stage and
  reports each problem by type — broken connections, wires on the wrong
  layer or crossing blocked areas, dangling wire, two signals sharing one
  track, wires that could not be placed — plus a list of spots where a bus
  physically cannot fit.
- **Handoff to OpenROAD.**  The planned wiring lanes (corridors) are written
  in the guide format OpenROAD's router reads.  The guides are advisory by
  design — the router follows them where it can and deviates where it must —
  so the Results above are what the router actually did with them, not what
  BUDA asked for.

Scale, as measured on the repository's test designs: a real 45 nm processor
core (Ariane: 5,576 nets, 133 on-chip memory blocks, 13,034 blocked-area
rectangles) plans in about 13 s; a generated system-on-chip with 128
clusters, 1,675 bottom-level blocks and 2,451 buses (69,592 individual
wires) routes clean top-down in 120 s; a 32×32 systolic array (1,024
processing units) in 29 s.

## How it is built and validated

The test code is larger than the product: 110 k lines of tests against 41 k
lines of C++ engine and 46 k of Python.  That ratio is the point: in a tool
whose worst failure is a believable wrong answer, the checking is the
product.

| Measure | Value |
| --- | --- |
| Engine (C++) | 41,066 lines, excluding the bundled SQLite database library |
| Command layer and viewer (Python) | 45,889 lines; tools a further 23,714 |
| Tests | 109,721 lines in 362 files; 4,352 automated tests at the source commit, all run automatically on every push and every proposed change (9 min 37 s on this document's own branch; 2,105 tests when that gate began on 1 August 2026), plus 52 behaviour specifications written in plain language (Gherkin) |
| Documentation | 156 Markdown files, 57,548 lines, with a test that checks every link |
| Benchmark suite (QoR corpus) | 57 complete design runs, swept nightly, and on a proposed change when it carries the `run-qor` label — a deliberate opt-in, since the sweep holds the merge for about 20 min; 47 end clean |
| Merged pull requests | 859, every one reviewed by an automated AI reviewer (Codex); 385 commits answer a review finding by name |

Four practices do most of the work:

1. **Nothing changes unless asked.**  A new capability stays off until a
   benchmark sweep shows it helps; a design that does not ask for it
   produces exactly the same output as before, byte for byte.  Several ideas
   that measured worse ship as documented switches rather than defaults,
   with the numbers that rejected them.
2. **Measure, do not argue.**  Design notes record the measurement that
   decided each question, and re-measure when a claim is challenged.  Where
   a first reading was wrong, the correction stays in the document beside
   it.  A guard refuses to benchmark a build older than its source code,
   because that mistake produces a wrong number rather than an error.
3. **Loud over silent.**  An input the tool cannot honour stops the run with
   a numbered message (`BUDA-1905` and so on) that names the fix.  Every
   check also reports what it could not verify, not only what passed.
4. **AI pair-programming with a human editor.**  2,035 of the 3,778 commits
   are authored by Claude, 1,731 by the founder, under a written engineering
   rulebook (`CLAUDE.md`) and independent automated review on every pull
   request.  This is how one engineer sustained 1,000-plus commits a month
   while every change ran the full test suite, and every engine change
   labelled for it also ran the benchmark sweep.

The build runs natively on macOS, Linux and Windows (a manual Windows
validation workflow exercises both Microsoft compiler build paths), installs
with one standard Python command (`pip install .`), and needs no other
chip-design software: SQLite is bundled, and the DEF/LEF/Verilog/GDS file
readers are BUDA's own.

## Future challenges

The method is proven and the pipeline is complete; what remains is proving
it pays on other people's designs, at sizes where the flat flow breaks down.
The items below come from the project's own open-items pages and study
notes, not from a wish list.

### Technical

1. **Timing.**  BUDA has never been run timing-aware: no timing targets
   reach the planner, and the per-connection timing budgets designed for the
   OpenROAD handoff are unbuilt.  The one metric H+B still loses to H is
   setup slack, 0.0185 ns on the arm with the LEF fix and 0.021 ns before the
   fix, where an independent repeat reproduced the loss bit for bit — small,
   but real.  A timing-aware planner is the next thing that number asks for,
   and it is what the original Galaxy work was valued for: fast timing
   feedback to the chip's logic designers.
2. **The chip-size penalty of hierarchy.**  Building from separate blocks
   pays for padding and routing channels around each block that the flat
   flow never needs: 3.8× the flat die at N = 8.  Two levers are identified
   (channel width, then padding), and the cost is reported rather than used
   as a pass/fail gate.  Closing it is a question about the whole flow as
   much as about BUDA.
3. **The crossover has not been measured.**  The case for hierarchy is that
   flat runs stop scaling: at ~55 k cells a flat run takes 76 min, and the
   next doubling five to six hours.  The next size up (N = 16), where H+B is
   expected to overtake flat, needs a 20 GB machine and hours per run, and
   has not been run.
4. **Keeping the levels in step, by default.**  When blocks and the top
   level are planned separately, a change at one level can undo the other,
   so the planning must be repeated until it stops changing (a fixpoint).
   The experiments (the "convergence ladder") settled what a block must set
   aside for the top level — specific wiring tracks, not a uniform
   percentage — and how the top level's plan must be handed down for the
   repetition to settle.  Two refinements measured as wins at some sizes and
   losses at others, so they ship as optional switches.  Making this loop a
   one-command default is unfinished.
5. **Real designs lose their repetition.**  Planning a repeated block once
   and copying it is the hierarchical flow's main saving, and it needs many
   identical copies of one block.  But real designs come out of synthesis
   (the step that turns a logic description into gates) with every copy made
   unique (**uniquified**): every real netlist measured (NVDLA: 307 module
   types for 306 instances; Ariane: 127 for 125) has no block type used
   twice, so this path is exercised only on generated arrays.  Recovering the
   repetition, or planning before synthesis, is an open design question.
6. **Remaining quality gaps.**  10 of the 57 benchmark designs end with a
   residual — nine with overlapping or unplaced wires, one (the real 45 nm
   core) with check violations only.  The chip-scale top-down test design
   leaves 134 wires unplaced; the generated system-on-chip's bottom-up
   variant leaves the same 8 wires of one bus unplaced from 32 clusters up,
   and a 128-cluster run trying wider wiring channels ran 90 minutes without
   finishing.  Each is diagnosed; none is fixed.
7. **Run time at scale.**  The repair steps search for fixes; on the largest
   designs a repair round takes minutes to hours.  Parallelism landed in
   planning, path generation and the repair sweeps, but a bottom-up sweep at
   128 clusters still has to be budgeted in hours.
8. **Commercial-tool interfaces.**  The bridge to OpenAccess — the design
   database that Cadence-style commercial flows use — is specified but
   blocked on proprietary libraries.  The installable package exists but is
   not published; there is no release process yet.

### Product and go-to-market

- **Whose design?**  Every result above is on a design BUDA's own tooling
  generated, plus one public benchmark core (Ariane) whose layout file places
  only its memory blocks, not its logic gates.  The first customer design
  will find what the test designs could not — the Ariane import found three
  reader defects on day one.  The most valuable next artifact is a
  measurement on somebody else's chip.
- **Where it sits in a flow.**  The demonstrated wedge is the open-source
  flow (OpenROAD/LibreLane), where BUDA hands its plan to the router as
  guides.  Whether the buyer is an open-source-flow team, a commercial-flow
  team wanting early congestion feedback, or a mixed-signal/chiplet
  floorplanning group is not yet decided, and the answer changes which
  interface gets built next.
- **One maintainer.**  The velocity of the last nine months comes from one
  engineer with AI pair-programming and automated review.  That is the
  strength and the bus factor.  The 57 k lines of design documentation exist
  so that a second engineer can be productive; there has not yet been one.
- **A hierarchical methodology is a sale in itself.**  BUDA's largest wins
  come from a top-down design style built from repeated blocks, which many
  teams do not practise.  Selling the tool means selling the flow.

### Known, deliberate limits

- Bus-level planning only: BUDA plans buses and hands single nets to a
  router.  It is not a router and does not compete with one.
- No repeater or buffer planning (the cells that re-amplify long wires), no
  signal-integrity model (interference between neighbouring wires), no clock
  planning.
- Standalone parsers: the DEF/LEF/Verilog/GDS readers are BUDA's own and
  cover what the vehicles needed; each new input file has found a gap, and
  each gap is now a documented, tested item.

## What this document does not claim, and what to add

Everything above is read from the repository, its design notes and the
founder's paper as of 18 September 2026.  Nothing here is a market figure, a
revenue figure or a forecast.  Before this goes to an investor, the founder
should add or confirm:

- [ ] **Team and history.**  Founder background beyond the Galaxy paper; who
  else has contributed; what the archived v1 was and why it was reset.
- [ ] **IP position.**  The 2002–2003 work was done at Intel.  State plainly
  that BUDA is a new implementation of published ideas and what, if
  anything, carries over.
- [ ] **Market and customer.**  Who buys interconnect planning today, at
  what price, and which segment BUDA targets first.  This document names the
  candidates; it does not choose.
- [ ] **Business model.**  Apache 2.0 is the license; the revenue model
  (support, hosted, enterprise features, services) is not stated anywhere in
  the repository.
- [ ] **The ask.**  Amount, use of funds, and the milestone it buys — the
  N = 16 crossover measurement, a first external design, or a timing-driven
  planner are the three the technical record points to.
- [ ] **Naming.**  The README expands BUDA as *BUndled Design Assistant*;
  the engineering guide says *Bundled Unified Design Automation*.  Pick one.

## Glossary

The chip-design terms used above, in plain words.

| Term | Meaning |
| --- | --- |
| Block (hard macro) | A self-contained piece of a chip, designed once and placed as a unit |
| Bus | A group of wires that start and end in the same places and are planned together |
| Die | The finished piece of silicon; its area is the chip's size |
| DRC | Design-rule check: the manufacturer's geometry rules; any violation means the chip cannot be made as drawn |
| Floorplan | Where each block sits on the chip |
| Flat vs hierarchical | Building the whole chip at once, versus building blocks separately and then assembling them |
| Healer | A BUDA repair step that reads the laid-out result and fixes what went wrong |
| Netlist | The list of connections: which pins each wire (net) joins |
| OpenROAD / LibreLane | Free, open-source software that takes a chip from logic description to manufacturable layout |
| Pin | A block's connection point for one wire |
| RISC-V, Ariane | An open processor instruction set, and one open processor core built on it |
| Router | The tool that draws every individual wire exactly |
| Slack (setup, hold) | Timing margin in nanoseconds; at or above zero passes.  Setup failures cap the clock speed; hold failures break the chip at any speed |
| SoC | System-on-chip: processors, memory and interfaces on one die |
| Synthesis | The step that turns a logic description into gates |
| Systolic array | A grid of identical arithmetic units passing data to their neighbours; used in AI accelerators |
| Test design (vehicle) | A design kept in the repository to exercise and measure BUDA |
| Track | One of the evenly spaced lanes on a metal layer where a wire may run |
| Via | A vertical connection between two metal layers |
| DEF, LEF, Verilog, GDSII | Standard file formats: layout placement, block descriptions, netlists, and the final layout sent for manufacture |

## Sources

The public repository ([github.com/bulent2k2/buda](https://github.com/bulent2k2/buda),
Apache 2.0) and its `git log` through commit 63d4e67c;
[docs/origin/paper.md](../origin/paper.md) and
[docs/origin/talk_contents.md](../origin/talk_contents.md);
[librelane_hier_flow.md](librelane_hier_flow.md) (§7 results, §11 open
items); [opens.md](opens.md); [qor/qor_table.md](../../qor/qor_table.md)
(snapshot of 31 August 2026); and the CI workflow's measured suite size
(`.github/workflows/ci.yml`).
