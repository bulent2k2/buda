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
import sys

import buda

from buda_session.util import _RR_DEFAULT_MAX_ITER

from ._options import looks_numeric, reject_unknown_options

# The complete flag vocabulary of ripup_reroute (besides an optional integer
# max_iter).  Used both to strip flags from the numeric arg and to reject typos.
_RR_FLAGS = ("use_edge_candidates", "no_global",
             "fast_trials", "no_fast_trials",
             "screen", "no_screen",
             "warm_trials", "no_warm_trials",
             "converge_guard", "no_converge_guard",
             "no_class_moves", "no_release_moves",
             "no_parallel_sweep")


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
    # Tapered fan-in: (re)derive the selected candidates' per-segment bit
    # membership so extraction widths taper — covers the resume path where
    # run_planner was not re-run this session (idempotent otherwise).
    session._derive_fanin_bits_all(selected_only=True)
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
    # Post-NUTS dead-span escalation, run HERE (before any healer) so the
    # whole negotiate/ripup cascade can adapt around the escalated layers.
    # Measured (real config): escalating BEFORE the healers fixes flows the
    # stage-b `_heal_dead_spans` fold alone leaves open — mix 16->0 opens,
    # bigHalf 190->94 — and keeping that fold too (it still runs at stage b)
    # recovers the one flow this early pass alone regresses (mix2 stays 42,
    # not 66).  The two passes compose: a dead LOW segment moved to TOP here
    # is not re-found at stage b.  Fires on the explicit opt-in flag, or
    # AUTOMATICALLY when the flow has DECLARED healers ahead (issue #444:
    # explicit `set_planner_param healersAhead 1`, the same gate the kSegsRel
    # default uses — no script-text scan, so every execution structure agrees)
    # — off for a scriptless/interactive run with no declaration, where the
    # stage-b fold remains the sole path.
    _healers_declared = session._planner_params.get("healersAhead", 0.0) > 0.0
    do_escalate = (getattr(session, "_dead_span_escalate", False) or
                   (getattr(session, "_heal_dead_spans_in_healers", True)
                    and getattr(session, "_dead_span_auto_at_run_nuts", True)
                    and _healers_declared))
    if do_escalate:
        n_esc = session._escalate_dead_low_segments()
        if n_esc:
            print(f"[NUTS] dead-span escalation: moved {n_esc} dead LOW "
                  f"segment(s) to a TOP layer and re-solved.")
            # The escalation mutated w.plan.seg_layers — the PLANNER's layer
            # assignment.  The _persist_nuts() below writes only the bus_segment
            # rows (the TOP geometry); without also re-persisting the planner
            # output, topology_segment.assigned_layer keeps the stale LOW pin,
            # so a load_pipeline resume would restore LOW and a resumed run_nuts
            # could recreate the dead assignment.  Mirror the stage-b heal's
            # _checkpoint_routing (Codex #351 P2).  Trial-guarded inside.
            if session.bdb is not None:
                session._persist_planner_output()
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
    if args:
        # An unrecognized token used to be silently ignored, running the
        # LO_HI default — so a typo'd 'hi-lo'/'hilo' produced the OPPOSITE of
        # the requested bit order with no diagnostic (audit P5-06).
        if args[0].lower() not in ("lo_hi", "hi_lo"):
            print(f"Error: run_detailed_nuts bit-order must be 'lo_hi' or "
                  f"'hi_lo', got '{args[0]}'")
            return
        session._detailed_bit_order = args[0].upper()

    if session.nuts_result is None:
        print("Error: run_detailed_nuts requires run_nuts to have been called first")
        return
    if session.routing_grid is None:
        print("Error: run_detailed_nuts requires a routing grid (def_track_pattern)")
        return

    try:
        session._run_detailed_nuts(bit_order=session._detailed_bit_order)
    except RuntimeError as e:
        # Bottom-up mismatch under the 'stop' policy: refuse, keep the
        # session usable (the user can fix placement or switch policy with
        # 'check_template_tracks on_mismatch independent' and re-run).
        print(f"Error: {e}")
        return
    # Measured keepout-cull heal (the #523 spreader outcome): escalate
    # cull-doomed LOW segments under the refold's accept contract, so
    # healerless flows get the cover ripup's refold gives healered ones.
    # No-op without culled bits; persists below only the accepted state.
    session._final_cull_heal()
    # Measured TOP re-seat heal (#536 follow-up): re-seat supply-doomed TOP
    # segments — the class the LOW->TOP escalation family cannot reach — on
    # an alternate same-direction TOP layer with real supply, same
    # componentwise accept.  Skips itself when a healer already ran this
    # session (the flow owns its healing then).
    session._final_reseat_heal()
    # Measured-accept pairwise-overlap alignment (opt-in): re-solve DNUTS with
    # same-net stub pairs aligned, keep only when unplaced/overlaps don't rise
    # and WL strictly drops.  No-op unless set_pair_align_heal on.
    session._final_pair_align_heal()
    n_ns, n_nv = session._persist_detailed_nuts()
    if n_ns:
        print(f"[BDB] persisted {n_ns} net segment(s) and {n_nv} "
              f"net via(s) to the open BDB.")


