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

"""Stages 4 & 9 — track assignment (NUTS) + feedback re-route.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import buda

from buda_session.util import _RR_DEFAULT_MAX_ITER


def cmd_run_nuts(session, cmd, args, cmd_line):
    # Usage: run_nuts [track_pitch]
    # NUTS places the planner-selected topology of each bundle, so it
    # needs a plan first. Without one every selected_topology_index is -1
    # (reset by generate_topologies) and NUTS would place 0 segments.
    if not session.bundles:
        print("Warning: run_nuts has no bundles — run run_bundler, "
              "generate_topologies, and run_planner first.")
        return
    if not any(0 <= w.plan.selected_topology_index < len(w.input.candidates)
               for w in session.bundles):
        print("Warning: run_nuts found no selected topology to place — run "
              "`run_planner` (or `run_planner hier`) after generate_topologies "
              "first (or pin one with select_topology).")
        return
    # Default to the stored pitch (possibly set via set_track_pitch
    # before run_planner) rather than resetting to 1.0, so a planner
    # that reserved bands for a non-default pitch stays consistent.
    pitch = float(args[0]) if args else session._nuts_pitch
    session._nuts_pitch = pitch
    if (session._planner_pitch is not None and
            abs(pitch - session._planner_pitch) > 1e-9):
        print(f"Warning: run_nuts pitch {pitch} differs from the pitch "
              f"{session._planner_pitch} run_planner reserved bands for. "
              f"Set the pitch with 'set_track_pitch <p>' before "
              f"run_planner (or re-run run_planner) so the planner's "
              f"pitch-aware band reservations match this NUTS run.")
    nuts = buda.NUTSEngine(session.fp, session.layers)
    nuts.set_track_pitch(pitch)
    # Bottom-up cells: solve each marked cell's templates once locally and
    # register the per-instance copies as fixed (skipped by extraction,
    # blocking every other bundle). See hier_bottom_up_planning.md §4.
    session._inject_bottom_up_fixed(nuts)

    if session.planner is not None:
        nuts.set_extra_grid_points(
            list(session.planner.get_x_grid()),
            list(session.planner.get_y_grid()))
    # Snapshot topology-derived initial spans before the solve.
    before = session._segment_states_from_topology()
    # C++ prints its own [NUTS] N segments placed across K layer(s) line.
    with buda.ostream_redirect():
        session.nuts_result = nuts.run(session.bundles)
    session._adopt_doglegs()
    # A fresh abstract solve invalidates any prior detailed result:
    # ripup_reroute / negotiate_congestion key their stage off
    # detailed_result, and hill-climbing against a detailed route of
    # the PREVIOUS abstract solve would mix two states (a re-run
    # run_nuts could even no-op stage b off stale zero opens).
    # run_detailed_nuts re-derives it from this solve.  The internal
    # trial rerun (_run_nuts_internal) deliberately does NOT clear it —
    # trials re-run DNUTS themselves and restore refs via snapshot.
    session.detailed_result = None
    layer_names = session._make_layer_names()
    diag = session._nuts_diagnostics(session.nuts_result, layer_names, before)
    session._write_nuts_log(layer_names, extra_lines=diag)
    ns, nv = session._persist_nuts()
    if ns:
        print(f"[BDB] persisted {ns} bus segment(s) and {nv} bus via(s) "
              f"to the open BDB.")


def cmd_run_detailed_nuts(session, cmd, args, cmd_line):
    # Usage: run_detailed_nuts [lo_hi|hi_lo]
    session._detailed_bit_order = "LO_HI"
    if args and args[0].lower() in ("lo_hi", "hi_lo"):
        session._detailed_bit_order = args[0].upper()

    if session.nuts_result is None:
        print("Error: run_detailed_nuts requires run_nuts to have been called first")
        return
    if session.routing_grid is None:
        print("Error: run_detailed_nuts requires a routing grid (def_track_pattern)")
        return

    session._run_detailed_nuts(bit_order=session._detailed_bit_order)
    n_ns, n_nv = session._persist_detailed_nuts()
    if n_ns:
        print(f"[BDB] persisted {n_ns} net segment(s) and {n_nv} "
              f"net via(s) to the open BDB.")


def cmd_ripup_reroute(session, cmd, args, cmd_line):
    # Usage: ripup_reroute [max_iter] [use_edge_candidates]
    # Stage auto-detected: after run_detailed_nuts ⇒ drive down DNUTS opens;
    # else after run_nuts ⇒ drive down NUTS overlaps.
    # `use_edge_candidates` (off by default) toggles the per-edge MST L/Z
    # flip move-source; the numeric token (any order) is max_iter.
    use_edge_candidates = "use_edge_candidates" in args
    nums = [a for a in args if a != "use_edge_candidates"]
    max_iter = int(nums[0]) if nums else _RR_DEFAULT_MAX_ITER
    session._ripup_reroute(max_iter=max_iter,
                        use_edge_candidates=use_edge_candidates)


def cmd_negotiate_congestion(session, cmd, args, cmd_line):
    # Usage: negotiate_congestion [max_iter]
    # Measured-congestion feedback (run after run_nuts): inject the
    # actual NUTS overlaps as band demand and let the planner re-price
    # both sides of each overlap off the contended bands.
    max_iter = int(args[0]) if args else 5
    session._negotiate_congestion(max_iter=max_iter)


def cmd_run_nuts_on_layer(session, cmd, args, cmd_line):
    # Usage: run_nuts_on_layer <layer-name>
    if not args:
        print("Error: run_nuts_on_layer requires a layer name")
        return
    layer_name = args[0]
    layer_id = session._layer_name_map.get(layer_name)
    if layer_id is None:
        print(f"Error: unknown layer '{layer_name}' — define it with def_layer first")
        return
    if session.nuts_result is None:
        print("Error: run_nuts must be called before run_nuts_on_layer")
        return
    session._rerun_nuts_layer(layer_id)
    # The COMMAND commits the re-solved routing to the BDB; the
    # visualizer's interactive ↺ (same helper) stays a pure preview.
    if session.bdb is not None:
        session._checkpoint_routing()
        print("[BDB] re-persisted routing after run_nuts_on_layer.")


COMMANDS = {
    "run_nuts": cmd_run_nuts,
    "run_detailed_nuts": cmd_run_detailed_nuts,
    "ripup_reroute": cmd_ripup_reroute,
    "negotiate_congestion": cmd_negotiate_congestion,
    "run_nuts_on_layer": cmd_run_nuts_on_layer,
}
