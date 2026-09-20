# BUDA: Progress and the Road Ahead

Internal briefing for prospective angel investors, as of 2026-09-20.  The
tree twin of the published page listed in [artifacts.md](../artifacts.md);
every number here is read from this repository, its design notes and the
founder's paper as of commit 63d4e67c (2026-09-18), and the sources are
listed at the end.

## What BUDA is

BUDA is an open-source chip-design tool that plans a chip's major wiring as
**buses** — groups of wires routed together — instead of one wire at a time.
It sits between two steps every chip goes through: deciding where the blocks
go (floorplanning) and drawing every wire exactly (detailed routing).  That
gap is where the expensive surprises happen: a floorplan that looks fine turns
out to have no room for the wires that cross it, and the discovery comes days
or weeks later.

BUDA closes that gap early.  Given a netlist and a floorplan, it groups the
wires into buses, generates several candidate shapes for each bus, picks a
shape and metal layer for every bus under a congestion model, packs them onto
real tracks around the power grid, and hands the result to a conventional
router as guidance.  On the checked-in designs the planning stages re-run
in seconds to minutes, so a designer can move a block and see the wiring
consequence in the same sitting; the healers, which search, can take hours at
the largest sizes (one healing round on a 32-cluster design took 9,526 s).

Three properties distinguish it from a router:

- **Hierarchical.**  A block used eight times is planned once and copied
  eight times, with the copies becoming obstacles for the level above.
- **Early.**  It works before the blocks have pins, on abstract block
  outlines — the stage at which the floorplan can still be changed.
- **Measured.**  Every routing decision is checked against a placed result,
  and the tool reports its own failures rather than a clean number over a
  broken route.