def cmd_ripup_reroute(session, cmd, args, cmd_line):
    # Usage: ripup_reroute [max_iter] [use_edge_candidates] [no_global]
    #                      [no_class_moves]
    #                      [fast_trials|no_fast_trials] [screen|no_screen]
    # Stage auto-detected: after run_detailed_nuts ⇒ drive down DNUTS opens;
    # else after run_nuts ⇒ drive down NUTS overlaps.
    # `use_edge_candidates` (off by default) toggles the per-edge MST L/Z
    # flip move-source; `no_global` disables the global-occupant pass that
    # otherwise runs when the contender scan stalls above zero (the b61-class
    # re-route of NON-contended band holders); the numeric token (any order)
    # is max_iter.
    use_edge_candidates = "use_edge_candidates" in args
    use_global = "no_global" not in args
    # Fast trials (round 3): default on; `no_fast_trials` opts out (trials
    # then run the full pipeline, the pre-round-3 behavior), `fast_trials`
    # forces on.  Commits always re-run full either way.
    fast_trials = None
    if "no_fast_trials" in args:
        fast_trials = False
    elif "fast_trials" in args:
        fast_trials = True
    # Fixed-context screen (round 3, final lever): default per
    # _RR_SCREEN_DEFAULT; `no_screen` opts out (every contender alternate is
    # full-trialed in farness order, the pre-screen behavior), `screen`
    # forces on.  Accepts stay on the true full metric either way.
    screen = None
    if "no_screen" in args:
        screen = False
    elif "screen" in args:
        screen = True
    # Warm trials (round 4): default per _RR_WARM_TRIALS_DEFAULT;
    # `no_warm_trials` opts out (every move cold-trialed directly, the
    # round-3 behavior), `warm_trials` forces on.  Accepts stay on the true
    # cold metric either way, and the stop certificate stays a full cold
    # sweep (warm-rejected moves are cold-swept at the stall point).
    warm = None
    if "no_warm_trials" in args:
        warm = False
    elif "warm_trials" in args:
        warm = True
    # Convergence guard (default on): stop early on an over-capacity design
    # that can't converge.  `no_converge_guard` disables it (grind to
    # max_iter); `converge_guard` forces it on.
    converge_guard = None
    if "no_converge_guard" in args:
        converge_guard = False
    elif "converge_guard" in args:
        converge_guard = True
    # Bottom-up template CLASS moves (default on): when the residual
    # contention sits on hier.locked template instances, re-pin the cell
    # TEMPLATE and re-route the whole class in one measured move.  A no-op
    # on flat / non-bottom-up designs; `no_class_moves` disables it.
    use_class_moves = "no_class_moves" not in args
    # Measured-infeasibility uniformity break (default on, but ALSO gated
    # on the `check_template_tracks on_mismatch independent` policy — the
    # user's declared willingness to solve instances individually): at a
    # stage-b stall with even the class pass exhausted, release a locked
    # instance whose copied routing is measured DNUTS-open and solve it
    # individually (opens #14 (a)).  `no_release_moves` disables.
    use_release_moves = "no_release_moves" not in args
    # Parallel stall sweep (P1, default on): the deferred stall-certificate
    # moves are evaluated on C++ worker threads; `no_parallel_sweep`
    # restores the sequential sweep.
    use_parallel_sweep = None
    if "no_parallel_sweep" in args:
        use_parallel_sweep = False
    # Everything that isn't a known flag must be the single numeric max_iter —
    # a misspelled flag (e.g. `no_screeen`) used to be silently dropped when a
    # numeric token was also present (only nums[0] was consumed).
    leftover = [a for a in args if a not in _RR_FLAGS]
    unknown = [a for a in leftover if not looks_numeric(a)]
    reject_unknown_options("ripup_reroute", unknown, _RR_FLAGS)
    nums = [a for a in leftover if looks_numeric(a)]
    if len(nums) > 1:
        print(f"Error: ripup_reroute: at most one max_iter, got "
              f"{', '.join(nums)}"); return
    max_iter = int(nums[0]) if nums else _RR_DEFAULT_MAX_ITER
    session._ripup_reroute(max_iter=max_iter,
                           use_edge_candidates=use_edge_candidates,
                           use_global=use_global,
                           fast_trials=fast_trials,
                           screen=screen,
                           warm=warm,
                           converge_guard=converge_guard,
                           use_class_moves=use_class_moves,
                           use_release_moves=use_release_moves,
                           use_parallel_sweep=use_parallel_sweep)


