@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_ripup_reroute.py (~59 functions across stage a/b, the
# global pass, fast trials, the screen, and negotiate). The 3-scenario bound
# smoke spec is ripup_reroute.feature; this file is the comprehensive narrative
# for the whole RR speedup arc. Engine design: docs/internal/wishlist-healer.md.
Feature: ripup_reroute — feedback-driven rip-up & re-route (the full arc)
  As a chip planner
  I want a feedback pass that reads the ACTUAL NUTS overlaps / DetailedNUTS opens
  and re-routes contending bundles until the design is clean,
  So that congestion the planner's band model under-predicts is resolved.

  # ripup_reroute is a greedy hill-climb. Stage is auto-detected: after run_nuts
  # it drives down NUTS overlaps (scalar metric); after run_detailed_nuts it drives
  # down DNUTS opens with a lexicographic metric (opens, overlaps). Every accept is
  # a STRICT improvement on the true measured metric; snapshot/restore makes each
  # trial exactly reversible.

  Scenario: stage a drives NUTS overlaps to zero
    Given a congested design with NUTS overlaps after run_nuts
    When I run ripup_reroute
    Then it re-pins contending bundles and the overlap count reaches 0

  Scenario: stage b drives DetailedNUTS opens to zero
    Given DetailedNUTS opens after run_detailed_nuts
    When I run ripup_reroute
    Then the metric (opens, overlaps) is driven to (0, 0)

  Scenario: it is a no-op when already clean
    Given a design with no overlaps and no opens
    When I run ripup_reroute
    Then it reports the metric was already 0 and changes no selection

  Scenario: index alternates are tried farness-from-contention first
    # Under measured contention, the top-8 farness-ranked candidates from BEYOND
    # the 8-cheapest-estimate window are appended, so a higher-estimate class (OOB
    # trunk, BITRUNK tree) is promotable — committing only on a STRICTLY better metric.
    Given a contended bundle with index alternates
    When ripup_reroute scans its moves
    Then candidates farthest from the contention are tried first and cheaper ties win

  Scenario: the global-occupant pass fixes a non-contended band holder at stall
    # When the normal contender scan stalls above zero, a bounded pass ranks the
    # committed bundles holding each contention site's bands (band_occupants) and
    # trials each occupant's alternates ranked against THE SITE — reaching a fix no
    # contender-derived move can. Default on; no_global opts out.
    Given a stall where a NON-contended bundle holds the contended bands
    When ripup_reroute runs the global-occupant pass
    Then the occupant is re-pinned and the metric strictly drops
    And no_global reproduces the pre-global stop

  Scenario: fast trials skip metric-neutral passes; commits stay full-fidelity
    # Trials skip stage-a WL-only tighten and stage-b via emission; COMMITS always
    # re-run the full pipeline, so every commit strictly improves the true metric.
    Given ripup_reroute with fast trials (default)
    When it evaluates moves
    Then trials are cheaper and committed routes are full-pipeline states
    And no_fast_trials restores full trials

  Scenario: the fixed-context screen orders moves before full-trialing the top two
    # Each candidate is placed ALONE against every other bundle's frozen baseline
    # occupancy (~ms); only the top-2 screened moves are full-trialed, the rest are
    # DEFERRED to the iteration's full-fidelity stall sweep. The screen score is an
    # ORDERING, never a metric — accepts stay on the true full metric.
    Given ripup_reroute with the screen (default)
    When a contender has many index alternates
    Then only the top-2 screened moves are full-trialed and screened-out moves are deferred, not dropped
    And no_screen full-trials every alternate in farness order

  Scenario: warm trials are an opt-in pre-filter (default off)
    Given ripup_reroute with warm_trials
    Then each move is pre-filtered by a warm-start single-bundle re-solve, cold-swept at the stall point so the stop certificate stays a full cold sweep

  Scenario: max_iter bounds the number of outer iterations
    Given a congested design
    When I run ripup_reroute with max_iter 1
    Then at most one outer iteration runs

  Scenario: negotiate_congestion clears the bulk before ripup finishes the residual
    # Each actual overlap rectangle is injected as extra demand on the exact bands
    # where it happened, then both bundles of every overlap are re-planned unpinned.
    Given NUTS overlaps after run_nuts
    When I run negotiate_congestion
    Then the corrected cost model steers bundles off the contended bands, accepting only strict overlap-count improvement

  Scenario: ripup_reroute works in the hier flow
    Given a hier design after run_planner hier where self.bundles is the expanded per-instance list
    When I run ripup_reroute
    Then a re-route re-pins one instance in place
