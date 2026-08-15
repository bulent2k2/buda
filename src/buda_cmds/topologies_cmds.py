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
import contextlib
import difflib
import io
import os
import sys

import buda

# Small-work gate for the parallel hier-generation batch (P5): below this
# many bundles the pool-spawn cost outweighs the win and the sequential
# loop runs.  Module-level so tests can lower it to force the batch path
# on small fixtures.
_BATCH_MIN_BUNDLES = 8


def _endpoint_label(src, dsts, fanin=False):
    """Human-readable endpoint label for generation messages.  A fan-in
    bundle is rooted at the shared SINK with the drivers as leaves, so the
    arrow is reversed to read naturally."""
    if fanin:
        return f"[{','.join(dsts)}]->{src} fan-in"
    return f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"


# All keyword flags a generation command accepts (used to split them from the
# positional hint/bundle_id arguments).
_GEN_FLAGS = ("center_mode", "double_detour", "multi_trunk",
              "hanan_loci", "no_hanan_loci", "spine_relays")


def _parse_gen_flags(args):
    """Parse a generation command's keyword flags.

    Returns (use_center, use_double_detour, use_multi_trunk, use_hanan_loci,
    loci_explicit, use_spine_relays).  `hanan_loci` is DEFAULT-ON since the
    default flip (TopologyGenerator::allow_hanan_loci_, src/topology.h):
    `no_hanan_loci` is the per-command opt-out (midpoint-only trunk loci), and
    the legacy `hanan_loci` flag remains accepted as a keep-on no-op so
    pre-flip scripts and v15 knob memos keep working.  If both are passed,
    `no_hanan_loci` wins.  loci_explicit is True when either loci flag was
    given — the per-bundle commands record the explicit polarity in the
    knob memo then (see _record_gen_knob_memo).  `spine_relays` (default OFF —
    byte-identical without it) opts MST relay-hub completion into the
    collector-spine form (src/topology.cpp complete_relay_junctions); it is an
    opt-in knob-memo token like multi_trunk."""
    return ("center_mode" in args,
            "double_detour" in args,
            "multi_trunk" in args,
            "no_hanan_loci" not in args,
            ("hanan_loci" in args) or ("no_hanan_loci" in args),
            "spine_relays" in args)


def _reject_unknown_gen_flags(cmd, args):
    """Fail loudly (stop the flow) when a FLAG-ONLY generation command is given a
    token that is not a known option.

    `_parse_gen_flags` only tests membership (`"multi_trunk" in args`), so an
    unrecognized token — a typo like `multi_trunc`, or a stray `foo` — was
    silently ignored, and the command ran with the feature the user thought they
    asked for quietly disabled.  This mirrors the unknown-COMMAND guard in
    do_command: unknown input is a hard error, not a silent skip.  Prints the
    offending token(s) and the valid options (plus a close-match hint) and exits
    non-zero so the script stops.

    Only for commands whose every argument is a flag (`generate_topologies`,
    `generate_hier_topologies`).  The positional variants (`..._for_bundle`,
    `..._for_hbundle`, `generate_more_topologies`) take a hint/id first, so an
    unknown token there cannot be told apart from a bundle name and is left
    alone."""
    unknown = [a for a in args if a not in _GEN_FLAGS]
    if not unknown:
        return
    valid = " ".join(_GEN_FLAGS)
    hint = ""
    for u in unknown:
        m = difflib.get_close_matches(u, _GEN_FLAGS, n=1)
        if m:
            hint = f" Did you mean '{m[0]}'?"
            break
    plural = "s" if len(unknown) > 1 else ""
    print(f"Error: {cmd}: unknown option{plural} "
          f"{', '.join(repr(u) for u in unknown)}. "
          f"Valid options: {valid}.{hint}")
    sys.exit(1)