It is written in C++20 with a Python command layer, reads and writes the
industry formats (DEF/LEF, Verilog, GDSII) plus its own SQLite design
database, and drives the open-source OpenROAD/LibreLane flow end to end.
Public on GitHub since 30 April 2026 under the Apache 2.0 license, at
[github.com/bulent2k2/buda](https://github.com/bulent2k2/buda).

## Origin: a proven idea, rebuilt

BUDA is the second life of a technique that shipped inside Intel's full-chip
layout tool, Galaxy, in 2002–2003.  The founder's paper of that period,
*Assisted and Auto Bus Planning in Full-Chip Layout* (kept in this repository
at [docs/origin/paper.md](../origin/paper.md)), describes the same two ideas
BUDA is built on: generate a handful of candidate **topologies** per bus and
let the designer choose, then **track-share** the chosen topologies around
the power grid to legalise them without a full router.

The paper records where it was used and what it bought.  Manzano's full-chip
designer planned about 95 % of signal wires with auto bus planning early in
the design, so that RC extraction and timing could be run within days; the
accompanying presentation ([docs/origin/talk_contents.md](../origin/talk_contents.md))
puts the bussing effort at two weeks before and one day after.  Tanglewood
planned all full-chip buses with assisted planning to blocks two or more
levels down.  Nehalem piloted a bottom-up/top-down flow on a five-level
hierarchy.  Tejas planned buses through repeater stations.  Those results
belong to Galaxy, not BUDA; they are cited here as the evidence that the
method works on production processors.

What changed in twenty years is everything around the method: open-source
physical design (OpenROAD, LibreLane) now exists to plug into, and
AI-assisted development lets one engineer build and verify a system that
used to take a team.  BUDA's own origin conversation (linked from the README)
frames the project as "revitalizing a proven concept with modern algorithms
and software architecture".  A first prototype was set aside on 1 January
2026 ("clean-up to get ready for v2"); the current codebase dates from that
reset.

The name is Turkish: *bu da ne* — "what in the world is this?"  The README
expands it as BUndled Design Assistant.

## Timeline

From a reset on 1 January 2026 to a system that drives a full open-source
RTL-to-GDS flow by September: 3,778 commits and 859 merged pull requests in
under nine months, with the rate peaking at 1,370 commits in July.

Commits to `main` by month (`git log`, 1 Jan–18 Sep 2026; February and March
had none, September counts to the 18th):

| 2026 | Jan | Apr | May | Jun | Jul | Aug | Sep |
|---|---|---|---|---|---|---|---|
| commits | 7 | 19 | 318 | 608 | 1,370 | 1,127 | 329 |

| Date | Milestone |
| --- | --- |
| 2002–2003 | Assisted and auto bus planning ship in Intel's Galaxy; used on Manzano, Tanglewood, Nehalem and Tejas |
| 1 Jan 2026 | Repository reset for v2; the first prototype archived |
| 28 Apr 2026 | Track-sharing engine (NUTS) and the full pipeline specification land |
| 30 Apr 2026 | Public on GitHub under Apache 2.0 |
| 16 May 2026 | First real design: the Ariane (CVA6) RISC-V core, with a DEF reader |
| 30 May 2026 | SQLite physical design database (BDB); hierarchy becomes first-class |
| 1–9 Jun 2026 | Verilog import; hierarchical bundler |
| 11 Jun 2026 | Interactive Floorplanner GUI; overflow made a hard constraint with rip-up and replan |
| 2 Jul 2026 | GDSII import and deterministic export |
| 10 Jul 2026 | Bottom-up templates: plan a repeated block once, copy to every instance |
| 20–22 Jul 2026 | Web server and Scala.js client; QoR corpus harness (34 flows then, 57 now) |
| 1–4 Aug 2026 | Continuous integration on every PR; nightly QoR sweep; PR QoR gate |
| 8–9 Aug 2026 | Non-default rules (width, spacing, shields) usable end to end; Tcl front end |
| 5–7 Sep 2026 | LibreLane/OpenROAD hierarchical flow: N = 8 systolic-array benchmark measured |
| 16–18 Sep 2026 | Convergence-ladder experiments E1 and E5; positional track reservations and plan hand-down |

## What it does today

BUDA runs a complete interconnect-planning pipeline from netlist to router
guidance, on flat and hierarchical designs, in the formats the industry uses.
The five stages below are each a separate engine with its own tests and its
own audit.

| Stage | What it does | Where it stands |
| --- | --- | --- |
| Bundling | Groups nets into buses by driver and receiver: strict, fan-in, fan-out, bidirectional, or their join; hierarchical templates solved once per cell type | Five strategies; templates expand to every instance, including rotated ones |
| Topology generation | Enumerates candidate shapes per bus on a Hanan grid: L, Z, U, trunk-and-stub, MST trees, two-level datapath trees, multi-rectangle blocks | Parallel on a C++ thread pool; every candidate passes a coverage and connectivity gate |
| Congestion planning | Picks one topology per bus and a metal layer per segment under a capacity model; overflow is a hard constraint with rip-up and replan; per-cell layer policies, shares and positional reservations | Decision-identical across thread counts; measured 1.7× speed-up on the chip vehicle |
| Track sharing (NUTS) | Packs bus segments onto real track positions per layer, then snaps every bit to a signal track around the power grid and blockages | Two levels: abstract buses, then bit-level with per-bit vias |
| Healers | Closes the loop on the measured result: negotiate congestion, rip up and re-route, refine selection | Three healers; a 66-violation design heals to clean on the RISC-V SoC vehicle |

Around the pipeline:

- **Interchange.**  DEF/LEF and Verilog in (streaming DEF reader, 1 M lines
  in 0.3 s), GDSII in and out (deterministic bytes, round-trip tested), an
  SQLite design database at schema v30 that persists every stage so a
  session can resume at any point.
- **Three ways to drive it.**  A script language (`.buda`, 293 checked-in
  flows), a Tcl front end that turns any command into `buda::<name>` (81 Tcl
  vehicles), and a web server with a Scala.js client.  An interactive
  Floorplanner GUI edits placement and launches the flow.
- **Audits.**  `check_design` reports typed violations at every stage —
  opens, layer-direction errors, keepout crossings, antennas, bit shorts,
  unplaced bits — and a census of seats that cannot host their bus.
- **OpenROAD handoff.**  Corridors are written in the guide format
  OpenROAD's router reads.  The guides are advisory by design — the router
  follows them where it can and deviates where it must — so the measured
  result below is what the router actually did with them, not what BUDA
  asked for.

Scale, as measured on the checked-in vehicles: a real 45 nm CPU core
(Ariane, 5,576 nets, 133 SRAM macros, 13,034 obstruction rectangles) plans
in about 13 s; a generated SoC with 128 clusters, 1,675 leaf blocks and
2,451 buses (69,592 bit-wires) routes clean top-down in 120 s; a 32×32
systolic array (1,024 processing elements) in 29 s.

## How it is built and validated

The test code is larger than the product: 110 k lines of tests against 41 k
lines of C++ engine and 46 k of Python.  That ratio is the point — in a tool
whose failure mode is a plausible wrong number, the verification is the
product.

| Measure | Value |
| --- | --- |
| Engine (C++20) | 41,066 lines, excluding the bundled SQLite |
| Command layer and viewer (Python) | 45,889 lines; tools a further 23,714 |
| Tests | 109,721 lines in 362 files; 4,352 tests collected at the source commit, every tier run by CI on each push and PR — 9m37s on this document's own branch; 2,105 when the gate landed on 1 August 2026 — plus 52 Gherkin feature specs |
| Documentation | 156 Markdown files, 57,548 lines, with a test that walks every link |
| QoR corpus | 57 full-pipeline flows swept nightly, and on a PR when it carries the `run-qor` label — a deliberate opt-in, since the sweep gates the merge for about 20 min; 47 end clean |
| Merged pull requests | 859, every one reviewed by an automated reviewer (Codex); 385 commits answer a review finding by name |

Four practices do most of the work:

1. **Byte-identical by default.**  A new capability is opt-in until a corpus
   sweep shows it helps; a flow that does not ask for it produces the same
   bytes it did before.  Several measured-worse ideas ship as documented
   levers rather than defaults, with the numbers that rejected them.
2. **Measure, do not argue.**  Design notes record the measurement that
   decided each question, and re-measure when a claim is challenged.  Where a
   first reading was wrong, the correction stays in the document beside it.
   A guard module (`tools/measure_guard.py`) refuses a benchmark run against
   a build older than its sources, because that failure reports a wrong
   number rather than an error.
3. **Loud over silent.**  An input the tool cannot honour stops the run with
   a numbered diagnostic (`BUDA-1905`, and so on) that names the remedy.
   Every audit prints what it could not check, not only what passed.
4. **AI pair-programming with a human editor.**  2,035 of the 3,778 commits
   are authored by Claude, 1,731 by the founder, under a written engineering
   rulebook (`CLAUDE.md`) and independent automated review on every pull
   request.  This is how one engineer sustained 1,000-plus commits a month
   while every change ran the full test suite, and every engine change
   labelled for it a corpus sweep.

The build runs natively on macOS, Linux and Windows (a manual Windows
validation workflow exercises both MSVC generator paths), installs with
`pip install .`, and has no external EDA dependency — SQLite is bundled and
the DEF/LEF/Verilog/GDS readers are its own.

## Results

The headline measurement: on an 8×8 systolic array taken through the full
open-source flow (LibreLane 3.0.11, sky130A, measured 7 September 2026), a
hierarchical flow **with BUDA** beats the same hierarchical flow **without
it** on wall time, die, wire, power and hold slack — and turns a hold-timing
failure into a pass.  Every number is LibreLane's own signoff metric, not
BUDA's estimate.

| N = 8 | F: flat | H: hierarchical, no BUDA | H+B: hierarchical with BUDA | H+B vs H |
| --- | --- | --- | --- | --- |
| Wall time | 4,541 s | 6,208 s | 4,797 s | −23 % |
| Die | 1.032 mm² | 6.347 mm² | 3.935 mm² | 0.62× |
| Top-level wire | 934,831 µm | 803,897 µm | 300,704 µm | −63 % |
| Total wire (top + blocks) | — | 1,980,337 µm | 1,682,776 µm | −15 % |
| Setup slack | −0.550 ns | +0.389 ns | +0.368 ns | −0.021 ns |
| Hold slack | +0.091 ns | **−1.075 ns (fails)** | **+0.112 ns** | fixed |
| Signoff DRC (KLayout) | 0 | 0 | 2, then 0 after a LEF fix | — |

Three things BUDA contributes were separated by controls, so the table is not
one lump: its block **sizes** (die 6.35 → 3.94 mm²), its **pin placement**,
and its **corridors** handed to the router.  Against a control carrying the
sizes only, pins and corridors cut the top-level wire by 59.9 % while costing
the blocks 40.8 % of their own — a net 2.8 % saving on total wire at N = 8,
and the top-side saving grew from N = 2 to N = 8 while the block-side cost
held.  The two DRC markers traced to one hole in one cell's abstract, not to
routing; closing it took signoff to fully clean for the first time on this
arm.

What the table does not say, stated plainly: at this size the hierarchical
flow with BUDA is still 5.6 % slower than the flat flow and its die is 3.8×
larger, because a hard-macro flow pays for block padding and channels the
flat flow never sees.  The study's own success criterion — beat the flat flow
by 2× on wall time at a size where flat becomes slow — is not yet measured;
the flat run at ~55 k cells takes 76 min and the next doubling five to six
hours, which is where hierarchy is expected to pay.  One setup-slack
regression remains: 0.021 ns before the LEF fix, 0.0185 ns after it.  It is
real rather than noise — an independent repeat of the pre-fix arm reproduced
every metric bit for bit, that slack included — while the post-fix figure is
a single run.  The flow has never been timing-driven.

On BUDA's own vehicles, the healer stack takes a dual-core RISC-V SoC (1,230
nets, 44 leaves at four depths) from 66 audit violations to clean; a
generated SoC is clean top-down at every size measured up to 128 clusters;
the QoR corpus stands at 47 of 57 flows clean, with the 10 residuals
documented by cause.

