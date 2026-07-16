@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_planner_*.py (incl. test_planner_refine.py) and the
# signal-track / kPeak tests. Design: docs/internal/planner_signal_track_capacity.md,
# docs/internal/kpeak_measurements.md, docs/internal/refine_passes_default.md.
Feature: Congestion-planner tuning knobs
  As a chip planner
  I want tunable planner knobs and capacity modes,
  So that topology selection and layer assignment trade off congestion,
  wirelength, routability, and track supply for my design.

  # set_planner_param <name> <value> takes effect at the next run_planner; knobs
  # may be changed between runs to re-plan. Overflow is a HARD constraint enforced
  # by the escalation ladder: STRICT -> rip-up & replan -> ALLOW_OVERFLOW -> BEST_EFFORT.

  Scenario: signal_tracks charges band capacity in discrete signal-track count
    # Instead of layout width, so a band short of tracks surfaces as planner
    # overflow rather than a silent DNUTS open. Needs def_track_pattern.
    Given track patterns defined
    When I run run_planner signal_tracks
    Then a band short of signal tracks surfaces as planner overflow

  Scenario: kPeak steers candidates off nearly-full bands (routability-aware)
    # Opt-in (default 0 = off): pay for the worst band's EXISTING fill fraction so
    # candidates avoid nearly-full bands before overflow. Carries an absolute-supply
    # floor: a band whose real signal-track supply cannot host the bundle prices as full.
    Given set_planner_param kPeak set above 0
    When I run run_planner
    Then candidates steer off nearly-full bands and a track-starved band is priced as full

  Scenario: refine_passes revisits committed bundles deepest-first against real usage
    # Adopts a replan only when leaving the old topology is STRICTLY better by score.
    # Default 1 for hier planning, 0 for flat (an explicit set, incl. 0, always wins).
    Given a hier design
    When I run run_planner hier
    Then committed bundles are refined deepest-first, adopting only strictly-better replans

  Scenario: the cost knobs weight the selection score
    When I set kCong, kSpan, kWL, kBalance, kHeight, base_cost_non_top
    Then congestion, span mismatch, wirelength, TOP-layer balancing, short-stub layer height, and non-TOP penalty are weighted accordingly

  Scenario: overflow escalates through the ladder, never silently overflowing
    Given a bundle with no overflow-free candidate under STRICT
    When run_planner reaches it
    Then it rips up earlier bundles ranked by demand on the contended bands, then ALLOW_OVERFLOW with a WARNING, then BEST_EFFORT — each step loud

  Scenario: run_planner hier expands cell bundles and plans top-down
    Given an open hier BDB
    When I run run_planner hier
    Then cell-level bundles expand to per-instance wrappers planned top-down with per-instance demand reservations