def _record_gen_knob_memo(session, wrappers, tokens):
    """THE v15 per-bundle generation-knob memo writer (BDB
    `bundle_gen_knobs`; every write site goes through here).

    ENCODING: a space-separated, sorted SET of knob tokens per bundle id —
    the opt-in tokens `center_mode` / `double_detour` / `multi_trunk` /
    `hanan_loci` / `spine_relays`, plus the default-flip opt-OUT token
    `no_hanan_loci`.
    Merging is a union with any previously recorded tokens, EXCEPT that
    `hanan_loci` and `no_hanan_loci` are mutually exclusive in the store:
    recording either polarity discards the other, so the LAST explicit
    choice wins (post-flip, a stored `hanan_loci` is otherwise a keep-on
    no-op, retained for pre-flip v15 memos).

    REPLAY (the bulk generate_[hier_]topologies via _apply_gen_knobs /
    _apply_hier_gen_knobs): opt-in tokens re-generate ADDITIVELY (uid-dedup
    merge, today's semantics), while `no_hanan_loci` re-generates the
    bundle's BASE pool midpoint-only — a replacement, because an additive
    merge can only ADD and cannot remove the default-on loci candidates —
    so an explicit opt-out round-trips across a bulk regeneration exactly
    like the opt-ins round-trip: a pool generated with `no_hanan_loci`
    stays midpoint-only after the bulk replays memos.

    No-op without an open BDB or with no tokens."""
    if session.bdb is None or not tokens:
        return
    new = set(tokens)
    for w in wrappers:
        bid = str(w.input.original_bundle.id)
        merged = set(session.bdb.bundle_gen_knobs(bid).split()) | new
        if "no_hanan_loci" in new:
            merged.discard("hanan_loci")
        elif "hanan_loci" in new:
            merged.discard("no_hanan_loci")
        session.bdb.set_bundle_gen_knobs(bid, " ".join(sorted(merged)))


def cmd_set_prune_dominated(session, cmd, args, cmd_line):
    # Usage: set_prune_dominated [on|off]
    # Opt-in WL-dominance candidate pruning (default OFF — flows are
    # bit-identical without it).  When on, every generation command
    # (generate_topologies / generate_hier_topologies / the per-bundle
    # variants, but NOT the additive generate_more_topologies) drops a
    # candidate whose WL envelope bottom exceeds an EQUIVALENT candidate's
    # envelope top — gated on non-WL routing equivalence (same block
    # contract/feedthru declarations, same segment directions and layer
    # hints, survivor slide windows covering the dominated one's, survivor
    # spans inside the dominated one's), so the escalation ladder / ripup
    # never lose the only routable option.  Declare BEFORE the generation
    # command; pruning renumbers candidate indices, so select_topology pins
    # must come from an opted-in run.
    val = args[0].lower() if args else "on"
    if val not in ("on", "off"):
        print(f"Error: set_prune_dominated expects on|off, got {args[0]!r}")
        return
    session._prune_dominated = (val == "on")
    print(f"WL-dominance candidate pruning {'ENABLED' if val == 'on' else 'disabled'} "
          f"(applies at the next generate_[hier_]topologies).")


def cmd_set_dedup_loci(session, cmd, args, cmd_line):
    # Usage: set_dedup_loci [on|off]
    # Opt-in nominal-locus candidate dedup (default OFF — bit-identical without
    # it).  When on, every generation command (NOT the additive
    # generate_more_topologies) collapses candidates that are the SAME
    # topological choice differing only in a nominal trunk locus WITHIN a
    # shared slide window — same connectivity, slide windows, block taps and
    # net-pull (e.g. b44's TRUNK_H@y10830 / y11330 / y11830, all on trunk slide
    # window [10830,11830]).  The best-estimated (lowest-WL) member of each
    # group survives; the trunk slides with its stubs, so members route within
    # NUTS realization-noise of each other (the residual spread is the b44
    # realization sensitivity, not a real DOF).  A LOSSY cleanup for a tidier
    # pool — declare BEFORE generation; it renumbers indices, so select_topology
    # pins must come from an opted-in run.
    val = args[0].lower() if args else "on"
    if val not in ("on", "off"):
        print(f"Error: set_dedup_loci expects on|off, got {args[0]!r}")
        return
    session._dedup_loci = (val == "on")
    print(f"Nominal-locus candidate dedup {'ENABLED' if val == 'on' else 'disabled'} "
          f"(applies at the next generate_[hier_]topologies).")


