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

An argument that **contains whitespace** is re-quoted on the way out, because
Tcl eats the source-level quotes before the generated proc runs:
`buda::open_bdb "my dir/ck.bdb"` arrives as ONE argument, and a bare join
would hand the engine `open_bdb my dir/ck.bdb` — read as the path `my`
followed by an unknown option.  Re-quoting is what makes the call mean what
was written; the engine understands a quoted path (see [Paths, and paths
with spaces](BUDA_SCRIPT_REFERENCE.md#paths-and-paths-with-spaces)), and the
alternative is not neutrality but corruption, since the argument boundary is
destroyed either way.  Whitespace-free arguments — every argument in every
existing flow — pass through untouched, so the wire line is byte-identical
for them.  `buda::do` is unaffected: it sends a raw command line, quotes and
all.

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
buda::vizfinal ?on|off?  ;# open a viewer at buda::stop even with no visualize
buda::log ?<flow>|off?   ;# summarize like `bin/buda`; no arg = the log path
buda::endreport     ;# the runtime summary `bin/buda` prints when a run ends
buda::do <cmd> ...  ;# send a raw command line (the generic form)
```

`-echo 0` keeps command output off the terminal; it is still available from
`buda::output`.

### `buda::log` — the terminal `bin/buda` gives you

`bin/buda` prints **one line per command** and files the full detail in
`<flow_dir>/log/<stem>_flow.log`.  That shape is not the CLI's: it is the
engine's, gated on a flow log being open — and until now only the CLI ever
opened one, so the same flow driven from Tcl printed every line of every
command.  Measured on `flow/big_data_test/bigHalf.buda`: **677 lines from
Tcl against the CLI's 51**, and no log afterwards to read the detail in.

```tcl
buda::log flow/big_data_test/bigHalf.buda   ;# arm — returns the log path
buda::source flow/big_data_test/bigHalf.buda
buda::endreport                             ;# runtime summary + the pointer
buda::log off                               ;# disarm — returns the path
```

Opt-in, and deliberately not what `buda::start` does: a Tcl flow issues its
commands one at a time and usually wants each one's output, so arming this by
default would change every existing flow's console.  It is the driver that
runs a WHOLE `.buda` flow in one go that wants the other shape — which is why
**`btcl -i` arms it for you** (see below).

Disarm around anything whose output *is* the point — `dump_topologies` at a
prompt — since while armed that output goes to the log and the terminal gets
the one-line abstract.  A re-arm **appends**, so one session is one log file.

## The viewer

`buda::visualize` opens the same window `visualize` opens in a `.buda`
script, and it **blocks** the same way — the run stops until you close it.
A GUI that must stay live sends it as `buda::async visualize`; a batch flow
declares itself with `buda::start -viz 0` (what `flow/tcl/corpus/harness.tcl`
does — 31 of the 41 corpus flows end in `visualize`, and a sweep that opened
31 windows would never finish).

### `btcl -v` — a viewer at the end without editing the flow

To eyeball a Tcl test vehicle without adding a `visualize` to it, launch it
with the **`bin/btcl`** wrapper and `-v` (the twin of `buda -v`):

```bash
btcl -v flow.tcl [args...]        # the flow's own args pass through untouched
BUDA_VIZ_FINAL=1 tclsh flow.tcl   # the same thing with no wrapper
```

`btcl -v` sets **`BUDA_VIZ_FINAL`**; `buda::start` reads it and turns on
**viz-final**, so the viewer opens on the finished design when the flow ends —
**unless the flow already ended by visualizing** (its last BUDA command was
`visualize`/`visualize_topologies`), in which case nothing is doubled. A GUI or
embedder can set the same behaviour directly with `buda::vizfinal on`. The
window blocks until you close it, exactly as `buda::visualize` blocks; with
`-viz 0` (or no display) the appended viewer reports `BUDA-1903` instead.

Both ways of ending a flow open it: `buda::stop`, and the engine's own
`buda::exit` (which ends the session before `buda::stop` can be reached).

### `btcl -j N` — worker threads for the parallel stages

The Tcl twin of **`buda -j`**: `-j N` (or `--threads N`, or the attached `-jN`
/ `--threads=N`, or **`-j max`**) sets the worker-thread count for the parallel
pipeline stages — planner candidate scoring, the NUTS per-layer solvers, and the
healers' trial sweep.

```bash
btcl -j 4 flow.tcl [args...]      # 4 worker threads
btcl -j max flow.tcl              # the whole machine maximum
btcl flow.tcl                     # no -j: half the maximum, as `buda` caps
BUDA_THREADS_REQUEST=4 tclsh flow.tcl   # the same thing with no wrapper
```

Like `-v`, the value is read **only before the script** — a `-j` among the
flow's own args passes through untouched — and travels to the child engine as
an environment variable (**`BUDA_THREADS_REQUEST`**) rather than an argv token.
`buda_server` resolves it through the same `buda_cli.configure_threads` the CLI
runs, so `btcl` and `buda` are **consistent**:

- **`-j N`** — that count, clamped (LOUD) to the machine's affinity- and
  quota-aware logical-CPU count, applied with EXPLICIT semantics (the per-engine
  `BUDA_PLAN`/`NUTS`/`SWEEP_THREADS` vars).
- **`-j max`** — the whole machine maximum, the explicit opt-out of the default.
- **no `-j`** — the launcher default: **half** the maximum, applied as the
  `BUDA_THREADS` ceiling (the engines' small-work gates intact) — the SAME
  policy a bare `buda` applies. (Before, a bare `btcl` left the engine's own
  uncapped auto count; the two launchers now match.)

The resolved count prints as a `[threads] N of M` line. Only the launchers
impose the default — a bare `tclsh flow.tcl` (no `btcl`) sends nothing and keeps
the engine's own auto count, byte-identical to before.

`buda::exit 3` still **fails**, and the viewer cannot change that: its note is
appended after the reply's status is decided, so a `FATAL` stays `FATAL`. What
it does *not* do is reach the shell — `buda::_request` turns a `FATAL` into a
Tcl error, and an unhandled Tcl error exits **1**, not 3 (measured). The
engine's code is in the error text; a flow that wants the process to carry it
has to say so:

```tcl
if {[catch {buda::exit 3} err]} { puts stderr $err; exit 3 }
```

This is the one place the Tcl twin differs from `buda -v`, where a trailing
`exit 3` really does exit 3 — the CLI owns its own process and the bridge does
not own `tclsh`'s.

**Wrapper options are read only before the script**, and `--` ends them
explicitly. Everything from the script onward is the flow's, in its original
position — including a `-v` of the flow's own, which is why the request travels
as an environment variable rather than an injected `$argv` token: Tcl is a
general language, and scanning `$argv` for a bare `-v` cannot tell a launcher's
request from an argument the flow wants for itself.

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

Eight requests are the server's own rather than script commands:
`__commands` (the registry, which is how `buda::<name>` procs are minted
with no second list to keep in step), `__query <name>`, `__stream on|off`,
`__viz [on|off]`, `__log <flow>|off`, `__script <path>|off`, `__end_report`,
and `__exit`.

`__script` (`buda::script`) declares **which script's commands are being
sent**, so the engine's own path rule applies to them: a relative path
resolves against that script's directory, and against the CWD only when no
script is running.  A driver that REPLAYS a flow's recorded lines one at a
time — `btcl -r`, whose whole job is that — is running no script, so
without it every relative path in them silently re-rooted at the CWD and
named a different file, or none.  It is deliberately separate from
`buda::log`, which also learns the flow: arming a *log* must not change what
a *path* means.

The root is armed **per line**, not once per replay, because a build trace
FLATTENS the source tree: a relative path in a `source`d file was resolved
against *that* file's directory.  So the recorder writes a `# origin:`
marker whenever the running script changes (a comment, so a recording stays
a replayable `.buda`), the trace carries them, and the replay arms each
line's own root.  A line whose text was recorded from two different roots
has no single right answer and falls back to the entry flow, as does any
trace written before the markers existed.

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
tclsh flow/tcl/array_save.tcl                            # build, route, look
BUDA_ARRAY_VIZ=1 tclsh flow/tcl/array_save.tcl           # ...with the viewer

BUDA_ARRAY_PIN=diag=14 tclsh flow/tcl/array_resume.tcl   # re-plan with a choice
tclsh flow/tcl/array_resume.tcl                          # again; the pin holds
```

The default 2 × 2 is clean, so every line above exits 0. Passing `3 2`
selects the vehicle's known-dirty shape (`array.tcl`'s documented antenna
residual) — still a valid checkpoint, and still resumable, but the flow's
self-check exits **1** on it by design, which will stop a `set -e` script at
the first line.

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
title bar's `topo N/n` back as `BUDA_ARRAY_PIN=<bus>=N`. Candidate ids are
**1-based everywhere** they are shown or typed — the title bar,
`dump_topologies`' `topo` column, `select_topology`, `edit_topology` — so the
number you read is the number you use.

### One file you just keep running: `design.tcl`

The pair above is the iteration loop split into named sessions; for working a
design by hand there is a form with nothing to remember:

```bash
bin/btcl -v flow/tcl/design.tcl      # session 1: build, route, iterate
bin/btcl -v flow/tcl/design.tcl      # session 2..N: resume, iterate more
```

`flow/tcl/design.tcl` is **one self-contained file** (technology, floorplan,
buses, pipeline) that decides which session it is by looking for its own
checkpoint (`BUDA_DESIGN_BDB`, default `flow/tcl/out/design.bdb`): missing
means BUILD, present means RESUME via `load_pipeline`, and
`BUDA_DESIGN_FRESH=1` starts over. Being one file is what guarantees two
sessions can never disagree about the design they are iterating on.

After the route it drops into a **prompt**: `topos d1` lists a bundle's
candidates, `explore d1` / `show` open the explorer / viewer (close the
window to come back), `pin d1 4` pins a candidate (1-based, durable at once),
`replan` re-runs planner → NUTS → detailed with the pins in force, `done`
saves and exits — auto-replanning first if any pin changed since the last
route, so the checkpoint's metal always belongs to the pinned candidates.
Anything else is passed to the engine **if its command registry knows it**
(`edit_topology …`, `dump_messages`, …); an unknown word is refused locally,
because the engine's own fail-fast on unknown commands would end the session
over a typo. Every engine error is caught and printed — a mistake costs a
message, never the session.

**`help` lists the verbs; `commands ?glob?` lists the engine's.** The verb
list is generated from the same table the dispatch switches on, so it cannot
go stale, and `commands` asks the running engine's registry rather than a
copy of it (`commands *topolog*` filters). Between them and `info commands
::buda::*` for the bridge's own procs, the three namespaces a session can
reach are all enumerable from inside it — the header comment in the script
was no use at the prompt, and the refusal message named four verbs.

A bundle selector — for `topos`, `explore`, `pin`, `unpin` — is whatever
`select_topology` takes, so **a bare integer is the bundle ID `check_design`
prints**: read `Bundle 8: Seg 1: 4 bit(s) — unplaced`, type `topos 8`, and the
header names the bus (`── bundle 8  nets=4 (n1_0…)`).

The loop itself lives ONCE, in [`tools/buda_prompt.tcl`](../tools/buda_prompt.tcl),
and both drivers call it (`prompt::run <tag> <ps> <route-proc> <bdb>
<example>`, returning `pins_dirty`). It was duplicated character-for-character
in `design.tcl` and `hdesign.tcl` — including the comments explaining why
`done` sits outside the `catch` and why a pin is marked dirty BEFORE the send —
and the copies had already drifted in their prose. The only real difference
was which `route` to call, which is now an argument.

The prompt reads stdin, so the interactive session and the scripted one are
the same code path — `echo "pin d1 4
done" | bin/btcl flow/tcl/design.tcl` is a complete iteration, EOF simply
finishes the session, and the exit code is the design's cleanliness. Each
session also writes a diffable `<bdb>.sql` snapshot, so iterations can be
compared instead of just replaced. Pinned by
`test/tests/test_tcl_design_iterate.py`.

### The hierarchical twin: `hdesign.tcl`

`flow/tcl/hdesign.tcl` is the same loop on a THREE-level design —
`flow/hbundles/08_cross_level.buda`'s chip/blk/leaf hierarchy, with every
kind of bundle a hierarchy holds: a cell-LOCAL D2 template (`b_lohi`, four
blk instances), same-level D0/D1 bundles, and seven CROSS-LEVEL buses
(blk↔leaf, chip↔leaf). At the prompt, `pin b_lohi 2` resolves through the
expansion map to the TEMPLATE, so one pin re-routes all four instances —
and it survives into the next session like any other pin.

What a hierarchy adds is planning **options**:

```bash
bin/btcl -v flow/tcl/hdesign.tcl -mode topdown|bottomup|hybrid \
         -reserve N -share cell=layer=pct        # -h prints the full header
```

`-mode` picks how cell-local interconnect is planned — `bottomup` marks
every eligible cell (`set_bottom_up *`), `hybrid` only the deepest cell with
local interconnect (`blk_cell`) — with `align_bottom_up` +
`check_template_tracks on_mismatch independent` declared for both, because
08's fixed geometry cannot fully phase-align (the moves auto-revert on the
parent-overlap guard) and the honest answer is the engine's own policy:
aligned instances copy the template's solve, the rest solve individually.
`-reserve 2` keeps M6+M7 for the top level (`reserve_top_layers` — the
governed `b_lohi` templates measurably drop from M6 to M4), and
`-share blk_cell=M6=50` leases half of a reserved layer back — the
wiring-limited escape valve (they come back up onto M6 through the lease).

The options split by what a RESUME may change: `-reserve`/`-share` are
planning POLICY — persisted, and changing them on a resume is the
experiment loop (a tightened cap VOIDS the restored plan loudly and the
session re-plans) — while `-mode` is a BUILD premise (alignment happens
before busterms are derived), so a resume with a different mode is refused
with the rebuild recipe. Pinned by `test/tests/test_tcl_hdesign_iterate.py`.

Building it exposed a checkpoint-clobber: a pin typed AFTER the route (the
prompt's whole point) persisted the EXPANDED per-instance wrapper list over
the template/replica rows, and the next session could not restore. Fixed in
the engine, not the vehicle — persists now source the pre-expansion view
(`_persist_wrappers`), `run_planner hier` snapshots it on a resumed session,
and the expansion-map pin/unpin branches mirror onto the original — so the
GUI explorer and any other driver get the same guarantee
(`test/tests/test_hier_pin_persist.py`).

### Any flow at all: `btcl -i <flow>.buda`

The two vehicles carry their own design and their own route recipe; the
generalization carries neither:

```bash
bin/btcl -i flow/hbundles/08_cross_level.buda     # any .buda flow
bin/btcl -i demo/comprehensive_demo.buda
```

`tools/buda_interact.tcl` runs the flow **verbatim** (the engine's own
`source`, so it means exactly what `bin/buda` makes it mean), then drops
into the same prompt.

The flow's **output** means what `bin/buda` makes it mean too: the driver
arms `buda::log` around the flow, so the console gets one line per command
and a runtime summary, and the detail goes to the same
`<flow_dir>/log/<stem>_flow.log` the CLI writes (`bigHalf`: 677 console lines
→ 58, with the log now written at all).  The arming follows what is running:
the **flow** and every **replay** (`replan`, a stage resume) are summarized;
the **prompt** is not, because a command you typed — `topos`, a raw
`dump_topologies` — is one whose output you asked to read.

A `.buda` flow handed to the wrapper **without** `-i` is refused with both
ways to run one, rather than passed to `tclsh`, which reads it as Tcl and
complains about whatever the flow's first line happens to be (an `invalid
command name "add_block"`, or — for a flow opening with `source` — Tcl's own
`source` failing on a path resolved against a different root).

What it knows about the flow it learns from the
**recorder** (`BUDA_RECORD` at `do_command` — loops unrolled, `source`
trees flattened), not from parsing its text:

- **hier vs flat** — a recorded `run_planner hier`;
- **the `replan` recipe** — the flow's own ROUTING TAIL: everything from
  the first `run_planner` on, minus what a replay must not repeat
  (viz/exports/dumps, pins and edits — a prompt unpin must not be fought
  by a replayed pin — and generation/bundling: an iteration re-plans, it
  does not rebuild). The flow's healers, checks, and reports replay with
  it, so `replan` heals the way the flow heals;
- **whether it routed** — no recorded `run_nuts` means nothing to
  verdict, so a deliberately partial flow exits 0 with a note instead of
  reading "never computed" as dirty. A **replay that stops partway** is a
  failure, not a finished route: the error re-raises through the prompt
  (which keeps the pins marked dirty), and a sticky flag fails the run
  even if the session then quits with clean pins — a verdict is never
  read off half-mutated state;
- **where its checkpoint lives** — the last file-backed `open_bdb`; with
  none, the driver says pins die with the session, because `save_bdb`
  snapshots the OPEN BDB (opening one after the fact would not backfill
  the rows the pipeline persists as it runs).  A flow that wants durable
  pins opens a BDB, as `flow/tcl/design.tcl` does — or you arm one from
  the command line without editing the flow:

  ```bash
  bin/btcl -i demo/comprehensive_demo.buda ckpt.bdb   # opened BEFORE the flow
  ```

**Back-to-back sessions work on the pin's durable form**: a `pin` at the
prompt writes `topology.is_pinned` through to the checkpoint at once, and
the next `btcl -i` on the same flow+BDB — a RERUN of the flow, not a
`load_pipeline` resume — re-attaches it onto the freshly regenerated
candidate pool by stable content uid (`_apply_bdb_pins`, the generation
tail's mirror of the sidecar baseline restore, running just before the
re-persist that used to wipe it), so the flow's own `run_planner` honors
the previous session's choice.  `unpin` is durable the same way.  Session
precedence is unchanged: a `select_topology` in the flow's own text wins
over the restored pin.

**Or skip the rebuild entirely — the optional STAGE argument** is the
`design.tcl`/`hdesign.tcl` RESUME recipe generalized to any flow:

```bash
bin/btcl -i mini.buda ckpt.bdb plan    # skip bundling + generation
bin/btcl -i mini.buda ckpt.bdb topo    # skip bundling only
bin/btcl -i mini.buda ckpt.bdb nuts    # keep the plan too
bin/btcl -i mini.buda ckpt.bdb dnuts   # keep abstract NUTS too
```

Both operands may contain **spaces** — quote them for your shell:

```bash
bin/btcl -i "my designs/mini.buda" "my designs/ck.bdb" plan
```

The driver reads the recorded `open_bdb` line with the engine's own rule
([Paths, and paths with spaces](BUDA_SCRIPT_REFERENCE.md#paths-and-paths-with-spaces)),
so the checkpoint it names and the trace it writes are the file you gave it.
Until #783 it split that line on whitespace: the `.trace` went missing and
every stage session afterwards refused, advising the build session that had
just run.  The resume recipe the banner prints is shell-quoted for the same
reason — it is meant to be typed back.

**The `-b` / `-r` / `-s` spellings** are the same machinery with the two
invented names taken off your plate — the checkpoint filename and the
stage ([Build & Resume Sessions](BUILD_RESUME.md) is the short practical
guide, with a flat and a hier demo vehicle):

```bash
bin/btcl -b mini.buda            # build; checkpoint auto-named
                                 #   <flow_dir>/<stem>.ckpt.bdb
bin/btcl -r mini.buda            # resume at the DEEPEST recorded stage
bin/btcl -r -s plan mini.buda    # ...or name the stage (-s alone implies -r)
```

`-b` **pre-flights the flow text first** (a `.buda` flow is a flat command
language — no branching — so a static scan in the engine's own reading
order, `source`-following and stopping where an `exit` stops the run, sees
the `open_bdb` sequence the run will execute), and decides by what it
finds.  A flow that opens its own **durable** BDB keeps it (`-b` arms
nothing and says so — an armed checkpoint would just be replaced by the
flow's own open); a flow that opens **none** gets the auto-named checkpoint
armed before it, and a re-run of `-b` re-arms the same file, so pins
persisted there re-attach to the rebuilt pool.  A flow whose LAST
`open_bdb` is **non-durable** (a `.sql` without `writeback`, or `:memory:`)
would route to the end and then discard everything (measured the expensive
way: a 2000-second heal, gone), so t=0 is where `-b` acts — and for the two
commonest such shapes it has something better than a refusal.  Both are
SINGLE-open only: the redirect request names one file, so with several opens
it could land on the wrong one.

**The read-only `.sql` input redirects.**  Many hier flows open an input
`.bdb.sql` *without* `writeback` — "read the design, never write it back".
The engine already materializes that open into a binary copy and persists
the whole pipeline into the copy; the only thing wrong with the copy was
its name (a throwaway temp).  When the flow's **only** `open_bdb` is that
shape, `-b` names the copy: the materialization lands in the checkpoint
(`BUDA_BDB_MATERIALIZE_TO`, popped by the first no-`writeback` `.sql`
open), the input stays read-only **by construction** — same code path, no
writeback source armed — and the copy survives the session, continuously
written, so even a killed run keeps its routed state.  The trace records
the input's path and checksum: a `-b` rerun with the input unchanged
**reuses** the checkpoint (pins re-attach to the rebuilt pool, exactly as
in the auto-armed case), a changed input re-materializes fresh — loudly,
pins discarded, since they would pin a different design's candidates — and
a `-r` resume rewrites the recorded open onto the checkpoint (re-opening
the input would materialize a fresh throwaway copy and restore nothing),
NOTing when the input changed since the build.  A missing input, or a flow
with **several** opens ending non-durable, are still refused at t=0, naming
the file and line — and the refusal now says which of the two it is, so a
multi-open flow is not left wondering why its `:memory:` was not redirected.

**`:memory:` redirects too** — the commonest shape in the tree (30 checked-in
flows).  `open_bdb :memory:` says two things at once: the design is *built*
here rather than read from a file, and nothing is kept.  Only the second is
a problem, and nothing about the flow needs it: the author simply had no
reason to name a file, and the refusal asked them to edit a working flow to
get a checkpoint.  So when a flow's **only** `open_bdb` is `:memory:`, `-b`
builds that same fresh database in the checkpoint instead
(`BUDA_BDB_MEMORY_TO`, popped by the first `:memory:` open), continuously
written as the pipeline runs, so even a killed run keeps its routed state.
The flow's text is untouched — run it without `-b` and its BDB is `:memory:`
again, leaving the checkpoint alone.

Where this **differs from the `.sql` redirect** is the rerun, and the
asymmetry is forced rather than chosen: a `.sql` open READS a design, so
reopening its materialization resumes it, while `:memory:` BUILDS one — the
flow's own `add_cell` / `add_inst` lines run again on the next `-b`, and
against a populated checkpoint that is a duplicate-instance error.  So a
`:memory:` checkpoint is rebuilt **fresh** every `-b`, its pins go with it,
and to keep pins you RESUME (`btcl -r`) rather than rebuild.  Both the
rebuild line and the closing banner say that outright instead of inheriting
the `.sql` case's "rerun and they hold", which would be simply false here.
The resume half needs no new machinery: the trace stamps `# input: :memory:`
(no checksum — there is no file to stamp, and the changed-input NOTE is
guarded on one), and the recorded `open_bdb :memory:` is rewritten onto the
checkpoint by the same rule that rewrites a recorded `.sql` input.

The build also stamps the flow text's checksum into the trace, and
a later `-r` NOTEs when the flow changed since — the resume replays the
*recorded* build — with `btcl -b` as the rebuild remedy.
`-r` finds the checkpoint at the auto name first, else through any
`*.trace` beside the flow whose `# flow:` header names it; zero found is a
refusal with the remedy, two or more is a question, never a guess.  And a
session whose pins have nowhere durable to land prints them at exit **as
flow text** (`select_topology d1 4` paste lines, committed edit
transactions included), so the experiment's outcome survives the session
either way: as checkpoint rows, or as source.

Every build session with a live file-backed checkpoint writes its recorded
trace beside it (`<ckpt.bdb>.trace`); a stage session replays only the
trace's SETUP portion, calls `load_pipeline` — which restores bundles,
every candidate, the plan and the PINS through the machinery built for
exactly this, so no rebuild-path restore is even involved — and re-enters
the flow's own recorded commands at the chosen stage.  What "setup" means
follows the vehicles: a FLAT flow's blocks and buses are session state and
replay wholesale (`design.tcl` re-declares them every session), while a
HIER flow's cells/instances/buses/busterms are IN the checkpoint (a
replayed `add_inst` is a duplicate-instance error, a re-derive would
renumber busterm ids the restored bundles reference), so only the
session-state verbs replay — stack, patterns, `add_blocks_from_bdb`
projections, layer policies — and the construction commands are held,
counted, and said.  Hier `topo`/`plan` restore the pre-expansion view
(the cuts that still run `run_planner hier`, whose expansion it feeds);
hier `nuts`/`dnuts` restore the POST-expansion view (`load_pipeline
expanded`) as an **INSPECTION session** — the quick look at a long
healer flow's routed result: the stage replays (DNUTS + its checks), the
verdict comes out, `topos`/`explore`/`show` all read the restored
routing — while pins, their raw-command bypass, edits and `replan` are
guarded with the remedy (their persist would write the expanded view
over the checkpoint's template rows; a `plan` resume is where the design
changes).

A cut **below the planner** additionally HOLDS the planner-dependent
commands (`ripup_reroute`, `negotiate_congestion`, `refine_selection`,
`run_planner post_nuts`), counted and named: `load_pipeline` restores
the plan, not the planner object, so they would refuse — and for a
HEALED checkpoint holding them is also the honest fast path, because a
healer commit is a full-pipeline state: the restored plan already
carries the healing, and re-solving NUTS/DNUTS from it reproduces the
healed endpoint without paying for the healers again.  Re-healing is
what a `plan` resume is for.

A stage without a prior build (no trace), a trace built by a DIFFERENT
flow, or a checkpoint the flow later replaced with `:memory:` are each
refused with the remedy.

The trace is written only beside a **durable** checkpoint — a binary
`.bdb`, or a `.sql` opened `writeback`.  A flow that opens a `.sql`
*without* `writeback` (a throwaway materialized copy) or `:memory:`, or
that ends in `exit` having replaced the armed BDB with its own, leaves
nothing to resume from: the build session says so at the end (a
`WARNING` that the routing was DISCARDED, with the cause), and a later
`<stage>` resume — the BDB present but no trace beside it — names that
cause rather than advising the build session that just produced it.  A
flow ending in `exit` is fine to resume **when its checkpoint is
durable**: the trace is written on the exit path too, so
`btcl -i <flow> <ckpt> <stage>` works without an interactive build
session in between.

**Two concurrent sessions on the same BDB cannot corrupt it**: SQLite
allows one writer, so the unlucky session fails LOUDLY — at the arming
open ("cannot open the armed BDB", another session holding it) or
mid-flow ("the flow failed: … database is locked") — while the file stays
consistent (`pragma integrity_check` ok) and a follow-up session runs
clean.  Concurrent *iteration* on one checkpoint is a conflict by
construction (each session rebuilds the same tables); give each session
its own BDB path when you want them in parallel.

The prompt itself is **`tools/buda_prompt.tcl`** — one source, every
driver: `design.tcl`, `hdesign.tcl`, and `btcl -i` all call
`prompt::run`, so a pin means the same thing (durable at once,
coherence auto-replan on `done`, registry-gated raw pass-through, typo
tolerance) no matter which door you came in through. A Tcl flow given to
`-i` is refused with a pointer — it runs its own `buda::start` and can
source the prompt lib itself, exactly as the vehicles do. Pinned by
`test/tests/test_tcl_interact.py`.

This pair is also what measured the **fan-in taper gap**, now fixed: a
resumed CONVERGENT/DIVERGENT bundle used to come back untapered and route
wider than the design that was saved (this vehicle: 88 → 106 bit-wires, all
of it in the two fan-in bundles, both endpoints clean, nothing said). Since
schema v27 the per-bit endpoints ride the checkpoint and the taper is
re-derived on load, so a continuation reproduces it exactly — same
`route_snapshot` hash. A checkpoint written before v27 still resumes
untapered and now says so (`BUDA-1904`). Details in
[BDB_REFERENCE.md](BDB_REFERENCE.md#load_pipeline).

## The other direction: a Tcl run as the `.buda` it ran

`tools/buda2tcl.py` reads a `.buda` and rewrites it; the reverse cannot work
that way. `.buda` is a command list, but Tcl is a general programming
language, so which commands a flow issues is not a property of its **text**:

```bash
tclsh flow/tcl/array.tcl 3 2      # 18 bundles
tclsh flow/tcl/array.tcl 4 5      # 62 bundles, same file
```

A static translator would have to resolve `$argv`, the loops, `expr`,
`catch`, and a `source` chosen at run time — i.e. be a Tcl interpreter. So
`tools/tcl2buda.py` uses the one that already exists: it **runs the flow and
records what reached the engine**.

```bash
tools/tcl2buda.py flow/tcl/array.tcl 3 2 -o /tmp/array_3x2.buda --verify
```

That is cheap because the translation has already happened by then. A
command arrives at the engine as the flat line it became —
`buda::add_bus "bh_${R}_${C}\[4\]" $t/l_0_0.out …` is
`add_bus bh_0_1[4] t_0_1/l_0_0.out …` — so `BUDA_RECORD=<path>` just writes
it down, at `BudaSession.do_command`, the choke point **every** driver
passes through. The same mechanism records a `.buda` run (flattening its
`source` tree) or the web server; nothing in it is Tcl-specific.

What comes out is a **flattening**, which is the honest description: one
concrete design, straight-line, loops unrolled, branches resolved to
whichever way they went, the parameterization gone. That is what "the
`.buda` for `array.tcl 3 2`" can mean. It does not round-trip back to a
program.

`--verify` replays the recording through the CLI and compares the measured
result — for the 3 × 2 array, identical on abstract WL, detailed WL,
bit-wires and overlaps. Two caveats it reports rather than hides: a flow
that used **relative paths** only replays from the directory it ran in (a
`.buda` resolves them against its own directory — the recording's `# cwd:`
header says which, and a failed replay prints the remedy), and a flow run
with `-echo 0` prints no metric to compare against, so the replay is checked
only for having run clean.

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
