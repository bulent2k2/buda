# Driving BUDA from Tcl

*Phase 5 of [lefdef_interface_plan.md](internal/lefdef_interface_plan.md).
Implementation: `tools/buda.tcl` (the package you source) and
`tools/buda_server.py` (the engine behind it).*

EDA flows are written in Tcl.  A tool that can only be driven by its own
script language is a tool every site has to wrap before it can use it — and
each of those wrappers is written once, badly.

```tcl
source /path/to/buda/tools/buda.tcl
buda::start

buda::def_layer 4 M4 H TOP 30
buda::def_layer 5 M5 V TOP 30
foreach blk {alu regfile icache} {
    buda::add_block $blk 0 0 100 100
}
buda::run_bundler STRICT
buda::generate_topologies
buda::run_planner 3
buda::run_nuts

if {[buda::query overlaps] > 0} {
    buda::ripup_reroute 20
}
buda::emit_guides guides.json tcl guides.tcl
buda::stop
```

## Which way round the processes go, and why

The obvious build is an embedded interpreter — `tkinter.Tcl()` inside
Python, a few lines — and it was the first draft.  It is the wrong way round
twice over:

* it puts BUDA's Python in charge and asks the site's flow to run *under*
  it, which is the opposite of integrating with a flow that already exists
  and already has its own variables, procs and libraries;
* it makes the Tcl front end depend on **tkinter**, a GUI toolkit, so a
  headless compute farm — exactly where flows run — may not have it.

So the arrangement is inverted: **your** `tclsh` is the parent, it sources
`tools/buda.tcl`, and BUDA runs as a child process behind a pipe.  Nothing
about your interpreter changes, and no GUI toolkit is involved.

## Commands

Every command in BUDA's registry is available as `buda::<name>`, with the
same arguments as in a `.buda` script.  The list is **asked of the running
engine** at `buda::start`, so a command added to `src/buda_cmds/` is
callable from Tcl immediately — there is no second list to fall out of date.

Arguments are joined with spaces and handed to the same handler the script
parser uses, so a command means exactly what it means in a script.  A Tcl
list argument therefore arrives as its space-joined elements, which is what
`{a b c}` already looks like to the parser.

## Errors are Tcl errors

This is the one deliberate difference from a `.buda` script.  BUDA's
handlers report most failures by *printing* `Error: …` and returning; inside
a flow, silently continuing past a failed step is how a wrong result gets
shipped.  So:

| What happened | Tcl sees |
|---|---|
| command succeeded | returns its output |
| command printed a warning | returns its output (**not** an error) |
| command printed `Error: …`, or raised | `error` — `catch` works |
| `exit` / `exit 0` | returns; the session is over |
| a fail-fast command (`add_block a 0 0 10.5 10`) | `error`, and the session is over |

The last row matters: a malformed argument ends a `.buda` run on the spot,
and it ends the Tcl session too, because a command must mean the same thing
in both.  What it must not do is *look* like a clean finish — reporting only
the ending let a flow run on and then fail later with "the engine exited
unexpectedly", blaming the wrong command.

## Values you can branch on

The reason to drive a tool from Tcl at all is that a command can **return**
something.

```tcl
buda::query bundles      ;# bundles created
buda::query blocks       ;# floorplan blocks
buda::query nets         ;# nets in the netlist
buda::query overlaps     ;# NUTS track overlaps, -1 if NUTS has not run
buda::query unplaced     ;# unplaced bits, -1 if detailed NUTS has not run
buda::query violations   ;# the LAST check_design's violation count,
                         ;#   -1 if no audit has run (or it could not) —
                         ;#   a gate must not read "never audited" as clean
buda::query messages     ;# {id severity} pairs — the message catalogue
```

A count that has not been computed answers **-1**, not 0: "no NUTS result"
and "no overlaps" are opposite conclusions, and a flow that branches on the
number has to be able to tell them apart.

Deliberately few names.  This is a bridge, not a second API, and each one is
a promise to keep.