def cmd_set_trim_mst_legs(session, cmd, args, cmd_line):
    # Usage: set_trim_mst_legs [on|off]        (default off)
    # Opt-in shared-leg trim for MST candidates (default OFF — byte-identical
    # without it).  `realize_mst_edge` routes each edge on its own, so two
    # edges incident on the SAME block start at the same face point; when both
    # go L-shaped with the same first axis, the shorter one's leg lies entirely
    # inside the longer one's and the longer leg's prefix is a duplicate with a
    # FREE END.  No audit catches it (the ANTENNA rule counts attachment
    # POSITIONS and the long leg has two elsewhere; #514's tap-overhang rule
    # wants the piece over a block the segment taps, and this one taps
    # nothing).  Its cost is downstream: the overshoot pushes the leg's end PAST
    # the divergence junction, which makes that junction a mid-span conn, and
    # DetailedNUTS only snaps a bit to its own via at an ENDPOINT conn — so
    # every bit keeps the shared abstract end.
    #
    # When on, each leg is cut back to the shorter sibling's far end (that
    # edge's own bend, or a block face, so connectivity holds by construction),
    # on both realization paths.
    #
    # OPT-IN because the mutation is not local to the wire it fixes: candidates
    # are WL-SORTED, so shortening one re-sorts the pool and changes which
    # candidates clear generation gates.  Measured on chip3_topdown, 9 of 640
    # bundles change selected topology TYPE and 3 change pool SIZE (control,
    # main vs main: 0), and leaf bundles elsewhere lose tracks — 260 -> 636
    # unplaced bits.  The geometry is sound; the SEARCH SPACE moves.  Declare
    # BEFORE the generation command; the trim renumbers candidate indices, so
    # select_topology pins must come from an opted-in run.
    #
    # Covers the START-shared half only — legs that END at the shared node, and
    # the same geometry from TRUNK stub generation, are still open (strict
    # xfails in test_mst_shared_leg_prefix.py).
    val = args[0].lower() if args else "on"
    if val not in ("on", "off"):
        print(f"Error: set_trim_mst_legs expects on|off, got {args[0]!r}")
        return
    session._trim_mst_legs = (val == "on")
    print(f"MST shared-leg trim {'ENABLED' if val == 'on' else 'disabled'} "
          f"(applies at the next generate_[hier_]topologies; renumbers indices).")


def cmd_set_trim_trunk_stubs(session, cmd, args, cmd_line):
    # Usage: set_trim_trunk_stubs [on|off]      (default off)
    # Let the H trunk path suppress a redundant collinear stub — the pass
    # add_trunk ALREADY runs, which only add_trunk_v has ever been allowed to.
    #
    # A trunk whose spine passes several blocks stacked along the stub axis
    # taps each with its own stub off the same spine point.  When a farther
    # block's stub already crosses a nearer block's along-extent, the nearer
    # stub is a duplicate lying entirely inside it — no extra coverage, no
    # extra flexibility.  add_trunk suppresses exactly that, farthest-first,
    # and only a CONFIRMED SURVIVOR may suppress (so a chain A<-B<-C cannot
    # leave B uncovered); the pass is gated on `suppress_stubs`, and the H/V
    # unification passed false from add_trunk_h to keep the H output
    # byte-for-byte identical.  The asymmetry outlived the refactor.
    #
    # Measured (tools/experiment/scan_collinear_stubs.py, 13 flows): all 108
    # REDUNDANT collinear pairs are VERTICAL stubs off a HORIZONTAL spine, in
    # TRUNK_H / TRUNK_H_OOB.  TRUNK_V carries none — its suppressor removes
    # them already.
    #
    # OPT-IN for the set_trim_mst_legs reason: candidates are WL-SORTED, so
    # dropping a stub re-sorts the pool and changes which candidates clear the
    # generation gates.  Declare BEFORE the generation command; it renumbers
    # candidate indices, so select_topology pins must come from an opted-in run.
    val = args[0].lower() if args else "on"
    if val not in ("on", "off"):
        print(f"Error: set_trim_trunk_stubs expects on|off, got {args[0]!r}")
        return
    session._trim_trunk_stubs = (val == "on")
    print(f"Trunk redundant-stub trim {'ENABLED' if val == 'on' else 'disabled'} "
          f"(applies at the next generate_[hier_]topologies; renumbers indices).")


