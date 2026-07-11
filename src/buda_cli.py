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

import argparse
import faulthandler
import os
import re
import sys
import time

# Ensure the compiled extension is loaded from build/ rather than a stale
# copy that might exist alongside this script.
_build = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'build'))
if _build not in sys.path:
    sys.path.insert(0, _build)

# tools/ holds bdb_serialize (used by open_bdb to load *.bdb.sql text fixtures).
_tools = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'tools'))
if _tools not in sys.path:
    sys.path.append(_tools)

# On macOS the native 'macosx' backend can intermittently segfault,
# especially with the IPC timer or when multiple windows open.
# Force TkAgg to ensure stability.
if sys.platform == 'darwin':
    import matplotlib
    matplotlib.use('TkAgg')

import buda

faulthandler.enable()
from buda_cmds import COMMANDS
from buda_session import (PersistMixin, HierMixin, NutsFlowMixin,
                          EditMixin, ReportsMixin, RipupMixin)
from buda_session.util import (_batched, _RR_DEFAULT_MAX_ITER,  # noqa: F401
                               _RR_MAX_CANDIDATES_PER_BUNDLE)   # compat re-exports

# Every command BudaSession.do_command() understands. Used to detect typos in
# scripts (e.g. 'add_layer' for 'def_layer') and suggest the closest match.
# Keep in sync with the dispatch chain in do_command().
# Derived from the command registry (src/buda_cmds/): a command exists iff
# it is dispatchable — the old hand-maintained literal cannot drift.
KNOWN_COMMANDS = frozenset(COMMANDS)


class _Capture:
    """A stdout/stderr replacement installed *per command* during a flow run.

    Buffers everything a command writes — Python prints and C++ output routed
    through sys.stdout via buda.ostream_redirect — so the CLI can persist the
    full detail to the flow log and derive a one-line terminal summary + runtime
    stats afterwards.  `fallback` supplies fileno()/isatty() for the rare bit of
    code that consults them, so fd-level writes still reach the real terminal.
    """
    def __init__(self, fallback):
        self.buf = []
        self._fallback = fallback

    def write(self, data):
        self.buf.append(data)
        return len(data)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return self._fallback.fileno()

    def getvalue(self):
        return ''.join(self.buf)


# Regexes that identify the "headline" line of a command's output — the one
# worth echoing to the terminal.  Matched against captured lines bottom-up; the
# last match wins, else we fall back to a line count / the last non-empty line.
_SUMMARY_MARKERS = [re.compile(p, re.I) for p in (
    r'\btotal\b.*(candidate|violation|wrapper|segment|bundle|move|net)',
    r'\b\d+\s+(hbundles|busterms|blocks|wrappers|candidates|net segments|'
    r'segments|bundles|nets|violation)',
    r'segments placed',
    r'bits unplaced',
    r'wrappers after expansion',
    r'materialized',
    r'\bsuccess\b',
    r'no opens',
    r'done:\s*metric',
    r'metric \d+->\d+',
    r'added \d+ blocks',
)]

# Commands that must NOT be redirected/timed: `source` is a container whose
# child commands are each summarized instead, and the visualize commands open
# interactive windows whose output belongs on the terminal.
_PASSTHROUGH_CMDS = frozenset({"source", "visualize", "visualize_topologies"})


def _strip_inline_comment(line):
    """Strip a `#` comment from a script line: everything from the first `#`
    that begins a token (start of line, or preceded by whitespace) to the end
    of the line is removed. This lets a command be commented out partially —
    `run_bundler # strict` runs `run_bundler`, `def_layer … 0.0 # note` drops
    the note. A `#` embedded in a token (no preceding whitespace, e.g. a path
    fragment) is left intact so it can't silently swallow real arguments."""
    for i, ch in enumerate(line):
        if ch == '#' and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