## The vehicle

[`flow/tcl/array.tcl`](../flow/tcl/array.tcl) is the front end's own flow
vehicle — run it with `tclsh flow/tcl/array.tcl ?ROWS COLS?`.  A `.buda`
script can only *state* a design; this flow **computes** one, which is the
case the front end exists for: a parameterized ROWS × COLS array of tile
instances (each a cell containing a 2 × 2 leaf array) whose buses a pair of
nested loops emits — intra-tile cell templates replicated per instance,
cross-tile neighbor chains with leaf-deep endpoints, a corner-to-corner
diagonal, and a fan-in that CONVERGENT bundling merges into one per-bit
tapered tree.  The end of the flow branches on `buda::query`: healers run
only if the measured result is dirty, and the exit code is the design's
cleanliness.  Pinned end-to-end by `test/tests/test_tcl_array_flow.py`
(mid tier).

## Options

```tcl
buda::start ?-python <interpreter>? ?-server <path>? ?-echo 0|1? ?-viz 0|1?
buda::stop
buda::output      ;# the last command's output, echoed or not
buda::commands    ;# the command list the engine reported
buda::viz ?on|off?  ;# may `visualize` open a window; no arg = ask
buda::do <cmd> ...  ;# send a raw command line (the generic form)
```

`-echo 0` keeps command output off the terminal; it is still available from
`buda::output`.

## The viewer

`buda::visualize` opens the same window `visualize` opens in a `.buda`
script, and it **blocks** the same way — the run stops until you close it.
A GUI that must stay live sends it as `buda::async visualize`; a batch flow
declares itself with `buda::start -viz 0` (what `flow/tcl/corpus/harness.tcl`
does — 31 of the 41 corpus flows end in `visualize`, and a sweep that opened
31 windows would never finish).

Whenever a `visualize` command opens **no** window it now says so, with
`BUDA-1903` and the reason:

```
BUDA-1903: INFO: visualize: no window opened: visualization is off for this
           session (--no-viz, or buda::start -viz 0)
BUDA-1903: WARNING: visualize: no window opened: matplotlib's backend (agg)
           cannot display a window — no display, or MPLBACKEND names a
           file-only backend
```

The severity is the difference between "you asked for this" and "nobody
asked for this", and the second one had no voice at all before: `plt.show()`
under a file-only backend is a silent no-op — matplotlib does not even warn
— so a run over `ssh` drew nothing and reported success.  The note also goes
to the flow log, which is where the question gets asked afterwards.

The server used to force visualization off unconditionally, which made
`visualize` the one command that did not mean from Tcl what it means in a
script.  It is on by default now; `buda::viz off` is how a client declines
it, at any point in the session.

`buda::start` sets stdout's encoding to UTF-8 (and `buda::stop` restores
it).  BUDA's diagnostics contain `→`, `µm` and box-drawing rules; on a host
with no locale set — a compute farm, typically — `tclsh` defaults stdout to
iso8859-1 and every one of those becomes `?`, so a correct tool reads as a
corrupt one.  We chose to emit UTF-8, so we take responsibility for the
channel we echo it on.

## Streaming, async, and cancellation

The synchronous face above is all a flow script needs.  A **GUI** driving
BUDA needs three more things — output as it happens, an event loop that
stays live, and a stop button — and they are one section because they
compose:

```tcl
buda::stream 1                    ;# output arrives as it is produced
buda::onprogress {my_log_pane}    ;# invoked with each chunk, sync or async

buda::async {ripup_reroute 30} -done {apply {{status output} {
    ...                           ;# status is OK/ERR/BYE/FATAL — an
}}}                               ;#   argument, not a raise: no caller
                                  ;#   is on the stack to catch one
buda::running                     ;# 1 while a command is in flight
buda::cancel                      ;# SIGINT to the engine; the command
                                  ;#   fails as an ordinary ERR, the
                                  ;#   session stays alive
set status [buda::wait]           ;# block (vwait) until it finishes
```

