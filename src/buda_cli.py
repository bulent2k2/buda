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

# Put BUDA's Python layer on sys.path: the compiled extension (from build/,
# ahead of any stale copy sitting next to this script) and tools/, which holds
# bdb_serialize (used by open_bdb to load *.bdb.sql text fixtures).
#
# WHICH directories those are is `buda_runtime`'s to say, not ours — a
# checkout spreads the layer over build/src/tools and an installed wheel folds
# it into one directory, and a second copy of that rule here would be
# exercised in only one of the two layouts and drift in the other.  All this
# does is FIND buda_runtime: it is a sibling of our directory in a checkout,
# and in a wheel this file lives inside it.
#
# The EXISTENCE test runs first and the name test second, deliberately.  A
# directory that actually contains `buda_runtime/__init__.py` is an answer; a
# directory merely CALLED buda_runtime is a guess, and it is wrong for a
# checkout that happens to be cloned under that name (`git clone -o`, a
# vendored copy, a worktree) — the name would match `<repo>` and point us at
# the repo's PARENT.  Installed, `buda_runtime/buda_runtime/` does not exist,
# so the existence test falls through to the name test exactly as before.
_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (_here, os.path.dirname(_here)):
    if os.path.isfile(os.path.join(_cand, 'buda_runtime', '__init__.py')):
        _pkg_root = _cand
        break
    if os.path.basename(_cand) == 'buda_runtime':
        _pkg_root = os.path.dirname(_cand)
        break
else:                                        # pragma: no cover - broken tree
    raise ImportError('buda_runtime is missing beside ' + _here +
                      ' — BUDA cannot tell where its Python layer is')
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)
import buda_runtime
buda_runtime.install()

import buda

faulthandler.enable()
import buda_diag
from buda_cmds import COMMANDS
from buda_session import (PersistMixin, HierMixin, NutsFlowMixin,
                          EditMixin, ReportsMixin, RipupMixin,
                          RRStateMixin, NegotiateMixin, RefineMixin,
                          RRTrialsMixin, RRSweepsMixin, AdvisoryMixin)
from buda_session.util import (_batched, _RR_DEFAULT_MAX_ITER,  # noqa: F401
                               _RR_MAX_CANDIDATES_PER_BUNDLE)   # compat re-exports

# Diagnostic-marker matchers for the per-command error/warning counts.  The
# engines emit bracketed uppercase (`[Planner] WARNING: …`, `[NUTS] WARNING`)
# and the CLI emits capitalised-with-colon (`Error: unknown argument …`);
# incidental prose containing the word — a net named `error_flag`, a comment
# about warnings — must NOT count.  Word-bounded so `errors`/`warnings` match
# but `error_flag` does not.
# Two accepted marker forms, chosen from what the code actually emits:
#   * bare uppercase  — `[Planner] WARNING`, `[BDB] ERROR` (tag style), and
#   * any case + colon — `Error:`, `Warning:`, and the C++ `BDB SQL error:`
#     (lowercase-with-colon is a REAL diagnostic here, so a capitalised-only
#     matcher would lose it — a regression against the old substring count).
# `error_flag` matches neither: `_` is a word character, so `\berror\b` does
# not fire, and there is no colon.  A summary line like "2 warnings raised"
# is likewise not a diagnostic and correctly does not count.
_DIAG_ERR_RE  = re.compile(r'\bERRORS?\b|(?i:\berrors?\s*:)')
_DIAG_WARN_RE = re.compile(r'\bWARNINGS?\b|(?i:\bwarnings?\s*:)')


def _count_diags(lines):
    """(warnings, errors) over a command's captured output.

    An IDENTIFIED line (`BUDA-1602: WARNING: …`) is counted at its DECLARED
    severity; everything else falls back to the marker regexes above.  That
    ordering is the point of Phase 5's message ids: guessing severity from
    the words in the text is a heuristic that is wrong in both directions —
    it counts a line whose detail merely mentions "warning", and it counts a
    FATAL as nothing, because "fatal" is not one of the words it looks for.

    Unidentified output is matched exactly as before, so every existing flow
    log counts the same (test-pinned)."""
    nwarn = nerr = 0
    for ln in lines:
        sev = buda_diag.severity_of_line(ln)
        if sev is None:
            if _DIAG_WARN_RE.search(ln):
                nwarn += 1
            if _DIAG_ERR_RE.search(ln):
                nerr += 1
        elif sev == buda_diag.WARNING:
            nwarn += 1
        elif sev in (buda_diag.ERROR, buda_diag.FATAL):
            nerr += 1
    return nwarn, nerr


def _is_error_line(ln):
    """Does this line LEAD with a failure diagnostic?

    Both accepted forms: the prose `Error: …` and an identified
    `BUDA-NNNN: ERROR|FATAL: …`.  Used for headline selection, so converting
    a call site to an id cannot demote its message out of the terminal
    summary — a silent cost that would have made every conversion a small
    regression."""
    if _ERROR_RE.match(ln):
        return True
    return buda_diag.severity_of_line(ln) in (buda_diag.ERROR, buda_diag.FATAL)

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

# A line that IS the failure diagnostic (see _extract_headline).  `match`
# anchors at the start, so only a line LEADING with the marker qualifies — not
# prose mentioning an error, and not "Errors found: 3" (\b rejects the plural).
# Deliberately ERROR-only: an error means the command failed, so its diagnostic
# is the whole story, while a warning is usually incidental to a command whose
# summary still is — and the '!' marker plus the [N warn] flag already surface
# that one without displacing the summary.
_ERROR_RE = re.compile(r'\s*Error\b', re.I)

# Commands that must NOT be redirected/timed: `source` is a container whose
# child commands are each summarized instead, and the visualize commands open
# interactive windows whose output belongs on the terminal.
_PASSTHROUGH_CMDS = frozenset({"source", "visualize", "visualize_topologies"})


