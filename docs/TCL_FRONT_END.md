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
buda::start ?-python <interpreter>? ?-server <path>? ?-echo 0|1?
buda::stop
buda::output      ;# the last command's output, echoed or not
buda::commands    ;# the command list the engine reported
```

`-echo 0` keeps command output off the terminal; it is still available from
`buda::output`.

`buda::start` sets stdout's encoding to UTF-8 (and `buda::stop` restores
it).  BUDA's diagnostics contain `→`, `µm` and box-drawing rules; on a host
with no locale set — a compute farm, typically — `tclsh` defaults stdout to
iso8859-1 and every one of those becomes `?`, so a correct tool reads as a
corrupt one.  We chose to emit UTF-8, so we take responsibility for the
channel we echo it on.

## The protocol

Documented in full in `tools/buda_server.py`.  In short: one request line
per command, and a reply of `<STATUS> <n_chars>\n` followed by that many
characters of output, where `STATUS` is `OK`, `ERR`, `BYE` or `FATAL`.
Character counts, not bytes: both sides speak UTF-8 and count code points.

Anything that writes directly to file descriptor 1 inside the engine — as
opposed to Python's `sys.stdout` or C++'s `std::cout`, both of which are
captured — would land in the middle of a frame.  Nothing in BUDA does; it is
noted here because it is the one way to break the channel.
