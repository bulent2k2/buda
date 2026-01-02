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