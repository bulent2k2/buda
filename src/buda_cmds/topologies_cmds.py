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

"""Stage 2 — topology generation commands.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import buda


def _endpoint_label(src, dsts, fanin=False):
    """Human-readable endpoint label for generation messages.  A fan-in
    bundle is rooted at the shared SINK with the drivers as leaves, so the
    arrow is reversed to read naturally."""
    if fanin:
        return f"[{','.join(dsts)}]->{src} fan-in"
    return f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"


def cmd_generate_topologies_for_bundle(session, cmd, args, cmd_line):
    # Usage: generate_topologies_for_bundle <hint> [center_mode] [double_detour] [multi_trunk]
    # Single dst  → 2-pin L/Z/U candidates
    # Multiple dst → multicast trunk+branch candidates
    # Append "center_mode"    to use block centres instead of busterm faces.
    # Append "double_detour"  to include UU_VHV / UU_HVH high-congestion variants.
    # Append "multi_trunk"    to add two-level BITRUNK_HVH/VHV datapath trees.
    use_center        = "center_mode"   in args
    use_double_detour = "double_detour" in args
    use_multi_trunk   = "multi_trunk"   in args
    pos_args = [a for a in args
                if a not in ("center_mode", "double_detour", "multi_trunk")]
    if not pos_args:
        print("Error: generate_topologies_for_bundle requires a hint")
        return
    hint = pos_args[0]
    topo_gen = session._make_topo_gen(session.fp, use_center, use_double_detour,
                                   use_multi_trunk)
    found = False
    for w in session.bundles:
        net_name = w.input.original_bundle.get_net_names()[0]
        if net_name.startswith(hint):
            ep = session._bundle_endpoints(w)
            if ep is None:
                print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
                continue
            src, dsts, fanin = ep
            session._validate_endpoint_blocks(net_name, src, dsts)
            old_pin_uid = session._pinned_uid(w)
            kept_user = session._user_candidates(w)
            w.input.candidates = topo_gen.generate_candidates(src, dsts)
            session._reset_plan_for_regen(w, old_pin_uid, kept_user)
            label = _endpoint_label(src, dsts, fanin)
            print(f"Generated {len(w.input.candidates)} topologies for bundle "
                  f"{w.input.original_bundle.id} ({label})")
            if not w.input.candidates:
                # Match the bulk generate_topologies diagnostic — this
                # per-bundle command is documented for debugging exactly
                # these zero-candidate cases, so it should be as loud.
                print(f"Warning: bundle {w.input.original_bundle.id} ({label}) "
                      f"produced NO candidate topology — this bus will be "
                      f"unrouted. Check the placement of blocks {src} and "
                      f"{', '.join(dsts)} "
                      f"(coincident / corner-touch / one contained in the other?).")
            found = True
    if not found: print(f"Warning: Could not find bundle matching hint {hint}")
    elif session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


def cmd_generate_more_topologies(session, cmd, args, cmd_line):
    # Usage: generate_more_topologies <hint> [center_mode] [double_detour] [multi_trunk]
    # ADDITIVE variant of generate_topologies_for_bundle /
    # generate_topologies_for_hbundle (Phase E2 of topo_conn_unification.md):
    # run the generator with the given knobs and merge the new candidates
    # into the bundle's existing list, deduplicated by stable content uid —
    # instead of replacing it.  The merged pool is re-sorted by the same key
    # as generation (wirelength, then type) so cand_index stays a meaningful
    # ranking; the expert accretes a candidate pool across knob experiments
    # without losing SELECTIONS — the pin (and dogleg slot) are remapped to
    # follow their candidate across the re-sort, so raw indices may move but
    # the selected/dogleg candidate is preserved.
    #
    # HIER-AWARE: in a hier-bundled session the hint matches an HBundle id
    # or its first net-name prefix, generation goes through the same 3-case
    # dispatch as generate_hier_topologies (cell-local / cross-level /
    # cross-block floorplans), and a replica match redirects to its template
    # (the bundle that actually carries the routing for all instances).
    # Accretion happens on PRE-expansion templates: after run_planner hier
    # the pools live on per-instance expanded wrappers, so re-run the flow
    # from generate_hier_topologies instead.
    use_center        = "center_mode"   in args
    use_double_detour = "double_detour" in args
    use_multi_trunk   = "multi_trunk"   in args
    pos_args = [a for a in args
                if a not in ("center_mode", "double_detour", "multi_trunk")]
    if not pos_args:
        print("Error: generate_more_topologies requires a hint")
        return
    hint = pos_args[0]
    # Hier session detection: the hier bundler ran (live), or this is a
    # resumed BDB session whose bundles carry hier markers, or — the
    # markerless resume (Codex #254) — a load_pipeline checkpoint holding
    # ONLY same-level cross-block HBundles (no cell_context, no
    # drv_spec_depth): its nets never went through the flat add_net
    # endpoint bookkeeping, which every flat session's did, so an empty
    # _net_endpoints with bundles present marks it hierarchical.
    hier = bool(getattr(session, "_hier_bundles_orig", None)) or (
        session.bdb is not None and (
            any(b.cell_context or b.drv_spec_depth >= 0
                for w in session.bundles
                for b in [w.input.original_bundle])
            or (bool(session.bundles) and not session._net_endpoints)))

    targets = []
    if hier:
        if getattr(session, "_planner_is_hier", False):
            print("Error: generate_more_topologies after run_planner hier — "
                  "the pools now live on per-instance expanded wrappers. "
                  "Accrete on the pre-expansion templates instead: re-run "
                  "generate_hier_topologies (the per-bundle knob memo "
                  "re-applies prior accretions), generate_more_topologies, "
                  "then run_planner hier.")
            return
        by_id = {w.input.original_bundle.id: w for w in session.bundles}
        cell_ids = {w.input.original_bundle.id for w in session.bundles
                    if w.input.original_bundle.cell_context}
        if hint.isdigit() and int(hint) in by_id:
            matches = [by_id[int(hint)]]
        else:
            matches = [w for w in session.bundles
                       if w.input.original_bundle.get_net_names()
                       and w.input.original_bundle.get_net_names()[0]
                          .startswith(hint)]
        seen_ids = set()
        for w in matches:
            b = w.input.original_bundle
            if b.cell_context and b.parent_id in cell_ids:
                # Replica: its routing comes from its template's expansion.
                tw = by_id.get(b.parent_id)
                if tw is not None:
                    print(f"Note: bundle {b.id} is a replica — accreting on "
                          f"its template bundle {b.parent_id} (which carries "
                          f"the routing for every instance).")
                    w = tw
            bid = w.input.original_bundle.id
            if bid not in seen_ids:
                seen_ids.add(bid)
                targets.append(w)
        if not targets:
            print(f"Warning: Could not find bundle matching hint {hint}")
            return
        fp_cache = {}
        comps_by_name = {c.name: c for c in session.bdb.all_components()}
        for w in targets:
            session._generate_hier_topo_one(w, use_center, use_double_detour,
                                            fp_cache, comps_by_name,
                                            use_multi_trunk, additive=True)
    else:
        topo_gen = session._make_topo_gen(session.fp, use_center,
                                          use_double_detour, use_multi_trunk)
        for w in session.bundles:
            net_name = w.input.original_bundle.get_net_names()[0]
            if not net_name.startswith(hint):
                continue
            ep = session._bundle_endpoints(w)
            if ep is None:
                print(f"Warning: no endpoint info for net '{net_name}' — "
                      f"skipping bundle {w.input.original_bundle.id}")
                continue
            src, dsts, fanin = ep
            session._validate_endpoint_blocks(net_name, src, dsts)
            fresh = topo_gen.generate_candidates(src, dsts)
            # topo_uid dedup + WL re-sort with selection/dogleg remap — the
            # SAME helper runs in the knob-memo replays (_apply_gen_knobs /
            # _apply_hier_gen_knobs) so a resumed bundle stays ranked.
            added, dups = session._merge_more_candidates(w, fresh)
            label = _endpoint_label(src, dsts, fanin)
            print(f"Added {added} new topolog{'y' if added == 1 else 'ies'} "
                  f"for bundle {w.input.original_bundle.id} ({label}) — "
                  f"{dups} duplicate(s) skipped, pool now "
                  f"{len(w.input.candidates)}.")
            targets.append(w)
        if not targets:
            print(f"Warning: Could not find bundle matching hint {hint}")
            return
    # Per-bundle knob memo (v15): a resumed bulk generate_[hier_]topologies
    # re-applies these knobs additively, so the accreted pool does not
    # silently revert (Phase E2b).
    knobs = " ".join(k for k, on in (
        ("center_mode", use_center),
        ("double_detour", use_double_detour),
        ("multi_trunk", use_multi_trunk)) if on)
    if session.bdb is not None and knobs:
        for w in targets:
            bid = str(w.input.original_bundle.id)
            prev = set(session.bdb.bundle_gen_knobs(bid).split())
            session.bdb.set_bundle_gen_knobs(
                bid, " ".join(sorted(prev | set(knobs.split()))))
    if session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


def cmd_generate_topologies(session, cmd, args, cmd_line):
    # Usage: generate_topologies [center_mode] [double_detour]
    # Generates topologies for every bundle produced by run_bundler,
    # deriving src/dst block names from the netlist automatically.
    if not session.bundles:
        if session._net_endpoints:
            print("Warning: no bundles to generate topologies for — nets are "
                  "defined but the netlist hasn't been bundled. Run `run_bundler` "
                  "(or `run_hier_bundler` for a BDB hierarchy) first.")
        else:
            print("Warning: no bundles to generate topologies for — define nets "
                  "with add_net/add_bus, then run `run_bundler` first.")
        return
    use_center        = "center_mode"   in args
    use_double_detour = "double_detour" in args
    use_multi_trunk   = "multi_trunk"   in args
    topo_gen = session._make_topo_gen(session.fp, use_center, use_double_detour,
                                   use_multi_trunk)
    for w in session.bundles:
        net_name = w.input.original_bundle.get_net_names()[0]
        ep = session._bundle_endpoints(w)
        if ep is None:
            print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
            continue
        src, dsts, fanin = ep
        session._validate_endpoint_blocks(net_name, src, dsts)
        old_pin_uid = session._pinned_uid(w)
        kept_user = session._user_candidates(w)
        w.input.candidates = topo_gen.generate_candidates(src, dsts)
        session._reset_plan_for_regen(w, old_pin_uid, kept_user)
        session._apply_gen_knobs(w, src, dsts, old_pin_uid)
        label = _endpoint_label(src, dsts, fanin)
        print(f"Generated {len(w.input.candidates)} topologies for bundle "
              f"{w.input.original_bundle.id} ({label}) {session._bundle_nets_suffix(w)}")
        if not w.input.candidates:
            # A zero-candidate bundle is a guaranteed unrouted bus (the
            # planner has nothing to select and run_nuts places nothing).
            # Surface it loudly here — naming the blocks — rather than
            # leaving only the late, generic run_nuts "no selected
            # topology" warning.  Usually a degenerate placement: the two
            # endpoint blocks coincide, touch only at a corner, or one
            # fully contains the other (no routable channel between them).
            print(f"Warning: bundle {w.input.original_bundle.id} ({label}) "
                  f"produced NO candidate topology — this bus will be "
                  f"unrouted. Check the placement of blocks {src} and "
                  f"{', '.join(dsts)} "
                  f"(coincident / corner-touch / one contained in the other?).")
    # Restore the sidecar baseline (pins + per-segment layer overrides) onto
    # the freshly generated candidates, so the live state matches the GUI
    # even before run_planner. A later select_topology overrides it; the
    # sidecar's layer overrides for a matching topology are still merged.
    session._apply_selections()
    nt = session._persist_topologies()
    if nt:
        print(f"[BDB] persisted {nt} candidate topolog"
              f"{'y' if nt == 1 else 'ies'} to the open BDB.")


def cmd_generate_hier_topologies(session, cmd, args, cmd_line):
    # generate_hier_topologies [center_mode] [double_detour] [multi_trunk]
    # Generates topology candidates for all HBundles produced by
    # run_hier_bundler.  Three cases per bundle:
    #   (a) cell-level (cell_context set)     → cell-local floorplan
    #   (c) cross-level (drv_spec_depth >= 0) → custom floorplan from actual endpoint blocks
    #   (b) same-level cross-block             → BDB depth-D floorplan
    if session.bdb is None:
        print("Error: generate_hier_topologies requires an open BDB"); return
    if not session.bundles:
        print("Warning: no HBundles to generate topologies for — run "
              "`run_hier_bundler` first.")
        return
    use_center        = "center_mode"   in args
    use_double_detour = "double_detour" in args
    use_multi_trunk   = "multi_trunk"   in args
    # Remembered so a rotation-class clone created later (at run_planner
    # hier) generates its candidates with the same knobs.
    session._hier_gen_knobs = (use_center, use_double_detour, use_multi_trunk)

    # Cache floorplans keyed by (depth, is_cell_local, instance_or_empty)
    fp_cache = {}
    total_candidates = 0
    comps_by_name = {c.name: c for c in session.bdb.all_components()}

    for w in session.bundles:
        old_pin_uid = session._pinned_uid(w)
        n = session._generate_hier_topo_one(w, use_center, use_double_detour,
                                          fp_cache, comps_by_name,
                                          use_multi_trunk)
        # Honor the bundle's persisted generation-knob memo (v15): re-apply
        # prior generate_more_topologies accretions additively so the pool
        # does not silently revert on a bulk regeneration (Phase E2b — the
        # hier twin of the flat command's _apply_gen_knobs call).
        session._apply_hier_gen_knobs(w, fp_cache, comps_by_name,
                                      old_pin_uid)
        total_candidates += n
    print(f"generate_hier_topologies: {len(session.bundles)} bundles, "
          f"{total_candidates} total candidates")
    # Restore the sidecar baseline onto the fresh candidates (see
    # generate_topologies); keeps live state and GUI consistent pre-plan.
    session._apply_selections()
    nt = session._persist_topologies()
    if nt:
        print(f"[BDB] persisted {nt} candidate topolog"
              f"{'y' if nt == 1 else 'ies'} to the open BDB.")


def cmd_generate_topologies_for_hbundle(session, cmd, args, cmd_line):
    # Usage: generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour] [multi_trunk]
    if not args:
        print("Error: generate_topologies_for_hbundle requires a bundle_id"); return
    if session.bdb is None:
        print("Error: generate_topologies_for_hbundle requires an open BDB"); return
    try:
        bid = int(args[0])
    except ValueError:
        print(f"Error: invalid bundle_id {args[0]!r}"); return
    use_center        = "center_mode"   in args[1:]
    use_double_detour = "double_detour" in args[1:]
    use_multi_trunk   = "multi_trunk"   in args[1:]
    target_w = next((w for w in session.bundles if w.input.original_bundle.id == bid), None)
    if target_w is None:
        orig_w = next((w for w in session._hier_bundles_orig
                       if w.input.original_bundle.id == bid), None)
        if orig_w is not None:
            print(f"Note: bundle {bid} was expanded by run_planner hier — "
                  f"re-run generate_hier_topologies before planning.")
        else:
            print(f"Error: bundle {bid} not found")
        return
    fp_cache = {}
    comps_by_name = {c.name: c for c in session.bdb.all_components()}
    n = session._generate_hier_topo_one(target_w, use_center, use_double_detour,
                                      fp_cache, comps_by_name, use_multi_trunk)
    print(f"generate_topologies_for_hbundle: bundle {bid} — {n} candidates")
    if session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


COMMANDS = {
    "generate_topologies_for_bundle": cmd_generate_topologies_for_bundle,
    "generate_more_topologies": cmd_generate_more_topologies,
    "generate_topologies": cmd_generate_topologies,
    "generate_hier_topologies": cmd_generate_hier_topologies,
    "generate_topologies_for_hbundle": cmd_generate_topologies_for_hbundle,
}