def cmd_set_keepout_loci(session, cmd, args, cmd_line):
    # Usage: set_keepout_loci all|outside          (default all)
    #
    # WHICH KEEPOUTS CONTRIBUTE HANAN LOCI (opens_interchange.md item 12).
    # `all` (default, historical) = every keepout's four edges become grid
    # lines.  `outside` = only keepouts reaching beyond a block do; one lying
    # wholly inside a block still BLOCKS identically, it just stops adding
    # coordinates.
    #
    # The problem it answers is quadratic.  The Hanan grid is the PRODUCT of
    # its two axis sets, so a technology that draws obstruction finely buys
    # grid without bound: one `fakeram45_256x16` carries 99 OBS rects, 133 of
    # them import 13,034 keepouts, and demo/ariane's grid goes 2,479 ->
    # 2,508,972 cells with `run_planner hier` killed at 50 minutes.  Under
    # `outside` it is 6,327 cells and the flow finishes in ~19 s.
    #
    # OPT-IN, and the reason is a correction worth keeping: the first version
    # of this shipped unconditionally, justified by "a position inside a
    # block is unreachable anyway".  That is FALSE — a trunk may cross a
    # block over-the-cell, so an interior locus is a real candidate position.
    # Measured on flow/rv/soc_conv_div, whose macros carry 10 OBS rects:
    # bundle 42 moved off a locus contributed by an OBS edge (TRUNK_V@x108000,
    # 2 segments) onto a 4-segment alternative carrying dangling metal, which
    # the QoR corpus could not see (its metric is overlaps/unplaced/
    # viol_bundles) and test_tapered_bit_spans caught.  So this REMOVES
    # candidate positions, exactly like set_prune_dominated / set_dedup_loci /
    # set_trim_mst_legs, and like them it is asked for by name.
    #
    # Declare it BEFORE import_def_lef / generation.
    val = args[0].lower() if args else "all"
    if val not in ("all", "outside"):
        print(f"Error: set_keepout_loci expects all|outside, got {args[0]!r}")
        return
    session.fp.set_keepout_loci_outside_only(val == "outside")
    if val == "outside":
        print("Keepout Hanan loci: OUTSIDE-only — a keepout inside a block "
              "still blocks but adds no grid line (candidate positions change).")
    else:
        print("Keepout Hanan loci: ALL (default).")


_DROP_DANGLING_MODES = {"off", "on", "drop", "clamp", "clamp_drop"}


def cmd_set_drop_dangling(session, cmd, args, cmd_line):
    # Usage: set_drop_dangling [off|on|drop|clamp|clamp_drop]   (default off)
    # Opt-in handling (default OFF — bit-identical without it) for candidates
    # with a DANGLING segment (a single non-block connection — a wire whose
    # other end connects to nothing) or an UNBOUNDED slide window (the +/-2^30
    # no-clamp sentinel): the OOB detour / MST-relay geometry that survives the
    # coverage gate but renders as a dangling overshoot (e.g. b44's
    # TRUNK_*_OOB(+MST) candidates).  Applies at the end of every generation
    # command (NOT the additive generate_more_topologies).  Modes, least to
    # most aggressive:
    #   clamp      — bound every unbounded slide window to the design extent
    #                (blocks bbox + candidate spans + margin) via perp_clamp;
    #                drop NOTHING, so OOB detour routes are KEPT but can no
    #                longer be flung to infinity.
    #   clamp_drop — clamp the windows AND drop only the TRULY dangling
    #                candidates (a segment with a single non-block connection);
    #                the merely-unbounded OOB detours survive (clamped).
    #   drop (=on) — drop ANY candidate with a dangling seg OR an unbounded
    #                window (the original behavior; most aggressive — removes
    #                OOB detours entirely).
    # Never drops/clamps a USER candidate, never drops the pinned candidate,
    # and never strands a bundle.  Declare BEFORE generation; drop/clamp_drop
    # renumber indices, so select_topology pins must come from an opted-in run.
    val = args[0].lower() if args else "on"
    if val not in _DROP_DANGLING_MODES:
        print(f"Error: set_drop_dangling expects "
              f"off|on|drop|clamp|clamp_drop, got {args[0]!r}")
        return
    mode = "drop" if val == "on" else val
    session._drop_dangling_mode = mode
    if mode == "off":
        print("Dangling/unclamped candidate handling disabled "
              "(applies at the next generate_[hier_]topologies).")
    else:
        print(f"Dangling/unclamped candidate handling: {mode.upper()} "
              f"(applies at the next generate_[hier_]topologies).")