class BudaSession(PersistMixin, HierMixin, NutsFlowMixin, EditMixin,
                  ReportsMixin, RipupMixin):
    def _get_log_path(self, suffix):
        """Get the log path for a given suffix, ensuring the log directory exists."""
        if self.script_path:
            script_dir = os.path.dirname(self.script_path)
            script_stem = os.path.splitext(os.path.basename(self.script_path))[0]
            log_dir = os.path.join(script_dir, 'log')
            os.makedirs(log_dir, exist_ok=True)
            return os.path.join(log_dir, f"{script_stem}_{suffix}")
        else:
            log_dir = 'log'
            os.makedirs(log_dir, exist_ok=True)
            return os.path.join(log_dir, suffix)

    def __init__(self):
        self.fp = buda.Floorplan()
        self.netlist = buda.Netlist()
        self.layers = buda.LayerStack()
        self.bundler = buda.Bundler()
        self.planner = None
        self.bundles = []
        self._bundler_strategy = "STRICT"  # last run_[hier_]bundler strategy (for re-persist)
        self.nuts_result = None
        self._layer_overheads = {}   # layer_id -> overhead_percent
        self._planner_params  = {}   # param_name -> value (buffered before planner exists)
        self._net_endpoints   = {}   # net_name -> (driver_instance, [receiver_instances])
        self._layer_name_map = {}    # layer_name -> layer_id
        self._gds_label_layers = []  # def_gds_layer labels <csv> (import default)
        self._nuts_pitch = 1.0
        self._planner_pitch = None   # pitch the last run_planner reserved bands for
        self._detailed_bit_order = "LO_HI"
       # last track pitch used by run_nuts
        self._planner_iterations = 5 # last iteration count used by run_planner
        self._script_stack = []      # stack of absolute paths of sourced scripts
        self.script_path = None      # set when a .buda script is sourced
        self.routing_grid = None     # RoutingGridStack (stage 8)
        self.detailed_result = None  # DetailedNUTSResult (stage 9)
        self._dogleg_originals = {}  # bid -> pre-split selected_topology_index (restored on re-plan)
        self._dogleg_slot = {}       # bid -> appended candidate index holding the split topology
        # TopoEdit session (Phase E3b): edit_topology opens a working COPY of a
        # candidate (or an empty one); edit_* ops mutate it transactionally;
        # edit_commit appends it to the bundle's pool (uid-deduped, source
        # 'user'); edit_abort discards.  One session at a time.
        self._edit_w = None          # BundleWrapper being edited
        self._edit_topo = None       # the working Topology copy
        self._edit_src = ""          # description of what was opened
        self.no_viz = False          # set by --no-viz CLI flag
        self.verbose_conn = False    # set by --verbose-conn: print every per-bit violation
        self.ipc_verbose = False     # set by --ipc-verbose: surface buda_viz/def_viz IPC chatter
        self._die_w = 0.0            # stored by set_die when no BDB is open (flat flow)
        self._die_h = 0.0
        self.bdb = None              # BDB (opened by open_bdb command)
        self._bdb_writeback_src = None  # *.sql to write back to on save_bdb/exit (opt-in)
        self._bdb_writeback_bin = None  # temp binary materialized from that .sql
        self._bdb_added_ids = set()  # component ids loaded into fp via add_blocks_from_bdb
        self._busterm_gen = None     # BustermGen instance (created by derive_busterms)
        self.bdb_net_mode = False    # when True, add_net/add_bus also write to BDB
        self._corner_margin = (0, 0) # (dx, dy) — mirrors fp global corner margin
        # Mirrors the fp min-stub-length tiers, so derived hier floorplans
        # (cell-local / cross-level / depth projection) re-apply them via
        # _apply_fp_session_settings.
        self._min_stub = {"global": None, "dir": {}, "layer": {}}
        self._hier_expansion_map = {}  # original bundle id → [expanded BundleWrappers]
        self._hier_bundles_orig = []   # pre-expansion snapshot set by run_hier_bundler
        self._planner_is_hier = False  # True after `run_planner hier` (self.bundles is expanded)
        self._flow_log = None          # open flow-log file (set by main); enables per-command logging
        self._flow_log_path = None     # its path (for the "Full detail →" line)
        self._cmd_stats = []           # per-command (cmd_line, elapsed, nlines, nwarn, nerr) for runtime summary
        self._end_report_done = False  # runtime summary emitted (idempotent guard)
        self._at_last_command = True   # True while running the flow's final command
        self._at_tail_stack = []       # per-source-frame: was this source at its parent's tail?

    # ── Per-command logging / runtime stats ─────────────────────────────────
    def run_command(self, cmd_line):
        """Run one script command, routing its detailed output to the flow log
        and printing only a one-line summary (plus runtime) to the terminal.

        `do_command` stays the raw dispatcher (used directly by tests/tools);
        this wrapper is what the CLI flow drives so the terminal is not flooded
        with the same lines the log already captures.
        """
        stripped = _strip_inline_comment(cmd_line).strip()
        if not stripped:
            return
        cmd = stripped.split()[0].lower()

        # No flow log (interactive/embedded use), or a passthrough command:
        # run it directly with no redirect.  For `source` this means its child
        # commands recurse back through run_command and are each summarized.
        if self._flow_log is None or cmd in _PASSTHROUGH_CMDS:
            return self.do_command(cmd_line)

        real_out, real_err = sys.stdout, sys.stderr
        cap = _Capture(real_out)
        sys.stdout = sys.stderr = cap
        t0 = time.perf_counter()
        raised = None
        try:
            # ostream_redirect routes C++ std::cout/std::cerr to sys.stdout
            # (now `cap`), so even C++ output printed outside the inner
            # per-call redirects is captured to the log instead of leaking to
            # the terminal.
            with buda.ostream_redirect():
                self.do_command(cmd_line)
        except BaseException as e:   # incl. SystemExit from `exit`/fail-fast commands
            raised = e
        finally:
            elapsed = time.perf_counter() - t0
            sys.stdout, sys.stderr = real_out, real_err

        text     = cap.getvalue()
        lines    = text.splitlines()
        nonblank = [ln for ln in lines if ln.strip()]
        nlines   = len(nonblank)
        nwarn    = sum(1 for ln in lines if 'warning' in ln.lower())
        nerr     = sum(1 for ln in lines if 'error' in ln.lower())

        # Silent, instant setup commands (add_block, def_layer, set_*, …) are
        # not worth a terminal line or a log section — only surface commands
        # that produced output, took real time, raised, or reported a problem.
        significant = bool(nonblank) or nwarn or nerr or elapsed >= 0.02 \
            or raised is not None
        if significant:
            # Persist the full detail + a runtime line to the flow log …
            self._flow_log.write(f"\n━━━ {stripped} ━━━\n")
            self._flow_log.write(text if text.endswith('\n') or not text else text + '\n')
            self._flow_log.write(
                f"[runtime] {stripped}: {elapsed:.3f}s "
                f"({nlines} lines, {nwarn} warn, {nerr} err)\n")
            self._flow_log.flush()
            # … and a one-line abstract summary to the terminal.
            self._cmd_stats.append((stripped, elapsed, nlines, nwarn, nerr))
            headline = self._extract_headline(nonblank)
            self._emit_cmd_summary(real_out, stripped, elapsed, nlines,
                                   nwarn, nerr, headline)

        if raised is not None:
            raise raised

    @staticmethod
    def _extract_headline(nonblank):
        """Pick the most summary-like line from a command's (non-blank) output."""
        for ln in reversed(nonblank):
            if any(m.search(ln) for m in _SUMMARY_MARKERS):
                return ln.strip()
        if len(nonblank) > 3:
            return f"({len(nonblank)} lines)"
        return nonblank[-1].strip() if nonblank else ""

    @staticmethod
    def _emit_cmd_summary(out, cmd_line, elapsed, nlines, nwarn, nerr, headline):
        marker = 'x ' if nerr else ('! ' if nwarn else '  ')
        flags  = ''
        if nerr:  flags += f"[{nerr} err] "
        if nwarn: flags += f"[{nwarn} warn] "
        detail = (flags + headline).strip()
        if len(detail) > 68:
            detail = detail[:67] + '…'
        label = cmd_line if len(cmd_line) <= 34 else cmd_line[:33] + '…'
        out.write(f"{marker}{label:<34} {elapsed:6.2f}s  {detail}\n")
        out.flush()

    def print_runtime_summary(self, out):
        """Print a per-command runtime table (also to the flow log)."""
        if not self._cmd_stats:
            return
        total = sum(e for _, e, _, _, _ in self._cmd_stats)
        tw = sum(w for _, _, _, w, _ in self._cmd_stats)
        te = sum(x for _, _, _, _, x in self._cmd_stats)
        slowest = max(self._cmd_stats, key=lambda r: r[1])
        name = os.path.basename(self.script_path) if self.script_path else 'flow'
        lines = [f"\n═══════ Runtime summary ({name}) ═══════"]
        for cmd_line, elapsed, _nl, w, e in self._cmd_stats:
            tag = ' x' if e else (' !' if w else '')
            lines.append(f"  {cmd_line[:40]:<40} {elapsed:7.2f}s{tag}")
        lines.append(f"  {'':-<40} {'-'*8}")
        lines.append(f"  {'total (' + str(len(self._cmd_stats)) + ' commands)':<40} "
                     f"{total:7.2f}s")
        lines.append(f"  slowest: {slowest[0][:40]} ({slowest[1]:.2f}s)")
        if tw or te:
            lines.append(f"  {te} error line(s), {tw} warning line(s) — see the flow log for detail")
        text = '\n'.join(lines) + '\n'
        out.write(text); out.flush()
        if self._flow_log is not None:
            self._flow_log.write(text); self._flow_log.flush()

    def _print_end_report(self):
        """Emit the runtime summary + flow-log pointer exactly once.

        Called from main()'s finally AND right before a blocking GUI `show()`
        — but the latter only when the `visualize` is the flow's LAST command
        (`_at_last_command`). Rationale: launched through the macOS .app,
        closing the last window makes Cocoa terminate the process
        (quit-after-last-window) before the finally runs, so a trailing
        visualize's summary would never print — emitting it before that window
        opens makes it survive. An INTERLEAVED visualize (more commands follow)
        must NOT early-print: the process keeps running after that window
        closes, so the finally prints the complete summary. The idempotent guard
        keeps it to a single print on every path."""
        if self._end_report_done:
            return
        self._end_report_done = True
        self.print_runtime_summary(sys.stdout)
        if self._flow_log is not None and self._flow_log_path is not None:
            print(f"Full per-command detail → {self._flow_log_path}")

    def _log_write(self, text):
        """Mirror a diagnostic to the flow log, independent of the per-command
        capture.  Used by passthrough commands (e.g. a `source` that fails fast)
        whose own output bypasses run_command's capture but must still land in
        the post-mortem log."""
        if self._flow_log is not None:
            self._flow_log.write(text if text.endswith('\n') else text + '\n')
            self._flow_log.flush()

    def do_command(self, cmd_line):
        parts = _strip_inline_comment(cmd_line).strip().split()
        if not parts: return
        cmd = parts[0].lower()
        args = parts[1:]

        handler = COMMANDS.get(cmd)
        if handler is None:
            # Unknown command — fail loudly rather than silently skipping it.
            # A typo like 'add_layer' (the command is 'def_layer') would otherwise
            # leave the design misconfigured (no layers) with no warning.
            import difflib
            sugg = difflib.get_close_matches(cmd, KNOWN_COMMANDS, n=1)
            hint = f" Did you mean '{sugg[0]}'?" if sugg else ""
            where = (f" in {os.path.basename(self._script_stack[-1])}"
                     if self._script_stack else "")
            print(f"Error: unknown command '{cmd}'{where} — "
                  f"'{cmd_line.strip()}'.{hint}")
            sys.exit(1)
            return
        return handler(self, cmd, args, cmd_line)