`buda::async` sends the command and returns immediately; frames are
assembled by a `fileevent` reader, so a Tk GUI's event loop keeps
dispatching while the engine works.  A sync `buda::<name>` call while a
command is in flight is refused loudly.  `buda::output` holds the same
whole text in every mode.

**Cancellation is cooperative, and the boundary is honest**: Python raises
`KeyboardInterrupt` at the next bytecode boundary, so a Python-level loop —
`source`, the healers' iterations — cancels promptly, while one long C++
call (a single `run_planner` on a huge design) returns before the interrupt
lands.  The cancelled command keeps whatever state it had committed,
exactly like any other failing command.  POSIX only (`exec kill`); on other
hosts `buda::cancel` errors rather than pretending.

## The protocol

Documented in full in `tools/buda_server.py`.  In short: one request line
per command, and a reply of `<STATUS> <n_chars>\n` followed by that many
characters of output, where `STATUS` is `OK`, `ERR`, `BYE` or `FATAL`.
Character counts, not bytes: both sides speak UTF-8 and count code points.

Five requests are the server's own rather than script commands:
`__commands` (the registry, which is how `buda::<name>` procs are minted
with no second list to keep in step), `__query <name>`, `__stream on|off`,
`__viz [on|off]`, and `__exit`.

With streaming opted in (`__stream on`), a reply is zero or more
`OUT <n_chars>\n<payload>` progress frames followed by exactly one final
status frame carrying the not-yet-streamed tail — concatenating them yields
exactly the buffered-mode output.  A client that never opts in never sees
an `OUT` frame, so the one-frame form above remains the whole contract for
existing flows.  Cancellation is deliberately NOT a protocol feature:
POSIX already has one (SIGINT), and the server defers it while a frame is
on the wire so a cancel cannot tear a frame in half.

