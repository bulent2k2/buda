@landed
Feature: ripup_reroute clears congestion-induced NUTS overlaps
  As a chip planner
  I want a feedback pass that re-routes a contending bundle after NUTS
  So that congestion the planner's band model under-predicts gets resolved

  Scenario: Stage-a NUTS overlaps are reduced to zero
    Given a congested two-bus floorplan pinned into one NUTS overlap
    When I run ripup_reroute after run_nuts
    Then the NUTS overlap count is 0
    And ripup_reroute re-pinned at least one bundle

  Scenario: ripup_reroute is a no-op when there are no overlaps
    Given a clean two-bus floorplan with no NUTS overlap
    When I run ripup_reroute after run_nuts
    Then ripup_reroute reports the metric was already 0
    And no bundle topology selection changed

  Scenario: max_iter bounds the number of moves
    Given a congested two-bus floorplan pinned into one NUTS overlap
    When I run ripup_reroute with max_iter 1
    Then ripup_reroute performed at most 1 iteration