def cmd_generate_topologies_for_bundle(session, cmd, args, cmd_line):
    # Usage: generate_topologies_for_bundle <bundle_id | hint | id:N | net:PFX> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]
    # The selector resolves like select_topology's: a bare integer is a bundle
    # ID, a bare non-numeric token is a net-name prefix (hint), and id:/net:
    # force one or the other (net:10 matches a bus '10net' vs bare 10 = ID 10).
    # Single dst  → 2-pin L/Z/U candidates
    # Multiple dst → multicast trunk+branch candidates
    # Append "center_mode"    to use block centres instead of busterm faces.
    # Append "double_detour"  to include UU_VHV / UU_HVH high-congestion variants.
    # Append "multi_trunk"    to add two-level BITRUNK_HVH/VHV datapath trees.
    # Append "no_hanan_loci"  for midpoint-only trunk loci (Hanan-line loci
    #                         are the default; "hanan_loci" = keep-on no-op).
    # Append "spine_relays"   to complete MST relay hubs with a collector spine
    #                         instead of the over-the-cell bracket chain.
    (use_center, use_double_detour, use_multi_trunk,
     use_hanan_loci, loci_explicit, use_spine_relays) = _parse_gen_flags(args)
    pos_args = [a for a in args if a not in _GEN_FLAGS]
    if not pos_args:
        print("Error: generate_topologies_for_bundle requires a bundle id or hint")
        return
    selector = pos_args[0]
    bids, err = session._resolve_bundle_selector(selector)
    if err:
        print(f"Error: generate_topologies_for_bundle: {err}")
        return
    bid_set = set(bids)
    topo_gen = session._make_topo_gen(session.fp, use_center, use_double_detour,
                                   use_multi_trunk, use_hanan_loci,
                                   use_spine_relays)
    found = False
    remembered = []
    for w in session.bundles:
        names = w.input.original_bundle.get_net_names()
        net_name = names[0] if names else ""
        if w.input.original_bundle.id in bid_set:
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
            session._cleanup_candidate_pools([w])
            remembered.append(w)
            found = True
    if not found: print(f"Warning: Could not find bundle matching '{selector}'")
    else:
        # An explicit loci polarity (and a spine_relays opt-in) is recorded in
        # the knob memo so a later bulk generate_topologies cannot silently
        # flip this bundle's pool back (the loci opt-out especially — see
        # _record_gen_knob_memo).
        memo_tokens = []
        if loci_explicit:
            memo_tokens.append("hanan_loci" if use_hanan_loci else "no_hanan_loci")
        if use_spine_relays:
            memo_tokens.append("spine_relays")
        if memo_tokens:
            _record_gen_knob_memo(session, remembered, memo_tokens)
        if session._persist_topologies():
            print("[BDB] re-persisted candidate topologies to the open BDB.")