def cmd_refine_selection(session, cmd, args, cmd_line):
    # Usage: refine_selection [max_moves] [chase_overlaps]
    # Measured selection WL polish (selection-basis lever 3,
    # wishlist-planner): sweep every eligible bundle's SELECTION on the
    # MEASURED result — screen all alternates against the frozen placement,
    # full-trial the best two, adopt only when opens, overlaps and interval
    # violations are parity-or-better componentwise AND realized WL is lower
    # (chase_overlaps: plain lexicographic — the aggressive pre-healer
    # form).  Run it at the END of the flow, after the healers.  Skips
    # user pins and hier.locked bottom-up copies.  Opt-in: flows that do
    # not call it are byte-identical.
    flags = ("chase_overlaps",)
    unknown = [a for a in args if not looks_numeric(a) and a not in flags]
    reject_unknown_options("refine_selection", unknown, flags)
    nums = [a for a in args if looks_numeric(a)]
    # looks_numeric admits floats ("2.5") and any count of values; the syntax
    # promises at most ONE integer max_moves — reject the rest loudly instead
    # of crashing on int() or silently dropping the extras (review #525).
    if len(nums) > 1 or (nums and not nums[0].isdigit()):
        print(f"Error: refine_selection takes at most one non-negative "
              f"integer max_moves argument, got: {' '.join(nums)}")
        sys.exit(1)
    max_moves = int(nums[0]) if nums else 30
    session._refine_selection(max_moves,
                              chase_overlaps="chase_overlaps" in args)