def main():
    parser = argparse.ArgumentParser(
        prog='buda',
        description='Run a BUDA interconnect-planning flow script (.buda). '
                    'Executes the script top-to-bottom, printing a one-line '
                    'summary per command; full detail goes to the flow log.',
        epilog='Script commands are documented in docs/BUDA_SCRIPT_REFERENCE.md; '
               'these command-line options in docs/BUDA_CLI.md.')
    parser.add_argument('script', nargs='?',
                        help='path to a .buda flow script; a missing .buda '
                             'suffix is added automatically')
    parser.add_argument('-nv', '--no-viz', action='store_true',
                        help='skip visualize commands (useful for batch/CI runs)')
    parser.add_argument('--verbose-conn', action='store_true',
                        help='print every connectivity violation individually; '
                             'default collapses per-bit violations into a summary')
    parser.add_argument('--ipc-verbose', action='store_true',
                        help='surface buda_viz/def_viz IPC socket status chatter '
                             '(listening/connected/timer lines); off by default')
    args = parser.parse_args()
    session = BudaSession()
    session.no_viz = args.no_viz
    session.verbose_conn = args.verbose_conn
    session.ipc_verbose = args.ipc_verbose
    if args.script:
        script = args.script
        if not os.path.exists(script) and not script.endswith('.buda'):
            script = script + '.buda'
        session.script_path = os.path.abspath(script)

        # Relabel the macOS app name (dock / menu bar / Cmd-Tab) from
        # 'python3' to the design's name. This MUST run before matplotlib
        # realizes the first Tk window — AppKit caches CFBundleName when
        # NSApplication is created — so it lives here at startup, ahead of any
        # `visualize` command, not in BudaVisualizer.__init__ (too late there).
        if not session.no_viz:
            try:
                from buda_viz import set_app_name
                set_app_name(os.path.splitext(os.path.basename(script))[0])
            except Exception:
                pass

        # Open a flow log that captures the FULL detail of every command
        # (Python prints + C++ output routed through sys.stdout via
        # buda.ostream_redirect).  run_command mirrors each command's detail
        # here and prints only a one-line summary to the terminal, so the two
        # are no longer duplicated.
        flow_log_path = session._get_log_path('flow.log')
        session._flow_log_path = flow_log_path
        try:
            session._flow_log = open(flow_log_path, 'w', buffering=1)
        except OSError as e:
            print(f"Warning: could not open flow log {flow_log_path}: {e}")

        try:
            # The top-level source IS the whole run, so it starts "at the tail".
            session._at_last_command = True
            session.run_command(f"source {script}")
            # Persist a fixture opened with `open_bdb <file>.sql writeback` if the
            # run completed without an explicit exit (which flushes on its own).
            session._flush_bdb_writeback()
        finally:
            # Idempotent: a blocking `visualize` already emitted this before the
            # GUI opened (so it survives a macOS .app quit-on-window-close).
            session._print_end_report()
            if session._flow_log is not None:
                session._flow_log.close()
    else:
        # No script: show usage and insist on one rather than quietly exiting.
        parser.print_help(sys.stderr)
        print("\nerror: a .buda script is required "
              "(e.g. `buda demo/user_guide[.buda]`).", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