def _rotate_log(path):
    """Move an existing log to `<path>.1` so a re-run does not destroy it.

    Best-effort by design: a log that cannot be rotated must not stop the
    run — losing the previous log is bad, losing the run is worse."""
    try:
        if os.path.exists(path):
            os.replace(path, path + ".1")
    except OSError:
        pass


# Both live in `buda_session.util` now — a command HANDLER needs the same
# comment rule the dispatcher applies (see `sole_path_arg`), and a handler
# cannot import `buda_cli` without a cycle.  Re-exported here because this is
# where they were, and `tools/buda2tcl.py` imports one of them by this name.
from buda_session.util import (                             # noqa: E402
    _strip_inline_comment, sole_path_arg)


class BudaSession(PersistMixin, HierMixin, NutsFlowMixin, EditMixin,
                  ReportsMixin, RipupMixin, RRStateMixin,
                  NegotiateMixin, RefineMixin, RRTrialsMixin,
                  RRSweepsMixin, AdvisoryMixin):
    def _get_log_path(self, suffix):
        """Get the log path for a given suffix, ensuring the log directory exists.

        Default: <script_dir>/log/<stem>_<suffix> — one file per cell, silently
        overwritten by every re-run.  With --log (-l), all logs go into the
        run's archive dir log/<cell>/<timestamp>/<suffix> instead, next to
        copies of the exact scripts that produced them, so exploratory re-runs
        never collide (see _archive_script / main).  With --tag (-t), the tag
        is inserted immediately before the suffix (<stem>_<tag>_<suffix>), so
        parallel experiments on one script write to distinct log files instead
        of overwriting each other."""
        # --tag: fold the tag in right before the suffix, in every layout below.
        if self._log_tag:
            suffix = f"{self._log_tag}_{suffix}"
        if self._log_run_dir:
            os.makedirs(self._log_run_dir, exist_ok=True)
            return os.path.join(self._log_run_dir, suffix)
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

    def _archive_script(self, path):
        """--log mode: copy a sourced .buda script into the run's archive dir
        the moment it is sourced, so the archive holds exactly the script text
        this run executed (the top-level script arrives here too — main runs it
        via `source`).  Deduplication is by ORIGIN PATH: a re-sourced file is
        archived once, but every distinct origin gets its own copy (basename
        uniquified on collision) and MANIFEST line — even when two origins are
        byte-identical, since the manifest's job is complete provenance (which
        files this run read), not content storage (Codex #278)."""
        if not self._log_run_dir:
            return
        src = os.path.abspath(path)
        if src in self._log_archived:
            return                               # this exact file, already archived
        try:
            import shutil
            os.makedirs(self._log_run_dir, exist_ok=True)
            stem, ext = os.path.splitext(os.path.basename(src))
            dst, n = os.path.join(self._log_run_dir, stem + ext), 2
            while os.path.exists(dst):
                dst = os.path.join(self._log_run_dir, f"{stem}-{n}{ext}")
                n += 1
            shutil.copy2(src, dst)
            with open(os.path.join(self._log_run_dir, 'MANIFEST'), 'a') as mf:
                mf.write(f"{os.path.basename(dst)} <- {src}\n")
            self._log_archived.add(src)
        except OSError as e:
            print(f"Warning: could not archive {path} to {self._log_run_dir}: {e}")

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
        # Where each layer / track pattern came from: 'script' (an explicit
        # def_layer / def_track_pattern) or 'lef' (import_lef_tech).  The
        # precedence rule needs this in BOTH directions — the import must
        # skip what the script declared, and a script declaration must be
        # able to replace what the import provided (Phase 2b).
        self._layer_source = {}      # layer_id -> 'script' | 'lef'
        self._pattern_source = {}    # layer_id -> 'script' | 'lef' | 'def'
        # Wire WIDTH per layer, from LEF.  DEF TRACKS give a pitch and a
        # position but no width — only the technology knows that — so a
        # tech import feeds this and `import_def_lef` consumes it.
        self._lef_track_width = {}   # layer_id -> width
        self._gds_label_layers = []  # def_gds_layer labels <csv> (import default)
        self._nuts_pitch = 1.0
        self._planner_pitch = None   # pitch the last run_planner reserved bands for
        self._detailed_bit_order = "LO_HI"
        self._dead_span_escalate = False  # opt-in post-NUTS dead-span escalation
        self._heal_dead_spans_in_healers = True  # auto dead-span escalation in healers
        self._dead_span_auto_at_run_nuts = True  # auto-escalate at run_nuts when healers ahead
       # last track pitch used by run_nuts
        self._planner_iterations = 5 # last iteration count used by run_planner
        self._script_stack = []      # stack of absolute paths of sourced scripts
        self.script_path = None      # set when a .buda script is sourced
        self.routing_grid = None     # RoutingGridStack (stage 8)
        # Unit-plausibility guard (Phase 1d): 'on' (hard error) | 'warn' | 'off'
        self._unit_check = "on"
        # The (layer, unit_pitch) tuple last judged — report once per stage
        # per GRID, so a pattern declared after the first check re-arms it.
        self._unit_check_done = None
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
        self._edit_slide = {}        # staged slide windows {seg: (lo, hi)} —
                                     # edit_set_slide; applied to
                                     # plan.seg_slide_lo/hi at edit_commit
        self._edit_layers_changed = False   # edit_set_layer used: commit
                                     # rebuilds pinned_seg_layers (GUI parity)
        self._edit_fp = None         # the session's floorplan: cell-local /
                                     # endpoint-depth for a hier bundle (the
                                     # frame its candidates were generated in),
                                     # else self.fp; set by edit_topology
        self._edit_ops = None        # op-log of the open session: the applied
                                     # edit_* command lines, in order (GUI
                                     # parity) — commit stores them as BDB
                                     # meta provenance (user_ops:<bid>:<uid>)
        self._edit_base = 'new'      # the source candidate's topo_uid ('new'
                                     # for an empty session) the ops apply on
        # Command recorder (BUDA_RECORD=<path>): every command the session
        # executes, written as the flat `.buda` line it already is.  See
        # `_record` — this is what `tools/tcl2buda.py` is built on.
        self._record_fh = None
        self._record_path = os.environ.get("BUDA_RECORD", "").strip()
        self.no_viz = False          # set by --no-viz CLI flag
        self.viz_final = False       # set by -v/--visualize: open a viewer at the
                                     # end even if the script has no `visualize`
        self._recent_verbs = []      # leaf command verbs in run order (for -v's
                                     # "does the flow already end by visualizing?")
        self.verbose_conn = False    # set by --verbose-conn: print every per-bit violation
        self.ipc_verbose = False     # set by --ipc-verbose: surface buda_viz/def_viz IPC chatter
        self._die_w = 0.0            # stored by set_die when no BDB is open (flat flow)
        self._die_h = 0.0
        self.bdb = None              # BDB (opened by open_bdb command)
        self._persisted_plan_fp = None   # selective-persist fingerprint memo
        # Decision trace (risk_reduction_plan.md R2): None = off.  Tests set
        # a list directly; BUDA_DECISION_TRACE=<path> makes the CLI collect
        # and dump the run's decision records as JSON lines at exit, so two
        # runs can be diffed decision-wise without parsing log text.
        self._decision_trace = ([] if os.environ.get("BUDA_DECISION_TRACE")
                                else None)
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
        # Bundling permissions per net-name prefix (set_bundling) and the
        # optional bundle bit bound (set_max_bundle_bits) — consumed by
        # run_bundler's generalized/COMBINED path and its split pass.
        self._bundling_overrides = {}
        # Opt-in WL-dominance candidate pruning (set_prune_dominated): drop a
        # candidate whose envelope bottom exceeds an EQUIVALENT candidate's
        # envelope top at generation time.  Default OFF — bit-identical flows.
        self._prune_dominated = False
        # Opt-in candidate-pool cleanups (default OFF — bit-identical flows).
        # set_dedup_loci: collapse candidates that are the same topological
        # choice differing only in a nominal trunk locus WITHIN a shared slide
        # window (same connectivity/slide-windows/taps/net_pull), keeping the
        # best-estimated representative.  set_drop_dangling <mode>: handle
        # candidates with a dangling segment (a single non-block connection) or
        # an unbounded/unclamped slide window (the OOB detour / MST-relay
        # geometry).  Modes: 'off' (no-op); 'drop' (drop any such candidate —
        # most aggressive); 'clamp' (bound every unbounded slide window to the
        # design extent, drop nothing — least aggressive); 'clamp_drop' (clamp
        # the windows AND drop only the TRULY dangling candidates — a wire end
        # to nothing).
        self._dedup_loci = False
        self._drop_dangling_mode = "off"
        # set_trim_mst_legs: cut the duplicated PREFIX off MST legs that leave
        # one block along the same axis.  Same opt-in reason as the three above
        # and unlike them a GEOMETRY change, so it re-sorts the WL-ordered pool
        # and moves selection well beyond the bundle it fixes (chip3_topdown:
        # 9 of 640 bundles change selected type, 3 change pool size).
        # None = never declared (the env default stands); True/False = explicit.
        self._trim_mst_legs = None
        # set_trim_trunk_stubs: let the H trunk path suppress a redundant
        # collinear stub, which add_trunk_v has always done and add_trunk_h
        # never has.  Opt-in for the same search-space reason as the above.
        self._trim_trunk_stubs = None
        self._max_bundle_bits = None
        self._max_bundle_bits_auto = False
        self._hier_expansion_map = {}  # original bundle id → [expanded BundleWrappers]
        self._hier_bundles_orig = []   # pre-expansion snapshot set by run_hier_bundler
        self._planner_is_hier = False  # True after `run_planner hier` (self.bundles is expanded)
        self._flow_log = None          # open flow-log file (see open_flow_log); enables per-command logging
        self._flow_log_path = None     # its path (for the "Full detail →" line)
        self._flow_log_seen = set()    # paths this session already truncated (a re-arm appends)
        # Captured lines that `_is_error_line` matched, counted while a flow
        # log is armed.  A DRIVER's failure signal, not a report: summarizing
        # replaces a command's `Error: …` line with a one-line abstract that
        # LEADS with the `x ` marker, and the Tcl bridge decides "this command
        # failed" by scanning the transcript for a line that leads with the
        # diagnostic.  So a printed-error command inside a summarized `source`
        # stopped raising — the one contract the front end states outright
        # ("a command that fails by PRINTING `Error:` still raises").  Counted
        # with the bridge's OWN predicate rather than `_count_diags`, whose
        # rules are deliberately different, so armed and unarmed sessions
        # cannot disagree about what a failure is.
        self._error_line_count = 0
        self._log_run_dir = None       # --log: log/<cell>/<timestamp>/ archive dir (logs + script copies)
        self._log_archived = set()     # --log: origin abspaths already archived this run
        self._log_tag = None           # --tag: extra token inserted before the log suffix (e.g. rr_experiment)
        self._cmd_stats = []           # per-command (cmd_line, elapsed, nlines, nwarn, nerr) for runtime summary
        self._end_report_done = False  # runtime summary emitted (idempotent guard)
        self._at_last_command = True   # True while running the flow's final command
        self._at_tail_stack = []       # per-source-frame: was this source at its parent's tail?
        # ── Phase 0: design-outcome exit status + machine-readable report ───
        # (docs/internal/lefdef_interface_plan.md §1.)  A flow harness cannot
        # gate on a design that reports violations but exits 0, so every
        # check_design records its verdict here; --strict-check turns a dirty
        # verdict into a non-zero exit, and --report-json writes the run's
        # outcome as data instead of prose.  Both default OFF, so a run
        # without them is byte-identical to before.
        self.strict_check = False      # --strict-check: dirty audit ⇒ exit 3
        self.report_json_path = None   # --report-json PATH
        self._audits = []              # one dict per check_design call
        self._audit_failed = False     # any audit dirty or unable to run
        self._all_cmd_stats = []       # EVERY captured command (report only)

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
        # Count DIAGNOSTIC markers, not the substrings: a bare `'error' in
        # line.lower()` counted any line merely containing the word, so a net
        # or block named `error_flag` inflated the run's error count (Analysis
        # Lens B item 7).  Match the forms the codebase actually emits —
        # bracketed uppercase (`[Planner] WARNING:`) and capitalised-with-colon
        # (`  Error: run_nuts required first.`) — which is byte-identical on
        # every current corpus log (verified) while ignoring incidental prose.
        nwarn, nerr = _count_diags(lines)
        # Before `significant` filters anything: a command whose only output is
        # a printed `Error:` must still register, and the count is a failure
        # signal for whoever is driving, not a line worth surfacing.
        self._error_line_count += sum(1 for ln in lines if _is_error_line(ln))

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
            # … and a one-line abstract summary to the terminal.  This is the
            # RUN's visible output — every per-command headline a user sees
            # comes from here, so it must NOT be conditioned on any flag.
            # (#643 accidentally nested it inside the --report-json branch,
            # silencing normal runs entirely; see test_run_command_summary.py.)
            self._cmd_stats.append((stripped, elapsed, nlines, nwarn, nerr))
            headline = self._extract_headline(nonblank)
            self._emit_cmd_summary(real_out, stripped, elapsed, nlines,
                                   nwarn, nerr, headline)
        if self.report_json_path:
            # The machine-readable report must list what RAN, not what was
            # worth printing: `_cmd_stats` deliberately drops silent, instant
            # setup commands (add_block, def_layer, …) so the terminal stays
            # readable, which would make the report's command list — and its
            # total — quietly incomplete.  Recorded only when the flag is on,
            # so a normal run pays nothing.
            self._all_cmd_stats.append((stripped, elapsed, nlines, nwarn,
                                        nerr, bool(significant)))

        if raised is not None:
            # A FAILING command gets its full detail on the terminal, not just
            # the one-line headline every other command gets.  The abstract is
            # right for a command that worked — the log has the rest, and the
            # run continues to the place where it matters.  It is wrong for a
            # command that ENDED the run: the detail IS the outcome, and the
            # useful half of a fail-fast diagnostic is exactly what does not
            # fit on the headline (which files, the remedy, the usage line).
            # Sending a user to a log for the reason their run stopped is
            # sending them there for the only thing they need.
            #
            # A clean `exit` is excluded: code 0 is 18 flows' normal
            # terminator, not a failure, and its one line already reads fine.
            failing = not (isinstance(raised, SystemExit)
                           and raised.code in (0, None))
            if failing and nonblank:
                print(text.rstrip('\n'), file=real_out)
            raise raised

    @staticmethod
    def _extract_headline(nonblank):
        """Pick the most summary-like line from a command's (non-blank) output.

        A command that FAILED is summarized by its error, not by whatever it
        printed last: a multi-line diagnostic (message + usage line) would
        otherwise surface its trailing usage text and bury the actionable
        first line in the flow log.  So an `Error:` line wins, and the FIRST
        one is the primary complaint (later ones are continuations).
        """
        for ln in nonblank:
            if _is_error_line(ln):
                return ln.strip()
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
        # When the headline IS the error, "[1 err]" only repeats what the line
        # already says and eats ~8 columns of the truncation budget — columns
        # the actionable part of the message needs.  Only the redundant err
        # count is dropped; a warn count still carries new information.
        if nerr and not _is_error_line(headline):
            flags += f"[{nerr} err] "
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

    def record_audit(self, result):
        """Record one check_design verdict (Phase 0).

        `result` is the dict `_check_design` returns: stage, violations,
        by_kind, plus `ok=False` when the audit could not run at all (a
        missing prerequisite).  Either condition marks the run dirty, because
        both mean "this flow did not demonstrate a clean design" — which is
        the question a harness is asking."""
        if not result:
            return
        self._audits.append(result)
        if result.get("violations", 0) > 0 or not result.get("ok", True):
            self._audit_failed = True

    def write_report_json(self, path, exit_status):
        """Write the run's outcome as data (Phase 0).

        Everything here is already computed for the terminal summary; this
        just stops a flow harness from having to regex prose that is
        deliberately lossy and truncated to 68 columns."""
        import json
        # Prefer the COMPLETE record (every captured command, including the
        # silent setup ones the terminal summary drops); fall back to the
        # surfaced list only if the flag was set after the run started.
        src = self._all_cmd_stats or [(c, e, n, w, x, True)
                                      for c, e, n, w, x in self._cmd_stats]
        cmds = [{"command": c, "seconds": round(e, 4), "output_lines": n,
                 "warnings": w, "errors": x, "surfaced": s}
                for c, e, n, w, x, s in src]
        metrics = {}
        nr = getattr(self, "nuts_result", None)
        if nr is not None:
            metrics["nuts_overlaps"] = getattr(nr, "num_overlaps", None)
            metrics["nuts_interval_violations"] = getattr(nr, "num_violations",
                                                          None)
        dr = getattr(self, "detailed_result", None)
        if dr is not None:
            metrics["dnuts_unplaced_bits"] = getattr(dr, "num_unplaced", None)
        report = {
            "script": self.script_path,
            "exit_status": exit_status,
            "strict_check": bool(self.strict_check),
            "audit_failed": bool(self._audit_failed),
            "total_seconds": round(sum(c["seconds"] for c in cmds), 4),
            # `source`/`visualize` run as passthroughs (no capture), so they
            # are absent by construction; `source`'s children are each listed,
            # which is why counting it too would double the time.
            "commands_note": ("captured commands only; source/visualize are "
                              "passthroughs and their children are listed "
                              "individually"),
            "commands": cmds,
            "audits": self._audits,
            "metrics": metrics,
        }
        try:
            with open(path, "w") as f:
                json.dump(report, f, indent=2, default=str)
                f.write("\n")
        except OSError as e:
            print(f"Warning: could not write run report {path}: {e}")

    # ── arming the flow log ────────────────────────────────────────────────
    # The flow log is the switch that turns a run from "every line of every
    # command on the terminal" into "one summary line per command, full detail
    # in a file" (see run_command).  It lives here rather than in `main` so the
    # CLI is not the only driver that can have it: the Tcl bridge arms the same
    # machinery through the server's `__log` request, and a summarized run means
    # the same thing — same log path, same rotation, same summaries — whichever
    # door the flow came through.  It used to be eight lines inline in `main`,
    # which made "like `buda` does it" un-callable from anywhere else.
    def open_flow_log(self, script_path=None):
        """Arm per-command logging; returns the log path (None if it failed).

        `script_path` names the flow, so the log lands beside it in the
        canonical `<script_dir>/log/<stem>_flow.log` — passed only when the
        session has no script yet (the CLI sets `script_path` before calling;
        the Tcl bridge arms the log BEFORE its `source` command, which is what
        would otherwise set it).

        Idempotent, and a re-arm after `close_flow_log` APPENDS: a driver that
        summarizes each routing pass separately (the interactive prompt's
        `replan`) must not have its second pass rotate away the first one's
        log — one session is one file.
        """
        if self._flow_log is not None:
            return self._flow_log_path
        if script_path and self.script_path is None:
            self.script_path = os.path.abspath(script_path)
        # `_get_log_path` CREATES the log directory, so it can fail too — on a
        # read-only checkout, the case a site flow hits.  Inside the try with
        # the open: a run must not die for want of a log, and under the Tcl
        # bridge this raising would kill the engine mid-request, which the
        # client can only report as "the engine exited unexpectedly".
        try:
            path = self._get_log_path('flow.log')
            if path not in self._flow_log_seen:
                _rotate_log(path)
            self._flow_log = open(path, 'a' if path in self._flow_log_seen
                                  else 'w', buffering=1)
        except OSError as e:
            print(f"Warning: could not open the flow log: {e}")
            return None
        self._flow_log_seen.add(path)
        self._flow_log_path = path
        return path

    def close_flow_log(self):
        """Disarm per-command logging; returns the path that was open (or None).

        The inverse of `open_flow_log`, for a driver that summarizes only PART
        of a session.  An interactive prompt is the case: a command the user
        TYPED is one whose output they want to read — `dump_topologies` exists
        to print — so the prompt disarms while it waits and re-arms around the
        routing it replays.
        """
        if self._flow_log is None:
            return None
        path = self._flow_log_path
        try:
            self._flow_log.close()
        except OSError:
            pass
        self._flow_log = None
        return path

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

    # ── command recorder ──────────────────────────────────────────────────
    # `BUDA_RECORD=<path>` writes every command this session executes to a
    # `.buda` script.  It is the answer to "translate a Tcl flow into a
    # script", and it is a RECORDER rather than a translator on purpose:
    #
    #   Tcl is a general programming language, so what commands a flow issues
    #   is not a property of its TEXT — `flow/tcl/array.tcl 4 5` and the same
    #   file with `3 2` are different designs, and the loops, `expr`s and
    #   `catch`es between them are not statically resolvable.  Parsing Tcl
    #   would mean re-implementing Tcl.
    #
    #   But by the time a command reaches the engine it IS a flat `.buda`
    #   line — `buda::add_bus "bh_${R}_${C}\\[4\\]" …` arrives as
    #   `add_bus bh_0_1[4] …`.  So the translation already happened; it only
    #   needed writing down, at the one choke point every driver passes
    #   through (the CLI, the Tcl bridge via `run_command`, the web server).
    #
    # What comes out is a FLATTENING, which is the honest description: one
    # concrete design, straight-line, with the parameterization gone — that
    # is what "the .buda for `array.tcl 4 5`" means.  `source` is dropped
    # because its children are recorded individually (keeping both would run
    # them twice on replay), so an include tree flattens into one file too.
    def _record(self, cmd, cmd_line):
        if not self._record_path or cmd == "source":
            return
        if self._record_fh is None:
            try:
                self._record_fh = open(self._record_path, "w")
            except OSError as e:
                print(f"Warning: BUDA_RECORD: cannot write "
                      f"{self._record_path}: {e}")
                self._record_path = ""
                return
            # The note is supplied by whoever armed the recording, because
            # the RECORDING process is not the interesting one: under the Tcl
            # bridge `sys.argv` is the command server's, which says nothing
            # about the flow that drove it.
            note = os.environ.get("BUDA_RECORD_NOTE", "").strip()
            self._record_fh.write(
                f"# Recorded from a live BUDA session"
                f"{' — ' + note if note else ''}\n"
                f"# A flattened TRACE of the commands that ran, in order.\n"
                # The directory the recorded paths were relative TO.  A
                # `.buda` resolves a relative path against its OWN directory,
                # so a recording of a flow that used relative paths only
                # replays from here — worth stating in the file rather than
                # leaving to a FileNotFoundError three commands in.
                f"# cwd: {os.getcwd()}\n"
                f"# See tools/tcl2buda.py.\n\n")
        self._record_fh.write(_strip_inline_comment(cmd_line).strip() + "\n")
        self._record_fh.flush()

    def _record_note(self, text):
        """A comment in the recording (never a command)."""
        if self._record_fh is not None:
            self._record_fh.write(text + "\n")
            self._record_fh.flush()

    def do_command(self, cmd_line):
        parts = _strip_inline_comment(cmd_line).strip().split()
        if not parts: return
        cmd = parts[0].lower()
        args = parts[1:]
        self._record(cmd, cmd_line)
        # Track the leaf command sequence for -v's end-of-flow decision.  Skip
        # `source` (a wrapper — its children run through here too, so the tail
        # stays the real last commands).  Recorded here, BEFORE dispatch, so a
        # terminal `exit` (whose handler raises SystemExit) still lands.
        if cmd != "source":
            self._recent_verbs.append(cmd)

        handler = COMMANDS.get(cmd)
        if handler is None:
            self._record_note(f"# unknown command, recorded as attempted: {cmd}")
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

    _VIZ_VERBS = ("visualize", "visualize_topologies")

    def _flow_ends_by_visualizing(self):
        """True when the flow's own commands already open a viewer at the end,
        so -v should NOT add a second one.  That is: the last leaf command is a
        `visualize`/`visualize_topologies`, OR it is `exit` immediately preceded
        by one (`visualize` then `exit`).  A viewer earlier in the flow — with
        other commands after it — does NOT count: -v still adds a fresh final
        view of the finished design (the whole point of the flag)."""
        v = self._recent_verbs
        if v and v[-1] in self._VIZ_VERBS:
            return True
        if len(v) >= 2 and v[-1] == "exit" and v[-2] in self._VIZ_VERBS:
            return True
        return False

