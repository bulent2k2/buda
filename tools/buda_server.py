# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""BUDA as a command server, so a real Tcl interpreter can drive it.

Phase 5 of docs/internal/lefdef_interface_plan.md.  EDA flows are written in
Tcl; a tool that can only be driven by its own script language is a tool
every site has to wrap before it can use it, and each of those wrappers is
written once, badly.

**Why a server and not an embedded interpreter.**  The obvious build is
`tkinter.Tcl()` inside Python — a few lines, and it was the first draft.  It
is the wrong way round twice over:

  * it puts BUDA's Python in charge and asks the site's flow to run *under*
    it, which is the opposite of integrating into a flow that already exists
    and already has its own variables, procs and libraries; and
  * it makes the Tcl front end depend on **tkinter** — a GUI toolkit — so a
    headless compute farm, which is exactly where flows run, may not have it.
    (This container is such a machine: `tclsh` is installed and `tkinter` is
    not.)

So the arrangement is inverted.  The site's own `tclsh` is the parent, it
sources `tools/buda.tcl`, and BUDA runs as a child process behind a pipe.
Nothing about the site's interpreter changes.

**The protocol** is deliberately dull, because a protocol that needs
explaining is a protocol that gets out of sync:

    request   <one line of .buda script>\\n
    response  <STATUS> <n_chars>\\n<n_chars characters of output>

`STATUS` is one of four, and the fourth is there because three were not
enough:

    OK     the command succeeded; the session is alive
    ERR    the command failed; the session is alive
    BYE    the session ended cleanly (`exit`, `exit 0`, end of input)
    FATAL  the session ended BECAUSE the command failed

`FATAL` is the fail-fast case — `add_block a 0 0 10.5 10`, a malformed
argument that ends a `.buda` run on the spot.  Folding it into `BYE` made a
crash indistinguishable from a clean finish, so a Tcl flow sailed past it and
then failed later with "the engine exited unexpectedly", pointing at the
wrong command.  Keeping BUDA's own fail-fast semantics matters more than
being gentle here: a command must mean the same thing from Tcl as it does in
a script, and in a script this ends the run.

Character counts, not byte counts: both sides speak UTF-8 and count code
points, so the diagnostics' `µm` and `→` survive the trip.

**Streaming** (opt-in, `__stream on`): a long command's output is then sent
as it is produced — zero or more `OUT <n_chars>\n<payload>` frames, followed
by exactly ONE final status frame as before.  The final frame carries only
the not-yet-streamed tail, so a client that concatenates OUT payloads with
the final payload holds exactly the sync-mode output (the Tcl package does,
and `buda::output` is identical in both modes).  A client that never opts in
never sees an OUT frame, so the one-frame protocol above remains the whole
contract for existing flows.

