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

"""Verification, reporting, and visualisation commands.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import os

import buda_diag
from buda_session.util import resolve_script_path
from buda_script import leading_path_and_options

from ._options import reject_unknown_options
# NOTE: `buda_viz` is imported LAZILY inside the two visualize handlers below,
# not at module load.  Importing it pulls in matplotlib/numpy, and this module
# is eagerly imported by the buda_cmds registry (and thus by buda_cli) — a
# headless embedder (e.g. the web server) must be able to import the command
# layer without a matplotlib dependency.  The viz handlers return early under
# `no_viz` — which is checked BEFORE anything below touches matplotlib — so the
# heavy import only happens when a window is actually about to be opened.


def _backend_cannot_show():
    """The matplotlib backend is one that provably cannot put a window up.

    **Only valid AFTER `buda_viz` has been imported** — see
    `_import_viz_or_reason`.  `get_backend()` auto-selects and LOCKS the
    implicit default, and on macOS that default is the segfault-prone native
    `macosx`, which `buda_viz` exists to override; asking early would answer
    the question and silently decide it.

    Asked as "is it KNOWN to be non-interactive", never as "is it known to be
    interactive": a third-party backend (`module://…`) is in neither builtin
    list, and answering "not interactive" for it would refuse a window we
    could have opened.  Unknown means try — `plt.show()` on a backend that
    cannot show is a no-op, which is the cheaper mistake of the two.
    """
    try:
        import matplotlib
        name = matplotlib.get_backend().lower()
    except Exception:
        return False
    try:                                    # matplotlib >= 3.9
        from matplotlib.backends.registry import BackendFilter, backend_registry
        dead = backend_registry.list_builtin(BackendFilter.NON_INTERACTIVE)
    except Exception:
        try:                                # older matplotlib
            from matplotlib import rcsetup
            dead = rcsetup.non_interactive_bk
        except Exception:
            return False
    return name in [b.lower() for b in dead]


def _no_window_reason(session):
    """Why a `visualize*` command will open NO window — or None if it MIGHT.

    Only the reason knowable WITHOUT touching matplotlib, which is what keeps
    two separate promises: the command layer stays importable on a host with
    no plotting stack (the headless-embedder requirement above), and the
    backend is left unchosen for `buda_viz` to decide.  The rest of the
    verdict is `_import_viz_or_reason`'s.

    The suppression was ASKED for — `--no-viz`, `buda::viz off`, the
    web/embedded servers — so it is an INFO.  Said out loud all the same,
    because a log read against its script should show where the viewer was
    skipped; before this it was a bare `return`, and a command that succeeds
    and does nothing is exactly how `buda::visualize` read from a Tcl flow.

    Returns `(detail, severity)` or None.
    """
    if session.no_viz:
        return ("visualization is off for this session "
                "(--no-viz, or buda::start -viz 0)", buda_diag.INFO)
    return None


def _import_viz_or_reason():
    """Import the viz layer.  `(module, None)` or `(None, reason)`.

    An import that fails is a reason like any other rather than a traceback
    naming `matplotlib` but neither the command that wanted it nor the fact
    that the rest of the run is unaffected.
    """
    try:
        import buda_viz
    except Exception as e:
        return None, (f"the visualization layer could not be loaded on this "
                      f"host ({e})", buda_diag.WARNING)
    return buda_viz, None


def _note_if_nothing_can_be_shown(session, cmd):
    """Say that this viewer will not appear, and CARRY ON building it.

    Two things are deliberate here.

    **It does not stop.**  Constructing the figure under a file-only backend
    is a supported mode, not a waste: the viz suite drives `cmd_visualize`
    under Agg with `show` stubbed to check what each layer draws, and PNG
    rendering does the same.  Skipping the build to save the work would take
    that away — and the work was never the complaint.  What was missing is
    the sentence, so the sentence is all that is added.

    **It is asked AFTER `buda_viz` is imported.**  That import is where the
    backend is CHOSEN: on macOS it forces TkAgg over the native default that
    intermittently segfaults with the IPC timer or several windows — but only
    if nothing has been selected yet.  A `get_backend()` probe auto-selects
    and LOCKS that very default, so probing first would leave `buda_viz`
    finding a backend already chosen, skipping its override, and
    reintroducing the segfaults (Codex P1 on #688).  Import first, ask
    second; by then the answer is also the true one.
    """
    if not _backend_cannot_show():
        return
    import matplotlib
    _report_no_window(session, cmd,
                      (f"matplotlib's backend ({matplotlib.get_backend()}) "
                       f"cannot display a window — no display, or MPLBACKEND "
                       f"names a file-only backend; the figure is still built",
                       buda_diag.WARNING))


def _report_no_window(session, cmd, reason):
    """Say that `cmd` opened no window, and why.  Identified, so a headless
    methodology can waive BUDA-1903 once instead of grepping for prose.

    Mirrored into the flow log by hand because the visualize commands are
    PASSTHROUGHS (`_PASSTHROUGH_CMDS`): their output deliberately bypasses
    run_command's capture, since a blocking viewer's output belongs on the
    terminal.  That is right for a window that opened — and wrong for one
    that did not, because the post-mortem log is exactly where someone asks
    afterwards why they never saw it.
    """
    detail, severity = reason
    line = buda_diag.format("BUDA-1903", f"{cmd}: no window opened: {detail}",
                            severity=severity)
    print(line)
    session._log_write(line)


def cmd_report_wirelength(session, cmd, args, cmd_line):
    # Usage: report_wirelength   (alias: report_wl)
    # Report routed wirelength per bundle + total (abstract after
    # run_nuts, detailed too after run_detailed_nuts).  Full per-bundle
    # table lands in the flow log; the terminal shows the total.
    session._report_wirelength()


def cmd_check_design(session, cmd, args, cmd_line):
    # Usage: check_design [topo|nuts|dnuts] [all]   (alias: check_connectivity)
    # Design audit at the given stage: connectivity opens, layer-direction
    # validity, keepout crossings, unplaced bits.  'all' is only meaningful
    # for the topo stage: checks every candidate topology, not just the
    # selected one.  Automatically used when no topology has been selected
    # yet (i.e. before run_planner).  The command outgrew its original name
    # (it audits far more than connectivity), hence the rename; the old name
    # stays registered as an alias so existing scripts keep working.
    # CLAUDE.md documents `check_design [all]`: audit at the CURRENT stage,
    # with an optional `all` flag — so `all` must be accepted as the first
    # token (audit P5-05: it was rejected as an unknown stage), and a bare
    # `check_design` must audit at the deepest completed stage rather than
    # always demanding dnuts. An explicit topo/nuts/dnuts token still pins
    # the stage for back-compat.
    toks = [a.lower() for a in args]
    all_cands = "all" in toks
    stage_toks = [t for t in toks if t in ("topo", "nuts", "dnuts")]
    unknown = [t for t in toks if t not in ("all", "topo", "nuts", "dnuts")]
    if unknown:
        print(f"Error: unknown argument '{unknown[0]}' — "
              f"use topo, nuts, dnuts, or all")
        return
    if stage_toks:
        stage = stage_toks[0]
    elif session.detailed_result is not None:
        stage = "dnuts"
    elif session.nuts_result is not None:
        stage = "nuts"
    else:
        stage = "topo"
    # Record the verdict so --strict-check can fail the run and --report-json
    # can emit it (Phase 0).  Without those flags this is pure bookkeeping and
    # the printed output is unchanged.
    session.record_audit(session._check_design(stage,
                                               all_candidates=all_cands))


def select_explorer_bundles(session, all_wrappers, hints, all_mode):
    """Which bundles the explorer loads, and which one it opens on.

    Module-level and pure over its arguments so it can be TESTED: the command
    around it returns early under `no_viz` (BUDA-1903), which put this decision
    out of reach of every headless test — and this decision is where the bug
    was.

    Each hint is classified by `_split_bundle_selector`, the SAME rule
    `select_topology` and `dump_topologies` use, so a bare integer is a bundle
    ID here too: the explorer is what you open on the bundle an audit just
    named, and `visualize_topologies 8` used to hunt for a bus called '8'.

    The two readings are kept APART on purpose.  Letting an id-form hint also
    prefix-match meant `8` matched bundle 8 OR any bundle whose first net
    starts with '8' — and the scan takes the first in wrapper order — so the
    promised ID-only meaning held only while no such bus existed (Codex P2 on
    #746).  A name hint keeps the literal prefix test over THIS command's
    wrapper set, which is `collect_candidate_bundles` and not
    `session.bundles`, so name behaviour is untouched by construction.

    Returns (wrappers, start_index).
    """
    want, prefix_hints, bad = set(), [], []
    for h in hints:
        kind, val = session._split_bundle_selector(h)
        if kind == "id":
            want.add(val)
        elif kind == "prefix":
            prefix_hints.append(val)
        else:
            bad.append(f"{h} ({val})")
    for b in bad:
        print(f"Warning: visualize_topologies: ignoring selector {b}")

    def matches(w):
        if not hints:
            return True
        if w.input.original_bundle.id in want:
            return True
        if not prefix_hints:
            return False
        names = w.input.original_bundle.get_net_names()
        net0  = names[0] if names else ""
        return any(net0.startswith(h) for h in prefix_hints)

    if all_mode:
        # Filter to matching bundles (or all if no hints given).
        return ([w for w in all_wrappers if matches(w)] or all_wrappers), 0
    # Load every bundle; open on the first one matching the hint.
    return all_wrappers, next((i for i, w in enumerate(all_wrappers)
                               if matches(w)), 0)


def cmd_visualize_topologies(session, cmd, args, cmd_line):
    # Usage:
    #   visualize_topologies [hint]         — load ALL bundles; a hint just
    #                                         picks which one it opens on, and
    #                                         you can step through the rest
    #                                         with the ◀/▶ Bundle buttons.
    #   visualize_topologies -all [hints…]  — load only bundles matching hints
    #                                         (no hints = every bundle)
    #   visualize_topologies [...] debug    — order candidates by INCREASING
    #                                         planner cost (real cost post-plan,
    #                                         intrinsic wirelength pre-plan) and
    #                                         show the cost + its components; the
    #                                         candidate/group IDs are unchanged.
    # `debug` is a flag anywhere in the args, not a hint.
    debug = any(a.lower() == "debug" for a in args)
    args  = [a for a in args if a.lower() != "debug"]
    all_mode = bool(args) and args[0] == '-all'
    hints    = args[1:] if all_mode else args[:1]

    # Option validation (state-independent, like the #467 guards — run BEFORE the
    # no_viz early-out so a typo is caught in batch/CI runs too).  The only
    # keyword options are `debug` (stripped above) and `-all`; every other token
    # is a free-form bundle HINT, which never starts with '-'.
    #   (a) a '-'-prefixed token that isn't a LEADING `-all` is a mistyped flag;
    #   (b) without `-all`, only the FIRST hint is honored (the CLI used `args[:1]`
    #       and silently dropped the rest) — turn that footgun into a clear error.
    bad_flags = [t for t in args if t.startswith('-') and t != '-all']
    if bad_flags:
        reject_unknown_options("visualize_topologies", bad_flags, ("-all", "debug"))
    # `-all` is a mode flag valid only as the FIRST token and only once; ANY
    # later occurrence (`… -all foo -all`, `foo -all`) was silently dropped into
    # hints where it matches nothing — make that a clear error instead.
    if any(t == '-all' for t in args[1:]):
        print("Error: visualize_topologies: '-all' must be the first argument "
              "(before any hint) and may appear only once.")
        raise SystemExit(1)
    positional = [t for t in args if not t.startswith('-')]
    if not all_mode and len(positional) > 1:
        print(f"Error: visualize_topologies: at most one bundle hint without "
              f"'-all' (got {', '.join(repr(h) for h in positional)}). "
              f"Use '-all {' '.join(positional)}' to open several.")
        raise SystemExit(1)

    reason = _no_window_reason(session)
    if reason:
        _report_no_window(session, "visualize_topologies", reason)
        return
    _viz, reason = _import_viz_or_reason()
    if reason:
        _report_no_window(session, "visualize_topologies", reason)
        return
    _note_if_nothing_can_be_shown(session, "visualize_topologies")
    TopologyExplorer = _viz.TopologyExplorer
    collect_candidate_bundles = _viz.collect_candidate_bundles

    # Collect every candidate-bearing bundle once — including each hier
    # per-instance bundle (they route independently); shared with the GUI
    # "View Topologies" path.
    all_wrappers, cell_seen = collect_candidate_bundles(session.bundles)

    if not all_wrappers:
        print("Warning: no bundle with candidates")
    else:
        wrappers, start = select_explorer_bundles(session, all_wrappers,
                                                  hints, all_mode)

        for i, w in enumerate(wrappers):
            b = w.input.original_bundle
            cell_key = (b.cell_context, b.reason) if b.cell_context else None
            inst_note = ""
            if cell_key is not None and cell_key in cell_seen:
                cnt = cell_seen[cell_key][1]
                if cnt > 1:
                    inst_note = f" (cell {b.cell_context}, 1 of {cnt} instances)"
            marker = "  ← opens here" if (not all_mode and i == start) else ""
            print(f"  bundle {b.id}: {len(w.input.candidates)} "
                  f"topologies{inst_note}{marker}")
        # If this is the flow's LAST command, emit the runtime summary before
        # the window blocks — through the macOS .app, closing the last window
        # can terminate the process before main()'s finally. An interleaved
        # visualize skips this (the finally prints the complete summary).
        if session._at_last_command:
            session._print_end_report()
        TopologyExplorer(session.fp, wrappers,
                         sidecar_path=session._sidecar_path(),
                         layer_stack=session.layers,
                         start_bidx=start,
                         fp_resolver=session._make_topo_fp_resolver(),
                         groups_fn=session._loci_groups,
                         user_ops_sink=session._record_user_ops,
                         cost_fn=(session._candidate_costs if debug else None),
                         routing_grid=session.routing_grid).show()


def cmd_dump_topologies(session, cmd, args, cmd_line):
    # Usage: dump_topologies [hint] [--problems] [--conn]
    # Text inspection of the candidate topologies generated per bundle.
    # `hint` filters to bundles whose first net name starts with it.
    # `--problems` prints only bundles with flagged candidates (duplicate
    # geometry, pinched/zero-slide, single-candidate, pass-through) and
    # an aggregate summary.
    # `--conn` adds, per shown bundle, a per-segment connectivity detail
    # for the selected candidate: what each seg connects to (busterms +
    # other segs), the busterms it passes through, its slide range, and
    # its net-pull preference. Read-only: never mutates session state.
    # `--grouped` collapses nominal-locus FAMILIES (super-candidates) to one
    # representative row each (the lowest-WL member), annotated with the
    # variant count + perp span — the reduced set the user inspects before a
    # `select_topology <b> group:<rep>` group-pin.  Purely a display option.
    # Reject an unknown `--flag` — the first non-`--` token is the (free-form)
    # hint, so a typo like `--problem`/`--groupd` is neither a flag nor a hint
    # and used to be silently dropped (running a full unfiltered dump).
    reject_unknown_options("dump_topologies",
                           [a for a in args if a.startswith("--")],
                           ("--problems", "--conn", "--grouped"))
    problems_only = "--problems" in args
    conn_detail = "--conn" in args
    grouped = "--grouped" in args
    hint = next((a for a in args if not a.startswith("--")), None)
    session._dump_topologies(hint, problems_only, conn_detail, grouped)


def cmd_visualize(session, cmd, args, cmd_line):
    # `debug` flag: the TopologyExplorer this window opens ('v' / "View
    # Topologies") starts in the debug cost view (candidates stepped by
    # increasing planner cost, cost + components shown), exactly as
    # `visualize_topologies … debug`.  It is the ONLY option `visualize` takes —
    # there are no free-form args — so reject anything else (state-independent,
    # before the no_viz early-out, like the #467 guards).  Lowercased so the
    # check matches the case-insensitive `debug` detection below (a `DEBUG` token
    # must not both enable the view and fail validation).
    reject_unknown_options("visualize", [a.lower() for a in args], ("debug",))
    debug = any(a.lower() == "debug" for a in args)
    reason = _no_window_reason(session)
    if reason:
        _report_no_window(session, "visualize", reason)
        return
    _viz, reason = _import_viz_or_reason()
    if reason:
        _report_no_window(session, "visualize", reason)
        return
    _note_if_nothing_can_be_shown(session, "visualize")
    BudaVisualizer = _viz.BudaVisualizer
    rerun_layer_fn = session._rerun_nuts_layer if session.nuts_result is not None else None
    rerun_all_fn   = session._rerun_all        if session.nuts_result is not None else None
    ipc_session = (os.path.splitext(os.path.basename(session.script_path))[0]
                   if session.script_path else None)

    def _cuts_provider(_s=session):
        # Fresh planner cut/band state for the heatmap after an in-GUI re-run
        # (audit P7-05).  The re-run rebuilds _s.planner's congestion map, so
        # this always reads the CURRENT usage.
        if _s.planner is None:
            return None
        cuts = _s.planner.get_cuts()
        if not cuts:
            return None
        return (cuts, list(_s.planner.get_x_grid()),
                list(_s.planner.get_y_grid()))

    viz = BudaVisualizer(session.fp, session.bundles,
                         sidecar_path=session.script_path,
                         rerun_layer_fn=rerun_layer_fn,
                         rerun_fn=rerun_all_fn,
                         routing_grid=session.routing_grid,
                         layer_stack=session.layers,
                         net_endpoints=session._net_endpoints,
                         ipc_session=ipc_session,
                         ipc_verbose=session.ipc_verbose,
                         fp_resolver=session._make_topo_fp_resolver(),
                         cuts_provider=_cuts_provider,
                         groups_fn=session._loci_groups,
                         user_ops_sink=session._record_user_ops,
                         cost_fn=(session._candidate_costs if debug else None))
    viz.draw_blocks()
    if session.planner is not None:
        cuts = session.planner.get_cuts()
        if cuts:
            viz.draw_congestion_map(cuts, session.planner.get_x_grid(), session.planner.get_y_grid())
    viz.draw_hanan_grid()
    if session.routing_grid is not None:
        # Pre-route layer (first-class PreRoutedSegments; works in the
        # abstract view too — [Preroutes] cycles off/ALL/per-type).
        viz.draw_preroutes(session.routing_grid, session.layers)
    if session.nuts_result is not None:
        viz.draw_nuts_tracks(session.nuts_result)
        if session.detailed_result is not None:
            viz.draw_detailed_tracks(
                session.detailed_result, session.routing_grid, session.layers)
    else:
        viz.draw_buses()
    # If this is the flow's LAST command, emit the runtime summary before the
    # window blocks — through the macOS .app, closing the last window can
    # terminate the process before main()'s finally. An interleaved visualize
    # skips this (the finally prints the complete summary once it returns).
    if session._at_last_command:
        session._print_end_report()
    viz.show()


def cmd_check_template_tracks(session, cmd, args, cmd_line):
    # Usage: check_template_tracks [on_mismatch stop|independent]
    # Bottom-up template planning stage (c) verification: compare the
    # span-aware signal-track pools every instance of each marked cell sees
    # for its copied routing (run after run_nuts, before run_detailed_nuts).
    # The optional on_mismatch policy is consumed by run_detailed_nuts:
    # 'stop' (default) refuses to run while any instance is misaligned;
    # 'independent' copies the aligned instances and solves the misaligned
    # ones individually.  The verdict is cached; run_detailed_nuts runs this
    # check implicitly if it was never invoked.
    if args:
        if (len(args) >= 2 and args[0].lower() == "on_mismatch"
                and args[1].lower() in ("stop", "independent")):
            session._bu_mismatch_policy = args[1].lower()
            if session.bdb is not None:
                # Survives a save_bdb/load_pipeline checkpoint (meta row).
                session.bdb.meta_set("bu_mismatch_policy",
                                     session._bu_mismatch_policy)
            print(f"check_template_tracks: on_mismatch policy = "
                  f"{session._bu_mismatch_policy}")
        else:
            print("Error: usage: check_template_tracks "
                  "[on_mismatch stop|independent]")
            return
    if session.bdb is None:
        print("Error: check_template_tracks requires an open BDB "
              "(bottom-up cells live there)")
        return
    session._check_template_tracks()


COMMANDS = {
    "report_wirelength": cmd_report_wirelength,
    "report_wl": cmd_report_wirelength,
    "check_design": cmd_check_design,
    "check_connectivity": cmd_check_design,   # legacy alias (pre-rename)
    "check_template_tracks": cmd_check_template_tracks,
    "visualize_topologies": cmd_visualize_topologies,
    "dump_topologies": cmd_dump_topologies,
    "visualize": cmd_visualize,
}


def cmd_emit_guides(session, cmd, args, cmd_line):
    # Usage: emit_guides <file.json|.csv> [margin <n>] [tcl <file.tcl>]
    #                                     [csv <file.csv>]
    #        emit_guides <file.guide> [gcell <um>] [terminal <layer,...>]
    #                                 [plain_names]
    #
    # Phase 4a: the corridor manifest — the PRIMARY artifact, and the one
    # that carries positive intent ("route these nets here").  DEF cannot say
    # that, which is why this leads and the DEF (4b) follows.
    #
    # A `.guide` output is the same intent in the form a router READS —
    # OpenROAD's `read_guides` (docs/internal/librelane_hier_flow.md,
    # mechanism A): per net, gcell-aligned boxes (`gcell` from the imported
    # DEF's GCELLGRID or the option), DEF-escaped names unless `plain_names`,
    # and pin-access strips on the `terminal` layers.
    if not args:
        print("Error: emit_guides requires an output path "
              "(<file.json>, <file.csv> or <file.guide>)")
        return
    # A QUOTED path may contain spaces — for the output AND for the `tcl` /
    # `csv` values, which are paths too.  Unquoted, this is the old
    # args[0]/args[1:] split exactly.
    path, opts = leading_path_and_options(cmd_line, ("margin", "tcl", "csv"))
    margin, tcl, csv_path = 0.0, None, None
    gcell, terminal, escape, guide_opts = None, (), True, False
    i = 0
    while i < len(opts):
        kw = opts[i].lower()
        if kw == "gcell" and i + 1 < len(opts):
            try:
                gcell = float(opts[i + 1])
            except ValueError:
                print(f"Error: emit_guides gcell must be a length in um, "
                      f"got '{opts[i + 1]}'"); return
            guide_opts, i = True, i + 2
        elif kw == "terminal" and i + 1 < len(opts):
            terminal = tuple(t for t in opts[i + 1].split(",") if t)
            guide_opts, i = True, i + 2
        elif kw == "plain_names":
            escape, guide_opts, i = False, True, i + 1
        elif kw == "margin" and i + 1 < len(opts):
            try:
                margin = float(opts[i + 1])
            except ValueError:
                print(f"Error: emit_guides margin must be a number, "
                      f"got '{opts[i + 1]}'"); return
            i += 2
        elif kw == "tcl" and i + 1 < len(opts):
            # A quoted value arrives as ONE token (split_quoted_args), so this
            # is the same indexing as `margin` above.
            tcl, i = opts[i + 1], i + 2
        elif kw == "csv" and i + 1 < len(opts):
            csv_path, i = opts[i + 1], i + 2
        else:
            reject_unknown_options("emit_guides", [kw],
                                   ("margin", "tcl", "csv", "gcell", "terminal",
                                    "plain_names"))
            return
    is_guide = path.lower().endswith(".guide")
    if is_guide and (margin or tcl or csv_path):
        print("Error: emit_guides: `margin`, `tcl` and `csv` belong to the "
              "manifest, not to a .guide file (a guide is gcells, and a "
              "margin in layout units has no meaning on that grid)")
        return
    if guide_opts and not is_guide:
        print("Error: emit_guides: `gcell`, `terminal` and `plain_names` "
              "apply to a .guide output only")
        return
    if is_guide:
        session._emit_guide_file(resolve_script_path(session, path),
                                 gcell_um=gcell, terminal=terminal, escape=escape)
        return
    session._emit_guides(resolve_script_path(session, path), margin=margin,
                         tcl=resolve_script_path(session, tcl),
                         csv_path=resolve_script_path(session, csv_path))


def cmd_export_def_blockages(session, cmd, args, cmd_line):
    # Usage: export_def_blockages <file.def> [density <frac>] [margin <n>]
    #
    # Phase 4b: DEF with NEGATIVE semantics only.  Emitting the corridors as
    # blockages would tell the router to avoid the plan; what goes in is the
    # design's real keepouts (which is what a blockage means) plus, with
    # `density`, `+ PARTIAL` PLACEMENT blockages over the corridors.
    #
    # That last one is narrower than it reads: `PARTIAL maxDensity` is a
    # PLACEMENT-blockage option in DEF 5.8, so it caps CELL density under a
    # planned bus.  DEF has no routing-density concept, so the routing intent
    # lives in the manifest and nowhere else.
    if not args:
        print("Error: export_def_blockages requires an output path"); return
    # A QUOTED path may contain spaces; unquoted this is the old split.
    path, opts = leading_path_and_options(cmd_line, ("density", "margin"))
    density, margin = None, 0.0
    i = 0
    while i < len(opts):
        kw = opts[i].lower()
        if kw == "density" and i + 1 < len(opts):
            try:
                density = float(opts[i + 1])
            except ValueError:
                print(f"Error: export_def_blockages density must be a "
                      f"number, got '{opts[i + 1]}'"); return
            if not 0.0 < density <= 1.0:
                print("Error: export_def_blockages density must be in (0, 1]")
                return
            i += 2
        elif kw == "margin" and i + 1 < len(opts):
            margin = float(opts[i + 1]); i += 2
        else:
            reject_unknown_options("export_def_blockages", [kw],
                                   ("density", "margin"))
            return
    from buda_session.advisory import build_manifest, write_def_blockages
    path = resolve_script_path(session, path)
    m = build_manifest(session, margin)
    n_hard, n_soft = write_def_blockages(session, m, path, max_density=density)
    print(f"[Advisory] DEF blockages -> {path} "
          f"({n_hard} keep-clear, {n_soft} density-limited)")
    if density is None and m["bundles"]:
        print("[Advisory] note: corridors were NOT emitted — a blockage tells "
              "the router to stay out, which is the opposite of the plan.  "
              "Pass `density <frac>` for `+ PARTIAL` PLACEMENT limits over "
              "them (a cell-density cap, not a routing reservation — that "
              "lives only in the emit_guides manifest).")


COMMANDS["emit_guides"] = cmd_emit_guides
COMMANDS["export_def_blockages"] = cmd_export_def_blockages


def cmd_dump_messages(session, cmd, args, cmd_line):
    # Usage: dump_messages
    #
    # Phase 5: the message catalogue.  A methodology needs to know what it
    # may waive or gate on BEFORE the message fires, which is what an id
    # buys over prose that changes with the next wording improvement.
    import buda_diag
    rows = buda_diag.catalogue()
    print(f"[Messages] {len(rows)} identified diagnostic(s)")
    for mid, sev, text in rows:
        print(f"  {mid}  {sev:<7}  {text}")
    # Retired ids are printed too, and this is the point of recording them:
    # a flow gating on one would otherwise just stop firing, which reads
    # exactly like a design that stopped having the problem.
    gone = buda_diag.retired()
    if gone:
        print(f"[Messages] {len(gone)} retired id(s) — never reused, never "
              f"emitted again; a gate on one of these is dead")
        for mid, why in gone:
            print(f"  {mid}  RETIRED  {why}")


COMMANDS["dump_messages"] = cmd_dump_messages