## Future challenges

The method is proven and the pipeline is complete; what remains is proving it
pays on designs that are not its own, at sizes where the flat flow breaks
down.  The items below are drawn from the project's own open-items pages and
study notes, not from a wish list.

### Technical

1. **Timing.**  BUDA has never run timing-driven: no timing constraints reach
   the planner and the per-pin timing budgets designed for the OpenROAD
   handoff are unbuilt.  The one metric H+B still loses to H is setup slack:
   0.0185 ns on the notch-fixed arm, 0.021 ns before the fix, where an
   independent repeat reproduced the loss bit for bit — small, but real.  A
   timing-aware
   planner is the next thing that number asks for, and it is the feature the
   original Galaxy work was valued for (fast timing feedback to RTL).
2. **The die-area penalty of hierarchy.**  A hard-macro flow pays for block
   padding and routing channels the flat flow never sees: 3.8× the flat die
   at N = 8.  Two levers are identified (channel width, then padding) and the
   cost is reported rather than gated.  Closing this is a flow question as
   much as a BUDA one.
3. **The crossover has not been measured.**  The case for hierarchy is that
   flat runs stop scaling; at ~55 k cells a flat run takes 76 min and the
   next doubling five to six hours.  The N = 16 point that would show H+B
   overtaking flat needs a 20 GB container and hours per arm, and has not
   been run.
