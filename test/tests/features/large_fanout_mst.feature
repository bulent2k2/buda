Feature: Large Fan-out Steiner Tree Topology Generation
  As a chip planner
  I want to generate efficient topologies for large fan-out bundles using Steiner Tree heuristics
  So that I can minimize wirelength and congestion for broadcast and multicast buses

  Background:
    Given a large-scale floorplan with 50 blocks in a 10x5 grid
    And routing layers M3 through M7 are defined

  Scenario: Generating Steiner Tree for Global Broadcast
    Given a bundle "broadcast" with 49 receivers distributed across the chip
    When I generate topologies with MST heuristic
    Then I should receive topology candidates with multiple branching points
    And the total wirelength should be significantly less than a star-topology (direct stubs)
    And the topology should consist of one or more main trunks and local branches

  Scenario: Multicast Bundle from Center to Corners
    Given a bundle "center_to_corners" with 4 receivers at the chip corners
    When I generate topologies
    Then I should receive a "TRUNK" based topology candidate
    And the planner should be able to select a congestion-aware path for the trunk

  Scenario: Steiner Tree Optimization for High-Density Channels
    Given multiple large fan-out bundles passing through the same central channel
    When I run the planner with MST topologies
    Then the peak congestion (overflow) should be within acceptable limits
    And the NUTS solver should successfully place all segments without major overlaps