def cmd_negotiate_congestion(session, cmd, args, cmd_line):
    # Usage: negotiate_congestion [max_iter] [class_moves|no_class_moves]
    # Measured-congestion feedback (run after run_nuts): inject the
    # actual NUTS overlaps as band demand and let the planner re-price
    # both sides of each overlap off the contended bands.
    # `class_moves` (OPT-IN, default off): when an affected bundle is a
    # hier.locked bottom-up template instance, negotiate its TEMPLATE
    # class in the cell-local frame under the price-translated injected
    # demand (negotiate v2 — docs/internal/bottomup_healer_templates.md
    # item D).  Opt-in because it was measured endpoint-neutral at best:
    # on mix2_fast_bottomup the stage-a metric (abstract overlaps) is
    # blind to DNUTS quality, so the bigger negotiated shuffles trade it
    # away (final opens 8->16 at default ripup budgets; equal at 30),
    # and the stage-b iteration is price-rejected — ripup's class moves
    # already cover the endpoint.
    # `press` (OPT-IN, default off): a rejected iteration does not end the
    # run — restore and RETRY under the grown PathFinder history pressure
    # (which the first-failure stop computes and then throws away), until
    # max_iter or a failed retry reproduces the SAME metric as the previous
    # failure (deterministic replans insensitive to the grown amounts =
    # certified waste).  Opt-in because it measured 0 better / 1 worse on
    # the corpus (2026-08-07): the caps/mix stalls are price-INSENSITIVE
    # (the retry repeats byte-identically), and on mix2_fast_on_aligned_sql
    # the pressed retries DO beat the stall on negotiate's own metric
    # (stage b 150 (ovl 12) -> 130 (ovl 6), abstract WL -6.5%) but the
    # better hand-off shifts ripup's greedy basin and the flow ENDPOINT
    # lands worse (2/16/1 -> 4/28/2) — the healer-composition hazard.
    flags = ("class_moves", "no_class_moves", "press", "no_press")
    use_class_moves = "class_moves" in args
    use_press = "press" in args
    leftover = [a for a in args if a not in flags]
    unknown = [a for a in leftover if not looks_numeric(a)]
    reject_unknown_options("negotiate_congestion", unknown, flags)
    max_iter = int(leftover[0]) if leftover else 5
    session._negotiate_congestion(max_iter=max_iter,
                                  use_class_moves=use_class_moves,
                                  use_press=use_press)


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


def cmd_set_dead_span_escalate(session, cmd, args, cmd_line):
    # Usage: set_dead_span_escalate [on|off]
    # Opt-in post-NUTS dead-span escalation: after every run_nuts, a LOW
    # segment whose FINAL placed geometry has zero keepout-clear signal
    # tracks (a guaranteed DetailedNUTS open) is moved to the cheapest
    # same-direction TOP layer and NUTS re-solves.  Off by default =
    # bit-identical.  See wishlist-planner "dead-span discriminator".
    if not args:
        state = "on" if getattr(session, "_dead_span_escalate", False) else "off"
        print(f"dead_span_escalate is {state}")
        return
    val = args[0].lower()
    if val not in ("on", "off"):
        print(f"Error: set_dead_span_escalate expects on|off, got {args[0]!r}")
        return
    session._dead_span_escalate = (val == "on")


def cmd_set_pair_align_heal(session, cmd, args, cmd_line):
    # Usage: set_pair_align_heal [on|off]
    # Opt-in MEASURED-ACCEPT pairwise-overlap alignment at run_detailed_nuts:
    # re-solve DNUTS with same-net stub pairs aligned to a shared track
    # window and KEEP it only when unplaced/overlaps do not rise and detailed
    # WL strictly drops (else restore).  Off by default = bit-identical.  The
    # accept is what makes it safe: the unconditional form was corpus
    # net-negative (concentration starves tracks); the gate keeps only the
    # designs it helps.  Scoped out of bottom-up (locked) sessions.
    if not args:
        state = "on" if getattr(session, "_pair_align_heal", False) else "off"
        print(f"pair_align_heal is {state}")
        return
    val = args[0].lower()
    if val not in ("on", "off"):
        print(f"Error: set_pair_align_heal expects on|off, got {args[0]!r}")
        return
    session._pair_align_heal = (val == "on")


COMMANDS = {
    "run_nuts": cmd_run_nuts,
    "run_detailed_nuts": cmd_run_detailed_nuts,
    "ripup_reroute": cmd_ripup_reroute,
    "negotiate_congestion": cmd_negotiate_congestion,
    "refine_selection": cmd_refine_selection,
    "run_nuts_on_layer": cmd_run_nuts_on_layer,
    "set_dead_span_escalate": cmd_set_dead_span_escalate,
    "set_pair_align_heal": cmd_set_pair_align_heal,
}