def cmd_generate_more_topologies(session, cmd, args, cmd_line):
    # Usage: generate_more_topologies <hint> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]
    # ADDITIVE variant of generate_topologies_for_bundle /
    # generate_topologies_for_hbundle (Phase E2 of topo_conn_unification.md):
    # run the generator with the given knobs and merge the new candidates
    # into the bundle's existing list, deduplicated by stable content uid —
    # instead of replacing it.  The merged pool is re-sorted by the same key
    # as generation (wirelength, then segment count, then type) so
    # cand_index stays a meaningful
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
    #
    # hanan_loci is default-ON since the default flip; "no_hanan_loci"
    # generates the accretion midpoint-only (a subset — additive, so it
    # removes nothing) AND records the opt-out in the knob memo below.
    # "spine_relays" accretes spine-completed MST variants (recorded in the
    # memo, replayed additively like multi_trunk).
    (use_center, use_double_detour, use_multi_trunk,
     use_hanan_loci, loci_explicit, use_spine_relays) = _parse_gen_flags(args)
    pos_args = [a for a in args if a not in _GEN_FLAGS]
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
                                            use_multi_trunk, additive=True,
                                            use_hanan_loci=use_hanan_loci,
                                            use_spine_relays=use_spine_relays)
    else:
        topo_gen = session._make_topo_gen(session.fp, use_center,
                                          use_double_detour, use_multi_trunk,
                                          use_hanan_loci, use_spine_relays)
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
    # re-applies these knobs — opt-ins additively, the no_hanan_loci opt-out
    # by midpoint-only base regeneration — so the accreted pool does not
    # silently revert (Phase E2b).  Encoding: _record_gen_knob_memo.
    tokens = [k for k, on in (
        ("center_mode", use_center),
        ("double_detour", use_double_detour),
        ("multi_trunk", use_multi_trunk),
        ("hanan_loci", loci_explicit and use_hanan_loci),
        ("no_hanan_loci", loci_explicit and not use_hanan_loci),
        ("spine_relays", use_spine_relays)) if on]
    _record_gen_knob_memo(session, targets, tokens)
    if session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


def cmd_generate_topologies(session, cmd, args, cmd_line):
    # Usage: generate_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]
    # Generates topologies for every bundle produced by run_bundler,
    # deriving src/dst block names from the netlist automatically.
    # Hanan-line trunk loci are DEFAULT-ON (the hanan_loci default flip);
    # "no_hanan_loci" opts this run out (midpoint-only), "hanan_loci" is a
    # keep-on no-op for backward compatibility.  "spine_relays" opts MST
    # relay-hub completion into the collector-spine form (default off —
    # byte-identical without it).  Bulk flags are per-invocation (not sticky);
    # a per-bundle no_hanan_loci / spine_relays knob memo is honored in
    # _apply_gen_knobs.
    # Validate the option syntax FIRST — a malformed command is rejected
    # regardless of session state, so it can't slip past the prerequisite
    # warning below (Codex #466).
    _reject_unknown_gen_flags(cmd, args)
    if not session.bundles:
        if session._net_endpoints:
            print("Warning: no bundles to generate topologies for — nets are "
                  "defined but the netlist hasn't been bundled. Run `run_bundler` "
                  "(or `run_hier_bundler` for a BDB hierarchy) first.")
        else:
            print("Warning: no bundles to generate topologies for — define nets "
                  "with add_net/add_bus, then run `run_bundler` first.")
        return
    (use_center, use_double_detour, use_multi_trunk,
     use_hanan_loci, _loci_explicit, use_spine_relays) = _parse_gen_flags(args)
    topo_gen = session._make_topo_gen(session.fp, use_center, use_double_detour,
                                   use_multi_trunk, use_hanan_loci,
                                   use_spine_relays)
    bulk_knobs = (use_center, use_double_detour, use_multi_trunk,
                  use_hanan_loci, use_spine_relays)
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
        session._apply_gen_knobs(w, src, dsts, old_pin_uid,
                                 bulk_knobs=bulk_knobs)
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
    # Opt-in WL-dominance prune (set_prune_dominated) — after the pool is
    # final (knob memo replayed), before sidecar restore + persistence.
    session._cleanup_candidate_pools()
    # Restore the sidecar baseline (pins + per-segment layer overrides) onto
    # the freshly generated candidates, so the live state matches the GUI
    # even before run_planner. A later select_topology overrides it; the
    # sidecar's layer overrides for a matching topology are still merged.
    session._apply_selections()
    # Then the BDB's durable pins (uid-keyed) — BEFORE the persist below
    # rewrites the topology table from session state, which is what wiped a
    # previous session's pin on every flow re-run (a rebuild never calls
    # load_pipeline, so nothing else restores them).
    session._apply_bdb_pins()
    nt = session._persist_topologies()
    if nt:
        print(f"[BDB] persisted {nt} candidate topolog"
              f"{'y' if nt == 1 else 'ies'} to the open BDB.")


