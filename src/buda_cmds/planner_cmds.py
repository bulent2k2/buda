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

"""Stage 3 — planner / selection commands.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import os
import sys

import buda

from ._options import looks_numeric, reject_unknown_options


def cmd_set_planner_param(session, cmd, args, cmd_line):
    name_p, value_p = args[0], float(args[1])
    # Always record in the stash: run_planner builds a fresh
    # CongestionPlanner seeded from _planner_params, so a value set
    # between runs must survive until the next run.
    session._planner_params[name_p] = value_p
    if session.planner is not None:
        session.planner.set_planner_param(name_p, value_p)


def cmd_run_planner(session, cmd, args, cmd_line):
    # Reject unknown options up front (state-independent).  `post_nuts` has its
    # own V/H/top/threshold sub-grammar (validated in its branch below); the
    # flat and hier forms accept only an iteration COUNT (numeric) plus the
    # keywords `hier` and `signal_tracks`.  Without this, `run_planner foo bar`
    # silently planned with defaults.
    if not (args and args[0] == "post_nuts"):
        opts = [a for a in args if not looks_numeric(a)]
        reject_unknown_options("run_planner", opts, ("hier", "signal_tracks"))
        # The iteration count is a single INTEGER; reject `3 30` (two counts,
        # only the first used) and `2.5` (non-integer, silently ignored → the
        # default effort limit).
        nums = [a for a in args if looks_numeric(a)]
        if len(nums) > 1:
            print(f"Error: run_planner: at most one iteration count, got "
                  f"{', '.join(nums)}"); sys.exit(1)
        if nums and not nums[0].lstrip("-").isdigit():
            print(f"Error: run_planner: iteration count must be an integer, "
                  f"got '{nums[0]}'"); sys.exit(1)
    if args and args[0] == "post_nuts":
        # Stage 4c: post-NUTS stub layer reassignment.
        # Syntax: post_nuts [V [short [long]]] [H [short [long]]]
        # Bare "post_nuts" (no letter) → V with defaults (backward compat).
        _V_DEFAULTS = (80.0, 200.0)
        _H_DEFAULTS = (150.0, 400.0)
        rest = args[1:]
        # Optional leading 'top' keyword: reassign within TOP layers only
        # (short → next-highest TOP, long → highest TOP), leaving the LOW
        # escape layers out of the spread.
        top_only = False
        if rest and rest[0].lower() == "top":
            top_only = True
            rest = rest[1:]
        v_thresholds = None
        h_thresholds = None
        i = 0
        while i < len(rest):
            tok = rest[i].upper()
            if tok in ("V", "H"):
                # Consume up to two following numeric tokens as thresholds.
                defaults = _V_DEFAULTS if tok == "V" else _H_DEFAULTS
                s = float(rest[i + 1]) if i + 1 < len(rest) and rest[i + 1].replace('.','',1).isdigit() else defaults[0]
                l = float(rest[i + 2]) if i + 2 < len(rest) and rest[i + 2].replace('.','',1).isdigit() else defaults[1]
                # Advance past any numeric tokens we consumed.
                i += 1
                if i < len(rest) and rest[i].replace('.','',1).isdigit():
                    i += 1
                    if i < len(rest) and rest[i].replace('.','',1).isdigit():
                        i += 1
                if tok == "V":
                    v_thresholds = (s, l)
                else:
                    h_thresholds = (s, l)
            else:
                print(f"Warning: run_planner post_nuts — unexpected token '{rest[i]}', ignored")
                i += 1
        # Bare "post_nuts" with no direction letters → V with defaults.
        if v_thresholds is None and h_thresholds is None:
            v_thresholds = _V_DEFAULTS
        session._run_post_nuts_planner(v_thresholds, h_thresholds, top_only=top_only)
    elif args and args[0] == "hier":
        # run_planner hier [N]
        # Hierarchy-aware planning: expand cell-level bundles to per-instance
        # absolute-coord wrappers, assign priorities, then run the flat planner.
        if session.bdb is None:
            print("Error: run_planner hier requires an open BDB"); return
        iterations = session._planner_iters(args)
        # Re-plan: restore the pre-expansion TEMPLATE wrappers so this pass
        # re-derives its per-instance ids from the templates (stable) rather
        # than re-expanding the PRIOR run's per-instance wrappers.  Without
        # this, a second run_planner hier expands wrappers whose ids already
        # sit above the first expansion (5..8 -> 9..12) and keys the new
        # _hier_expansion_map on those now-deleted instance ids, so the
        # checkpoint persist links every expanded row's parent_id to a bundle
        # clear_expanded_bundles just removed — an FK-rejected insert that is
        # silently dropped, leaving a resume missing the whole routing subtree
        # (the coupled staleness bug that blocked C6-09).
        if getattr(session, "_hier_expansion_map", None) and \
                getattr(session, "_hier_bundles_orig", None):
            session.bundles = list(session._hier_bundles_orig)
            session._hier_expansion_map = {}
        # Re-planning invalidates any adopted dogleg (and its pins).
        session._reset_doglegs()
        # Apply user-pinned selections to template wrappers BEFORE expansion
        # so topology_pinned + pinned_seg_layers propagate to all instances.
        session._apply_selections()
        # Per-cell layer policies onto the TEMPLATE wrappers (before the
        # bottom-up cell-local solves plan under them).
        session._apply_layer_policies()
        # Bottom-up cells (set_bottom_up): first give any 90°-rotated
        # instance class its own clone template (candidates generated from
        # the rotated reference's cell-local floorplan), then solve each
        # marked cell's local template bundles once in a dedicated
        # cell-local planner and pin the decision, so expansion broadcasts
        # one uniform assignment to every instance and marks them
        # hier.locked (planned first, never moved).
        # See docs/internal/hier_bottom_up_planning.md §3.
        session._split_bottom_up_rotation_classes()
        session._plan_bottom_up_templates(iterations)
        # Expand cell-level bundles → per-instance absolute-coord wrappers.
        # Each expanded wrapper gets a unique HBundle ID.
        expanded_l, exp_map_raw = session._expand_hier_bundles(session.bundles)
        # P2: freeze the expanded set into the C++-backed container NOW and
        # remap the expansion map onto the vec's ELEMENTS (same objects as
        # `expanded_l` by identity), so every alias — exp_map instance
        # lists, replica entries — mutates the same storage the engines see
        # and every healer call from here on crosses the binding zero-copy.
        expanded = buda.BundleWrapperVec(expanded_l)
        _idx_of = {id(w): i for i, w in enumerate(expanded_l)}
        session._hier_expansion_map = {
            tid: [expanded[_idx_of[id(w)]] for w in ws]
            for tid, ws in exp_map_raw.items()}
        del expanded_l, exp_map_raw, _idx_of
        # priority = -(level * 10000 + n_candidates): higher routes first.
        # Depth-0 before depth-1; fewer candidates (less flexibility) first.
        # BUDA_HIER_DEEP_FIRST=1 (experiment): invert the level key only —
        # deepest level plans first; still fewest-candidates-first per level.
        deep_first = os.environ.get("BUDA_HIER_DEEP_FIRST") == "1"
        for w in expanded:
            b = w.input.original_bundle
            lvl_key = b.level if deep_first else -b.level
            w.hier.priority = lvl_key * 10_000 - len(w.input.candidates)
            w.hier.level    = b.level   # for the per-level planning summary
        session.planner = buda.CongestionPlanner(session.fp, session.layers)
        for pname, pval in session._planner_params.items():
            session.planner.set_planner_param(pname, pval)
        session._apply_healers_ahead(session.planner)
        # Hier planning defaults refine_passes to 1 (an explicit
        # set_planner_param wins, including 0) — see
        # BudaSession._apply_hier_refine_default for the decision record.
        session._apply_hier_refine_default(session.planner)
        # Mirror NUTS inter-bus pitch so the band books reserve the
        # spacing NUTS enforces (Gap 1).  Use set_track_pitch before
        # run_planner to plan a non-default pitch; run_nuts warns if its
        # pitch ends up differing from the one planned here.
        session.planner.set_track_pitch(session._nuts_pitch)
        session._planner_pitch = session._nuts_pitch
        session._configure_capacity_mode(args)   # opt-in signal_tracks (Gap A part 2)
        session.planner.build_congestion_map()
        # Opt-in kWLSpread: stamp each candidate's WL envelope so the planner
        # can price realization risk (post-expansion wrappers are
        # absolute-coord, so the resolver hands back session.fp for them).
        if session._planner_params.get("kWLSpread", -1.0) >= 0.0:
            session._annotate_wl_envelopes(expanded)
        session._planner_iterations = iterations
        with buda.ostream_redirect():
            assignments = session.planner.optimize_topologies(expanded, iterations)
        # Apply assignments.  Each expanded wrapper has a unique HBundle ID so
        # this lookup is unambiguous even for multiple cell instances.
        bid_to_wrapper = {w.input.original_bundle.id: w for w in expanded}
        for asn in assignments:
            w = bid_to_wrapper.get(asn.bundle_id)
            if w is not None:
                w.plan.selected_topology_index = asn.topo_index
                w.input.assigned_v_layer = asn.v_layer_id
                w.input.assigned_h_layer = asn.h_layer_id
                w.plan.seg_layers = list(asn.seg_layers)
                w.plan.seg_perp = list(asn.seg_perp)
        session.bundles = expanded
        # Expansion built FRESH BundleInput objects — re-resolve the layer
        # policies onto the per-instance wrappers before the global planner.
        session._apply_layer_policies()
        session._planner_is_hier = True
        print(f"run_planner hier: {len(session.bundles)} wrappers after expansion")
    else:
        # Re-planning invalidates any adopted dogleg (and its pins): the
        # planner may move neighbors, so cycles are re-detected next NUTS.
        session._reset_doglegs()
        session.planner = buda.CongestionPlanner(session.fp, session.layers)
        for pname, pval in session._planner_params.items():
            session.planner.set_planner_param(pname, pval)
        session._apply_healers_ahead(session.planner)
        # Mirror NUTS inter-bus pitch so the band books reserve the
        # spacing NUTS enforces (Gap 1).  Use set_track_pitch before
        # run_planner to plan a non-default pitch; run_nuts warns if its
        # pitch ends up differing from the one planned here.
        session.planner.set_track_pitch(session._nuts_pitch)
        session._planner_pitch = session._nuts_pitch
        session._configure_capacity_mode(args)   # opt-in signal_tracks (Gap A part 2)
        session.planner.build_congestion_map()
        # Apply architect-pinned selections BEFORE optimizing so the
        # planner scores the correct topology and assigns layers for it.
        session._apply_selections()
        # Tapered fan-in: derive per-segment bit membership on every fan-in
        # bundle's candidates so the planner charges each driver stub for its
        # own sub-bus only (Topology.seg_bits; no-op for non-fan-in bundles).
        session._derive_fanin_bits_all()
        # Opt-in kWLSpread: stamp each candidate's WL envelope so the planner
        # can price realization risk on top of the nominal.
        if session._planner_params.get("kWLSpread", -1.0) >= 0.0:
            session._annotate_wl_envelopes(session.bundles)
        session._planner_is_hier = False
        session._planner_iterations = session._planner_iters(args)
        with buda.ostream_redirect():
            assignments = session.planner.optimize_topologies(session.bundles, session._planner_iterations)
        # Apply planner layer decisions (vector copy in C++ means we must apply here).
        bid_to_wrapper = {w.input.original_bundle.id: w for w in session.bundles}
        for asn in assignments:
            w = bid_to_wrapper.get(asn.bundle_id)
            if w is not None:
                w.plan.selected_topology_index = asn.topo_index
                w.input.assigned_v_layer = asn.v_layer_id
                w.input.assigned_h_layer = asn.h_layer_id
                w.plan.seg_layers = list(asn.seg_layers)
                w.plan.seg_perp = list(asn.seg_perp)
    # Persist the planner's decision into the BDB: expanded per-instance
    # bundles (hier), the selected topology, and per-segment assigned
    # layers — for both flows.
    session._persist_planner_output()


def cmd_select_topology(session, cmd, args, cmd_line):
    # Usage: select_topology <bundle_id | hint | id:N | net:PREFIX> <topo_id | group:N>
    #   bare integer  -> bundle ID (legacy);  bare non-numeric -> net-name hint
    #   id:N          -> force bundle ID;     net:PFX (name:/hint:) -> force hint
    #   <topo_id>     -> pin that single 1-based candidate
    #   group:<N>     -> pin the whole nominal-locus FAMILY (super-candidate) that
    #                    contains candidate N; the planner refines which member wins
    if len(args) < 2:
        print("Error: select_topology requires <bundle_id|hint> and "
              "<topo_id|group:N> (1-based)")
        return
    grp = False
    tok = args[1]
    if tok.lower().startswith("group:"):
        grp, tok = True, tok.split(":", 1)[1]
    try:
        tid = int(tok)
    except ValueError:
        print(f"Error: invalid topology id '{args[1]}'")
        return
    bids, err = session._resolve_bundle_selector(args[0])
    if err:
        print(f"Error: {err}")
        return
    applied = False
    for bid in bids:
        if session._select_single_topology_internal(bid, tid, group=grp):
            applied = True
    if applied:
        session._replan_layers()
        session._persist_topologies()   # refresh is_selected in the BDB


def _parse_selector_list(session, sel_str):
    """Expand a comma list of bundle selectors — numeric IDs, numeric ranges
    (a-b), and net-name hints — into a flat list of bundle IDs.  Prints an error
    per unresolved chunk; returns whatever resolved."""
    bids = []
    for chunk in sel_str.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split('-', 1)
        is_num_range = ('-' in chunk and len(parts) == 2 and
                        all(p.strip().lstrip('-').isdigit() for p in parts))
        if is_num_range:
            b_start, b_end = int(parts[0]), int(parts[1])
            bids.extend(range(b_start, b_end + 1) if b_start <= b_end
                        else range(b_start, b_end - 1, -1))
        else:
            sub, err = session._resolve_bundle_selector(chunk)
            if err:
                print(f"Error: {err}")
                continue
            bids.extend(sub)
    return bids


def cmd_select_topologies(session, cmd, args, cmd_line):
    # Usage: select_topologies <bundle_ids> <topo_id> [<bundle_ids> <topo_id> ...]
    #   bundle_ids: comma list of IDs, ranges (1,5-9,11), and/or net-name hints
    #   (e.g. bus_007,bus_044).  Same id:/net: disambiguation as select_topology.
    if len(args) < 2 or len(args) % 2 != 0:
        print("Error: select_topologies requires (bundle_ids, topo_id) pairs")
        return
    applied = False
    for i in range(0, len(args), 2):
        try:
            tid = int(args[i+1])
        except ValueError:
            print(f"Error: invalid topology ID '{args[i+1]}'")
            continue
        for bid in _parse_selector_list(session, args[i]):
            if session._select_single_topology_internal(bid, tid):
                applied = True
    if applied:
        session._replan_layers()
        session._persist_topologies()   # refresh is_selected in the BDB


def cmd_unpin_topology(session, cmd, args, cmd_line):
    # Usage: unpin_topology <bundle_id|*>
    # Clears select_topology's pin so the next planner run may re-choose.
    if len(args) < 1:
        print("Error: unpin_topology requires a bundle_id (or *)")
        return
    if args[0] == '*':
        n = 0
        for w in session.bundles:
            if (getattr(w.input, "topology_pinned", False)
                    or w.input.pinned_seg_layers
                    or getattr(w.input, "pinned_group", [])):
                n += 1
            w.input.topology_pinned = False
            w.input.pinned_seg_layers = []   # also drop forced edit-pinned layers
            w.input.pinned_group = []        # and any super-candidate group pin
        print(f"Unpinned all bundles ({n} pinned)")
        session._persist_topologies()
        return
    try:
        bid = int(args[0])
    except ValueError:
        print(f"Error: invalid bundle id '{args[0]}'")
        return
    if session._unpin_topology_internal(bid):
        session._persist_topologies()   # refresh is_pinned in the BDB


COMMANDS = {
    "set_planner_param": cmd_set_planner_param,
    "run_planner": cmd_run_planner,
    "select_topology": cmd_select_topology,
    "select_topologies": cmd_select_topologies,
    "unpin_topology": cmd_unpin_topology,
}