A write directly to file descriptor 1 inside the engine — as opposed to
Python's `sys.stdout` or C++'s `std::cout`, both of which are captured —
**cannot corrupt the channel**: at startup the server duplicates fd 1 to a
private descriptor the protocol alone writes, and repoints fd 1 at stderr.
A library that writes the raw descriptor lands beside the diagnostics —
visible, in order, outside every frame — instead of inside the
conversation.  ("Nothing in BUDA does" was true, but it was a promise about
other people's code; now it does not need to be one.)

## Design iteration: a decision that survives the process

`array.tcl` opens `:memory:`, so everything it decides dies with it — which
is how a candidate picked by hand in the explorer gets lost between runs.
`array_save.tcl` and `array_resume.tcl` are the same design against a FILE,
split into the two sessions a person actually works in:

```bash
tclsh flow/tcl/array_save.tcl 3 2                        # build, route, look
BUDA_ARRAY_VIZ=1 tclsh flow/tcl/array_save.tcl 3 2       # ...with the viewer

BUDA_ARRAY_PIN=diag=14 tclsh flow/tcl/array_resume.tcl   # re-plan with a choice
tclsh flow/tcl/array_resume.tcl                          # again; the pin holds
```

Session 2 declares the technology and **nothing else**: no cells, no buses,
no bundler, no generation. `load_pipeline` brings back the bundles, every
candidate topology, the planner's selection and layers, the pre-plan pins and
the abstract routing, and the flow starts at the planner — a design iteration
re-plans, it does not rebuild. Both sessions get their stack and geometry
from `array_lib.tcl`, because a checkpoint is worth nothing if the session
reopening it declares a different design.

**A pin is the durable form of a choice.** `buda::select_topology` writes
`topology.is_pinned` the moment it is applied, so it survives the save, binds
the next session's planner, and stays in force until it is changed —
measured: unpinned this design plans `diag` as `TRUNK_V@x240`, pinned to 14
it keeps `TRUNK_H+MST@y480` through the resume, the re-plan, and the
checkpoint after that. A pin made **in the explorer** is a preview by
design — the viewer's re-run buttons never write a checkpoint — so bring the
title bar's `topo N/n` back as `BUDA_ARRAY_PIN=<bus>=N`. That N is 1-based
and is exactly what `select_topology` takes; `dump_topologies` numbers its
own table from **0**, so add one when reading it there.

Known gap, worth knowing before trusting a resumed number: the per-bit
**fan-in taper is not restored**, so a resumed CONVERGENT/DIVERGENT bundle
comes back untapered and routes wider than the design that was saved (this
vehicle: 88 → 106 bit-wires, all of it in the two fan-in bundles, both
endpoints clean). Details and the measurement in
[BDB_REFERENCE.md](BDB_REFERENCE.md#load_pipeline).

## The corpus: the same 41 flows, driven from Tcl

`flow/tcl/corpus/` is the QoR corpus translated to Tcl — every flow
`tools/qor_corpus.py` runs through the CLI, run instead through a real
`tclsh` and the pipe:

```bash
tclsh flow/tcl/corpus/rnr/mix.tcl            # one flow
tools/tcl_corpus.py -j 4 --out tcl.json      # the sweep
tools/qor_corpus.py --compare cli.json tcl.json
```

The point is the **comparison**, not the coverage: a `.buda` script and its
translation say the same things to the same engine, so any difference in the
routed result is a fault in the bridge.  The two sides are kept in step by
`tools/buda2tcl.py`, which generates the tree (a hand-written translation
would drift from its original the first time anyone edited the `.buda`), and
by `test_tcl_corpus.py`, which regenerates in memory and fails if what is
committed no longer matches.

Each translated flow reports one machine-readable line in
`qor_corpus.py --out`'s schema, so the existing `--compare` does the diffing:

```
QOR {"flow": "flow/rnr/mix.buda", "overlaps": 0, "unplaced": 0, ...}
```

### The result, measured 2026-08-11

41 flows, ~13.5 k commands over the pipe, against a CLI sweep of the same
corpus **on the same build**:

```
0 better, 0 worse, 41 unchanged (of 41 flows).  Metric = overlaps/unplaced/viol_bundles.
  abstract WL (after NUTS)        19,623,671 ->      19,623,671  (+0, +0.00%)
  detailed WL (after DNUTS)      385,795,633 ->     385,795,633  (+0, +0.00%)
```

Field by field it is **41/41 exact** on all five metrics.  The wirelengths
carry the weight of that claim: three small integers can agree by luck, and
385 million units of routed metal cannot.

Against the checked-in nightly snapshot (`qor/qor_table_rows.json`, then 5
days old) 35 of 41 matched outright, and all 8 remaining deltas are `main`
moving underneath, not the bridge — **today's CLI reports exactly what
today's Tcl reports** in every one of them.  That is why the same-build
sweep is the comparison that means something: run only against the snapshot,
a driver fault and a week of drift look identical.

The cost of the round trip, measured serially on the highest-command flow
(`big.buda`, 2909 commands, three runs): **0.35–0.42 s, ≈0.13 ms per
command**, engine spawn included.  It scales with the number of COMMANDS,
not the size of the design — the sweep's chip-scale flows issue a few dozen
commands each and pay nothing measurable, while a flow that declares
thousands of nets one line at a time pays a few tenths of a second.

Two things this corpus found on its first run, both invisible at the scale of
a hand-written example:

* **`buda.tcl` defined itself relative to the calling namespace.**  Sourced
  from inside one — `namespace eval myapp { source buda.tcl }`, which is what
  a GUI does — the package landed at `::myapp::buda::*` and failed later,
  somewhere far from the cause.  The namespace and every proc are now
  absolute (`::buda::`).
* **The engine is a child process and inherits the directory it was SPAWNED
  in.**  A `cd` after `buda::start` moves the interpreter and leaves the
  engine behind, so every path-taking command (`open_bdb mix.bdb.sql`)
  resolved against wherever the sweep was launched.  The harness now cds
  first — and a test pins that order rather than the comment alone.