def cmd_generate_hier_topologies(session, cmd, args, cmd_line):
    # generate_hier_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]
    # Generates topology candidates for all HBundles produced by
    # run_hier_bundler.  Three cases per bundle:
    #   (a) cell-level (cell_context set)     → cell-local floorplan
    #   (c) cross-level (drv_spec_depth >= 0) → custom floorplan from actual endpoint blocks
    #   (b) same-level cross-block             → BDB depth-D floorplan
    # Validate the option syntax FIRST — a malformed command is rejected
    # regardless of session state (open BDB / bundles), so it can't slip past
    # the prerequisite checks below (Codex #466).
    # "no_hanan_loci" opts this run out of the default-on Hanan-line trunk loci,
    # "hanan_loci" is a keep-on no-op; "spine_relays" opts MST relay-hub
    # completion into the collector-spine form (default off).
    _reject_unknown_gen_flags(cmd, args)
    if session.bdb is None:
        print("Error: generate_hier_topologies requires an open BDB"); return
    if not session.bundles:
        print("Warning: no HBundles to generate topologies for — run "
              "`run_hier_bundler` first.")
        return
    (use_center, use_double_detour, use_multi_trunk,
     use_hanan_loci, _loci_explicit, use_spine_relays) = _parse_gen_flags(args)
    # Remembered so a rotation-class clone created later (at run_planner
    # hier) generates its candidates with the same knobs, and so the knob
    # memo replay (_apply_hier_gen_knobs) knows this run's effective loci /
    # spine setting.
    session._hier_gen_knobs = (use_center, use_double_detour, use_multi_trunk,
                               use_hanan_loci, use_spine_relays)

    # Cache floorplans keyed by (depth, is_cell_local, instance_or_empty)
    fp_cache = {}
    total_candidates = 0
    comps_by_name = {c.name: c for c in session.bdb.all_components()}

    # Parallel batch generation (chip_flow_parallelism.md P5): per-bundle
    # generation is independent (private generators; shared floorplans are
    # read-only), so the C++ calls fan out across a thread pool with the
    # GIL released (buda.generate_candidates_batch) — decision-identical
    # by construction, and print-identical too: each bundle's prep
    # warnings are buffered and replayed in visit order, and each task's
    # "[TopoGen]" notes come back as a string written just before its
    # install line, exactly where the sequential loop printed them.
    # BUDA_TOPO_THREADS (explicit) > BUDA_THREADS (the CLI governor) >
    # hardware; a 1-thread pool or a small design keeps the sequential
    # loop.  The knob-memo replay stays sequential in both paths (it can
    # itself regenerate additively).
    try:
        n_thr = int(os.environ.get("BUDA_TOPO_THREADS")
                    or os.environ.get("BUDA_THREADS", "0") or 0)
    except ValueError:
        n_thr = 0
    n_thr = n_thr or (os.cpu_count() or 1)
    if n_thr > 1 and len(session.bundles) >= _BATCH_MIN_BUNDLES:
        preps = []
        for w in session.bundles:
            old_pin_uid = session._pinned_uid(w)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                prep = session._prep_hier_topo_gen(
                    w, use_center, use_double_detour, fp_cache,
                    comps_by_name, use_multi_trunk, use_hanan_loci,
                    use_spine_relays)
            preps.append((w, old_pin_uid, prep, buf.getvalue()))
        live = [(i, p) for i, (_w, _u, p, _t) in enumerate(preps)
                if p is not None]
        results = buda.generate_candidates_batch(
            [p[0] for _i, p in live], [p[1] for _i, p in live],
            [list(p[2]) for _i, p in live], n_thr) if live else []
        pool_of = {i: r for (i, _p), r in zip(live, results)}
        for i, (w, old_pin_uid, prep, pre_text) in enumerate(preps):
            if pre_text:
                sys.stdout.write(pre_text)
            n = 0
            if prep is not None:
                pool, notes = pool_of[i]
                if notes:
                    sys.stderr.write(notes)
                n = session._install_hier_topo_result(w, pool, prep[3])
            session._apply_hier_gen_knobs(w, fp_cache, comps_by_name,
                                          old_pin_uid)
            total_candidates += n
    else:
        for w in session.bundles:
            old_pin_uid = session._pinned_uid(w)
            n = session._generate_hier_topo_one(w, use_center, use_double_detour,
                                              fp_cache, comps_by_name,
                                              use_multi_trunk,
                                              use_hanan_loci=use_hanan_loci,
                                              use_spine_relays=use_spine_relays)
            # Honor the bundle's persisted generation-knob memo (v15): re-apply
            # prior generate_more_topologies accretions additively so the pool
            # does not silently revert on a bulk regeneration (Phase E2b — the
            # hier twin of the flat command's _apply_gen_knobs call).
            session._apply_hier_gen_knobs(w, fp_cache, comps_by_name,
                                          old_pin_uid)
            total_candidates += n
    print(f"generate_hier_topologies: {len(session.bundles)} bundles, "
          f"{total_candidates} total candidates")
    # Opt-in WL-dominance prune (set_prune_dominated) — after the pool is
    # final (knob memo replayed), before sidecar restore + persistence.
    session._cleanup_candidate_pools()
    # Restore the sidecar baseline onto the fresh candidates (see
    # generate_topologies); keeps live state and GUI consistent pre-plan.
    session._apply_selections()
    # Then the BDB's durable pins — before the persist wipes them (see
    # generate_topologies; template bundles restore pre-expansion, so a
    # restored pin propagates to every instance at run_planner hier).
    session._apply_bdb_pins()
    nt = session._persist_topologies()
    if nt:
        print(f"[BDB] persisted {nt} candidate topolog"
              f"{'y' if nt == 1 else 'ies'} to the open BDB.")


