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
from buda_viz import BudaVisualizer, TopologyExplorer, collect_candidate_bundles


def cmd_report_wirelength(session, cmd, args, cmd_line):
    # Usage: report_wirelength   (alias: report_wl)
    # Report routed wirelength per bundle + total (abstract after
    # run_nuts, detailed too after run_detailed_nuts).  Full per-bundle
    # table lands in the flow log; the terminal shows the total.
    session._report_wirelength()


def cmd_check_connectivity(session, cmd, args, cmd_line):
    # Usage: check_connectivity [topo|nuts|dnuts] [all]
    # 'all' is only meaningful for the topo stage: checks every candidate
    # topology, not just the selected one.  Automatically used when no
    # topology has been selected yet (i.e. before run_planner).
    stage     = args[0].lower() if args else "dnuts"
    all_cands = len(args) > 1 and args[1].lower() == "all"
    if stage in ("topo", "nuts", "dnuts"):
        session._check_connectivity(stage, all_candidates=all_cands)
    else:
        print(f"Error: unknown stage '{stage}' — use topo, nuts, or dnuts")


def cmd_visualize_topologies(session, cmd, args, cmd_line):
    if session.no_viz:
        return
    # Usage:
    #   visualize_topologies [hint]         — load ALL bundles; a hint just
    #                                         picks which one it opens on, and
    #                                         you can step through the rest
    #                                         with the ◀/▶ Bundle buttons.
    #   visualize_topologies -all [hints…]  — load only bundles matching hints
    #                                         (no hints = every bundle)
    all_mode = bool(args) and args[0] == '-all'
    hints    = args[1:] if all_mode else args[:1]

    # Collect every candidate-bearing bundle once (cell-level hier
    # templates deduplicated); shared with the GUI "View Topologies" path.
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
                    inst_note = f" ({cnt} instances — showing first)"
            marker = "  ← opens here" if (not all_mode and i == start) else ""
            print(f"  bundle {b.id}: {len(w.input.candidates)} "
                  f"topologies{inst_note}{marker}")
        TopologyExplorer(session.fp, wrappers,
                         sidecar_path=session._sidecar_path(),
                         layer_stack=session.layers,
                         start_bidx=start).show()


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
    problems_only = "--problems" in args
    conn_detail = "--conn" in args
    hint = next((a for a in args if not a.startswith("--")), None)
    session._dump_topologies(hint, problems_only, conn_detail)


def cmd_visualize(session, cmd, args, cmd_line):
    if session.no_viz:
        return
    rerun_layer_fn = session._rerun_nuts_layer if session.nuts_result is not None else None
    rerun_all_fn   = session._rerun_all        if session.nuts_result is not None else None
    ipc_session = (os.path.splitext(os.path.basename(session.script_path))[0]
                   if session.script_path else None)
    viz = BudaVisualizer(session.fp, session.bundles,
                         sidecar_path=session.script_path,
                         rerun_layer_fn=rerun_layer_fn,
                         rerun_fn=rerun_all_fn,
                         routing_grid=session.routing_grid,
                         layer_stack=session.layers,
                         net_endpoints=session._net_endpoints,
                         ipc_session=ipc_session,
                         ipc_verbose=session.ipc_verbose)
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
    viz.show()


COMMANDS = {
    "report_wirelength": cmd_report_wirelength,
    "report_wl": cmd_report_wirelength,
    "check_connectivity": cmd_check_connectivity,
    "visualize_topologies": cmd_visualize_topologies,
    "dump_topologies": cmd_dump_topologies,
    "visualize": cmd_visualize,
}