**Cancellation** is not a protocol feature, deliberately: POSIX already has
one.  The client sends SIGINT to the server process (Tcl knows the pipe's
pid); Python raises `KeyboardInterrupt` at the next bytecode boundary, the
command's `BaseException` handler turns it into an ordinary `ERR` reply, and
the session stays alive with whatever state the command had committed — the
same contract as any other failing command.  The boundary is honest:
Python-level loops (`source`, the healers' iterations) cancel promptly,
while one long C++ call (a single `run_planner` on a huge design) returns
before the interrupt lands.  SIGINT is blocked while a frame is being
written, so a cancel cannot tear a frame in half.

Five requests are the server's own rather than script commands:

    __commands           the command registry, space-separated
    __query <name>       one scalar about the session (see `_QUERIES`)
    __stream on|off      stream command output as OUT frames (default off)
    __viz [on|off]       may `visualize` open a window (default on); no
                         argument reports the current setting
    __exit               shut down

`__viz` exists because a window BLOCKS — `visualize` in a `.buda` script
holds the run until the viewer is closed, and it means the same thing here.
A batch driver (the corpus harness) says `off` the way a batch CLI run says
`--no-viz`; an interactive one leaves it on and gets a viewer.

`__commands` is what makes the two sides stay in sync automatically: the Tcl
package asks for the list at startup and defines one proc per name, so a
command added to `src/buda_cmds/` is callable from Tcl with no second list to
maintain — the drift a hand-written binding always eventually has.
"""
import contextlib
import io
import os
import signal
import sys


def claim_protocol_channel():
    """Make a direct fd-1 write harmless BY CONSTRUCTION.

    The protocol's one documented fragility: Python's `sys.stdout` and C++'s
    `std::cout` are both captured during a command, but a library writing
    straight to file descriptor 1 would land in the middle of a frame and
    desynchronise the channel.  Nothing in BUDA does — the note said so —
    but "nothing does today" is a promise about other people's code.

    So the channel stops BEING fd 1: the descriptor is duplicated to a
    private fd the protocol alone writes, and fd 1 is repointed at stderr.
    A rogue write to fd 1 now lands beside the diagnostics — visible, in
    order, and outside every frame — instead of inside the conversation.
    Returns the protocol stream; falls back to plain stdout if the dup
    fails (an exotic host is better served than none).
    """
    try:
        proto_fd = os.dup(1)
        os.dup2(2, 1)
        proto = os.fdopen(proto_fd, "w", encoding="utf-8", newline="\n")
    except OSError:
        return sys.stdout
    # sys.stdout still wraps fd 1, which is now stderr: command output is
    # captured anyway, and anything the interpreter itself prints between
    # commands lands visibly rather than in a frame.
    return proto


# Claimed BEFORE the engine imports below, and only when running AS the
# server (an importer — the tests' protocol drivers — claims explicitly
# when it wants to): the imports initialize the compiled extension and its
# native dependencies, and module-init output written straight to fd 1
# would already be queued in the pipe by the time main() ran — parsed by
# the client as the response header to its first request (Codex P2 on
# #681).  The window between exec and this line is the OS's, not ours.
_PROTO_OUT = claim_protocol_channel() if __name__ == "__main__" else None

_HERE = os.path.dirname(os.path.abspath(__file__))
# All this has to do is REACH `buda_cli`; its own bootstrap then puts the rest
# of the Python layer on sys.path (see `buda_runtime.paths()`).  Deliberately
# not a second copy of that rule: a checkout spreads the layer over
# build/src/tools while an installed wheel folds it into one directory, so a
# copy here would be exercised in one layout and drift in the other.  In a
# checkout `buda_cli` is in the sibling `src/`; in a wheel it is one directory
# up, since `tools/` is installed inside `buda_runtime/`.
for _p in (os.path.join(_HERE, "..", "src"), os.path.dirname(_HERE)):
    _p = os.path.normpath(_p)
    if os.path.isfile(os.path.join(_p, "buda_cli.py")) and _p not in sys.path:
        sys.path.insert(0, _p)

# `buda_cli` FIRST, and the order is load-bearing: its bootstrap is what puts
# `build` on sys.path for the `import buda` below.  Under `bin/buda` the
# wrapper's PYTHONPATH hides that, but `python3 tools/buda_server.py` in a bare
# shell depends on it — and that is the environment nothing tests.  `noqa:
# E402` says "not at the top of the file"; it does not say "do not reorder",
# so an import sorter (or a reader tidying two adjacent `import buda*` lines
# back into alphabetical order) would break it silently.
import buda_cli                                             # noqa: E402
import buda                                                 # noqa: E402
import buda_diag                                            # noqa: E402
from buda_cmds import COMMANDS                              # noqa: E402


def _n_bundles(s):
    return len(getattr(s, "bundles", []) or [])


def _n_blocks(s):
    return len(s.fp.get_all_blocks()) if getattr(s, "fp", None) else 0


def _n_nets(s):
    return s.netlist.size() if getattr(s, "netlist", None) else 0


def _n_overlaps(s):
    r = getattr(s, "nuts_result", None)
    return int(r.num_overlaps) if r is not None else -1


def _n_unplaced(s):
    r = getattr(s, "detailed_result", None)
    return int(r.num_unplaced) if r is not None else -1


def _n_violations(s):
    # The MOST RECENT check_design's violation count — the audit leg of
    # cleanliness, which overlaps/unplaced do not cover: a design can place
    # every bit overlap-free and still be electrically wrong (SEG_OPEN,
    # BIT_SHORT, LAYER_DIR...), and a flow gating on the other two queries
    # alone would call that clean (Codex P1 on #679).  -1 when no audit has
    # run, and -1 too when the last one could not run at all (ok=False):
    # both mean "nothing was demonstrated", which a gate must not read as 0.
    audits = getattr(s, "_audits", None) or []
    last = audits[-1] if audits else None
    if not last or not last.get("ok", True):
        return -1
    return int(last.get("violations", 0))


def _messages(_s):
    # `{id severity}` pairs — a Tcl list of two-element lists, so a flow can
    # `foreach {m} [buda::query messages] { ... }` without parsing text.
    return " ".join(f"{{{mid} {sev}}}" for mid, sev, _t in buda_diag.catalogue())


# The scalars a flow script actually branches on.  Deliberately few: this is
# a bridge, not a second API, and every name here is a promise to keep.
# A count that has not been computed yet answers -1 rather than 0, because
# "no NUTS result" and "no overlaps" are opposite conclusions.
_QUERIES = {
    "bundles": _n_bundles,
    "blocks": _n_blocks,
    "nets": _n_nets,
    "overlaps": _n_overlaps,
    "unplaced": _n_unplaced,
    "violations": _n_violations,
    "messages": _messages,
}


@contextlib.contextmanager
def _sigint_deferred():
    """Hold SIGINT while a frame is on the wire.

    A cancel (SIGINT from the client) may arrive at any bytecode boundary —
    including between writing a frame's header and its payload, which would
    desynchronise the channel the ERR reply has to travel on.  Deferring
    delivery for the microseconds a frame write takes closes that window;
    the interrupt still lands, one frame later.  No-op where the platform
    has no pthread_sigmask (Windows), where the cancel path does not exist
    either.
    """
    if not hasattr(signal, "pthread_sigmask"):
        yield
        return
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


class _StreamCapture(io.TextIOBase):
    """A capture that FORWARDS as it fills (the `__stream on` mode).

    Complete lines are emitted immediately as `OUT` frames; a partial line
    is held until its newline arrives (or the command ends), so a frame
    never splits a diagnostic mid-word.  The full transcript is kept too —
    the error-line scan and the final frame's tail need it.
    """
    def __init__(self, emit):
        self._emit = emit          # callable(text) -> writes one OUT frame
        self._all = []             # the full transcript, for getvalue()
        self._pending = ""         # the unterminated tail line

    def writable(self):
        return True

    def write(self, s):
        s = str(s)
        if not s:
            return 0
        self._all.append(s)
        chunk = self._pending + s
        nl = chunk.rfind("\n")
        if nl < 0:
            self._pending = chunk
        else:
            self._emit(chunk[:nl + 1])
            self._pending = chunk[nl + 1:]
        return len(s)

    def getvalue(self):
        return "".join(self._all)

    def tail(self):
        """What was never streamed — the final frame's payload."""
        t = self._pending
        self._pending = ""
        return t


class Server:
    def __init__(self, session=None, out=None):
        self.session = session or buda_cli.BudaSession()
        # Deliberately NOT `no_viz = True`.  It was, and that quietly made
        # `visualize` the one command that does not mean from Tcl what it
        # means in a script: it returned OK, opened nothing, and said nothing
        # — on a workstation with a perfectly good display.  A client that
        # wants the batch behaviour asks for it (`__viz off`, which is what
        # `buda::start -viz 0` sends), exactly as a batch CLI run passes
        # `--no-viz`; and when there is no display to open on, the command
        # itself now says so (BUDA-1903) instead of succeeding silently.
        self.out = out or sys.stdout
        self.stream = False
        # -v twin (set by `__viz_final on`, which `buda::start` sends when the
        # flow was launched with `btcl -v` / `tclsh …tcl -v`): open a viewer on
        # the finished design at `__exit` (buda::stop) even if the flow never
        # called `visualize`, unless it already ended by visualizing.
        self.viz_final = False

    # ── framing ────────────────────────────────────────────────────────────
    def _reply(self, status, text=""):
        with _sigint_deferred():
            self.out.write(f"{status} {len(text)}\n")
            self.out.write(text)
            self.out.flush()

    def _out_frame(self, text):
        # A progress frame.  Emitted from INSIDE a running command (the
        # stream capture calls this as lines complete), so the SIGINT hold
        # matters most here: this is where a cancel is likeliest to land.
        self._reply("OUT", text)

    # ── shutdown ───────────────────────────────────────────────────────────
    def _viz_final_output(self):
        """The -v viewer on the FINISHED design; returns what it printed.

        Empty when viz-final is off, the session is headless by request, or the
        flow already ended by visualizing — the same rule the CLI's `-v` uses,
        read from the same `_flow_ends_by_visualizing()`.

        Called from BOTH ways a session can end, and that is the point of it
        being a method.  `__exit` (buda::stop) is the tidy one; a flow that ends
        with the engine's own `exit` command — `buda::exit`, which the Tcl
        bridge defines from the command registry like any other — raises
        SystemExit inside `handle`, which replies BYE/FATAL and terminates the
        server there and then.  `buda::stop` afterwards finds the pipe already
        closed and never sends `__exit`, so a viewer that lives only in that
        branch is a viewer `btcl -v` loses for every flow using the documented
        exit command.  Measured before the fix: `buda::stop` printed BUDA-1903,
        `buda::exit` printed nothing at all.

        The window BLOCKS until closed (as `visualize` does anywhere), so the
        reply — and with it buda::stop or buda::exit — waits for it.
        """
        if not (self.viz_final and not self.session.no_viz
                and not self.session._flow_ends_by_visualizing()):
            return ""
        cap = io.StringIO()
        try:
            with contextlib.redirect_stdout(cap), \
                 contextlib.redirect_stderr(cap), \
                 buda.ostream_redirect():
                self.session.run_command("visualize")
        except BaseException as e:                        # noqa: BLE001
            # REPORTED, not swallowed.  A viewer that fails to open — a broken
            # backend, a render that raises — otherwise gives the user neither
            # the window they asked for nor a reason, and the reply is an empty
            # clean BYE.  The CLI's `-v` prints the same warning; this is the
            # one place the two paths could drift and say different things
            # about the same failure.  The flow's own outcome is untouched:
            # this only ADDS a line, and never changes the reply's status.
            cap.write(f"\nWarning: -v visualize failed: "
                      f"{type(e).__name__}: {e}\n")
        return cap.getvalue()

    # ── one request ────────────────────────────────────────────────────────
    def handle(self, line):
        """Run one request.  Returns False when the session should end.

        Every path replies exactly once — a request with no reply would hang
        the Tcl side on its blocking read, which is the one failure mode a
        pipe protocol must not have.
        """
        req = line.strip()
        if not req:
            self._reply("OK")
            return True
        if req == "__exit":
            # -v twin: a viewer on the finished design before shutting down.
            # Empty string when there is nothing to add, so an ordinary stop
            # is the same `BYE` frame with no payload it always was.
            self._reply("BYE", self._viz_final_output())
            return False
        if req == "__viz_final" or req.startswith("__viz_final "):
            parts = req.split(None, 1)
            mode = parts[1].strip().lower() if len(parts) > 1 else ""
            if mode == "":
                self._reply("OK", "on" if self.viz_final else "off")
            elif mode in ("on", "off"):
                self.viz_final = mode == "on"
                self._reply("OK", mode)
            else:
                self._reply("ERR", "usage: __viz_final [on|off]")
            return True
        if req == "__commands":
            self._reply("OK", " ".join(sorted(COMMANDS)))
            return True
        if req == "__viz" or req.startswith("__viz "):
            # Exact, not `startswith("__viz")`: the no-argument form REPORTS
            # the setting, so a mistyped `__vizz` would otherwise answer OK
            # with a plausible value instead of saying it was not understood.
            #
            # A window BLOCKS the session until it is closed (as it does in a
            # `.buda` run), so a batch driver turns it off rather than
            # discovering that on a machine that happens to have a display.
            parts = req.split(None, 1)
            mode = parts[1].strip().lower() if len(parts) > 1 else ""
            if mode == "":
                self._reply("OK", "off" if self.session.no_viz else "on")
            elif mode in ("on", "off"):
                self.session.no_viz = mode == "off"
                self._reply("OK", mode)
            else:
                self._reply("ERR", "usage: __viz [on|off]")
            return True
        if req.startswith("__stream"):
            parts = req.split(None, 1)
            mode = parts[1].strip().lower() if len(parts) > 1 else ""
            if mode not in ("on", "off"):
                self._reply("ERR", "usage: __stream on|off")
            else:
                self.stream = mode == "on"
                self._reply("OK")
            return True
        if req.startswith("__query"):
            parts = req.split(None, 1)
            name = parts[1].strip() if len(parts) > 1 else ""
            fn = _QUERIES.get(name)
            if fn is None:
                self._reply("ERR", f"unknown query {name!r}; known: "
                                   f"{', '.join(sorted(_QUERIES))}")
            else:
                self._reply("OK", str(fn(self.session)))
            return True

        cap = _StreamCapture(self._out_frame) if self.stream else io.StringIO()
        status, ended = "OK", False
        try:
            # Both redirects: the C++ engine writes to std::cout, and without
            # ostream_redirect that output would go straight to fd 1 and be
            # read by Tcl as a protocol frame — the whole conversation
            # desynchronised by a line of planner progress.
            with contextlib.redirect_stdout(cap), \
                 contextlib.redirect_stderr(cap), \
                 buda.ostream_redirect():
                self.session.run_command(req)
        except SystemExit as e:
            # `exit` is a script command and means the same thing here as in
            # a .buda file: this run is over.  A NONZERO code is the
            # fail-fast path, which is a failure and must not be reported as
            # a clean finish.
            code = e.code if isinstance(e.code, int) else 0
            ended = True
            status = "FATAL" if code else "BYE"
            if code:
                cap.write(f"\nthe engine stopped with exit code {code}\n")
        except BaseException as e:                    # noqa: BLE001
            # A raising command has NOT ended the session — the engine is
            # still here and its state is whatever the command left, exactly
            # as in an interactive .buda session.
            status = "ERR"
            cap.write(f"\n{type(e).__name__}: {e}\n")
        text = cap.getvalue()
        # A command that printed a diagnostic FAILED, even when it returned
        # normally: the handlers report most errors by printing `Error: …`
        # rather than raising.  Tcl's contract is that a failed command
        # raises, so a site's `catch` has to see those too.  The scan reads
        # the FULL transcript in both modes — streaming changes how output
        # travels, never what a command means.
        if status == "OK" and any(buda_cli._is_error_line(ln)
                                  for ln in text.splitlines()):
            status = "ERR"
        # -v twin, second shutdown path: the flow's own `exit` ends the session
        # HERE, and returning False below terminates the server before
        # buda::stop can send `__exit` — so the viewer has to open on this path
        # too or `btcl -v` silently does nothing for every flow that ends the
        # documented way.  Appended AFTER the error scan on purpose: the
        # viewer's note is about the viewer, and must not be able to turn a
        # clean run into an ERR (or a FATAL's exit code into anything else).
        viz = self._viz_final_output() if ended else ""
        # Streamed: everything up to the last newline already went out as
        # OUT frames; the final frame carries only the tail, and the client
        # concatenates.  Buffered: the final frame is the whole output.
        self._reply(status, (cap.tail() if self.stream else text) + viz)
        return not ended

    def serve(self, inp=None):
        inp = inp or sys.stdin
        while True:
            try:
                line = inp.readline()
                if not line:
                    return 0
                if not self.handle(line):
                    return 0
            except KeyboardInterrupt:
                # A cancel that raced its command's COMPLETION: the SIGINT
                # was deferred while the final frame went out (or arrived
                # while the server sat idle between requests), so there is
                # nothing left to cancel — the command finished and its
                # reply was sent.  handle() only converts an interrupt to
                # ERR while a command is RUNNING; here the late interrupt
                # would escape and kill the server right after the client
                # read an apparently successful reply (Codex P2 on #680).
                # Dropping it is the correct semantics of "too late".
                continue


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    # Claimed at module load when running as the server (see the top of the
    # file — before the engine imports); an importer that calls main() gets
    # the claim here, after its own imports, which is the best it can ask.
    out = _PROTO_OUT if _PROTO_OUT is not None else claim_protocol_channel()
    for stream in (sys.stdin, out):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass
    return Server(out=out).serve()


if __name__ == "__main__":
    sys.exit(main())