def cmd_generate_topologies_for_hbundle(session, cmd, args, cmd_line):
    # Usage: generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]
    # ("no_hanan_loci" opts out of the default-on Hanan-line trunk loci;
    #  "hanan_loci" = keep-on no-op.  "spine_relays" opts MST relay-hub
    #  completion into the collector-spine form.  An explicit loci polarity /
    #  spine opt-in is recorded in the knob memo so a bulk
    #  generate_hier_topologies can't silently flip the pool back — see
    #  _record_gen_knob_memo.)
    if not args:
        print("Error: generate_topologies_for_hbundle requires a bundle_id"); return
    if session.bdb is None:
        print("Error: generate_topologies_for_hbundle requires an open BDB"); return
    try:
        bid = int(args[0])
    except ValueError:
        print(f"Error: invalid bundle_id {args[0]!r}"); return
    (use_center, use_double_detour, use_multi_trunk,
     use_hanan_loci, loci_explicit, use_spine_relays) = _parse_gen_flags(args[1:])
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
                                      fp_cache, comps_by_name, use_multi_trunk,
                                      use_hanan_loci=use_hanan_loci,
                                      use_spine_relays=use_spine_relays)
    session._cleanup_candidate_pools([target_w])
    memo_tokens = []
    if loci_explicit:
        memo_tokens.append("hanan_loci" if use_hanan_loci else "no_hanan_loci")
    if use_spine_relays:
        memo_tokens.append("spine_relays")
    if memo_tokens:
        _record_gen_knob_memo(session, [target_w], memo_tokens)
    print(f"generate_topologies_for_hbundle: bundle {bid} — {n} candidates")
    if session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


COMMANDS = {
    "set_prune_dominated": cmd_set_prune_dominated,
    "set_dedup_loci": cmd_set_dedup_loci,
    "set_trim_mst_legs": cmd_set_trim_mst_legs,
    "set_trim_trunk_stubs": cmd_set_trim_trunk_stubs,
    "set_drop_dangling": cmd_set_drop_dangling,
    "set_keepout_loci": cmd_set_keepout_loci,
    "generate_topologies_for_bundle": cmd_generate_topologies_for_bundle,
    "generate_more_topologies": cmd_generate_more_topologies,
    "generate_topologies": cmd_generate_topologies,
    "generate_hier_topologies": cmd_generate_hier_topologies,
    "generate_topologies_for_hbundle": cmd_generate_topologies_for_hbundle,
}
