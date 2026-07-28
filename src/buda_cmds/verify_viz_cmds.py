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

from ._options import reject_unknown_options
# NOTE: `buda_viz` is imported LAZILY inside the two visualize handlers below,
# not at module load.  Importing it pulls in matplotlib/numpy, and this module
# is eagerly imported by the buda_cmds registry (and thus by buda_cli) — a
# headless embedder (e.g. the web server) must be able to import the command
# layer without a matplotlib dependency.  The viz handlers return early under
# `no_viz`, so the heavy import only happens when a window is actually opened.


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
    session._check_design(stage, all_candidates=all_cands)


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

    if session.no_viz:
        return
    from buda_viz import TopologyExplorer, collect_candidate_bundles

    # Collect every candidate-bearing bundle once — including each hier
    # per-instance bundle (they route independently); shared with the GUI
    # "View Topologies" path.
    all_wrappers, cell_seen = collect_candidate_bundles(session.bundles)

    def _matches(w):
        names = w.input.original_bundle.get_net_names()
        net0  = names[0] if names else ""
        return (not hints) or any(net0.startswith(h) for h in hints)

    if not all_wrappers:
        print("Warning: no bundle with candidates")
    else:
        if all_mode:
            # Filter to matching bundles (or all if no hints given).
            wrappers = [w for w in all_wrappers if _matches(w)] or all_wrappers
            start = 0
        else:
            # Load every bundle; open on the first one matching the hint.
            wrappers = all_wrappers
            start = next((i for i, w in enumerate(all_wrappers)
                          if _matches(w)), 0)

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
                         cost_fn=(session._candidate_costs if debug else None)).show()


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
    if session.no_viz:
        return
    from buda_viz import BudaVisualizer
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