def _enable_line_buffered_stdio():
    """Issue #31: force line buffering on stdout/stderr so that, when they are
    redirected to a file, Python's prints and the C++ engine's std::cout output
    (line-buffered via setvbuf at the buda module init, see bindings.cpp) both
    flush per line and therefore interleave in CHRONOLOGICAL order.  Without it a
    redirected stream is block-buffered on some platforms (notably macOS), so a
    partial [Planner] block strands in the buffer and lands out of order at the
    end of the log.  `reconfigure` exists on TextIOWrapper (the normal
    console/redirect case); guarded for exotic stdout replacements."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass


def _cgroup_cpu_quota(root="/sys/fs/cgroup"):
    """The container's CFS CPU quota as a CPU count (ceil), or None when
    unlimited/undetectable.  A cgroup can throttle to N CPUs WITHOUT
    narrowing the cpuset (cgroup v2 `cpu.max`, v1 cfs_quota_us), in which
    case affinity alone over-reports the usable parallelism (Codex #598 P2).
    Reads v2 first, then v1; any read/parse problem means None (no limit
    assumed — affinity remains the bound)."""
    try:
        with open(f"{root}/cpu.max") as fh:            # cgroup v2
            quota, period = fh.read().split()
            if quota != "max" and int(period) > 0:
                return max(1, -(-int(quota) // int(period)))   # ceil
            return None
    except (OSError, ValueError):
        pass
    try:
        with open(f"{root}/cpu/cpu.cfs_quota_us") as fh:       # cgroup v1
            quota = int(fh.read())
        with open(f"{root}/cpu/cpu.cfs_period_us") as fh:
            period = int(fh.read())
        if quota > 0 and period > 0:
            return max(1, -(-quota // period))                 # ceil
    except (OSError, ValueError):
        pass
    return None


def detect_max_threads():
    """The machine-dependent thread ceiling: the number of LOGICAL CPUs this
    process may run on.  os.sched_getaffinity respects cpuset/taskset limits,
    and on hybrid parts (performance cores with SMT + efficiency cores) the
    logical count is exactly superscalar-cores x 2 + low-power-cores — the
    accurate, automatic form of that estimate.  A container can ALSO be
    throttled by a CFS quota without a narrowed cpuset, so the quota (as a
    CPU count) is folded in as a min (Codex #598 P2).  Falls back to
    os.cpu_count() where affinity is unavailable (macOS/Windows)."""
    try:
        n = max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        n = max(1, os.cpu_count() or 1)
    q = _cgroup_cpu_quota()
    if q is not None:
        n = min(n, q)
    return max(1, n)


def resolve_threads(requested, max_threads):
    """The run's worker-thread count: an explicit --threads N is clamped to
    [1, max_threads] (LOUD when clamped — a request outside the machine's
    range is honored as closely as possible, never errored); flag absent
    defaults to half the machine maximum (min 1)."""
    if requested is None:
        return max(1, max_threads // 2)
    n = max(1, min(requested, max_threads))
    if n != requested:
        print(f"Warning: --threads {requested} clamped to {n} "
              f"(this machine allows 1..{max_threads})")
    return n


# One knob, three engines: the parallel stages each read their own env var
# (planner candidate scoring / NUTS per-layer solvers / the healers' trial
# sweep), and --threads drives all of them.
_THREAD_ENV_VARS = ("BUDA_PLAN_THREADS", "BUDA_NUTS_THREADS",
                    "BUDA_SWEEP_THREADS")


def apply_thread_env(n, explicit):
    """Export the resolved count to the engines.  An EXPLICIT --threads
    OVERRIDES the per-engine vars (one flag governs the run — explicit
    semantics, which also bypass the engines' small-work gates, as a hand-set
    per-engine var always did).  The flag-absent DEFAULT must NOT masquerade
    as an explicit request (Codex #598 P1: it would bypass the planner's
    small-pool gate and NUTS's work-size gate, spawning pools for tiny work),
    so it sets only BUDA_THREADS — the governor the engines' AUTO paths read
    as a CEILING, gates preserved — and never touches a per-engine var the
    user set.  Engine-internal safety pins are unaffected either way
    (parallel_sweep's trial workers set their private planners/engines to 1
    thread directly — no nested pools)."""
    if explicit:
        for var in _THREAD_ENV_VARS:
            os.environ[var] = str(n)
    else:
        os.environ.setdefault("BUDA_THREADS", str(n))


def threads_arg(s):
    """argparse type for -j/--threads: a plain integer or the literal 'max'.

    Returns the string 'max' or an int; anything else raises so argparse (or a
    caller) renders one message.  'max' means the whole machine maximum — the
    explicit opt-out of the max/2 default."""
    if s == "max":
        return "max"
    try:
        return int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid thread count '{s}' (an integer or 'max')")


def configure_threads(raw, apply=True):
    """Resolve a -j/--threads request, export it to the engines, and report it.

    ONE source for both entry points — the CLI (`buda -j`) and the Tcl server
    (`btcl -j`, carried in BUDA_THREADS_REQUEST) — so a `-j` means the same
    thing through either door.  `raw` is what the launcher asked for:

      * None / 'default' — the launcher DEFAULT: half the machine maximum,
        applied as the BUDA_THREADS CEILING (the engines' auto paths honor it
        with their small-work gates intact).
      * 'max'            — the whole machine maximum, EXPLICIT (per-engine vars,
        gates bypassed).
      * an int / digits  — that count, EXPLICIT, clamped LOUD to [1, max].

    Prints the resolved `[threads] N of M` line and returns N.  Raises
    ValueError on a request that is neither 'max'/'default' nor an integer."""
    max_threads = detect_max_threads()
    if raw is None or raw == "default":
        requested, explicit, label = None, False, "default: max/2"
    elif raw == "max":
        requested, explicit, label = max_threads, True, "--threads max"
    else:
        requested, explicit, label = int(raw), True, "--threads"
    n = resolve_threads(requested, max_threads)
    if apply:
        apply_thread_env(n, explicit=explicit)
    print(f"[threads] {n} of {max_threads} logical CPUs ({label})")
    return n


def main():
    _enable_line_buffered_stdio()
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
    viz_group = parser.add_mutually_exclusive_group()
    viz_group.add_argument('-nv', '--no-viz', action='store_true',
                        help='skip visualize commands (useful for batch/CI runs)')
    viz_group.add_argument('-v', '--visualize', dest='viz_final',
                        action='store_true',
                        help='open a viewer on the finished design even if the '
                             'script has no `visualize` — for running test '
                             'vehicles without adding one; no-op if the flow '
                             'already ends by visualizing')
    parser.add_argument('-t', '--tag', metavar='TAG',
                        help='insert TAG into every log file name for this run '
                             '(<stem>_<TAG>_flow.log etc.), so parallel '
                             'experiments on one script keep separate logs '
                             'instead of overwriting each other')
    parser.add_argument('-l', '--log', action='store_true',
                        help='archive this run: logs go to log/<cell>/<timestamp>/ '
                             '(never overwriting a previous run) together with '
                             'copies of the exact .buda scripts executed — the '
                             'top-level script and every sourced file (MANIFEST '
                             'maps each copy to its origin)')
    parser.add_argument('--verbose-conn', action='store_true',
                        help='print every connectivity violation individually; '
                             'default collapses per-bit violations into a summary')
    parser.add_argument('--strict-check', action='store_true',
                        help='exit 3 when any check_design reported violations '
                             '(or could not run). Without it a dirty design '
                             'still exits 0, so a flow harness cannot gate on '
                             'design quality — see docs/internal/'
                             'lefdef_interface_plan.md')
    parser.add_argument('--report-json', metavar='PATH',
                        help='write a machine-readable run report to PATH: '
                             'per-command runtime/diagnostics, every '
                             'check_design verdict with violation counts by '
                             'kind, and end-of-run metrics (overlaps, '
                             'unplaced bits). For flow harnesses that should '
                             'not parse terminal prose')
    parser.add_argument('--ipc-verbose', action='store_true',
                        help='surface buda_viz/def_viz IPC socket status chatter '
                             '(listening/connected/timer lines); off by default')
    parser.add_argument('-j', '--threads', type=threads_arg, metavar='N|max',
                        help='worker threads for the parallel pipeline stages: '
                             'planner candidate scoring, NUTS per-layer '
                             'solvers, and the healers\' trial sweep. '
                             'Clamped to [1, <logical CPUs available to this '
                             'process>] — affinity- AND cpu-quota-aware; on '
                             'hybrid CPUs that count is P-cores x SMT + '
                             'E-cores. Default (flag absent): half the machine '
                             'maximum; `-j max` uses the whole maximum. An '
                             'explicit N (or `max`) overrides the per-engine '
                             'env vars (BUDA_PLAN/NUTS/SWEEP_THREADS, explicit '
                             'semantics); the default sets only the '
                             'BUDA_THREADS ceiling, which the engines\' auto '
                             'paths honor with their small-work gates intact')
    args = parser.parse_args()
    configure_threads(args.threads)
    session = BudaSession()
    session.no_viz = args.no_viz
    session.viz_final = args.viz_final
    session.verbose_conn = args.verbose_conn
    session.strict_check = args.strict_check
    session.report_json_path = args.report_json
    session.ipc_verbose = args.ipc_verbose
    if args.tag:
        # Sanitize to a filename-safe token (the tag becomes part of a path):
        # keep alphanumerics, dash, underscore and dot; fold everything else
        # (spaces, slashes, …) to '_'.  Underscore is itself legal, so it is
        # NOT stripped — `--tag exp` and `--tag exp/` must stay distinct (the
        # latter folds to `exp_`), and `--tag _` is a real tag.  Only a tag
        # that sanitizes to empty (e.g. `--tag ""`) falls back to no tag.
        tag = re.sub(r'[^0-9A-Za-z._-]', '_', args.tag)
        session._log_tag = tag or None
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

        # --log: per-run archive dir log/<cell>/<timestamp>/ — all logs land
        # there (instead of the shared log/<cell>_*.log files a re-run would
        # overwrite) plus a copy of every script sourced, so each exploratory
        # run keeps its logs AND the exact script text that produced them.
        if args.log:
            stem = os.path.splitext(os.path.basename(session.script_path))[0]
            base = os.path.join(os.path.dirname(session.script_path), 'log', stem)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            run_dir, n = os.path.join(base, stamp), 2
            while os.path.exists(run_dir):       # same-second re-run
                run_dir = os.path.join(base, f"{stamp}-{n}")
                n += 1
            try:
                os.makedirs(run_dir)
                session._log_run_dir = run_dir
                print(f"Run archive → {run_dir}")
            except OSError as e:
                print(f"Warning: could not create run archive {run_dir}: {e}")

        # Open a flow log that captures the FULL detail of every command
        # (Python prints + C++ output routed through sys.stdout via
        # buda.ostream_redirect).  run_command mirrors each command's detail
        # here and prints only a one-line summary to the terminal, so the two
        # are no longer duplicated.
        #
        # Non-overwriting logs (Phase 5).  A re-run used to destroy the
        # previous log in place, so the evidence for "it worked an hour ago"
        # was gone the moment you tried to reproduce it.  The CANONICAL path
        # is kept — 36 tests and every doc reference point at it — and the
        # previous run is rotated to `<name>.1` instead.  One generation is
        # enough to compare a run against the one before it, which is the
        # case that actually comes up; `--log` still archives every run.
        session.open_flow_log()

        # A script's `exit` command raises SystemExit, which would otherwise
        # sail straight past the end-of-run bookkeeping below — and most real
        # flows DO end with `exit`, so the strict-check status and the JSON
        # report would silently never be produced on exactly the flows that
        # matter.  Catch it, keep the script's own code, and let the tail run.
        script_exit = 0
        try:
            # The top-level source IS the whole run, so it starts "at the tail".
            session._at_last_command = True
            session.run_command(f"source {script}")
            # Persist a fixture opened with `open_bdb <file>.sql writeback` if the
            # run completed without an explicit exit (which flushes on its own).
            session._flush_bdb_writeback()
        except SystemExit as e:
            script_exit = e.code if isinstance(e.code, int) else 0
        except Exception as e:          # noqa: BLE001 — an ENGINE fault
            # Deliberately `Exception`, not `BaseException`: SystemExit is
            # handled above and a KeyboardInterrupt is the user stopping the
            # run, not the engine failing — neither is this diagnostic.
            # A command that RAISES (a C++ RuntimeError, a bad argument that
            # reached a binding) used to sail past this block entirely, so
            # the run ended: end report first — a runtime summary listing the
            # commands that DID run, which reads exactly like a clean short
            # flow — and only then Python's default excepthook, printing a
            # traceback through code the user did not write.  The failure was
            # on screen, in the least legible place and after the evidence
            # that it had not happened.
            #
            # Reported here, BEFORE the `finally` runs, so the diagnostic
            # comes first and the summary is read as the tail of a failed
            # run.  The traceback is not lost: it goes to the flow log
            # always, and to the terminal under BUDA_TRACEBACK=1.
            import traceback
            tb = traceback.format_exc()
            if session._flow_log is not None:
                session._flow_log.write(f"\n{tb}\n")
                session._flow_log.flush()
            print(buda_diag.format(
                "BUDA-1906", f"{type(e).__name__}: {e}"))
            where = session._script_stack[-1] if session._script_stack else script
            print(f"  while running {os.path.basename(where)}"
                  f" — the commands below it did NOT run")
            if os.environ.get("BUDA_TRACEBACK", "").strip() not in ("", "0"):
                print(tb, file=sys.stderr)
            else:
                print("  (set BUDA_TRACEBACK=1 for the Python traceback; "
                      "it is in the flow log either way)")
            script_exit = 1
        finally:
            # Idempotent: a blocking `visualize` already emitted this before the
            # GUI opened (so it survives a macOS .app quit-on-window-close).
            session._print_end_report()
            # -v (--visualize): open a viewer on the FINISHED design even when
            # the script never called `visualize`, so a test vehicle can be
            # eyeballed without editing it.  Runs here — after the end report
            # (so the summary survives the macOS .app quit-on-close) and while
            # the flow log is still OPEN (the skip-note path writes to it) — and
            # after a trailing `exit` was caught above, so the window still
            # opens and the script's exit code is honored below once it closes.
            # A flow that already ends by visualizing is left alone; `visualize`
            # self-skips (BUDA-1903) with no display, so this is safe headless.
            if (session.viz_final and not session.no_viz
                    and not session._flow_ends_by_visualizing()):
                session._at_last_command = True
                try:
                    session.run_command("visualize")
                except BaseException as e:   # never mask the flow's own outcome
                    print(f"Warning: -v visualize failed: {e}")
            if session._flow_log is not None:
                session._flow_log.close()
            # Decision-trace dump (risk_reduction_plan.md R2): one JSON line
            # per decision record, so two runs diff decision-wise without
            # parsing log text (wording changes never re-baseline this).
            tp = os.environ.get("BUDA_DECISION_TRACE")
            if tp and session._decision_trace is not None:
                try:
                    import json
                    with open(tp, 'w') as f:
                        for tag, kv in session._decision_trace:
                            f.write(json.dumps({"tag": tag, **kv},
                                               default=str) + "\n")
                except OSError as e:
                    print(f"Warning: could not write decision trace "
                          f"{tp}: {e}")

        # Phase 0: derive the run's exit status from the DESIGN outcome, not
        # just from whether the script parsed.  Without --strict-check the
        # status is the script's own (0 unless it asked otherwise), exactly as
        # before; with it, a flow whose check_design reported violations — or
        # could not run one — fails, which is what lets a harness gate on
        # quality.  Code 3 is distinct from 1 (script/setup error) and 2
        # (usage) so a harness can tell "the tool worked, the design is dirty"
        # from "the tool broke".
        #
        # An explicit NON-ZERO `exit <code>` wins — that is the author stating
        # a failure of their own.  A zero one deliberately does NOT: 18 of the
        # repo's flows end with a bare `exit` (SystemExit code 0) as their
        # normal terminator, so letting that suppress the verdict would make
        # --strict-check a no-op on essentially every real flow.
        status = script_exit
        strict_failed = bool(session.strict_check and session._audit_failed
                             and status == 0)
        if strict_failed:
            status = 3
        if session.report_json_path:
            session.write_report_json(session.report_json_path, status)
        # Attribute the message to strict-check ONLY when strict-check is what
        # produced it: a script's own `exit 3` must not be reported as a failed
        # design audit that never ran.
        if strict_failed:
            n = sum(a.get("violations", 0) for a in session._audits)
            print()
            buda_diag.emit("BUDA-1902",
                           f"--strict-check — design audit failed "
                           f"({n} violation(s) across {len(session._audits)} "
                           f"check_design call(s)); exiting {status}.")
        if status:
            sys.exit(status)
    else:
        # No script: show usage and insist on one rather than quietly exiting.
        parser.print_help(sys.stderr)
        print("\nerror: a .buda script is required "
              "(e.g. `buda demo/user_guide[.buda]`).", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
