@doc
Feature: Global Congestion & Topology Selection
  As a chip planner
  I want to select the best topology for each bundle based on global congestion
  So that I avoid creating unroutable hotspots before detailed track assignment

  Background:
    Given a layer stack with:
      | Name    | ID | Direction  | Overhead |
      | M_Top_H | 3  | HORIZONTAL | 25%      | 
      # 25% overhead means width is scaled by 100/(100-25) = 1.33x

  Scenario: effective Width Calculation (Dilution)
    Given a bundle "DataBus" with raw width 10.0
    When I calculate demand on layer "M_Top_H"
    Then the effective width should be 13.33

  Scenario: Detecting Congestion on a Cut
    Given a routing channel (Cut) with capacity 100.0
    And I assign 8 bundles, each with effective width 13.0, to this channel
    When I calculate congestion
    Then the channel should be marked as "Overflowed" (Total 104.0 > 100.0)

  Scenario: Topology Selection Loop
    Given a bundle has two topology candidates:
      | Type | Path Description | Congestion Cost |
      | L    | Goes through Cut_A (Overflowed) | High            |
      | Z    | Goes through Cut_B (Empty)      | Low             |
    When I run the global topology selector
    Then the bundle should be assigned the "Z" topology

  Scenario: Multi-layer V routing — optimize_topologies returns BundleAssignment list
    Given a layer stack with M3 (V), M4 (H), M5 (V), M7 (V)
    And a wide open channel (capacity >> total demand)
    And a single bundle with a vertical segment
    When I call optimize_topologies
    Then the return value is a list of BundleAssignment objects
    And each BundleAssignment has bundle_id, topo_index, and v_layer_id fields
    And v_layer_id is 3 (M3 fills first)

  Scenario: Multi-layer V routing — spill from M3 to M5 under congestion
    Given a layer stack with M3 (V, id=3), M4 (H, id=4), M5 (V, id=5)
    And a floorplan with a narrow bottleneck channel (capacity < 2 × bundle width)
    And two bundles of equal width whose vertical segments cross the bottleneck
    When I call optimize_topologies
    Then the first (widest-first) bundle is assigned to M3
    And the second bundle spills to M5 (M3 now overflowed)