@landed
# Bound executable smoke spec (test_refine_selection.py) for the measured
# selection WL polish — selection-basis lever 3 (wishlist-planner "Selection
# basis"): the two existing measured loops cannot recover realized WL (ripup's
# metric is overlap/opens-only; the planner's refine_passes scores through the
# cost model whose WL term is the generation-time estimate), so this end-of-flow
# pass re-ranks selections on the MEASURED result under an accept guard that
# can only improve WL — opens and overlaps parity-or-better componentwise.
Feature: refine_selection recovers realized wirelength on measured routes
  As a chip planner
  I want a final measured pass that re-ranks topology selections on placed WL
  So that a candidate that routes shorter than it estimates wins the selection

  Scenario: A measurably suboptimal selection is re-ranked to the shorter route
    Given a one-bus floorplan planned on its longest candidate and unpinned
    When I run refine_selection
    Then the realized wirelength strictly decreases
    And the NUTS overlap count did not increase
    And refine_selection committed at least one move

  Scenario: refine_selection is a no-op on an optimal selection
    Given a one-bus floorplan planned normally
    When I run refine_selection
    Then refine_selection committed no moves
    And no bundle topology selection changed

  Scenario: A user-pinned bundle is inviolable
    Given a one-bus floorplan planned on its longest candidate and still pinned
    When I run refine_selection
    Then refine_selection committed no moves
    And no bundle topology selection changed

  Scenario: chase_overlaps is an accepted token
    Given a one-bus floorplan planned normally
    When I run refine_selection with chase_overlaps
    Then refine_selection committed no moves

  Scenario: An unknown option is rejected
    Given a one-bus floorplan planned normally
    When I run refine_selection with a bogus option
    Then refine_selection reports the unknown option

  Scenario: refine_selection requires the pipeline to have run
    Given a fresh session with no routed design
    When I run refine_selection
    Then refine_selection reports it has nothing to refine
