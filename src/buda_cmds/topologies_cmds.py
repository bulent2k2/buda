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
            ep = session._net_endpoints.get(net_name)
            if ep is None:
                print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
                continue
            src, dsts = ep
            session._validate_endpoint_blocks(net_name, src, dsts)
            old_pin_uid = session._pinned_uid(w)
            kept_user = session._user_candidates(w)
            w.input.candidates = topo_gen.generate_candidates(src, dsts)
            session._reset_plan_for_regen(w, old_pin_uid, kept_user)
            label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
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
    # ADDITIVE variant of generate_topologies_for_bundle (Phase E2 of
    # topo_conn_unification.md): run the generator with the given knobs
    # and merge the new candidates into the bundle's existing list,
    # deduplicated by stable content uid — instead of replacing it.
    # The merged pool is re-sorted by the same key as generation
    # (wirelength, then type) so cand_index stays a meaningful ranking;
    # the expert accretes a candidate pool across knob experiments without
    # losing SELECTIONS — the pin (and dogleg slot) are remapped to follow
    # their candidate across the re-sort, so raw indices may move but the
    # selected/dogleg candidate is preserved.
    use_center        = "center_mode"   in args
    use_double_detour = "double_detour" in args
    use_multi_trunk   = "multi_trunk"   in args
    pos_args = [a for a in args
                if a not in ("center_mode", "double_detour", "multi_trunk")]
    if not pos_args:
        print("Error: generate_more_topologies requires a hint")
        return
    hint = pos_args[0]
    topo_gen = session._make_topo_gen(session.fp, use_center, use_double_detour,
                                   use_multi_trunk)
    found = False
    for w in session.bundles:
        net_name = w.input.original_bundle.get_net_names()[0]
        if net_name.startswith(hint):
            ep = session._net_endpoints.get(net_name)
            if ep is None:
                print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
                continue
            src, dsts = ep
            session._validate_endpoint_blocks(net_name, src, dsts)
            fresh = topo_gen.generate_candidates(src, dsts)
            existing = list(w.input.candidates)
            seen = {buda.topo_uid(c) for c in existing}
            added = 0
            for c in fresh:
                uid = buda.topo_uid(c)
                if uid in seen:
                    continue
                seen.add(uid)
                existing.append(c)
                added += 1
            # Keep the accreted pool WL-sorted (mirrors the C++ annotate_and_sort:
            # wirelength ascending, then type) so cand_index stays a meaningful
            # ranking instead of "old pool, then newly-appended tail".  The shared
            # helper re-sorts and remaps the selection + dogleg refs so the pin
            # follows its candidate; the SAME helper runs in the knob-memo replay
            # (_apply_gen_knobs) so a resumed bundle stays ranked.
            existing = session._resort_pool_preserving_selection(w, existing)
            w.input.candidates = existing
            label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
            print(f"Added {added} new topolog{'y' if added == 1 else 'ies'} "
                  f"for bundle {w.input.original_bundle.id} ({label}) — "
                  f"{len(fresh) - added} duplicate(s) skipped, pool now "
                  f"{len(existing)}.")
            found = True
    if not found:
        print(f"Warning: Could not find bundle matching hint {hint}")
    else:
        # Per-bundle knob memo (v15): a resumed bulk generate_topologies
        # re-applies these knobs additively, so the accreted pool does
        # not silently revert (Phase E2b).
        knobs = " ".join(k for k, on in (
            ("center_mode", use_center),
            ("double_detour", use_double_detour),
            ("multi_trunk", use_multi_trunk)) if on)
        if session.bdb is not None and knobs:
            for w in session.bundles:
                nn = w.input.original_bundle.get_net_names()[0]
                if nn.startswith(hint):
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
        ep = session._net_endpoints.get(net_name)
        if ep is None:
            print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
            continue
        src, dsts = ep
        session._validate_endpoint_blocks(net_name, src, dsts)
        old_pin_uid = session._pinned_uid(w)
        kept_user = session._user_candidates(w)
        w.input.candidates = topo_gen.generate_candidates(src, dsts)
        session._reset_plan_for_regen(w, old_pin_uid, kept_user)
        session._apply_gen_knobs(w, src, dsts, old_pin_uid)
        label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
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

    # Cache floorplans keyed by (depth, is_cell_local, instance_or_empty)
    fp_cache = {}
    total_candidates = 0
    comps_by_name = {c.name: c for c in session.bdb.all_components()}

    for w in session.bundles:
        n = session._generate_hier_topo_one(w, use_center, use_double_detour,
                                          fp_cache, comps_by_name,
                                          use_multi_trunk)
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