4. **Convergence as a default, not a lever.**  The convergence-ladder
   experiments settled what the block-level budget must look like (a
   positional track reservation, not a uniform share) and how the top's plan
   must be handed down for the loop to reach a fixpoint.  The
   corridor-steering and seat-yielding refinements measured as wins on some
   sizes and losses on others, so they ship as opt-in levers.  Making the
   loop a one-command default is unfinished.
5. **Real netlists are uniquified.**  Solve-once-copy — the hierarchical
   flow's economic engine — needs many identical instances of one cell.
   Every real synthesized netlist measured (NVDLA: 307 modules, 306
   instances; Ariane: 127/125) has zero repeated module types, so the
   bottom-up path is exercised only on generated arrays.  Recovering
   repetition from a uniquified netlist, or planning above synthesis, is an
   open design question.
6. **Residual quality.**  10 of the 57 corpus flows end with a residual —
   nine with overlaps or unplaced bits, one (the real 45 nm core) with audit
   violations only; the chip-scale top-down vehicle strands 134 bits; the
   generated SoC's bottom-up arm strands the same 8 bits of one bus from 32
   clusters up, and a 128-cluster channel sweep ran 90 minutes without
   finishing.  Each is diagnosed; none is fixed.
7. **Runtime at scale.**  The healers are measured search; on the largest
   vehicles a healing round is minutes to hours.  Parallelism landed in the
   planner, generation and the healer sweeps, but a bottom-up sweep at 128
   clusters still has to be budgeted in hours.
8. **Commercial-flow interfaces.**  The OpenAccess bridge that would connect
   BUDA to Cadence-style flows is specified and blocked on proprietary
   libraries.  The packaged wheel exists but is not published; there is no
   release process yet.

### Product and go-to-market

- **Whose design?**  Every result above is on a design BUDA's own tooling
  generated, plus one public benchmark core (Ariane) that has no standard
  cells placed.  The first customer design will find what the vehicles could
  not — the Ariane import found three reader defects on day one.  The most
  valuable next artifact is a measurement on somebody else's chip.
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
  come from a top-down, template-based design methodology that many teams do
  not practise.  Selling the tool means selling the flow.

### Known, deliberate limits

- Bus-level planning only: BUDA plans buses and hands single nets to a
  router.  It is not a router and does not compete with one.
- No repeater or buffer planning, no signal-integrity model, no clock
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
- [ ] **Market and customer.**  Who buys interconnect planning today, at what
  price, and which segment BUDA targets first.  This document names the
  candidates; it does not choose.
- [ ] **Business model.**  Apache 2.0 is the license; the revenue model
  (support, hosted, enterprise features, services) is not stated anywhere in
  the repository.
- [ ] **The ask.**  Amount, use of funds, and the milestone it buys — the
  N = 16 crossover measurement, a first external design, or a timing-driven
  planner are the three the technical record points to.
- [ ] **Naming.**  The README expands BUDA as *BUndled Design Assistant*;
  the engineering guide says *Bundled Unified Design Automation*.  Pick one.

## Sources

The public repository ([github.com/bulent2k2/buda](https://github.com/bulent2k2/buda),
Apache 2.0) and its `git log` through commit 63d4e67c;
[docs/origin/paper.md](../origin/paper.md) and
[docs/origin/talk_contents.md](../origin/talk_contents.md);
[librelane_hier_flow.md](librelane_hier_flow.md) (§7 results, §11 open
items); [opens.md](opens.md); [qor/qor_table.md](../../qor/qor_table.md)
(snapshot of 31 August 2026); and the CI workflow's measured suite size
(`.github/workflows/ci.yml`).
